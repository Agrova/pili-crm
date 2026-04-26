"""ADR-011 raздел 3: Pydantic schemas for `structured_extract`.

These are the shapes Qwen is asked to emit in pass 2 of the analysis pipeline.
Every leaf field is nullable — the model may legitimately find no value. Lists
are modelled as `list[X] | None`: `None` means the section was not attempted /
unknown, whereas `[]` means "attempted and found nothing". For an LLM extract
this distinction carries signal, so we preserve it.

Serialisation convention (matches ADR-009):
  - JSON key `_v` ↔ Python attribute `schema_version`
  - `Field(alias='_v')` + `model_config = ConfigDict(populate_by_name=True)`
  - `extra='forbid'` rejects stray keys Qwen might hallucinate
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_Config = ConfigDict(extra="ignore", populate_by_name=True)


class Identity(BaseModel):
    model_config = _Config

    name_guess: str | None = None
    telegram_username: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    confidence_notes: str | None = None


class Preference(BaseModel):
    model_config = _Config

    product_hint: str | None = None
    note: str | None = None
    source_message_ids: list[str] | None = None


class DeliveryPreferences(BaseModel):
    model_config = _Config

    method: str | None = None
    preferred_time: str | None = None
    notes: str | None = None


class Incident(BaseModel):
    model_config = _Config

    date: str | None = None
    summary: str | None = None
    resolved: bool | None = None
    source_message_ids: list[str] | None = None


class OrderItem(BaseModel):
    model_config = _Config

    items_text: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    source_message_ids: list[str] | None = None


class Order(BaseModel):
    model_config = _Config

    description: str | None = None
    items: list[OrderItem] | None = None
    status_delivery: (
        Literal["ordered", "shipped", "delivered", "returned", "unknown"] | None
    ) = None
    status_payment: Literal["unpaid", "partial", "paid", "unknown"] | None = None
    date_guess: str | None = None
    source_message_ids: list[str] | None = None


class Payment(BaseModel):
    model_config = _Config

    amount: Decimal | None = None
    currency: str | None = None
    method: str | None = None
    date_guess: str | None = None
    source_message_ids: list[str] | None = None


class StructuredExtract(BaseModel):
    """Root schema for `analysis_chat_analysis.structured_extract` (pass 2)."""

    model_config = _Config

    schema_version: int = Field(alias="_v")
    identity: Identity | None = None
    preferences: list[Preference] | None = None
    delivery_preferences: DeliveryPreferences | None = None
    incidents: list[Incident] | None = None
    orders: list[Order] | None = None
    payments: list[Payment] | None = None


# === Preflight classification schemas (ADR-013) ===


PreflightClass = Literal[
    "client",
    "possible_client",
    "not_client",
    "family",
    "friend",
    "service",
    "empty",
]

PreflightConfidence = Literal["low", "medium", "high"]

SkippedReason = Literal["not_client", "empty"]


class PreflightClassification(BaseModel):
    """Qwen preflight verdict — decides whether full analysis runs (ADR-013)."""

    model_config = ConfigDict(extra="ignore")

    classification: PreflightClass
    confidence: PreflightConfidence
    reason: str


# === Matched extract — output of catalog matching pass (ADR-011 §5) ===
#
# ``MatchedStructuredExtract`` is the shape of ``structured_extract`` *after*
# the catalog-matching pass has decorated every order item with its matching
# verdict. It is the payload consumed by ``apply_analysis_to_customer``.

MatchingStatus = Literal["confident_match", "ambiguous", "not_found"]


class ProductCandidate(BaseModel):
    model_config = _Config

    product_id: int
    confidence_note: str


class MatchedOrderItem(OrderItem):
    """``OrderItem`` annotated with catalog-matching result."""

    matching_status: MatchingStatus
    matched_product_id: int | None = None
    candidates: list[ProductCandidate] | None = None
    not_found_reason: str | None = None

    # pydantic forbid-extra + populate_by_name model_config is inherited.

    @model_validator(mode="after")
    def _validate_matching_fields(self) -> MatchedOrderItem:
        if (
            self.matching_status == "confident_match"
            and self.matched_product_id is None
        ):
            raise ValueError(
                "matched_product_id is required when "
                "matching_status='confident_match'"
            )
        if self.matching_status == "ambiguous" and not self.candidates:
            raise ValueError(
                "candidates must be non-empty when "
                "matching_status='ambiguous'"
            )
        # 'not_found' leaves not_found_reason optional (recommended but not
        # required — ADR-011 Task 2 TZ §MatchedStructuredExtract).
        return self


class MatchedOrder(Order):
    items: list[MatchedOrderItem] | None = None  # type: ignore[assignment]


class MatchedStructuredExtract(StructuredExtract):
    """Extract with every order item annotated by catalog matching.

    ``orders`` overrides the base-class annotation to carry
    ``MatchedOrder`` (which in turn carries ``MatchedOrderItem``).
    """

    orders: list[MatchedOrder] | None = None  # type: ignore[assignment]
