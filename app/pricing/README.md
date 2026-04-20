# Модуль pricing

Детерминированный расчёт цены клиенту. Pure-function формула, два пути расчёта, immutable snapshot результата.

Источник истины: [`docs/adr/ADR-004-pricing-profit-policy.md`](../../docs/adr/ADR-004-pricing-profit-policy.md).

## Структура

```
app/pricing/
├── constants.py   # Defaults: DEFAULT_MARGIN_PERCENT, DEFAULT_SHIPPING_PER_KG_USD, …
├── schemas.py     # Pydantic: RetailPriceInput, ManufacturerPriceInput, PriceCalculationResult
├── service.py     # Pure functions: формула, breakdown, аллокация скидки
├── models.py      # SQLAlchemy: PricingExchangeRate, PricingPriceCalculation
└── __init__.py    # Публичный интерфейс модуля
```

## Зависимости

- Зависит от: `catalog`, `finance`
- Используется: `orders`, `api`

## Две ветки формулы

### Retail path

Товар закупается в розницу. Закупка может быть в RUB или в валюте (USD/EUR/JPY/CHF).

```
purchase_currency == "RUB"  →  purchase_cost_rub = purchase_cost
purchase_currency != "RUB"  →  purchase_cost_rub = purchase_cost × pricing_rate

shipping_cost_rub = weight_kg × shipping_per_kg_usd × pricing_rate
base_cost_rub     = purchase_cost_rub + shipping_cost_rub
```

**Пример** (RUB-закупка, rate=92.50):
```
5000 RUB + 2.5 кг × 17 USD × 92.50 = 5000 + 3931.25 = 8931.25
× 1.20 (margin 20%) = 10717.50 → ceil/100 = 10800 RUB
```

### Manufacturer path

Закупка от производителя (часто через Казахстан). Логистика по legs.

```
base_cost_rub = product_price_fcy × pricing_rate
              + origin_shipping (если указан)
              + intl_shipping   (если указан)
              + kz_to_moscow    (если указан)
              + customs_fee     (если указан)
              + intermediary_fee(если указан)
```

**Пример** (ADR-004 canonical, rate=92.50, discount=7%):
```
50 USD × 92.50 = 4625
+ 0 (origin) + 1200 (intl) + 800 (kz) + 300 (customs) + 500 (intermediary)
= base 7425
× 1.20 = 8910 → − 7% = 8286.30 → ceil/100 = 8300 RUB
```

## Общий pipeline

```
base_cost_rub
  → apply_margin(base_cost, margin_percent)    → subtotal, margin_amount
  → apply_discount(subtotal, discount_percent) → pre_round_price, discount_amount
  → determine_rounding_step(pre_round_price)   → step (10 | 100 | override)
  → apply_rounding(pre_round_price, step)      → final_price
```

## Rounding policy

| pre_round_price | step |
|-----------------|------|
| < 1000 RUB      | 10   |
| ≥ 1000 RUB      | 100  |
| operator override | любое значение |

Метод: ceiling (`ceil(price / step) × step`).

## Использование

```python
from app.pricing import calculate_retail_price, RetailPriceInput
from decimal import Decimal

params = RetailPriceInput(
    purchase_cost=Decimal("5000"),
    purchase_currency="RUB",
    weight_kg=Decimal("2.5"),
    pricing_exchange_rate=Decimal("92.50"),
    pricing_rate_id=42,
)
result = calculate_retail_price(params)
print(result.final_price)   # Decimal('10800.0000')
print(result.breakdown)     # полный JSON audit trail
```

```python
from app.pricing import calculate_manufacturer_price, ManufacturerPriceInput

params = ManufacturerPriceInput(
    product_price_fcy=Decimal("50"),
    currency="USD",
    pricing_exchange_rate=Decimal("92.50"),
    pricing_rate_id=42,
    intl_shipping=Decimal("1200"),
    kz_to_moscow=Decimal("800"),
    customs_fee=Decimal("300"),
    intermediary_fee=Decimal("500"),
    discount_percent=Decimal("7"),
)
result = calculate_manufacturer_price(params)
print(result.final_price)   # Decimal('8300.0000')
```

```python
from app.pricing import allocate_order_discount

items = [(1, Decimal("5000")), (2, Decimal("3000")), (3, Decimal("2000"))]
allocation = allocate_order_discount(items, Decimal("7"))
# item 1: 350.00, item 2: 210.00, item 3: 140.00 (последний — остаток)
```

## Архитектурные ограничения

- `service.py` **не импортирует** `constants.py` — все параметры приходят через схемы.
- Все функции — pure, stateless, без обращений к БД.
- `relationship()` с моделями других модулей **запрещён** (ADR-001 v2).
- Profit query (actual) реализуется в модуле `finance` — не здесь.
