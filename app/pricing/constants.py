"""Default parameters for the pricing formula.

These are code-level constants (Variant 1 per ADR-004).
They serve as default values in Pydantic schemas only —
service.py does NOT import them directly.

Migration path: if params change more than once per quarter,
extract to pricing_formula_config table (separate ADR).
"""

from decimal import Decimal

DEFAULT_MARGIN_PERCENT = Decimal("20.00")
DEFAULT_ROUNDING_STEP = 100
SMALL_ITEM_ROUNDING_STEP = 10
ROUNDING_THRESHOLD_RUB = Decimal("1000.00")
DEFAULT_SHIPPING_PER_KG_USD = Decimal("17.00")
DEFAULT_SHIPPING_CURRENCY = "USD"
