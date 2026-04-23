"""adr007 catalog listings and price history

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a1b2c3
Create Date: 2026-04-22 12:00:00.000000

Changes (ADR-007 Package 1):
- New enum: catalog_source_kind (retail_shop, manufacturer, both)
- New enum: catalog_price_source (manual, parsed, email, purchase)
- catalog_supplier: add kind (default 'both')
- New table: catalog_product_listing
- New table: catalog_listing_price (immutable — no updated_at)
- Data migration: catalog_product → catalog_product_listing (primary listings)
- catalog_product: remove supplier_id column
- warehouse_stock_item: add price_calculation_id, receipt_item_id
- Views: v_listing_last_price, v_product_current_price
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create enum: catalog_source_kind
    op.execute(
        "CREATE TYPE catalog_source_kind AS ENUM "
        "('retail_shop', 'manufacturer', 'both')"
    )

    # 2. Create enum: catalog_price_source
    op.execute(
        "CREATE TYPE catalog_price_source AS ENUM "
        "('manual', 'parsed', 'email', 'purchase')"
    )

    # 3. Add catalog_supplier.kind (server_default 'both' for existing rows)
    op.add_column(
        "catalog_supplier",
        sa.Column(
            "kind",
            postgresql.ENUM(
                "retail_shop",
                "manufacturer",
                "both",
                name="catalog_source_kind",
                create_type=False,
            ),
            nullable=False,
            server_default="both",
        ),
    )

    # 4. Create catalog_product_listing
    op.create_table(
        "catalog_product_listing",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("sku_at_source", sa.Text(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["product_id"],
            ["catalog_product.id"],
            name="fk_catalog_product_listing_product_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["catalog_supplier.id"],
            name="fk_catalog_product_listing_source_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "source_id", name="uq_catalog_product_listing_product_source"
        ),
    )
    op.create_index(
        "uq_catalog_product_listing_primary",
        "catalog_product_listing",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )
    op.create_index(
        "ix_catalog_product_listing_product_id",
        "catalog_product_listing",
        ["product_id"],
    )
    op.create_index(
        "ix_catalog_product_listing_source_id",
        "catalog_product_listing",
        ["source_id"],
    )

    # 5. Create catalog_listing_price (immutable — no updated_at)
    op.create_table(
        "catalog_listing_price",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "manual",
                "parsed",
                "email",
                "purchase",
                name="catalog_price_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["catalog_product_listing.id"],
            name="fk_catalog_listing_price_listing_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("price >= 0", name="ck_catalog_listing_price_price"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_catalog_listing_price_currency"
        ),
    )
    op.create_index(
        "ix_catalog_listing_price_listing_observed",
        "catalog_listing_price",
        [sa.text("listing_id"), sa.text("observed_at DESC")],
    )
    op.create_index(
        "ix_catalog_listing_price_source",
        "catalog_listing_price",
        ["source"],
    )

    # 6. Data migration: create primary listings from catalog_product
    op.execute(
        """
        INSERT INTO catalog_product_listing (product_id, source_id, sku_at_source, is_primary)
        SELECT id, supplier_id, sku, true
        FROM catalog_product
        """
    )

    # 7. Drop UNIQUE index (supplier_id, sku) from catalog_product
    op.drop_index("uq_catalog_product_supplier_sku", table_name="catalog_product")

    # 8. Drop index ix_catalog_product_supplier_id
    op.drop_index("ix_catalog_product_supplier_id", table_name="catalog_product")

    # 9. Drop FK supplier_id → catalog_supplier
    op.drop_constraint(
        "catalog_product_supplier_id_fkey", "catalog_product", type_="foreignkey"
    )

    # 10. Drop column catalog_product.supplier_id
    op.drop_column("catalog_product", "supplier_id")

    # 11. Add warehouse_stock_item.price_calculation_id
    op.add_column(
        "warehouse_stock_item",
        sa.Column("price_calculation_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_warehouse_stock_item_price_calc",
        "warehouse_stock_item",
        "pricing_price_calculation",
        ["price_calculation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_warehouse_stock_item_price_calc_id",
        "warehouse_stock_item",
        ["price_calculation_id"],
        postgresql_where=sa.text("price_calculation_id IS NOT NULL"),
    )

    # 12. Add warehouse_stock_item.receipt_item_id
    op.add_column(
        "warehouse_stock_item",
        sa.Column("receipt_item_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_warehouse_stock_item_receipt_item",
        "warehouse_stock_item",
        "warehouse_receipt_item",
        ["receipt_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_warehouse_stock_item_receipt_item_id",
        "warehouse_stock_item",
        ["receipt_item_id"],
        postgresql_where=sa.text("receipt_item_id IS NOT NULL"),
    )

    # 13. Create view: v_listing_last_price
    op.execute(
        """
        CREATE VIEW v_listing_last_price AS
        SELECT DISTINCT ON (listing_id)
            listing_id, price, currency, observed_at, source, source_ref
        FROM catalog_listing_price
        ORDER BY listing_id, observed_at DESC
        """
    )

    # 14. Create view: v_product_current_price
    op.execute(
        """
        CREATE VIEW v_product_current_price AS
        SELECT
            l.product_id,
            COUNT(*) AS listings_count,
            MIN(p.price) AS min_last_price,
            MIN(p.price) FILTER (WHERE l.is_primary) AS primary_last_price,
            MAX(p.observed_at) AS last_observation_at
        FROM catalog_product_listing l
        JOIN v_listing_last_price p ON p.listing_id = l.id
        GROUP BY l.product_id
        """
    )


def downgrade() -> None:
    # 1. Drop views
    op.execute("DROP VIEW IF EXISTS v_product_current_price")
    op.execute("DROP VIEW IF EXISTS v_listing_last_price")

    # 2. Drop warehouse_stock_item.receipt_item_id
    op.drop_index(
        "ix_warehouse_stock_item_receipt_item_id",
        table_name="warehouse_stock_item",
    )
    op.drop_constraint(
        "fk_warehouse_stock_item_receipt_item",
        "warehouse_stock_item",
        type_="foreignkey",
    )
    op.drop_column("warehouse_stock_item", "receipt_item_id")

    # 3. Drop warehouse_stock_item.price_calculation_id
    op.drop_index(
        "ix_warehouse_stock_item_price_calc_id",
        table_name="warehouse_stock_item",
    )
    op.drop_constraint(
        "fk_warehouse_stock_item_price_calc",
        "warehouse_stock_item",
        type_="foreignkey",
    )
    op.drop_column("warehouse_stock_item", "price_calculation_id")

    # 4. Restore catalog_product.supplier_id as nullable first
    op.add_column(
        "catalog_product",
        sa.Column("supplier_id", sa.BigInteger(), nullable=True),
    )

    # 5. Data migration: restore supplier_id from primary listing
    op.execute(
        """
        UPDATE catalog_product p
        SET supplier_id = l.source_id
        FROM catalog_product_listing l
        WHERE l.product_id = p.id AND l.is_primary = true
        """
    )

    # 6. Set supplier_id NOT NULL
    op.alter_column("catalog_product", "supplier_id", nullable=False)

    # 7. Restore FK constraint
    op.create_foreign_key(
        "catalog_product_supplier_id_fkey",
        "catalog_product",
        "catalog_supplier",
        ["supplier_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 8. Restore indexes
    op.create_index(
        "uq_catalog_product_supplier_sku",
        "catalog_product",
        ["supplier_id", "sku"],
        unique=True,
        postgresql_where=sa.text("sku IS NOT NULL"),
    )
    op.create_index(
        "ix_catalog_product_supplier_id",
        "catalog_product",
        ["supplier_id"],
    )

    # 9. Drop catalog_listing_price
    op.drop_index("ix_catalog_listing_price_source", table_name="catalog_listing_price")
    op.drop_index(
        "ix_catalog_listing_price_listing_observed", table_name="catalog_listing_price"
    )
    op.drop_table("catalog_listing_price")

    # 10. Drop catalog_product_listing
    op.drop_index(
        "ix_catalog_product_listing_source_id", table_name="catalog_product_listing"
    )
    op.drop_index(
        "ix_catalog_product_listing_product_id", table_name="catalog_product_listing"
    )
    op.drop_index(
        "uq_catalog_product_listing_primary", table_name="catalog_product_listing"
    )
    op.drop_table("catalog_product_listing")

    # 11. Drop catalog_supplier.kind
    op.drop_column("catalog_supplier", "kind")

    # 12. Drop enums
    op.execute("DROP TYPE IF EXISTS catalog_price_source")
    op.execute("DROP TYPE IF EXISTS catalog_source_kind")
