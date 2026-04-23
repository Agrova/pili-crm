"""Unit tests for calculate_weighted_price (ADR-008, Package 3 helper)."""

from decimal import Decimal

import pytest

from app.pricing.service import calculate_weighted_price

D = Decimal


class TestCalculateWeightedPrice:
    def test_basic(self) -> None:
        # (3 × 100 + 2 × 150) / 5 = 600 / 5 = 120
        result = calculate_weighted_price(D("3"), D("100"), D("2"), D("150"))
        assert result == D("120.00")

    def test_equal_prices(self) -> None:
        result = calculate_weighted_price(D("3"), D("100"), D("2"), D("100"))
        assert result == D("100.00")

    def test_zero_total_raises(self) -> None:
        with pytest.raises(ValueError, match="total_quantity"):
            calculate_weighted_price(D("0"), D("100"), D("0"), D("150"))

    def test_precision_high_prices(self) -> None:
        # (3 × 10400 + 2 × 11500) / 5 = (31200 + 23000) / 5 = 54200 / 5 = 10840
        result = calculate_weighted_price(D("3"), D("10400"), D("2"), D("11500"))
        assert result == D("10840.00")

    def test_two_decimal_rounding(self) -> None:
        # (1 × 10 + 2 × 11) / 3 = 32 / 3 = 10.666... → rounds to 10.67
        result = calculate_weighted_price(D("1"), D("10"), D("2"), D("11"))
        assert result == D("10.67")

    def test_single_new_unit(self) -> None:
        # (0 × anything + 5 × 200) / 5 = 200
        result = calculate_weighted_price(D("0"), D("999"), D("5"), D("200"))
        assert result == D("200.00")
