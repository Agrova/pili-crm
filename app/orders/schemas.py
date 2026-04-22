"""Pydantic schemas for orders module JSONB fields.

ADR-009: structured validation for customer profile JSONB columns
(preferences, delivery_preferences, incidents).

These schemas are NOT integrated into the service/repository layer yet
(ADR-010 ingestion pipeline will do that). They exist for:
  - validation of data written to JSONB columns
  - documentation of the JSON structure expected in each column

Serialisation convention:
  - JSON field name: _v  (schema version)
  - Python attribute:  schema_version
  - Use Field(alias='_v') + model_config populate_by_name=True
  - Serialise with model.model_dump(by_alias=True)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreferenceEntry(BaseModel):
    """One product-preference fact extracted from a Telegram conversation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(alias="_v")
    product_id: int
    note: str
    source_message_id: str | None = None
    confidence: Literal["manual", "suggested", "auto"]
    extracted_at: datetime


class DeliveryPreferenceEntry(BaseModel):
    """One delivery-preference fact for a customer."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(alias="_v")
    method: str
    preferred_time: str | None = None
    source: Literal["manual", "suggested", "auto"]
    is_primary: bool


class IncidentEntry(BaseModel):
    """One incident (complaint / unresolved issue) in the customer's history."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(alias="_v")
    date: date
    summary: str
    resolved: bool
    source_message_id: str | None = None


class CustomerProfileJSONB(BaseModel):
    """Validates the full set of JSONB profile fields for orders_customer_profile.

    Invariant: if delivery_preferences is a non-empty list, exactly one entry
    must have is_primary=True. Zero or more than one primary is an error.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preferences: list[PreferenceEntry] | None = None
    delivery_preferences: list[DeliveryPreferenceEntry] | None = None
    incidents: list[IncidentEntry] | None = None

    @model_validator(mode="after")
    def _validate_delivery_primary(self) -> CustomerProfileJSONB:
        if self.delivery_preferences:
            primary_count = sum(
                1 for entry in self.delivery_preferences if entry.is_primary
            )
            if primary_count != 1:
                raise ValueError(
                    f"expected exactly 1 primary delivery preference, got {primary_count}"
                )
        return self
