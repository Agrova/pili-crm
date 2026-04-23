"""Unit tests for derive_order_status — pure function, no DB."""

from __future__ import annotations

import pytest

from app.orders.service import derive_order_status


def test_all_pending():
    assert derive_order_status(["pending", "pending"]) == "in_procurement"


def test_all_ordered():
    assert derive_order_status(["ordered", "ordered"]) == "in_procurement"


def test_mixed_pending_arrived():
    # min weight is pending (0) → in_procurement
    assert derive_order_status(["pending", "arrived", "delivered"]) == "in_procurement"


def test_all_arrived():
    assert derive_order_status(["arrived", "arrived"]) == "arrived"


def test_all_delivered():
    assert derive_order_status(["delivered", "delivered"]) == "delivered"


def test_all_cancelled():
    assert derive_order_status(["cancelled", "cancelled"]) == "cancelled"


def test_one_shipped_rest_arrived():
    # min weight is shipped (2) → shipped_by_supplier
    assert derive_order_status(["shipped", "arrived", "arrived"]) == "shipped_by_supplier"


def test_at_forwarder():
    # min weight is at_forwarder (3) → received_by_forwarder
    assert derive_order_status(["at_forwarder", "arrived"]) == "received_by_forwarder"


def test_cancelled_ignored_when_others_active():
    # cancelled items don't count; min of remaining is ordered (1)
    assert derive_order_status(["cancelled", "ordered"]) == "in_procurement"


def test_empty_items_raises():
    with pytest.raises(ValueError):
        derive_order_status([])
