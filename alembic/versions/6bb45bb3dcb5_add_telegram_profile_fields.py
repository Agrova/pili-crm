"""add_telegram_profile_fields

Revision ID: 6bb45bb3dcb5
Revises: 4f8fe83398af
Create Date: 2026-04-23 00:09:42.381980

ADR-009: Extend schema for Telegram customer profiles.

Changes:
  orders_customer            + telegram_username TEXT NULL
  orders_customer_profile    + preferences JSONB NULL
                             + delivery_preferences JSONB NULL
                             + incidents JSONB NULL
  communications_telegram_chat + last_imported_message_id TEXT NULL
                               + review_status telegram_chat_review_status NULL
  new enum: telegram_chat_review_status
  new partial index: ix_telegram_chat_unreviewed
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6bb45bb3dcb5"
down_revision: str | None = "4f8fe83398af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create enum type (before any column that references it)
    op.execute(
        "CREATE TYPE telegram_chat_review_status AS ENUM "
        "('unreviewed', 'linked', 'new_customer', 'ignored')"
    )

    # 2. orders_customer: add telegram_username
    op.add_column(
        "orders_customer",
        sa.Column("telegram_username", sa.Text(), nullable=True),
    )

    # 3. orders_customer_profile: add preferences JSONB
    op.add_column(
        "orders_customer_profile",
        sa.Column("preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 4. orders_customer_profile: add delivery_preferences JSONB
    op.add_column(
        "orders_customer_profile",
        sa.Column(
            "delivery_preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    # 5. orders_customer_profile: add incidents JSONB
    op.add_column(
        "orders_customer_profile",
        sa.Column("incidents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 6. communications_telegram_chat: watermark for incremental import
    op.add_column(
        "communications_telegram_chat",
        sa.Column("last_imported_message_id", sa.Text(), nullable=True),
    )

    # 7. communications_telegram_chat: operator review status
    op.add_column(
        "communications_telegram_chat",
        sa.Column(
            "review_status",
            postgresql.ENUM(
                "unreviewed",
                "linked",
                "new_customer",
                "ignored",
                name="telegram_chat_review_status",
                create_type=False,  # type already created above
            ),
            nullable=True,
        ),
    )

    # 8. Partial index for the moderation queue (unreviewed chats only)
    op.create_index(
        "ix_telegram_chat_unreviewed",
        "communications_telegram_chat",
        ["review_status"],
        postgresql_where=sa.text("review_status = 'unreviewed'"),
    )


def downgrade() -> None:
    # 1. Drop partial index first
    op.drop_index("ix_telegram_chat_unreviewed", table_name="communications_telegram_chat")

    # 2. Drop columns in reverse order
    op.drop_column("communications_telegram_chat", "review_status")
    op.drop_column("communications_telegram_chat", "last_imported_message_id")
    op.drop_column("orders_customer_profile", "incidents")
    op.drop_column("orders_customer_profile", "delivery_preferences")
    op.drop_column("orders_customer_profile", "preferences")
    op.drop_column("orders_customer", "telegram_username")

    # 3. Drop enum type last (no columns reference it anymore)
    op.execute("DROP TYPE telegram_chat_review_status")
