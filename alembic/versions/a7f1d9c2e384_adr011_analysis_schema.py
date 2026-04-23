"""adr011_analysis_schema

Revision ID: a7f1d9c2e384
Revises: c3d94a7f1e82
Create Date: 2026-04-24 12:00:00.000000

ADR-011 Task 1: schema for the Telegram chat analysis pipeline (9th module).

Creates:
  enum   analysis_pending_matching_status (ambiguous, not_found)
  table  analysis_chat_analysis          — archive of LLM results (immutable)
  table  analysis_chat_analysis_state    — per-chat resume checkpoints
  table  analysis_pending_order_item     — draft-order positions awaiting match
  table  analysis_created_entities       — journal enabling bulk rollback
Seeds:
  catalog_supplier row with name='Unknown (auto)' (idempotent via ON CONFLICT)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7f1d9c2e384"
down_revision: str | None = "c3d94a7f1e82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Enum used by analysis_pending_order_item.matching_status
    op.execute(
        "CREATE TYPE analysis_pending_matching_status AS ENUM "
        "('ambiguous', 'not_found')"
    )

    # 2. analysis_chat_analysis — archive of analysis results (immutable rows)
    op.create_table(
        "analysis_chat_analysis",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analyzer_version", sa.Text(), nullable=False),
        sa.Column("messages_analyzed_up_to", sa.Text(), nullable=False),
        sa.Column("narrative_markdown", sa.Text(), nullable=False),
        sa.Column(
            "structured_extract",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("chunks_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["communications_telegram_chat.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_id",
            "analyzer_version",
            name="uq_analysis_chat_analysis_chat_ver",
        ),
    )
    op.create_index(
        "ix_analysis_chat_analysis_chat_id",
        "analysis_chat_analysis",
        ["chat_id"],
    )
    op.create_index(
        "ix_analysis_chat_analysis_analyzed_at",
        "analysis_chat_analysis",
        ["analyzed_at"],
    )

    # 3. analysis_chat_analysis_state — per-chat checkpoint (one row per chat)
    op.create_table(
        "analysis_chat_analysis_state",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("chunks_done", sa.Integer(), nullable=True),
        sa.Column("chunks_total", sa.Integer(), nullable=True),
        sa.Column(
            "partial_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["communications_telegram_chat.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_id"),
    )

    # 4. analysis_pending_order_item — draft positions awaiting catalog match
    op.create_table(
        "analysis_pending_order_item",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("items_text", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=True),
        sa.Column(
            "matching_status",
            postgresql.ENUM(
                "ambiguous",
                "not_found",
                name="analysis_pending_matching_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "source_message_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_analysis_pending_order_item_currency",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders_order.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_pending_order_item_order_id",
        "analysis_pending_order_item",
        ["order_id"],
    )
    op.create_index(
        "ix_analysis_pending_order_item_matching_status",
        "analysis_pending_order_item",
        ["matching_status"],
    )

    # 5. analysis_created_entities — rollback journal (ADR-011 §8)
    op.create_table(
        "analysis_created_entities",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("analyzer_version", sa.Text(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_chat_id"],
            ["communications_telegram_chat.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_created_entities_ver_type",
        "analysis_created_entities",
        ["analyzer_version", "entity_type"],
    )
    op.create_index(
        "ix_analysis_created_entities_source_chat_id",
        "analysis_created_entities",
        ["source_chat_id"],
    )

    # 6. Seed supplier 'Unknown (auto)' — idempotent; UNIQUE(name) exists.
    op.execute(
        "INSERT INTO catalog_supplier (name, kind) "
        "VALUES ('Unknown (auto)', 'both') "
        "ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    # 1. Remove seed supplier
    op.execute(
        "DELETE FROM catalog_supplier WHERE name = 'Unknown (auto)'"
    )

    # 2. Drop tables in reverse order of creation
    op.drop_index(
        "ix_analysis_created_entities_source_chat_id",
        table_name="analysis_created_entities",
    )
    op.drop_index(
        "ix_analysis_created_entities_ver_type",
        table_name="analysis_created_entities",
    )
    op.drop_table("analysis_created_entities")

    op.drop_index(
        "ix_analysis_pending_order_item_matching_status",
        table_name="analysis_pending_order_item",
    )
    op.drop_index(
        "ix_analysis_pending_order_item_order_id",
        table_name="analysis_pending_order_item",
    )
    op.drop_table("analysis_pending_order_item")

    op.drop_table("analysis_chat_analysis_state")

    op.drop_index(
        "ix_analysis_chat_analysis_analyzed_at",
        table_name="analysis_chat_analysis",
    )
    op.drop_index(
        "ix_analysis_chat_analysis_chat_id",
        table_name="analysis_chat_analysis",
    )
    op.drop_table("analysis_chat_analysis")

    # 3. Drop enum last (no columns reference it anymore)
    op.execute("DROP TYPE analysis_pending_matching_status")
