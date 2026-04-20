from decimal import Decimal
from typing import Any, NewType

from sqlalchemy import CHAR
from sqlalchemy.orm import mapped_column

Money = NewType("Money", Decimal)
Weight = NewType("Weight", Decimal)
ExchangeRate = NewType("ExchangeRate", Decimal)
Percent = NewType("Percent", Decimal)
Currency = NewType("Currency", str)


def currency_column(nullable: bool = False, **kwargs: Any) -> Any:
    """CHAR(3) column for ISO 4217 currency codes.

    Add a CHECK constraint in the model's __table_args__:
        CheckConstraint("col_name ~ '^[A-Z]{3}$'", name="ck_tablename_colname")
    """
    return mapped_column(CHAR(3), nullable=nullable, **kwargs)
