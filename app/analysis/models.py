from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin
from app.shared.types import currency_column


class AnalysisPendingMatchingStatus(enum.StrEnum):
    ambiguous = "ambiguous"
    not_found = "not_found"


class AnalysisChatAnalysis(Base, TimestampMixin):
    """Archive of LLM analysis results — one row per (chat, analyzer_version)."""

    __tablename__ = "analysis_chat_analysis"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "analyzer_version",
            name="uq_analysis_chat_analysis_chat_ver",
        ),
        Index("ix_analysis_chat_analysis_chat_id", "chat_id"),
        Index("ix_analysis_chat_analysis_analyzed_at", "analyzed_at"),
        CheckConstraint(
            "skipped_reason IS NULL "
            "OR (narrative_markdown = '' "
            "AND structured_extract = '{\"_v\": 1}'::jsonb)",
            name="ck_analysis_chat_analysis_skipped_consistency",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_chat.id", ondelete="CASCADE"),
        nullable=False,
    )
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    analyzer_version: Mapped[str] = mapped_column(Text, nullable=False)
    messages_analyzed_up_to: Mapped[str] = mapped_column(Text, nullable=False)
    narrative_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    structured_extract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    chunks_count: Mapped[int] = mapped_column(Integer, nullable=False)
    preflight_classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    preflight_confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    preflight_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisChatAnalysisState(Base, TimestampMixin):
    """Per-chat checkpoint for resumable analysis runs."""

    __tablename__ = "analysis_chat_analysis_state"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_chat.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    chunks_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunks_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    partial_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisPendingOrderItem(Base, TimestampMixin):
    """Draft order positions awaiting operator catalog matching."""

    __tablename__ = "analysis_pending_order_item"
    __table_args__ = (
        Index("ix_analysis_pending_order_item_order_id", "order_id"),
        Index("ix_analysis_pending_order_item_matching_status", "matching_status"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_analysis_pending_order_item_currency",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("orders_order.id", ondelete="CASCADE"),
        nullable=False,
    )
    items_text: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = currency_column(nullable=True)
    matching_status: Mapped[AnalysisPendingMatchingStatus] = mapped_column(
        SAEnum(
            AnalysisPendingMatchingStatus,
            name="analysis_pending_matching_status",
            create_type=False,
        ),
        nullable=False,
    )
    candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    source_message_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)


class AnalysisCreatedEntity(Base, TimestampMixin):
    """Journal of entities created from analyzer output — enables bulk rollback."""

    __tablename__ = "analysis_created_entities"
    __table_args__ = (
        Index(
            "ix_analysis_created_entities_ver_type",
            "analyzer_version",
            "entity_type",
        ),
        Index(
            "ix_analysis_created_entities_source_chat_id",
            "source_chat_id",
        ),
        # ADR-011 Task 2: analyzer rows must reference a chat so bulk
        # rollback by (analyzer_version, source_chat_id) is possible.
        CheckConstraint(
            "created_by <> 'analyzer' OR source_chat_id IS NOT NULL",
            name="ck_analysis_created_entities_analyzer_requires_chat",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    analyzer_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_chat.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisExtractedIdentity(Base, TimestampMixin):
    """Quarantine table for identity fields extracted by full analysis.

    LLM-extracted identity values land here with ``status='pending'`` and
    are either auto-applied to ``orders_customer`` (only when the target
    column is NULL **and** ``confidence='high'``) or moderated manually
    through Cowork. The table never loses extracted data — operator
    backlog is preferable to silent overwrite of manual entries.

    ``customer_id`` is nullable: an unreviewed chat (``get_customer_for_chat``
    returned None) still has its identity quarantined; the operator binds
    the chat to a customer later.

    Two timestamp surfaces are intentional:

    - ``extracted_at`` — semantic moment of LLM extraction. Comes from the
      analyzer run; preserved verbatim for audit even if the row is
      written to the DB later (batch ingest, retry, etc.).
    - ``created_at`` / ``updated_at`` (TimestampMixin) — physical DB row
      lifecycle. ``updated_at`` advances on every status transition
      (``pending`` → ``applied`` / ``rejected`` / ``duplicate``).

    On the current pipeline ``extracted_at ≈ created_at`` but the two are
    deliberately decoupled.
    """

    __tablename__ = "analysis_extracted_identity"
    __table_args__ = (
        Index(
            "ix_extracted_identity_customer_status",
            "customer_id",
            "status",
        ),
        Index("ix_extracted_identity_chat", "chat_id"),
        Index(
            "ix_extracted_identity_type_status",
            "contact_type",
            "status",
        ),
        CheckConstraint(
            "contact_type IN ('phone', 'email', 'address', 'delivery_method', "
            "'city', 'telegram_username', 'name')",
            name="ck_extracted_identity_contact_type",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_extracted_identity_confidence",
        ),
        CheckConstraint(
            "status IN ('pending', 'applied', 'rejected', 'duplicate')",
            name="ck_extracted_identity_status",
        ),
        CheckConstraint(
            "applied_action IS NULL OR applied_action IN "
            "('auto_filled_empty', 'overwrite', 'add_as_secondary')",
            name="ck_extracted_identity_applied_action",
        ),
        CheckConstraint(
            "(status = 'pending') = "
            "(applied_at IS NULL AND applied_by IS NULL "
            "AND applied_action IS NULL)",
            name="ck_extracted_identity_pending_consistency",
        ),
    )

    extracted_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("orders_customer.id", ondelete="CASCADE"),
        nullable=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_chat.id", ondelete="CASCADE"),
        nullable=False,
    )
    analyzer_version: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    contact_type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    context_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    applied_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_by: Mapped[str | None] = mapped_column(Text, nullable=True)
