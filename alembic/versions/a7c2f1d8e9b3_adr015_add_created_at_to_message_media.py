"""adr015_add_created_at_to_message_media

Revision ID: a7c2f1d8e9b3
Revises: e3c9a2f8b541
Create Date: 2026-04-27

ADR-015 Task 1 leftover: add `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
to `communications_telegram_message_media`. The table is immutable
(append-only, deleted only via CASCADE) so `updated_at` is intentionally
absent — see `_IMMUTABLE_MODELS` in `tests/test_module_boundaries.py`.

Existing rows on prod (7160 after the Task 2 backfill) get
`created_at = NOW()` at apply time, which is acceptable because the actual
write happened on the same day as this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c2f1d8e9b3"
down_revision: str | None = "e3c9a2f8b541"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "communications_telegram_message_media"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "created_at")
