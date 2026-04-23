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

from pydantic import BaseModel, ConfigDict, Field

_Config = ConfigDict(extra="forbid", populate_by_name=True)


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
