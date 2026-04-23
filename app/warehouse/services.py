"""Warehouse domain services — hook on receipt item creation (ADR-007/ADR-008)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogProduct, CatalogSupplier
from app.catalog.services import record_listing_price_from_purchase
from app.pricing.constants import DEFAULT_MARGIN_PERCENT, DEFAULT_SHIPPING_PER_KG_USD
from app.pricing.models import (
    PricingExchangeRate,
    PricingPriceCalculation,
    PricingPurchaseType,
)
from app.pricing.schemas import ManufacturerPriceInput, RetailPriceInput
from app.pricing.service import (
    calculate_manufacturer_price,
    calculate_retail_price,
    determine_rounding_step,
)
from app.procurement.models import (
    ProcurementPurchase,
    ProcurementPurchaseItem,
    ProcurementShipment,
)
from app.warehouse.models import (
    WarehousePendingPriceResolution,
    WarehouseReceipt,
    WarehouseReceiptItem,
    WarehouseStockItem,
)

logger = logging.getLogger(__name__)

FORMULA_VERSION = "adr-007-pkg2-v1"
DEFAULT_STOCK_LOCATION = "склад"


async def _get_latest_exchange_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str = "RUB",
) -> PricingExchangeRate | None:
    result = await session.execute(
        select(PricingExchangeRate)
        .where(
            PricingExchangeRate.from_currency == from_currency,
            PricingExchangeRate.to_currency == to_currency,
        )
        .order_by(PricingExchangeRate.valid_from.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def build_price_input(
    session: AsyncSession,
    purchase_item: ProcurementPurchaseItem,
    purchase: ProcurementPurchase,
    supplier: CatalogSupplier,
    weight_kg: Decimal,
) -> RetailPriceInput | ManufacturerPriceInput | None:
    """Construct a pricing input schema from procurement data.

    Returns None with a WARNING if required exchange rate is unavailable.
    Chooses Retail vs Manufacturer path from supplier.default_purchase_type.
    """
    assert purchase_item.unit_cost is not None
    assert purchase.currency is not None

    currency = purchase.currency
    unit_cost = purchase_item.unit_cost

    # Determine whether we need an exchange rate.
    needs_rate = currency != "RUB"

    rate_obj: PricingExchangeRate | None = None
    if needs_rate:
        rate_obj = await _get_latest_exchange_rate(session, currency)
        if rate_obj is None:
            logger.warning(
                "No exchange rate found for %s→RUB — cannot calculate price "
                "for purchase %d item %d",
                currency,
                purchase.id,
                purchase_item.id,
            )
            return None

    purchase_type = supplier.default_purchase_type

    if purchase_type == "manufacturer":
        # Manufacturer path: product_price_fcy + exchange_rate, no logistics legs.
        if rate_obj is None:
            # Currency is RUB — ManufacturerPriceInput always requires exchange_rate.
            logger.warning(
                "Manufacturer path requires exchange rate but currency is RUB "
                "for purchase %d item %d — falling back to retail path",
                purchase.id,
                purchase_item.id,
            )
            purchase_type = "retail"
        else:
            return ManufacturerPriceInput(
                product_price_fcy=unit_cost,
                currency=currency,
                pricing_exchange_rate=rate_obj.rate,
                pricing_rate_id=rate_obj.id,
                margin_percent=DEFAULT_MARGIN_PERCENT,
            )

    # Retail path (default).
    if needs_rate:
        assert rate_obj is not None
        return RetailPriceInput(
            purchase_cost=unit_cost,
            purchase_currency=currency,
            weight_kg=weight_kg,
            shipping_per_kg_usd=DEFAULT_SHIPPING_PER_KG_USD,
            pricing_exchange_rate=rate_obj.rate,
            pricing_rate_id=rate_obj.id,
            margin_percent=DEFAULT_MARGIN_PERCENT,
        )

    # RUB purchase — no exchange rate needed; zero out shipping to avoid rate requirement.
    return RetailPriceInput(
        purchase_cost=unit_cost,
        purchase_currency="RUB",
        weight_kg=weight_kg,
        shipping_per_kg_usd=Decimal("0"),
        margin_percent=DEFAULT_MARGIN_PERCENT,
    )


async def on_warehouse_receipt_item_created(
    receipt_item_id: int,
    session: AsyncSession,
) -> None:
    """Hook: called after a warehouse_receipt_item row is flushed.

    Algorithm (ADR-008 section 2):
    1. Load receipt_item → receipt → shipment → purchase → purchase_item.
    2. Skip if no purchase_item, unit_cost, or currency.
    3. Build PriceInput, run pricing calculation, save PricingPriceCalculation.
    4. Find existing stock_item by (product_id, location).
       a. No stock → create stock_item with calculated price.
       b. Exists, price within rounding_step → merge quantity.
       c. Exists, price differs → create WarehousePendingPriceResolution.
    5. Always record catalog_listing_price from the purchase data.

    Must run in the same transaction as the receipt_item insert.
    """
    # 1. Load full chain.
    receipt_item_result = await session.execute(
        select(WarehouseReceiptItem).where(WarehouseReceiptItem.id == receipt_item_id)
    )
    receipt_item = receipt_item_result.scalar_one()

    receipt_result = await session.execute(
        select(WarehouseReceipt).where(WarehouseReceipt.id == receipt_item.receipt_id)
    )
    receipt = receipt_result.scalar_one()

    shipment_result = await session.execute(
        select(ProcurementShipment).where(ProcurementShipment.id == receipt.shipment_id)
    )
    shipment = shipment_result.scalar_one()

    purchase_result = await session.execute(
        select(ProcurementPurchase).where(ProcurementPurchase.id == shipment.purchase_id)
    )
    purchase = purchase_result.scalar_one()

    # 2. Find matching purchase item.
    purchase_item_result = await session.execute(
        select(ProcurementPurchaseItem).where(
            ProcurementPurchaseItem.purchase_id == purchase.id,
            ProcurementPurchaseItem.product_id == receipt_item.product_id,
        )
    )
    purchase_item = purchase_item_result.scalar_one_or_none()

    if purchase_item is None:
        logger.warning(
            "receipt_item %d: no matching purchase_item for "
            "purchase %d, product %d — skipping price hook",
            receipt_item_id,
            purchase.id,
            receipt_item.product_id,
        )
        return

    if purchase_item.unit_cost is None:
        logger.warning(
            "receipt_item %d: purchase_item %d has unit_cost=NULL — skipping",
            receipt_item_id,
            purchase_item.id,
        )
        return

    if purchase.currency is None:
        logger.warning(
            "receipt_item %d: purchase %d has currency=NULL — skipping",
            receipt_item_id,
            purchase.id,
        )
        return

    # Load supplier and product for type determination and weight.
    supplier_result = await session.execute(
        select(CatalogSupplier).where(CatalogSupplier.id == purchase.supplier_id)
    )
    supplier = supplier_result.scalar_one()

    product_result = await session.execute(
        select(CatalogProduct).where(CatalogProduct.id == receipt_item.product_id)
    )
    product = product_result.scalar_one()

    weight_kg = receipt_item.actual_weight_per_unit or product.declared_weight or Decimal("0")

    # 3. Build pricing input and calculate.
    price_input = await build_price_input(
        session=session,
        purchase_item=purchase_item,
        purchase=purchase,
        supplier=supplier,
        weight_kg=weight_kg,
    )
    if price_input is None:
        # Warning already logged inside build_price_input.
        return

    if isinstance(price_input, RetailPriceInput):
        result = calculate_retail_price(price_input)
    else:
        result = calculate_manufacturer_price(price_input)

    # 4. Persist PricingPriceCalculation.
    purchase_type = PricingPurchaseType(result.purchase_type)
    calc = PricingPriceCalculation(
        product_id=receipt_item.product_id,
        input_params=price_input.model_dump(mode="json"),
        breakdown=result.breakdown,
        final_price=result.final_price,
        currency="RUB",
        calculated_at=datetime.now(tz=UTC),
        formula_version=FORMULA_VERSION,
        purchase_type=purchase_type,
        pre_round_price=result.pre_round_price,
        rounding_step=result.rounding_step,
        margin_percent=result.margin_percent,
        discount_percent=result.discount_percent,
    )
    session.add(calc)
    await session.flush()

    new_price = result.final_price

    # 5. Find existing stock_item.
    stock_result = await session.execute(
        select(WarehouseStockItem).where(
            WarehouseStockItem.product_id == receipt_item.product_id,
            WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
        )
    )
    stock_item = stock_result.scalar_one_or_none()

    if stock_item is None:
        # 5a. First receipt — create stock.
        new_stock = WarehouseStockItem(
            product_id=receipt_item.product_id,
            quantity=receipt_item.quantity,
            location=DEFAULT_STOCK_LOCATION,
            price_calculation_id=calc.id,
            receipt_item_id=receipt_item.id,
        )
        session.add(new_stock)

    else:
        # Determine existing price from current price_calculation_id.
        existing_price: Decimal | None = None
        if stock_item.price_calculation_id is not None:
            existing_calc_result = await session.execute(
                select(PricingPriceCalculation).where(
                    PricingPriceCalculation.id == stock_item.price_calculation_id
                )
            )
            existing_calc = existing_calc_result.scalar_one_or_none()
            if existing_calc is not None:
                existing_price = existing_calc.final_price

        if existing_price is None:
            # Stock exists but has no price — treat as first receipt.
            stock_item.quantity = stock_item.quantity + receipt_item.quantity
            stock_item.price_calculation_id = calc.id
            stock_item.receipt_item_id = receipt_item.id

        else:
            step = determine_rounding_step(existing_price)
            price_diff = abs(new_price - existing_price)

            if price_diff <= Decimal(str(step)):
                # 5b. Prices match within rounding tolerance — merge.
                stock_item.quantity = stock_item.quantity + receipt_item.quantity
                stock_item.receipt_item_id = receipt_item.id
                # price_calculation_id unchanged (keep existing).

            else:
                # 5c. Price conflict — create pending resolution.
                pending = WarehousePendingPriceResolution(
                    receipt_item_id=receipt_item.id,
                    existing_stock_item_id=stock_item.id,
                    new_price_calculation_id=calc.id,
                )
                session.add(pending)

    # 6. Always record catalog listing price.
    await record_listing_price_from_purchase(
        session=session,
        product_id=receipt_item.product_id,
        source_id=purchase.supplier_id,
        unit_cost=purchase_item.unit_cost,
        currency=purchase.currency,
        observed_at=receipt.received_at,
        purchase_id=purchase.id,
    )
