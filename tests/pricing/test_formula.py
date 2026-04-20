"""Unit tests for the pricing formula (ADR-004).

Pure unit tests — no DB, no async, no fixtures.
All expected values derived from ADR-004 examples.
"""

from decimal import Decimal

from app.pricing.schemas import ManufacturerPriceInput, RetailPriceInput
from app.pricing.service import (
    allocate_order_discount,
    apply_discount,
    apply_margin,
    apply_rounding,
    calculate_manufacturer_price,
    calculate_retail_price,
    determine_rounding_step,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

D = Decimal
RATE = D("92.50")
RATE_ID = 42


# ---------------------------------------------------------------------------
# Retail path
# ---------------------------------------------------------------------------


class TestRetailPath:
    def test_retail_rub_purchase(self) -> None:
        """RUB purchase: 5000 + 2.5kg × 17 USD × 92.50 = 8931.25 → ×1.2 → ceil/100 = 10800"""
        params = RetailPriceInput(
            purchase_cost=D("5000"),
            purchase_currency="RUB",
            weight_kg=D("2.5"),
            shipping_per_kg_usd=D("17.00"),
            pricing_exchange_rate=RATE,
            pricing_rate_id=RATE_ID,
        )
        result = calculate_retail_price(params)

        assert result.base_cost_rub == D("8931.2500")
        assert result.margin_amount == D("1786.2500")
        assert result.subtotal == D("10717.5000")
        assert result.discount_amount == D("0.0000")
        assert result.pre_round_price == D("10717.5000")
        assert result.rounding_step == 100
        assert result.final_price == D("10800.0000")

    def test_retail_fx_purchase(self) -> None:
        """FX purchase: 50 USD × 92.50 + shipping → base 8556.25 → ×1.2 → ceil/100 = 10300"""
        params = RetailPriceInput(
            purchase_cost=D("50"),
            purchase_currency="USD",
            weight_kg=D("2.5"),
            shipping_per_kg_usd=D("17.00"),
            pricing_exchange_rate=RATE,
            pricing_rate_id=RATE_ID,
        )
        result = calculate_retail_price(params)

        assert result.base_cost_rub == D("8556.2500")
        assert result.subtotal == D("10267.5000")
        assert result.final_price == D("10300.0000")

    def test_retail_no_shipping(self) -> None:
        """Zero shipping: only purchase cost in base_cost."""
        params = RetailPriceInput(
            purchase_cost=D("5000"),
            purchase_currency="RUB",
            weight_kg=D("2.5"),
            shipping_per_kg_usd=D("0"),
            # No rate needed when both purchase=RUB and shipping=0
        )
        result = calculate_retail_price(params)

        assert result.base_cost_rub == D("5000.0000")
        assert result.breakdown["shipping_cost_rub"] == 0.0

    def test_retail_breakdown_structure(self) -> None:
        """All required keys present in retail breakdown."""
        params = RetailPriceInput(
            purchase_cost=D("5000"),
            purchase_currency="RUB",
            weight_kg=D("2.5"),
            pricing_exchange_rate=RATE,
            pricing_rate_id=RATE_ID,
        )
        bd = calculate_retail_price(params).breakdown

        required_keys = {
            "purchase_type",
            "purchase_cost",
            "purchase_currency",
            "purchase_cost_rub",
            "weight_kg",
            "shipping_per_kg_usd",
            "shipping_currency",
            "pricing_exchange_rate",
            "pricing_rate_id",
            "shipping_cost_rub",
            "base_cost_rub",
            "margin_percent",
            "margin_amount",
            "subtotal",
            "discount_percent",
            "discount_amount",
            "pre_round_price",
            "rounding_step",
            "final_price",
        }
        assert required_keys.issubset(bd.keys())
        assert bd["purchase_type"] == "retail"

    def test_retail_breakdown_no_rate_when_no_fx(self) -> None:
        """Rate fields absent from breakdown when no FX conversion needed."""
        params = RetailPriceInput(
            purchase_cost=D("5000"),
            purchase_currency="RUB",
            weight_kg=D("0"),
            shipping_per_kg_usd=D("0"),
        )
        bd = calculate_retail_price(params).breakdown
        assert "pricing_exchange_rate" not in bd
        assert "pricing_rate_id" not in bd


# ---------------------------------------------------------------------------
# Manufacturer path
# ---------------------------------------------------------------------------


class TestManufacturerPath:
    def _base_params(self, **kwargs) -> ManufacturerPriceInput:  # type: ignore[no-untyped-def]
        defaults = dict(
            product_price_fcy=D("50"),
            currency="USD",
            pricing_exchange_rate=RATE,
            pricing_rate_id=RATE_ID,
        )
        defaults.update(kwargs)
        return ManufacturerPriceInput(**defaults)

    def test_manufacturer_basic(self) -> None:
        """50 USD × 92.50 + intl 1200 → base 5825."""
        params = self._base_params(intl_shipping=D("1200"))
        result = calculate_manufacturer_price(params)

        assert result.base_cost_rub == D("5825.0000")

    def test_manufacturer_all_legs(self) -> None:
        """All 5 legs filled."""
        params = self._base_params(
            origin_shipping=D("200"),
            intl_shipping=D("1200"),
            kz_to_moscow=D("800"),
            customs_fee=D("300"),
            intermediary_fee=D("500"),
        )
        result = calculate_manufacturer_price(params)
        # 50×92.50=4625 + 200+1200+800+300+500=3000 → 7625
        assert result.base_cost_rub == D("7625.0000")

    def test_manufacturer_partial_legs(self) -> None:
        """Only intl_shipping provided → breakdown has no origin_shipping or kz_to_moscow."""
        params = self._base_params(intl_shipping=D("1200"))
        bd = calculate_manufacturer_price(params).breakdown

        assert "logistics" in bd
        assert "intl_shipping" in bd["logistics"]
        assert "origin_shipping" not in bd["logistics"]
        assert "kz_to_moscow" not in bd["logistics"]

    def test_manufacturer_zero_leg(self) -> None:
        """origin_shipping=0.00 (explicit zero) IS present in breakdown."""
        params = self._base_params(
            origin_shipping=D("0.00"),
            intl_shipping=D("1200"),
        )
        bd = calculate_manufacturer_price(params).breakdown

        assert "origin_shipping" in bd["logistics"]
        assert bd["logistics"]["origin_shipping"] == 0.0

    def test_manufacturer_no_legs(self) -> None:
        """No legs provided → logistics key absent from breakdown."""
        params = self._base_params()
        bd = calculate_manufacturer_price(params).breakdown
        assert "logistics" not in bd

    def test_manufacturer_with_intermediary(self) -> None:
        """Intermediary fee 500 included in base_cost."""
        params = self._base_params(intermediary_fee=D("500"))
        result = calculate_manufacturer_price(params)
        assert result.base_cost_rub == D("5125.0000")  # 4625 + 500

    def test_manufacturer_full_example(self) -> None:
        """ADR-004 canonical example.

        50 USD × 92.50 = 4625
        + origin_shipping 0 + intl 1200 + kz_to_moscow 800 + customs 300 + intermediary 500
        = base 7425
        × 1.20 = 8910 (margin 20%)
        − 7% = 623.70 → pre_round 8286.30
        → ceil/100 = 8300
        """
        params = ManufacturerPriceInput(
            product_price_fcy=D("50"),
            currency="USD",
            pricing_exchange_rate=RATE,
            pricing_rate_id=RATE_ID,
            origin_shipping=D("0.00"),
            intl_shipping=D("1200"),
            kz_to_moscow=D("800"),
            customs_fee=D("300"),
            intermediary_fee=D("500"),
            discount_percent=D("7"),
        )
        result = calculate_manufacturer_price(params)

        assert result.base_cost_rub == D("7425.0000")
        assert result.margin_amount == D("1485.0000")
        assert result.subtotal == D("8910.0000")
        assert result.discount_amount == D("623.7000")
        assert result.pre_round_price == D("8286.3000")
        assert result.rounding_step == 100
        assert result.final_price == D("8300.0000")

    def test_manufacturer_breakdown_structure(self) -> None:
        """Manufacturer breakdown has required keys and nested logistics."""
        params = self._base_params(
            intl_shipping=D("1200"),
            customs_fee=D("300"),
        )
        bd = calculate_manufacturer_price(params).breakdown

        required_keys = {
            "purchase_type",
            "product_price_fcy",
            "currency",
            "pricing_exchange_rate",
            "pricing_rate_id",
            "product_price_rub",
            "logistics",
            "customs_fee",
            "base_cost_rub",
            "margin_percent",
            "margin_amount",
            "subtotal",
            "discount_percent",
            "discount_amount",
            "pre_round_price",
            "rounding_step",
            "final_price",
        }
        assert required_keys.issubset(bd.keys())
        assert bd["purchase_type"] == "manufacturer"
        assert isinstance(bd["logistics"], dict)


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


class TestRounding:
    def test_rounding_above_threshold(self) -> None:
        """8286.30 → step 100 → 8300."""
        assert apply_rounding(D("8286.30"), 100) == D("8300.0000")

    def test_rounding_below_threshold(self) -> None:
        """950 → step 10 → 950 (already exact)."""
        assert apply_rounding(D("950"), 10) == D("950.0000")

    def test_rounding_below_not_exact(self) -> None:
        """951 → step 10 → 960."""
        assert apply_rounding(D("951"), 10) == D("960.0000")

    def test_rounding_exact_multiple(self) -> None:
        """8300 → step 100 → 8300 (already exact)."""
        assert apply_rounding(D("8300"), 100) == D("8300.0000")

    def test_rounding_override(self) -> None:
        """Override step=500 on 8286.30 → 8500."""
        step = determine_rounding_step(D("8286.30"), override=500)
        assert step == 500
        assert apply_rounding(D("8286.30"), step) == D("8500.0000")

    def test_rounding_boundary(self) -> None:
        """Exactly 1000.00 → step 100."""
        assert determine_rounding_step(D("1000.00")) == 100

    def test_rounding_just_below(self) -> None:
        """999.99 → step 10 → ceil(999.99/10)*10 = 1000."""
        step = determine_rounding_step(D("999.99"))
        assert step == 10
        assert apply_rounding(D("999.99"), step) == D("1000.0000")


# ---------------------------------------------------------------------------
# Margin & discount
# ---------------------------------------------------------------------------


class TestMarginAndDiscount:
    def test_margin_default_20(self) -> None:
        subtotal, margin = apply_margin(D("7425"), D("20.00"))
        assert margin == D("1485.0000")
        assert subtotal == D("8910.0000")

    def test_margin_custom_15(self) -> None:
        subtotal, margin = apply_margin(D("10000"), D("15.00"))
        assert margin == D("1500.0000")
        assert subtotal == D("11500.0000")

    def test_discount_none(self) -> None:
        price, amount = apply_discount(D("10717.50"), None)
        assert price == D("10717.5000")
        assert amount == D("0.0000")

    def test_discount_7_percent(self) -> None:
        price, amount = apply_discount(D("8910.00"), D("7"))
        assert amount == D("623.7000")
        assert price == D("8286.3000")

    def test_margin_then_discount_order(self) -> None:
        """Margin applied first, discount applied to subtotal (not base_cost)."""
        base = D("8931.25")
        subtotal, margin = apply_margin(base, D("20.00"))
        assert subtotal == D("10717.5000")
        # Now apply discount to subtotal
        price, disc = apply_discount(subtotal, D("10"))
        assert disc == D("1071.7500")
        assert price == D("9645.7500")


# ---------------------------------------------------------------------------
# Order-level discount allocation
# ---------------------------------------------------------------------------


class TestOrderDiscountAllocation:
    def test_allocate_proportional(self) -> None:
        """[5000, 3000, 2000] × 7% total = 700 allocated proportionally."""
        items = [(1, D("5000")), (2, D("3000")), (3, D("2000"))]
        result = allocate_order_discount(items, D("7"))
        allocs = result.item_allocations

        assert allocs[0].allocated_discount == D("350.0000")
        assert allocs[1].allocated_discount == D("210.0000")
        # last item gets remainder: 700 - 350 - 210 = 140
        assert allocs[2].allocated_discount == D("140.0000")

        # Net prices
        assert allocs[0].net_price == D("4650.0000")
        assert allocs[1].net_price == D("2790.0000")
        assert allocs[2].net_price == D("1860.0000")

    def test_allocate_remainder_to_last(self) -> None:
        """Rounding drift absorbed by last item; total allocated == total_discount."""
        # Chosen to force rounding drift
        items = [(1, D("100")), (2, D("100")), (3, D("100"))]
        result = allocate_order_discount(items, D("10"))  # total = 300, discount = 30
        allocs = result.item_allocations

        total_alloc = sum(a.allocated_discount for a in allocs)
        total_discount = D("300") * D("10") / D("100")
        assert total_alloc == total_discount.quantize(D("0.0001"))

    def test_allocate_single_item(self) -> None:
        """Single item receives full discount."""
        items = [(1, D("5000"))]
        result = allocate_order_discount(items, D("7"))
        alloc = result.item_allocations[0]

        expected_discount = (D("5000") * D("7") / D("100")).quantize(D("0.0001"))
        assert alloc.allocated_discount == expected_discount
        assert alloc.net_price == D("5000") - expected_discount

    def test_allocate_zero_discount(self) -> None:
        """0% discount → all allocations are zero."""
        items = [(1, D("5000")), (2, D("3000"))]
        result = allocate_order_discount(items, D("0"))
        for alloc in result.item_allocations:
            assert alloc.allocated_discount == D("0.0000")
            assert alloc.net_price == alloc.original_price

    def test_allocate_empty_items(self) -> None:
        """Empty items list → empty allocations."""
        result = allocate_order_discount([], D("7"))
        assert result.item_allocations == []
