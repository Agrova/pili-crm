"""add_reply_to_telegram_message_id

Revision ID: c3d94a7f1e82
Revises: 6bb45bb3dcb5
Create Date: 2026-04-23 12:00:00.000000

ADR-010 addendum [2026-04-23]: reply messages as first-class field.

Changes:
  communications_telegram_message + reply_to_telegram_message_id TEXT NULL
  new partial composite index: ix_telegram_message_reply_to
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d94a7f1e82"
down_revision: str | None = "6bb45bb3dcb5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. No FK per ADR-010 addendum: original may be deleted or not yet imported
    op.add_column(
        "communications_telegram_message",
        sa.Column("reply_to_telegram_message_id", sa.Text(), nullable=True),
    )

    # 2. Partial composite index — covers only reply messages (minority of rows)
    op.create_index(
        "ix_telegram_message_reply_to",
        "communications_telegram_message",
        ["chat_id", "reply_to_telegram_message_id"],
        postgresql_where=sa.text("reply_to_telegram_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 1. Drop index before dropping the column it references
    op.drop_index(
        "ix_telegram_message_reply_to",
        table_name="communications_telegram_message",
    )

    # 2. Drop column
    op.drop_column("communications_telegram_message", "reply_to_telegram_message_id")
