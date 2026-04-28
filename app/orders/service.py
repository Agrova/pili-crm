"""Order business logic: status derivation and profile/draft-order helpers.

The helpers live in this module so cross-module consumers (notably
``app/analysis/service.py`` — ADR-011 Task 2) can mutate
``orders_customer_profile`` and create draft ``orders_order`` rows without
importing ``app/orders/models``. Every function here works inside an open
transaction supplied by the caller: ``flush()`` is allowed, ``commit`` and
``rollback`` are the caller's responsibility.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.models import (
    ITEM_STATUS_WEIGHT,
    ITEM_TO_ORDER_STATUS_MAP,
    OrdersCustomer,
    OrdersCustomerProfile,
    OrdersOrder,
    OrdersOrderItem,
    OrdersOrderItemStatus,
    OrdersOrderStatus,
)

ProfileConfidence = Literal["manual", "suggested", "auto"]


def derive_order_status(item_statuses: list[str]) -> str:
    """Pure function: derive order status from a list of item statuses.

    Takes the minimum-weight active (non-cancelled) item status and maps it
    to the corresponding order-level status.

    Raises ValueError if item_statuses is empty.
    """
    if not item_statuses:
        raise ValueError("Cannot derive order status: no item statuses provided")

    active = [s for s in item_statuses if s != "cancelled"]
    if not active:
        return "cancelled"

    earliest = min(active, key=lambda s: ITEM_STATUS_WEIGHT.get(s, 999))
    return ITEM_TO_ORDER_STATUS_MAP[earliest]


async def update_order_status_from_items(
    order_id: int, session: AsyncSession
) -> str:
    """Load item statuses from DB, derive order status, persist, return new status."""
    statuses = list(
        (
            await session.execute(
                select(OrdersOrderItem.status).where(
                    OrdersOrderItem.order_id == order_id
                )
            )
        ).scalars()
    )

    if not statuses:
        return "draft"

    new_status = derive_order_status([str(s) for s in statuses])

    order = await session.get(OrdersOrder, order_id)
    if order is not None:
        order.status = OrdersOrderStatus(new_status)

    return new_status


# ── Profile helpers (ADR-009 + ADR-011 Task 2) ──────────────────────────────
#
# The profile exposes three JSONB-backed collections — ``preferences``,
# ``incidents``, ``delivery_preferences`` — whose element shapes come from
# ADR-009 and ADR-011 §7. Writes here go through a single
# ``.with_for_update()`` lock on the profile row: callers invoke
# ``get_or_create_profile_for_update`` exactly once and pass the locked
# profile into the append/upsert helpers.


async def get_or_create_profile_for_update(
    session: AsyncSession, customer_id: int
) -> OrdersCustomerProfile:
    """Return the customer's profile with a row-level write lock.

    Creates the profile if it does not yet exist (ADR-011: analyzer-driven
    first write must not fail because of a missing profile row). The
    ``.with_for_update()`` lock serialises concurrent apply calls for the
    same customer so dedup counters stay correct.
    """
    customer = await session.get(OrdersCustomer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")

    stmt = (
        select(OrdersCustomerProfile)
        .where(OrdersCustomerProfile.customer_id == customer_id)
        .with_for_update()
    )
    profile = (await session.execute(stmt)).scalar_one_or_none()
    if profile is not None:
        return profile

    profile = OrdersCustomerProfile(customer_id=customer_id)
    session.add(profile)
    await session.flush()
    # Re-select with FOR UPDATE so callers still hold the row-level lock.
    locked = (await session.execute(stmt)).scalar_one()
    return locked


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Interpret an existing JSONB value as a list of element dicts.

    ADR-011 stores ``preferences``/``incidents`` as JSONB arrays. Legacy
    profiles might have ``None`` (never written) or, in edge cases, a dict
    wrapper. We normalise by returning a fresh list in both non-list cases.
    """
    if isinstance(value, list):
        return list(value)
    return []


async def append_preference_in_locked_profile(
    session: AsyncSession,
    profile: OrdersCustomerProfile,
    preference: dict[str, Any],
    *,
    confidence: ProfileConfidence,
) -> None:
    """Append a preference element to an already-locked profile.

    Assumes the caller holds the row-level lock via
    ``get_or_create_profile_for_update``. The ``confidence`` value is
    stamped into the stored element so operators can tell analyzer
    suggestions from manual confirmations (ADR-009 confidence model).
    """
    existing = _as_list(profile.preferences)
    element = {**preference, "confidence": confidence}
    existing.append(element)
    profile.preferences = existing  # reassign so SQLAlchemy detects the change


async def append_incident_in_locked_profile(
    session: AsyncSession,
    profile: OrdersCustomerProfile,
    incident: dict[str, Any],
    *,
    confidence: ProfileConfidence,
) -> None:
    existing = _as_list(profile.incidents)
    element = {**incident, "confidence": confidence}
    existing.append(element)
    profile.incidents = existing


