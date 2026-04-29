"""adr011_identity_pending_unique

Revision ID: d61f0b465f78
Revises: f1de2c9a4b7e
Create Date: 2026-04-29

ADR-011 X1 iter 4 (identity pipeline v1.3): partial UNIQUE index over
``analysis_extracted_identity (chat_id, contact_type, value) WHERE
status='pending'``. Re-runs of the analyzer that re-extract the same
identity must not pollute the operator's Cowork queue with duplicate
pending rows. ``applied`` / ``rejected`` / ``duplicate`` history rows
are intentionally outside the unique scope so the audit trail across
analyzer_version generations is preserved.

Pre-flight: any ``pending`` duplicates that exist before upgrade must
be cleaned up by the operator (e.g. ``DELETE FROM
analysis_extracted_identity WHERE chat_id = N`` for the affected chat).
The upgrade itself is a CREATE INDEX and will fail loudly on duplicates.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d61f0b465f78"
down_revision: str | None = "f1de2c9a4b7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "analysis_extracted_identity"
_INDEX = "uq_extracted_identity_pending"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        _TABLE,
        ["chat_id", "contact_type", "value"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
