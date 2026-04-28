"""adr011_analysis_extracted_identity

Revision ID: f1de2c9a4b7e
Revises: 55ed02c4fb8e
Create Date: 2026-04-28

ADR-011 identity quarantine (Q-2026-04-27 X1): create the
``analysis_extracted_identity`` table where every identity field extracted
from a chat by full analysis is parked in ``status='pending'`` until either
auto-applied (NULL target column + ``confidence='high'``) or moderated
manually through Cowork. Closes the TODO in
``apply_analysis_to_customer`` that silently dropped identity extractions.

contact_type CHECK includes ``'name'`` (operator decision 2026-04-28):
``name_guess`` from the LLM is quarantined too — auto-apply is blocked
because ``orders_customer.name`` is NOT NULL, but operator-driven apply
remains valid (overwrites a name that is by definition non-empty).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1de2c9a4b7e"
down_revision: str | None = "55ed02c4fb8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "analysis_extracted_identity"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "extracted_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("analyzer_version", sa.Text(), nullable=False),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("contact_type", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("context_quote", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("applied_action", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by", sa.Text(), nullable=True),
        # TimestampMixin columns (project convention — see
        # tests/test_module_boundaries.py::test_all_models_have_timestamps).
        # ``extracted_at`` above is the semantic LLM-extraction timestamp;
        # ``created_at``/``updated_at`` track DB row lifecycle and status
        # transitions (pending → applied / rejected / duplicate).
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
            ["customer_id"],
            ["orders_customer.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["communications_telegram_chat.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("extracted_id"),
        sa.CheckConstraint(
            "contact_type IN ('phone', 'email', 'address', 'delivery_method', "
            "'city', 'telegram_username', 'name')",
            name="ck_extracted_identity_contact_type",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_extracted_identity_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'rejected', 'duplicate')",
            name="ck_extracted_identity_status",
        ),
        sa.CheckConstraint(
            "applied_action IS NULL OR applied_action IN "
            "('auto_filled_empty', 'overwrite', 'add_as_secondary')",
            name="ck_extracted_identity_applied_action",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = "
            "(applied_at IS NULL AND applied_by IS NULL "
            "AND applied_action IS NULL)",
            name="ck_extracted_identity_pending_consistency",
        ),
    )
    op.create_index(
        "ix_extracted_identity_customer_status",
        _TABLE,
        ["customer_id", "status"],
    )
    op.create_index(
        "ix_extracted_identity_chat",
        _TABLE,
        ["chat_id"],
    )
    op.create_index(
        "ix_extracted_identity_type_status",
        _TABLE,
        ["contact_type", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_extracted_identity_type_status", table_name=_TABLE)
    op.drop_index("ix_extracted_identity_chat", table_name=_TABLE)
    op.drop_index("ix_extracted_identity_customer_status", table_name=_TABLE)
    op.drop_table(_TABLE)
