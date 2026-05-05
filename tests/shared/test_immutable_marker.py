"""Tests for ImmutableMixin marker (ADR-008 Блок 4)."""

from __future__ import annotations

from app.catalog.models import CatalogListingPrice
from app.shared.base_model import Base
from app.warehouse.models import WarehousePendingPriceResolution


def test_warehouse_pending_is_immutable() -> None:
    assert getattr(WarehousePendingPriceResolution, "__immutable__", False) is True


def test_catalog_listing_price_is_immutable() -> None:
    assert getattr(CatalogListingPrice, "__immutable__", False) is True


def test_all_models_timestamps_pass_with_immutable_models() -> None:
    """Smoke test: the timestamp check must not fail on ImmutableMixin models.

    Mirrors test_all_models_have_timestamps from test_module_boundaries but
    runs without the DB fixture to confirm the attribute-based logic works.
    """
    missing: list[str] = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        col_names = {col.key for col in mapper.columns}
        if getattr(cls, "__immutable__", False):
            if "created_at" not in col_names:
                missing.append(f"{cls.__name__} (missing created_at)")
        elif "created_at" not in col_names or "updated_at" not in col_names:
            missing.append(cls.__name__)
    assert missing == [], (
        "Models missing created_at / updated_at:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_immutable_mixin_not_applied_to_non_immutable_model() -> None:
    """Regular models must NOT have __immutable__ = True."""
    from app.orders.models import OrdersOrder

    assert not getattr(OrdersOrder, "__immutable__", False)
