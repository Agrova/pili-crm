"""Tool: receive stock from a procurement purchase (ADR-007/008 hook integration)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.procurement.models import (
    ProcurementPurchase,
    ProcurementPurchaseItem,
    ProcurementPurchaseStatus,
    ProcurementShipment,
)
from app.procurement.services import on_purchase_delivered
from app.warehouse.models import WarehouseReceipt, WarehouseReceiptItem
from app.warehouse.services import on_warehouse_receipt_item_created
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "receive_stock"
DESCRIPTION = (
    "Оформляет приёмку товара от поставщика (создаёт warehouse_receipt_item). "
    "Запускает hook ADR-007/008: рассчитывает продажную цену, обновляет склад "
    "или создаёт pending price resolution при ценовом конфликте. "
    "Если все позиции закупки получены — автоматически переводит закупку "
    "в статус delivered."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "purchase_id": {
            "type": "integer",
            "description": "ID закупки (procurement_purchase.id)",
        },
        "product_id": {
            "type": "integer",
            "description": "ID товара из каталога (catalog_product.id)",
        },
        "quantity": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Количество принятых единиц",
        },
        "actual_weight_per_unit": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Фактический вес единицы, кг (опционально; переопределяет declared_weight)",  # noqa: E501
        },
    },
    "required": ["purchase_id", "product_id", "quantity"],
}

_RECEIVED_PRODUCTS_SQL = text(
    """
    SELECT COUNT(DISTINCT ri.product_id)
    FROM warehouse_receipt_item ri
    JOIN warehouse_receipt wr ON wr.id = ri.receipt_id
    JOIN procurement_shipment ps ON ps.id = wr.shipment_id
    WHERE ps.purchase_id = :pid
    """
)


async def run(
    session: AsyncSession,
    purchase_id: int,
    product_id: int,
    quantity: float,
    actual_weight_per_unit: float | None = None,
) -> dict[str, Any]:
    try:
        # 1. Load and validate purchase.
        purchase = (
            await session.execute(
                select(ProcurementPurchase).where(ProcurementPurchase.id == purchase_id)
            )
        ).scalar_one_or_none()
        if purchase is None:
            return {"status": "error", "error": "purchase_not_found", "purchase_id": purchase_id}
        if purchase.status == ProcurementPurchaseStatus.cancelled:
            return {"status": "error", "error": "purchase_cancelled", "purchase_id": purchase_id}

        # 2. Find or create shipment for this purchase.
        shipment = (
            await session.execute(
                select(ProcurementShipment)
                .where(ProcurementShipment.purchase_id == purchase_id)
                .order_by(ProcurementShipment.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if shipment is None:
            shipment = ProcurementShipment(purchase_id=purchase_id)
            session.add(shipment)
            await session.flush()

        # 3. Create a new receipt for this receiving event.
        receipt = WarehouseReceipt(
            shipment_id=shipment.id,
            received_at=datetime.now(tz=UTC),
        )
        session.add(receipt)
        await session.flush()

        # 4. Create receipt item.
        weight = (
            Decimal(str(actual_weight_per_unit)) if actual_weight_per_unit is not None else None
        )
        ri = WarehouseReceiptItem(
            receipt_id=receipt.id,
            product_id=product_id,
            quantity=Decimal(str(quantity)),
            actual_weight_per_unit=weight,
        )
        session.add(ri)
        await session.flush()

        # 5. Hook: price calculation + stock update / pending resolution (ADR-007/008).
        #    Must run in the same transaction as the receipt_item insert.
        await on_warehouse_receipt_item_created(ri.id, session)

        # 6. Auto-delivered: if all purchase_items now have receipt_items, mark delivered.
        if purchase.status != ProcurementPurchaseStatus.delivered:
            purchase_item_count = (
                await session.execute(
                    select(func.count(func.distinct(ProcurementPurchaseItem.product_id))).where(
                        ProcurementPurchaseItem.purchase_id == purchase_id
                    )
                )
            ).scalar_one()

            if purchase_item_count > 0:
                received_count = (
                    await session.execute(_RECEIVED_PRODUCTS_SQL, {"pid": purchase_id})
                ).scalar_one()

                if received_count >= purchase_item_count:
                    purchase.status = ProcurementPurchaseStatus.delivered
                    await session.flush()
                    await on_purchase_delivered(purchase_id, session)

        await session.commit()

        return {
            "status": "ok",
            "receipt_item_id": ri.id,
            "receipt_id": receipt.id,
            "purchase_id": purchase_id,
            "product_id": product_id,
            "quantity": float(quantity),
            "purchase_status": purchase.status.value,
        }

    except Exception:
        await session.rollback()
        raise


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") == "ok":
        status_note = ""
        if result.get("purchase_status") == "delivered":
            status_note = " Закупка переведена в статус delivered."
        return (
            f"✅ Приёмка оформлена: товар {result['product_id']}, "
            f"кол-во {result['quantity']:g}, receipt_item_id={result['receipt_item_id']}."
            f"{status_note}"
        )
    err = result.get("error")
    if err == "purchase_not_found":
        return f"❌ Закупка {result.get('purchase_id')} не найдена."
    if err == "purchase_cancelled":
        return f"❌ Закупка {result.get('purchase_id')} отменена, приёмка невозможна."
    return f"Ошибка: {result}"
