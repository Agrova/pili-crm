"""adr014_telegram_message_media_extraction

Revision ID: 55ed02c4fb8e
Revises: a7c2f1d8e9b3
Create Date: 2026-04-27

ADR-014 Task 2: extracted text content from Telegram message media.

Creates:
  table  communications_telegram_message_media_extraction — 1-to-1 with
         communications_telegram_message, holds extracted text from media
         files (vision descriptions, xlsx/docx plain text, placeholder
         records for non-extractable types).

         Allowable extraction_method values:
           'vision_qwen3-vl-30b-a3b' — vision processing, primary model
           'vision_qwen3-vl-8b'      — vision processing, fallback model
           'xlsx_openpyxl'           — Excel flat-text extraction
           'docx_python_docx'        — Word flat-text extraction
           'placeholder'             — non-extractable types (PDF, video, etc.)

Unique constraint name is stable — referenced by ADR-014 Task 5 ON CONFLICT.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "55ed02c4fb8e"
down_revision: str | None = "a7c2f1d8e9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "communications_telegram_message_media_extraction"
_UQ_NAME = "uq_communications_telegram_message_media_extraction_message_id"
_FK_NAME = "fk_communications_telegram_message_media_extraction_message_id"
_IX_METHOD = "ix_telegram_message_media_extraction_method"
_IX_VERSION = "ix_telegram_message_media_extraction_extractor_version"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["communications_telegram_message.id"],
            name=_FK_NAME,
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("message_id", name=_UQ_NAME),
    )

    op.create_index(_IX_METHOD, _TABLE, ["extraction_method"])
    op.create_index(_IX_VERSION, _TABLE, ["extractor_version"])


def downgrade() -> None:
    op.drop_table(_TABLE)
