"""Модуль pricing — детерминированный расчёт цены клиенту.

Формулы, расчётный курс валюты (может включать наценку),
логистика, упаковка, комиссии, маржа.
Ключевые сущности: PriceCalculation, PricingExchangeRate.
Внешние коннекторы: API курсов валют (источник для расчётного курса).
"""

from app.pricing.schemas import (
    ManufacturerPriceInput,
    OrderDiscountAllocation,
    PriceCalculationResult,
    RetailPriceInput,
)
from app.pricing.service import (
    allocate_order_discount,
    calculate_manufacturer_price,
    calculate_retail_price,
)

__all__ = [
    # Orchestrators
    "calculate_retail_price",
    "calculate_manufacturer_price",
    "allocate_order_discount",
    # Schemas
    "RetailPriceInput",
    "ManufacturerPriceInput",
    "PriceCalculationResult",
    "OrderDiscountAllocation",
]
