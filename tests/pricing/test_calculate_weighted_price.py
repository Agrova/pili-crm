"""Unit tests for calculate_weighted_price (ADR-008, list-based N-lot variant)."""

from decimal import Decimal

import pytest

from app.pricing.service import calculate_weighted_price

D = Decimal


def test_basic_two_lots() -> None:
    # (100*10 + 200*30) / 40 = (1000 + 6000) / 40 = 175
    result = calculate_weighted_price([D("100"), D("200")], [10, 30])
    assert result == D("175")


def test_single_lot_returns_same_price() -> None:
    result = calculate_weighted_price([D("123.45")], [5])
    assert result == D("123.45")


def test_three_lots_with_decimal_precision() -> None:
    # (10.1*1 + 10.2*2 + 10.3*3) / 6 = (10.1 + 20.4 + 30.9) / 6 = 61.4 / 6 = 10.2333...
    result = calculate_weighted_price([D("10.1"), D("10.2"), D("10.3")], [1, 2, 3])
    expected = (D("10.1") * 1 + D("10.2") * 2 + D("10.3") * 3) / D("6")
    assert result == expected
    assert isinstance(result, Decimal)


def test_zero_quantity_raises() -> None:
    with pytest.raises(ValueError, match="quantity"):
        calculate_weighted_price([D("100"), D("200")], [10, 0])


def test_negative_quantity_raises() -> None:
    with pytest.raises(ValueError):
        calculate_weighted_price([D("100"), D("200")], [10, -5])


def test_negative_price_raises() -> None:
    with pytest.raises(ValueError, match="price"):
        calculate_weighted_price([D("100"), D("-10")], [10, 5])


def test_empty_lists_raise() -> None:
    with pytest.raises(ValueError):
        calculate_weighted_price([], [])


def test_unequal_lengths_raise() -> None:
    with pytest.raises(ValueError):
        calculate_weighted_price([D("100"), D("200")], [5])


def test_returns_decimal_not_float() -> None:
    result = calculate_weighted_price([D("100"), D("200")], [10, 30])
    assert isinstance(result, Decimal)
