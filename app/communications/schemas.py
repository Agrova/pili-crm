"""Pydantic schemas for the communications module.

ADR-012: schemas for Telegram account registry — validation on write
(`TelegramAccountCreate`) and serialisation on read (`TelegramAccountRead`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.communications.models import TELEGRAM_ACCOUNT_PHONE_E164_REGEX


class TelegramAccountCreate(BaseModel):
    """Input schema for registering a new Telegram account."""

    model_config = ConfigDict(extra="forbid")

    phone_number: str = Field(pattern=TELEGRAM_ACCOUNT_PHONE_E164_REGEX)
    display_name: str = Field(min_length=1)
    notes: str | None = None
    telegram_user_id: str | None = None


class TelegramAccountRead(BaseModel):
    """Output schema — full state of a Telegram account row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_number: str
    display_name: str
    telegram_user_id: str | None
    first_import_at: datetime | None
    last_import_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
