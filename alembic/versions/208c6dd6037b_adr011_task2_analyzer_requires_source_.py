"""adr011_task2_analyzer_requires_source_chat_id

Revision ID: 208c6dd6037b
Revises: f465f34797b8
Create Date: 2026-04-24

ADR-011 Task 2: CHECK constraint on analysis_created_entities ensuring that
every analyzer-authored journal row carries a ``source_chat_id``. Without
the chat reference there is no way to roll the row back as part of a bulk
rollback by ``(analyzer_version, source_chat_id)``. Operator-authored rows
(``created_by='operator'``) may legitimately have NULL ``source_chat_id``
(e.g. product created manually outside an analyzer run).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "208c6dd6037b"
down_revision: str | None = "f465f34797b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHECK_NAME = "ck_analysis_created_entities_analyzer_requires_chat"
_CHECK_SQL = "created_by <> 'analyzer' OR source_chat_id IS NOT NULL"


def upgrade() -> None:
    op.create_check_constraint(
        _CHECK_NAME,
        "analysis_created_entities",
        _CHECK_SQL,
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "analysis_created_entities", type_="check")
