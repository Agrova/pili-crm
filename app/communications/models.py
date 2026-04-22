from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base_model import Base, TimestampMixin


class TelegramChatReviewStatus(enum.StrEnum):
    """Operator review status for Telegram chats imported via ADR-010 ingestion.

    NULL in the DB means the chat was created manually (not from ingestion).
    """

    unreviewed = "unreviewed"
    linked = "linked"
    new_customer = "new_customer"
    ignored = "ignored"


class CommunicationsLinkTargetModule(enum.StrEnum):
    catalog = "catalog"
    orders = "orders"
    procurement = "procurement"
    warehouse = "warehouse"


class CommunicationsLinkConfidence(enum.StrEnum):
    manual = "manual"
    auto = "auto"
    suggested = "suggested"


class CommunicationsEmailThread(Base, TimestampMixin):
    __tablename__ = "communications_email_thread"
    __table_args__ = (
        UniqueConstraint(
            "gmail_thread_id", name="uq_communications_email_thread_gmail_thread_id"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    gmail_thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    participants: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages: Mapped[list[CommunicationsEmailMessage]] = relationship(
        "CommunicationsEmailMessage", back_populates="thread"
    )


class CommunicationsEmailMessage(Base, TimestampMixin):
    __tablename__ = "communications_email_message"
    __table_args__ = (
        UniqueConstraint(
            "gmail_message_id",
            name="uq_communications_email_message_gmail_message_id",
        ),
        Index("ix_communications_email_message_thread_id", "thread_id"),
        Index("ix_communications_email_message_from_address", "from_address"),
        Index("ix_communications_email_message_sent_at", "sent_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("communications_email_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    gmail_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_address: Mapped[str] = mapped_column(Text, nullable=False)
    to_addresses: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_mime: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    parsed_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    thread: Mapped[CommunicationsEmailThread] = relationship(
        "CommunicationsEmailThread", back_populates="messages"
    )


class CommunicationsTelegramChat(Base, TimestampMixin):
    __tablename__ = "communications_telegram_chat"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            name="uq_communications_telegram_chat_telegram_chat_id",
        ),
        # ADR-009: partial index powers the moderation queue query
        # (WHERE review_status = 'unreviewed')
        Index(
            "ix_telegram_chat_unreviewed",
            "review_status",
            postgresql_where=text("review_status = 'unreviewed'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    chat_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ADR-009: watermark for incremental import (NULL = not yet imported)
    last_imported_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ADR-009: operator review status (NULL = manually created, not from ingestion)
    review_status: Mapped[TelegramChatReviewStatus | None] = mapped_column(
        SAEnum(
            TelegramChatReviewStatus,
            name="telegram_chat_review_status",
            create_type=False,  # type is created by the migration
        ),
        nullable=True,
    )

    messages: Mapped[list[CommunicationsTelegramMessage]] = relationship(
        "CommunicationsTelegramMessage", back_populates="chat"
    )


class CommunicationsTelegramMessage(Base, TimestampMixin):
    __tablename__ = "communications_telegram_message"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "telegram_message_id",
            name="uq_communications_telegram_message_chat_msg",
        ),
        Index("ix_communications_telegram_message_chat_id", "chat_id"),
        Index("ix_communications_telegram_message_sent_at", "sent_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_chat.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    chat: Mapped[CommunicationsTelegramChat] = relationship(
        "CommunicationsTelegramChat", back_populates="messages"
    )


class CommunicationsLink(Base, TimestampMixin):
    __tablename__ = "communications_link"
    __table_args__ = (
        CheckConstraint(
            "(email_message_id IS NOT NULL AND telegram_message_id IS NULL)"
            " OR (email_message_id IS NULL AND telegram_message_id IS NOT NULL)",
            name="ck_communications_link_source",
        ),
        Index(
            "ix_communications_link_email_message_id",
            "email_message_id",
            postgresql_where=text("email_message_id IS NOT NULL"),
        ),
        Index(
            "ix_communications_link_telegram_message_id",
            "telegram_message_id",
            postgresql_where=text("telegram_message_id IS NOT NULL"),
        ),
        Index(
            "ix_communications_link_target",
            "target_module",
            "target_entity",
            "target_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("communications_email_message.id", ondelete="CASCADE"),
        nullable=True,
    )
    telegram_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_message.id", ondelete="CASCADE"),
        nullable=True,
    )
    target_module: Mapped[CommunicationsLinkTargetModule] = mapped_column(
        SAEnum(CommunicationsLinkTargetModule, name="communications_link_target_module"),
        nullable=False,
    )
    target_entity: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    link_confidence: Mapped[CommunicationsLinkConfidence] = mapped_column(
        SAEnum(CommunicationsLinkConfidence, name="communications_link_confidence"),
        nullable=False,
    )
