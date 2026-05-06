"""G19.4: communications_message_template + seed quote_to_client."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b4e7d1f9c2a3"
down_revision: str | None = "d61f0b465f78"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "communications_message_template",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("language", sa.String(5), nullable=False, server_default="ru"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code", "language", name="uq_message_template_code_language"
        ),
    )
    op.create_index(
        "ix_communications_message_template_code_language",
        "communications_message_template",
        ["code", "language"],
    )

    op.execute(
        """
INSERT INTO communications_message_template
    (code, body_template, language, is_active)
VALUES (
    'quote_to_client',
    $TEMPLATE$Здравствуйте, {customer_name}!

По вашему запросу подготовили расчёт:

{items_block}
Итого: {total_rub} ₽ (курс USD: {rate_used} ₽).

Предложение действует {validity_text}.
Пожалуйста, дайте знать, если хотите подтвердить заказ.$TEMPLATE$,
    'ru',
    TRUE
);
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_communications_message_template_code_language",
        table_name="communications_message_template",
    )
    op.drop_table("communications_message_template")
