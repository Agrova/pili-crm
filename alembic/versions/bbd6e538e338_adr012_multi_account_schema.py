"""adr012_multi_account_schema

Revision ID: bbd6e538e338
Revises: a7f1d9c2e384
Create Date: 2026-04-24 10:59:04.487570

ADR-012 Task 1: multi-account Telegram support.

Creates:
  table  communications_telegram_account — registry of operator Telegram accounts
Alters:
  communications_telegram_chat — adds owner_account_id (NOT NULL, FK RESTRICT),
  swaps UNIQUE(telegram_chat_id) → UNIQUE(owner_account_id, telegram_chat_id).
Seeds:
  communications_telegram_account — Kazakhstan account (+77471057849), owner of
  all currently imported chats (ADR-010 first import, 2026-04-23).

Backfill is two-phase with an explicit validation step: after UPDATE, any row
with owner_account_id IS NULL raises RuntimeError and aborts the migration
before the NOT NULL constraint is applied.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bbd6e538e338"
down_revision: str | None = "a7f1d9c2e384"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SEED_PHONE = "+77471057849"
_SEED_DISPLAY_NAME = "Казахстан (+77471057849)"
_SEED_NOTES = "Первый импортированный аккаунт (ADR-010 Задание 1, 2026-04-23)"
_SEED_TS = "2026-04-23 15:48:00+00"

_OLD_UNIQUE = "uq_communications_telegram_chat_telegram_chat_id"
_NEW_UNIQUE = "uq_communications_telegram_chat_owner_telegram_chat_id"
_FK_NAME = "fk_communications_telegram_chat_owner_account"


def upgrade() -> None:
    # 1. New table communications_telegram_account
    op.create_table(
        "communications_telegram_account",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("phone_number", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("telegram_user_id", sa.Text(), nullable=True),
        sa.Column("first_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "phone_number", name="uq_communications_telegram_account_phone"
        ),
        sa.CheckConstraint(
            r"phone_number ~ '^\+[1-9]\d{7,14}$'",
            name="ck_communications_telegram_account_phone_e164",
        ),
    )

    # 2. Seed Kazakhstan account — owner of already-imported chats
    op.execute(
        sa.text(
            "INSERT INTO communications_telegram_account "
            "(phone_number, display_name, notes, first_import_at, last_import_at) "
            "VALUES (:phone, :display_name, :notes, "
            "CAST(:ts AS TIMESTAMPTZ), CAST(:ts AS TIMESTAMPTZ))"
        ).bindparams(
            phone=_SEED_PHONE,
            display_name=_SEED_DISPLAY_NAME,
            notes=_SEED_NOTES,
            ts=_SEED_TS,
        )
    )

    # 3. Add owner_account_id as nullable first — backfill happens next
    op.add_column(
        "communications_telegram_chat",
        sa.Column("owner_account_id", sa.BigInteger(), nullable=True),
    )

    # 4. Backfill: assign all existing chats to the Kazakhstan account
    op.execute(
        sa.text(
            "UPDATE communications_telegram_chat "
            "SET owner_account_id = ("
            "    SELECT id FROM communications_telegram_account "
            "    WHERE phone_number = :phone"
            ") "
            "WHERE owner_account_id IS NULL"
        ).bindparams(phone=_SEED_PHONE)
    )

    # 5. Validation: abort migration if any chat still lacks owner_account_id
    unbackfilled = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM communications_telegram_chat "
                "WHERE owner_account_id IS NULL"
            )
        )
        .scalar()
    )
    if unbackfilled != 0:
        raise RuntimeError(
            f"Backfill failed: {unbackfilled} rows without owner_account_id"
        )

    # 6. Now safe to enforce NOT NULL
    op.alter_column(
        "communications_telegram_chat",
        "owner_account_id",
        nullable=False,
    )

    # 7. FK with ON DELETE RESTRICT — accounts can't be deleted while chats exist
    op.create_foreign_key(
        _FK_NAME,
        source_table="communications_telegram_chat",
        referent_table="communications_telegram_account",
        local_cols=["owner_account_id"],
        remote_cols=["id"],
        ondelete="RESTRICT",
    )

    # 8. Swap UNIQUE constraints — telegram_chat_id is only unique per-account now
    op.drop_constraint(
        _OLD_UNIQUE, "communications_telegram_chat", type_="unique"
    )
    op.create_unique_constraint(
        _NEW_UNIQUE,
        "communications_telegram_chat",
        ["owner_account_id", "telegram_chat_id"],
    )


def downgrade() -> None:
    # Reverse order: UNIQUE → FK → COLUMN → TABLE
    op.drop_constraint(
        _NEW_UNIQUE, "communications_telegram_chat", type_="unique"
    )
    op.create_unique_constraint(
        _OLD_UNIQUE,
        "communications_telegram_chat",
        ["telegram_chat_id"],
    )
    op.drop_constraint(_FK_NAME, "communications_telegram_chat", type_="foreignkey")
    op.drop_column("communications_telegram_chat", "owner_account_id")
    op.drop_table("communications_telegram_account")