async def upsert_delivery_preferences_in_locked_profile(
    session: AsyncSession,
    profile: OrdersCustomerProfile,
    delivery_prefs: dict[str, Any],
    *,
    confidence: ProfileConfidence,
) -> None:
    """Overwrite ``delivery_preferences`` with a single-element array.

    ADR-011 §apply: analyzer-extracted delivery prefs replace the existing
    array wholesale, marked ``is_primary=False`` (operator promotes manually)
    and ``confidence='suggested'``. Callers only invoke this when the
    extract carries a non-empty value — empty extracts leave the existing
    array untouched (safer-by-default per ADR-011).
    """
    element = {
        **delivery_prefs,
        "confidence": confidence,
        "is_primary": False,
    }
    profile.delivery_preferences = [element]


# ── Draft order helpers (ADR-011 Task 2 §apply) ─────────────────────────────
#
# ``create_draft_order`` and ``add_order_item`` are the public write surface
# for ADR-011's analyzer: analysis.service never imports ``orders.models``.
# The ``origin`` parameter is accepted but not persisted — there is no
# ``orders_order.origin`` column; provenance is tracked via
# ``analysis_created_entities``. See ADR-011 Task 2 §create_draft_order.

DraftOrderOrigin = Literal["analysis", "operator"]


async def create_draft_order(
    session: AsyncSession,
    customer_id: int,
    items: list[Any],
    *,
    origin: DraftOrderOrigin,
    currency: str = "RUB",
) -> OrdersOrder:
    """Create an empty ``OrdersOrder`` row with ``status='draft'``.

    ``items`` is accepted for future extension but must currently be an empty
    list — analyzer-driven draft orders get their items added one-by-one via
    ``add_order_item`` so each line can be journaled in
    ``analysis_created_entities`` alongside confident_match rows (ADR-011
    §apply step f). ``origin`` is a required marker for callers — currently
    it is *not* persisted; the caller is expected to also write an
    ``analysis_created_entities`` row when ``origin='analysis'`` (ADR-011
    Task 2 TZ §create_draft_order, decision (A)).
    """
    if items:
        raise ValueError(
            "create_draft_order: non-empty items is not supported; "
            "add items via add_order_item so each can be journaled"
        )
    customer = await session.get(OrdersCustomer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")

    order = OrdersOrder(
        customer_id=customer_id,
        status=OrdersOrderStatus.draft,
        currency=currency,
    )
    session.add(order)
    await session.flush()
    return order


async def add_order_item(
    session: AsyncSession,
    order_id: int,
    product_id: int,
    quantity: Decimal,
    unit_price: Decimal | None,
    currency: str | None,
) -> OrdersOrderItem:
    """Create a single ``orders_order_item`` row.

    ``currency`` is accepted per ADR-011 schema but not persisted on the
    item row — currency lives on the parent ``orders_order``. The parameter
    is kept in the signature so analyzer callers can pass through the value
    they extracted; mismatches with the order currency are the caller's
    concern (currently ignored — left as a follow-up for verification UX).
    """
    order = await session.get(OrdersOrder, order_id)
    if order is None:
        raise ValueError(f"Order {order_id} not found")
    if quantity <= Decimal("0"):
        raise ValueError("quantity must be > 0")

    item = OrdersOrderItem(
        order_id=order_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=unit_price,
        status=OrdersOrderItemStatus.pending,
    )
    session.add(item)
    await session.flush()
    return item


# ── identity columns access (ADR-011 identity quarantine) ───────────────────
#
# These two helpers exist so ``app/analysis/identity_service.py`` can
# read/write the four identity columns of ``orders_customer`` without
# importing ``app.orders.models`` directly — that import is forbidden by
# the module-boundary test ``test_analysis_module_does_not_import_orders_models``.

IdentityColumn = Literal["phone", "email", "telegram_username", "name"]
_IDENTITY_COLUMNS: tuple[IdentityColumn, ...] = (
    "phone",
    "email",
    "telegram_username",
    "name",
)


async def get_customer_identity_columns(
    session: AsyncSession, customer_id: int
) -> dict[IdentityColumn, str | None]:
    """Snapshot of ``orders_customer`` identity columns for a customer.

    Returns ``{phone, email, telegram_username, name}`` with their current
    values (``None`` for empty nullable columns; ``name`` is NOT NULL so
    always a string). Raises ``ValueError`` when the customer is missing.
    """
    customer = await session.get(OrdersCustomer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")
    return {
        "phone": customer.phone,
        "email": customer.email,
        "telegram_username": customer.telegram_username,
        "name": customer.name,
    }


async def set_customer_identity_field(
    session: AsyncSession,
    customer_id: int,
    column: IdentityColumn,
    value: str,
) -> None:
    """Write ``value`` into ``orders_customer.<column>`` for ``customer_id``.

    ``column`` is enum-restricted to the four identity columns — callers
    cannot mutate arbitrary fields. Raises ``ValueError`` when the customer
    is missing. Does not commit.
    """
    if column not in _IDENTITY_COLUMNS:
        raise ValueError(f"Unsupported identity column: {column!r}")
    customer = await session.get(OrdersCustomer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")
    setattr(customer, column, value)
    await session.flush()
