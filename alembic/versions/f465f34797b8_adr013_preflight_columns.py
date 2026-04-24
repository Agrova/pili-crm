"""adr013_preflight_columns

Revision ID: f465f34797b8
Revises: bbd6e538e338
Create Date: 2026-04-24

ADR-013 Task 1: preflight classification columns on analysis_chat_analysis.

Adds:
  preflight_classification TEXT NULL
  preflight_confidence     TEXT NULL
  preflight_reason         TEXT NULL
  skipped_reason           TEXT NULL
CHECK constraint ck_analysis_chat_analysis_skipped_consistency — when
skipped_reason IS NOT NULL, narrative_markdown must be '' and
structured_extract must equal '{"_v": 1}'::jsonb (skipped rows carry no
full-analysis payload).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f465f34797b8"
down_revision: str | None = "bbd6e538e338"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHECK_NAME = "ck_analysis_chat_analysis_skipped_consistency"
_CHECK_SQL = (
    "skipped_reason IS NULL "
    "OR (narrative_markdown = '' AND structured_extract = '{\"_v\": 1}'::jsonb)"
)


def upgrade() -> None:
    op.add_column(
        "analysis_chat_analysis",
        sa.Column("preflight_classification", sa.Text(), nullable=True),
    )
    op.add_column(
        "analysis_chat_analysis",
        sa.Column("preflight_confidence", sa.Text(), nullable=True),
    )
    op.add_column(
        "analysis_chat_analysis",
        sa.Column("preflight_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "analysis_chat_analysis",
        sa.Column("skipped_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "analysis_chat_analysis",
        _CHECK_SQL,
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "analysis_chat_analysis", type_="check")
    op.drop_column("analysis_chat_analysis", "skipped_reason")
    op.drop_column("analysis_chat_analysis", "preflight_reason")
    op.drop_column("analysis_chat_analysis", "preflight_confidence")
    op.drop_column("analysis_chat_analysis", "preflight_classification")
