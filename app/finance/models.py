from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin
from app.shared.types import currency_column


class FinanceEntryType(enum.StrEnum):
    income = "income"
    expense = "expense"
    transfer = "transfer"
    exchange = "exchange"


class FinanceExpenseCategory(enum.StrEnum):
    purchase = "purchase"
    logistics = "logistics"
    packaging = "packaging"
    commission = "commission"
    tax = "tax"
    other = "other"
    # ADR-004 additions
    bank_commission = "bank_commission"
    overhead = "overhead"
    customs = "customs"
    intermediary = "intermediary"


class FinanceTaxType(enum.StrEnum):
    general = "general"


class FinanceExchangeRateSource(enum.StrEnum):
    bank_statement = "bank_statement"
    manual = "manual"


class FinanceLedgerEntry(Base, TimestampMixin):
    __tablename__ = "finance_ledger_entry"
    __table_args__ = (
        Index("ix_finance_ledger_entry_entry_at", "entry_at"),
        Index("ix_finance_ledger_entry_entry_type", "entry_type"),
        Index(
            "ix_finance_ledger_entry_related",
            "related_module",
            "related_entity",
            "related_id",
            postgresql_where=text("related_id IS NOT NULL"),
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_finance_ledger_entry_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_type: Mapped[FinanceEntryType] = mapped_column(
        SAEnum(FinanceEntryType, name="finance_entry_type"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = currency_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_module: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_entity: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class FinanceExpense(Base, TimestampMixin):
    __tablename__ = "finance_expense"
    __table_args__ = (
        UniqueConstraint(
            "ledger_entry_id", name="uq_finance_expense_ledger_entry_id"
        ),
        Index("ix_finance_expense_category", "category"),
        Index(
            "ix_finance_expense_supplier_id",
            "supplier_id",
            postgresql_where=text("supplier_id IS NOT NULL"),
        ),
        Index(
            "ix_finance_expense_purchase_id",
            "purchase_id",
            postgresql_where=text("purchase_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ledger_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance_ledger_entry.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[FinanceExpenseCategory] = mapped_column(
        SAEnum(FinanceExpenseCategory, name="finance_expense_category"),
        nullable=False,
    )
    supplier_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("catalog_supplier.id", ondelete="SET NULL"),
        nullable=True,
    )
    purchase_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("procurement_purchase.id", ondelete="SET NULL"),
        nullable=True,
    )


class FinanceTaxEntry(Base, TimestampMixin):
    __tablename__ = "finance_tax_entry"
    __table_args__ = (
        UniqueConstraint(
            "ledger_entry_id", name="uq_finance_tax_entry_ledger_entry_id"
        ),
        Index("ix_finance_tax_entry_tax_type_period", "tax_type", "period"),
        CheckConstraint("base_amount >= 0", name="ck_finance_tax_entry_base_amount"),
        CheckConstraint("tax_amount >= 0", name="ck_finance_tax_entry_tax_amount"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ledger_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance_ledger_entry.id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_type: Mapped[FinanceTaxType] = mapped_column(
        SAEnum(FinanceTaxType, name="finance_tax_type"),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)


class FinanceExchangeRate(Base, TimestampMixin):
    __tablename__ = "finance_exchange_rate"
    __table_args__ = (
        Index(
            "ix_finance_exchange_rate_currencies_observed",
            "from_currency",
            "to_currency",
            "observed_at",
        ),
        CheckConstraint("rate > 0", name="ck_finance_exchange_rate_rate"),
        CheckConstraint(
            "from_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_rate_from_currency",
        ),
        CheckConstraint(
            "to_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_rate_to_currency",
        ),
        CheckConstraint(
            "from_currency <> to_currency",
            name="ck_finance_exchange_rate_different_currencies",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    from_currency: Mapped[str] = currency_column(nullable=False)
    to_currency: Mapped[str] = currency_column(nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[FinanceExchangeRateSource] = mapped_column(
        SAEnum(FinanceExchangeRateSource, name="finance_exchange_rate_source"),
        nullable=False,
    )
    bank: Mapped[str | None] = mapped_column(Text, nullable=True)


class FinanceExchangeOperation(Base, TimestampMixin):
    __tablename__ = "finance_exchange_operation"
    __table_args__ = (
        Index("ix_finance_exchange_operation_operated_at", "operated_at"),
        Index(
            "ix_finance_exchange_operation_bank_exchange_rate_id",
            "bank_exchange_rate_id",
        ),
        CheckConstraint("from_amount > 0", name="ck_finance_exchange_operation_from_amount"),
        CheckConstraint("to_amount > 0", name="ck_finance_exchange_operation_to_amount"),
        CheckConstraint(
            "from_currency <> to_currency",
            name="ck_finance_exchange_operation_different_currencies",
        ),
        CheckConstraint(
            "from_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_operation_from_currency",
        ),
        CheckConstraint(
            "to_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_operation_to_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    operated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_currency: Mapped[str] = currency_column(nullable=False)
    from_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    to_currency: Mapped[str] = currency_column(nullable=False)
    to_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    bank_exchange_rate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance_exchange_rate.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bank: Mapped[str | None] = mapped_column(Text, nullable=True)
