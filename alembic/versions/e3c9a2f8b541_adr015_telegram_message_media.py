"""adr015_telegram_message_media

Revision ID: e3c9a2f8b541
Revises: 208c6dd6037b
Create Date: 2026-04-27

ADR-015 Task 1: normalized media metadata for Telegram messages.

Creates:
  table  communications_telegram_message_media — 1-to-1 with
         communications_telegram_message, holds media_type, file_name,
         relative_path, file_size_bytes, mime_type.

Unique constraint name is stable — referenced by ADR-015 Task 2 ON CONFLICT.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3c9a2f8b541"
down_revision: str | None = "208c6dd6037b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "communications_telegram_message_media"
_UQ_NAME = "uq_communications_telegram_message_media_message_id"
_FK_NAME = "fk_communications_telegram_message_media_message_id"
_IX_MEDIA_TYPE = "ix_telegram_message_media_media_type"
_IX_MIME_TYPE = "ix_telegram_message_media_mime_type"
_IX_HAS_PATH = "ix_telegram_message_media_has_relative_path"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("relative_path", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["communications_telegram_message.id"],
            name=_FK_NAME,
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("message_id", name=_UQ_NAME),
    )

    op.create_index(_IX_MEDIA_TYPE, _TABLE, ["media_type"])
    op.create_index(_IX_MIME_TYPE, _TABLE, ["mime_type"])
    op.create_index(
        _IX_HAS_PATH,
        _TABLE,
        ["message_id"],
        postgresql_where=sa.text("relative_path IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
