# ADR-004: Pricing & Profit Policy

## Контекст

ADR-001 v2 зафиксировал модульный монолит с модулями `pricing`, `finance`, `orders`, `catalog`. ADR-002 v2 зафиксировал стек (Python/FastAPI/SQLAlchemy/Alembic). ADR-003 final-ready зафиксировал core schema, включая `pricing_price_calculation` (immutable snapshot с `input_params JSONB`, `breakdown JSONB`, `final_price`), `pricing_exchange_rate`, `finance_exchange_rate`, `finance_expense`, `finance_ledger_entry`, `orders_order_item.unit_price` с FK на `pricing_price_calculation`.

Теперь нужно зафиксировать **бизнес-политику ценообразования и расчёта прибыли**: из каких компонентов складывается цена клиенту, как она рассчитывается для разных типов закупок, как фиксируется управленческое решение по цене, как учитываются скидки и округление, и как считается фактическая прибыль.

Это решение нужно принять сейчас, потому что без него невозможно реализовать модуль `pricing` — он должен знать формулу, входные параметры, структуру breakdown и правила работы с ценой.

Бизнес-контекст:

- Продажа всегда в рублях. Закупка может быть в USD, EUR, JPY, CHF.
- Фактический курс фиксируется вручную при обмене валюты (модуль `finance`). Расчётный курс (с наценкой) хранится в `pricing_exchange_rate`.
- Существуют два типа закупок с принципиально разной структурой себестоимости: розничная (retail) и от производителя (manufacturer, часто через Казахстан).
- Базовая маржа — 20%, но для отдельных клиентов возможна сниженная маржа и/или скидка (например, 7% от суммы заказа).
- Цена округляется: до 100 RUB для товаров от 1000 RUB, до 10 RUB для товаров дешевле 1000 RUB.
- Банковская комиссия (~0.4%, нестабильна) и постоянные расходы магазина учитываются только в фактической прибыли, не в плановой цене.
- Доставка клиенту по Москве/РФ оплачивается отдельно по тарифам СДЭК/Яндекс и не входит в цену товара.
- Фактическая прибыль должна считаться по реальным данным, а не только по плановой формуле.

Принципиальное ограничение: на этом этапе **не вводятся новые таблицы конфигурации** (`pricing_formula_config`, `pricing_customer_discount`). Политика фиксируется как поведенческие правила в коде модуля `pricing`. Если конфигурация начнёт часто меняться — сущности выделяются отдельным ADR позже.

## Варианты

### Вариант 1 — Единая формула с параметрическим ветвлением

- Описание: одна формула с `if purchase_type == retail / manufacturer`, разные входные параметры, общий pipeline margin → discount → rounding. Параметры формулы — в коде (constants). Скидки — параметры расчёта, передаваемые при вызове.
- Плюсы: минимум сущностей; формула в одном месте; легко покрывается тестами; параметры читаемы в breakdown.
- Минусы: при изменении параметров (маржа, шаг округления) нужен деплой; нет audit trail изменения параметров.

### Вариант 2 — Таблица конфигурации + таблица клиентских скидок

- Описание: параметры формулы хранятся в `pricing_formula_config`, клиентские условия — в `pricing_customer_discount`. Формула читает параметры из БД.
- Плюсы: параметры меняются без деплоя; audit trail через версионирование записей; клиентские скидки явно видны в БД.
- Минусы: две новые таблицы на старте; сложнее тестирование (нужны фикстуры); для одного оператора с редко меняющимися параметрами — избыточно.

### Вариант 3 — Формула как external DSL / конфигурационный файл

- Описание: формула описывается в YAML/JSON-конфиге, парсится и выполняется runtime.
- Плюсы: максимальная гибкость; можно менять формулу без изменения кода.
- Минусы: сложность парсера; труднее отлаживать; потеря type safety; для проекта одного человека — оверинжиниринг.

## Критерии выбора

- **Надёжность:** формула в коде (Вариант 1) — самая предсказуемая. Ошибки ловятся на этапе тестов и ревью, а не runtime.
- **Простота поддержки:** один оператор, редко меняющиеся параметры. Деплой для изменения маржи с 20% на 22% — приемлемо. Лишние таблицы — нет.
- **Совместимость с AnythingLLM:** AnythingLLM может вызывать pricing API как tool. Все три варианта совместимы. Вариант 1 — проще API (меньше зависимостей при вызове).
- **Простота интеграции с Claude Code:** формула в Python-коде с type hints — идеально для Claude Code. Constants — легко находить и менять.
- **Масштабируемость:** Вариант 1 масштабируется до Варианта 2 при появлении потребности (миграция параметров из констант в таблицу — прямолинейная задача).

## Принятое решение

**Вариант 1 — единая формула с параметрическим ветвлением.**

Параметры формулы — constants в коде модуля `pricing`. Клиентские скидки — параметры, передаваемые при вызове расчёта. Новые таблицы конфигурации не создаются.

### Два слоя: Planned и Actual

| Слой | Когда считается | Входные данные | Результат | Модуль-владелец |
|---|---|---|---|---|
| **Planned** | До продажи, при формировании цены | Расчётный курс, плановая логистика, параметры формулы, маржа, скидка | `pricing_price_calculation` с полным breakdown | `pricing` |
| **Actual** | После всех фактов (оплата, обмен валюты, получение товара) | Фактический курс, фактическая логистика, банковская комиссия, overhead | Computed profit (query/view, не отдельная таблица) | `finance` (читает `pricing`) |

Planned — immutable snapshot. Actual — наращивается по мере поступления данных из `finance`. Profit вычисляется, не хранится.

### Две ветки формулы, общий pipeline

Формула pricing имеет два пути расчёта в зависимости от типа закупки. Оба выдают одинаковую структуру на выходе: breakdown JSONB + pre_round_price + final_price.

```
                    ┌───────────────────┐
                    │  purchase_type =  │
                    │  retail / mfr     │
                    └────────┬──────────┘
                             │
             ┌───────────────┴───────────────┐
             ▼                               ▼
   ┌──────────────────┐           ┌────────────────────────┐
   │  Retail path      │           │  Manufacturer path      │
   │                  │           │                        │
   │  purchase_cost   │           │  product_price_fcy     │
   │  (RUB or FX      │           │  × pricing_rate        │
   │   → RUB via rate)│           │  + logistics legs:     │
   │  + weight_kg ×   │           │    origin_shipping     │
   │    shipping/kg   │           │    intl_shipping       │
   │    (USD→RUB via  │           │    kz_to_moscow        │
   │     pricing_rate)│           │  + customs_fee         │
   │                  │           │  + intermediary_fee    │
   └────────┬─────────┘           └───────────┬────────────┘
            │                                 │
            └──────────┬──────────────────────┘
                       ▼
             ┌──────────────────┐
             │  base_cost_rub   │
             │  × (1 + margin%) │
             │  = subtotal      │
             │  × (1 − discount%)│
             │  = pre_round     │
             │  → ceil(step)    │
             │  = final_price   │
             └──────────────────┘
```

Общие элементы (margin → discount → rounding) — единый code path. Ветвление — только на этапе вычисления `base_cost_rub`.

### Retail path

Товар закупается в розницу. Закупка может быть как в RUB, так и в иностранной валюте (USD, EUR, JPY, CHF). Доставка до склада номинирована в USD и конвертируется через `pricing_exchange_rate`.

**Формула:**
```
# Если закупка в валюте:
purchase_cost_rub = purchase_cost_fcy × pricing_exchange_rate

# Если закупка в RUB:
purchase_cost_rub = purchase_cost_rub  (без конвертации)

shipping_cost_rub = weight_kg × shipping_per_kg_usd × pricing_exchange_rate
base_cost_rub = purchase_cost_rub + shipping_cost_rub
```

**Входные параметры:**

| Параметр | Тип | Обязателен | Источник |
|---|---|---|---|
| `purchase_cost` | NUMERIC(18,4) | Да | Цена закупки (в валюте или RUB) |
| `purchase_currency` | CHAR(3) | Да | Валюта закупки (RUB / USD / EUR / JPY / CHF) |
| `weight_kg` | NUMERIC(10,3) | Да | Из `catalog_product.declared_weight` или ввод оператора |
| `shipping_per_kg_usd` | NUMERIC(18,4) | Нет | Константа (default 17.00 USD). NULL = без доставки по весу |
| `shipping_currency` | CHAR(3) | Нет | Валюта тарифа доставки (default USD) |
| `pricing_exchange_rate` | NUMERIC(18,8) | Если любой FX | Из `pricing_exchange_rate` (с наценкой) |
| `pricing_rate_id` | BIGINT | Если любой FX | FK на запись в `pricing_exchange_rate` |

Примечание: `pricing_exchange_rate` обязателен, если `purchase_currency ≠ RUB` или если указан `shipping_per_kg_usd`. Если и закупка в RUB, и доставка не указана — курс не требуется. На практике доставка почти всегда есть, поэтому курс нужен почти всегда.

### Manufacturer path

Товар закупается у производителя в иностранной валюте. Маршрут может идти через Казахстан. Логистика состоит из нескольких ног (legs).

**Формула:**
```
product_price_rub = product_price_fcy × pricing_exchange_rate
base_cost_rub = product_price_rub
              + origin_shipping       (доставка внутри страны производителя, RUB)
              + intl_shipping         (международная доставка, RUB)
              + kz_to_moscow          (KZ → Москва, RUB, если маршрут через KZ)
              + customs_fee           (таможенные сборы, RUB, если есть)
              + intermediary_fee      (услуги посредника, RUB, fixed per purchase)
```

**Обязательные входные параметры:**

| Параметр | Тип | Обязателен | Источник |
|---|---|---|---|
| `product_price_fcy` | NUMERIC(18,4) | Да | Цена от производителя в валюте |
| `currency` | CHAR(3) | Да | Валюта закупки (USD, EUR, JPY, CHF) |
| `pricing_exchange_rate` | NUMERIC(18,8) | Да | Из `pricing_exchange_rate` (с наценкой) |
| `pricing_rate_id` | BIGINT | Да | FK на запись в `pricing_exchange_rate` |
| `origin_shipping` | NUMERIC(18,4) | Нет | Доставка внутри страны производителя |
| `intl_shipping` | NUMERIC(18,4) | Нет | Международная доставка |
| `kz_to_moscow` | NUMERIC(18,4) | Нет | Доставка KZ → Москва |
| `customs_fee` | NUMERIC(18,4) | Нет | Таможенные сборы |
| `intermediary_fee` | NUMERIC(18,4) | Нет | Фиксированная сумма в RUB за закупку/поставку |

Необязательные параметры: если не указаны — равны 0 в расчёте. Но в breakdown отсутствующие legs не включаются (чтобы было видно, какие ноги реально участвовали).

**Intermediary fee (Бисембай):** фиксированная сумма в RUB за закупку или поставку. Не процент, не per unit. Если в закупке несколько товаров — fee аллоцируется по товарам пропорционально стоимости (`product_price_rub`) при расчёте per-item breakdown.

**Логистические legs:** хранятся раздельно в breakdown для прозрачности. Если данных мало и маршрут простой — оператор заполняет только `intl_shipping`, остальные legs = 0. Система не требует заполнения всех legs.

### Общий pipeline (после base_cost_rub)

```python
# 1. Margin
margin_percent = 20.0  # default, может быть снижена для клиента
subtotal = base_cost_rub * (1 + margin_percent / 100)

# 2. Discount (если есть)
discount_percent = 0.0  # или 7.0 для конкретного клиента
discount_amount = subtotal * (discount_percent / 100)
pre_round_price = subtotal - discount_amount

# 3. Rounding
if pre_round_price < 1000:
    rounding_step = 10
else:
    rounding_step = 100
# operator override возможен

final_price = ceil(pre_round_price / rounding_step) * rounding_step
```

### Порядок операций: margin → discount → rounding

Это зафиксированный порядок. Пример (manufacturer path, клиент с 20% маржой и 7% скидкой):

```
product_price_fcy = 50.00 USD
pricing_exchange_rate = 92.50
product_price_rub = 4625.00
origin_shipping = 0
intl_shipping = 1200.00
kz_to_moscow = 800.00
customs_fee = 300.00
intermediary_fee = 500.00
base_cost_rub = 7425.00

margin 20%: 7425.00 × 1.20 = 8910.00
discount 7%: 8910.00 × 0.07 = 623.70
pre_round_price = 8910.00 − 623.70 = 8286.30

pre_round >= 1000 → rounding_step = 100
final_price = ceil(8286.30 / 100) × 100 = 8300.00
```

### Rounding policy

| Правило | Значение |
|---|---|
| Метод | Ceiling (всегда вверх до ближайшего кратного шагу) |
| Порог переключения шага | `pre_round_price < 1000 RUB` |
| Шаг при `pre_round < 1000` | 10 RUB |
| Шаг при `pre_round >= 1000` | 100 RUB |
| Operator override | Допустим. Оператор может указать другой `rounding_step` при расчёте. |
| Если цена уже кратна шагу | Не округляется (ceil(8300/100)×100 = 8300) |

В `pricing_price_calculation` хранятся оба значения: `pre_round_price` и `final_price`, а также фактически применённый `rounding_step`.

### Margin policy

Маржа — обязательный компонент формулы, применяется к `base_cost_rub` до скидки и до округления.

**Default margin:** 20%. Фиксируется как `DEFAULT_MARGIN_PERCENT` в `app/pricing/constants.py`.

**Сниженная маржа по клиенту.** Для отдельных клиентов оператор может указать другое значение `margin_percent` (например, 15% или 10%). Это передаётся как параметр при вызове расчёта. Формула не знает «почему» маржа снижена — она получает конкретное число.

**Маржа в breakdown:** всегда фиксируется фактически применённый `margin_percent` и вычисленный `margin_amount`. Это позволяет при анализе прибыли видеть, какая маржа была заложена в цену.

**Маржа в pricing_price_calculation:** поле `margin_percent NUMERIC(5,2) NOT NULL` хранит применённую маржу. Это позволяет фильтровать расчёты по марже без разбора JSONB.

На этом этапе маржа — **параметр вызова**, а не запись в БД. Оператор помнит условия клиента и передаёт при расчёте. Если появится потребность в автоматическом применении (система «знает» условия клиента) — это отдельный ADR с таблицей `pricing_customer_discount`.

### Discount policy

Скидка — опциональный компонент формулы, применяется **после маржи, до округления**. Порядок (margin → discount → rounding) зафиксирован и не меняется.

**Скидка по клиенту (per-item level).** При расчёте цены для конкретного клиента формула принимает `discount_percent` как входной параметр. Результат фиксируется в breakdown конкретного price_calculation.

**Скидка по заказу (order-level, аллоцированная).** Если скидка применяется к заказу в целом (например, 7% от суммы заказа), она аллоцируется по order_item пропорционально стоимости позиции. Каждый order_item получает свою долю скидки, и его `unit_price` отражает net of allocated discount. Последняя позиция получает остаток (rounding error correction).

Пример: заказ из 3 позиций (5000 + 3000 + 2000 = 10000), скидка 7% = 700.

| Позиция | Доля | Скидка | unit_price |
|---|---|---|---|
| 5000 | 50% | 350 | 4650 |
| 3000 | 30% | 210 | 2790 |
| 2000 | 20% | 140 | 1860 |

Дополнительно на уровне `orders_order` хранится общая скидка заказа (`order_discount_percent`) как атрибут для прозрачности, но для item-level profit используется уже аллоцированный `unit_price`.

**Комбинация сниженной маржи и скидки.** Возможна ситуация, когда для клиента одновременно снижена маржа (например, 15% вместо 20%) И применена скидка (например, 7%). Порядок: base_cost × (1 + 15%) = subtotal → subtotal × (1 − 7%) = pre_round → rounding. Оба параметра фиксируются в breakdown и в полях `margin_percent` / `discount_percent` таблицы `pricing_price_calculation`.

На этом этапе **скидки — параметры вызова**, а не записи в БД. Если появится потребность в автоматическом применении — это отдельный ADR с таблицей `pricing_customer_discount`.

### Approved sale price

`orders_order_item.unit_price` — approved sale price. Это финальная цена, по которой товар продаётся клиенту. Связь с расчётом — через `price_calculation_id`.

Три сценария:

1. **Цена = расчётная.** `unit_price == price_calculation.final_price`, `operator_adjusted = false`.
2. **Цена изменена оператором.** `unit_price ≠ price_calculation.final_price`, `operator_adjusted = true`. Причина изменения — на усмотрение оператора (может быть зафиксирована в notes на order_item в будущем).
3. **Цена без расчёта.** `price_calculation_id = NULL`, оператор вручную задал `unit_price`. Допустимо для исключительных случаев.

### Доставка клиенту

Доставка по Москве/РФ для клиента **не входит в формулу pricing товара**. Это отдельная составляющая заказа.

На уровне `orders_order` добавляются поля:

- `delivery_cost NUMERIC(18,4) NULL` — стоимость доставки клиенту.
- `delivery_method TEXT NULL` — способ доставки (СДЭК, Яндекс и т.д.).
- `delivery_paid_by_customer BOOLEAN DEFAULT true` — оплачивается клиентом отдельно.

Если доставка бесплатная (промо-акция), `delivery_paid_by_customer = false`, и стоимость доставки учитывается как расход в `finance_expense`.

### Банковская комиссия

**В плановой цене:** не учитывается. Комиссия ~0.4% нестабильна и не контролируется оператором. Включение в цену создаёт ложную точность.

**В фактической прибыли:** учитывается как `finance_expense` с категорией `bank_commission`, привязанная к заказу через `related_module = orders`, `related_id = order.id`.

### Store overhead

**В плановой цене:** не учитывается. Overhead покрывается маржой 20%.

**В фактической прибыли:** учитывается как monthly pool. Расходы фиксируются в `finance_expense` с категорией `overhead` помесячно. Profit периода = Σ(order_profits) − Σ(overhead_expenses).

Система может показывать: «overhead этого месяца = X RUB, покрытие маржой = Y RUB, дельта = Z RUB» — это помогает оператору корректировать overhead или маржу при необходимости.

### Planned vs Actual Profit (вычисляемый, не хранимый)

| Компонент | Planned (из pricing) | Actual (из finance) |
|---|---|---|
| Закупочная стоимость | `breakdown.purchase_cost_rub` или `breakdown.product_price_rub` | `finance_expense` (category: `purchase`) |
| Курс валюты | `breakdown.pricing_exchange_rate` | `finance_exchange_rate` + `finance_exchange_operation` |
| Логистика | `breakdown.{shipping legs}` | `finance_expense` (category: `logistics`) |
| Таможня | `breakdown.customs_fee` | `finance_expense` (category: `customs`) |
| Intermediary | `breakdown.intermediary_fee` | `finance_expense` (category: `intermediary`) |
| Банковская комиссия | — (не в planned) | `finance_expense` (category: `bank_commission`) |
| Store overhead | — (не в planned) | `finance_expense` (category: `overhead`) — monthly |
| Выручка | `final_price` | `orders_order_item.unit_price` (может отличаться от planned) |

Profit по товару (item-level): `unit_price − Σ(actual direct costs)`.
Profit по заказу: `Σ(item profits) − bank_commission`.
Profit по периоду: `Σ(order profits) − Σ(overhead)`.

Все вычисляются query/view, не хранятся как отдельные записи. Для малого объёма (десятки-сотни заказов) join-ы тривиальны.

### Расширения схемы ADR-003

Настоящий ADR добавляет следующие поля/enum-ы к существующим таблицам ADR-003. Новые таблицы не создаются.

**Новый enum:** `purchase_type`: `retail`, `manufacturer`. Определяется в `app/shared/types.py` (не в модуле pricing), поскольку используется моделями двух модулей (`pricing` и `catalog`). Это кросс-модульный бизнес-тип, аналогичный `Currency`. Дополнение к конвенции ADR-003: «если enum используется моделями нескольких модулей — определяется в `shared`».

**Расширение `pricing_price_calculation`:**

| Поле | Тип | Назначение |
|---|---|---|
| `purchase_type` | ENUM `purchase_type` | Ветка формулы |
| `pre_round_price` | NUMERIC(18,4) | Цена до округления |
| `rounding_step` | INT | Фактически применённый шаг (10 или 100) |
| `margin_percent` | NUMERIC(5,2) | Применённая маржа |
| `discount_percent` | NUMERIC(5,2) NULL | Применённая скидка (NULL = без скидки) |

**Constraints на новые поля `pricing_price_calculation`** (стиль ADR-003 — constraints всегда явные):

- CHECK `pre_round_price >= 0`
- CHECK `rounding_step > 0`
- CHECK `margin_percent >= 0`
- CHECK `(discount_percent >= 0 AND discount_percent <= 100) OR discount_percent IS NULL`

**Constraints на новые поля `orders_order`:**

- CHECK `delivery_cost >= 0 OR delivery_cost IS NULL`
- CHECK `(order_discount_percent >= 0 AND order_discount_percent <= 100) OR order_discount_percent IS NULL`

Примечание: `customer_id` FK **не добавляется** в `pricing_price_calculation`. Причина: ADR-001 v2 фиксирует граф зависимостей как DAG, где `orders → pricing` уже существует. Добавление FK `pricing → orders_customer` создало бы цикл `orders → pricing → orders`, нарушающий архитектурный принцип модульного монолита. Информация о клиенте, для которого сделан расчёт, доступна через join `orders_order_item → orders_order → orders_customer`. Если для аудита нужно зафиксировать, для кого считалась цена — это хранится в `input_params JSONB` как контекстный атрибут (не FK).

**Расширение `orders_order_item`:**

| Поле | Тип | Назначение |
|---|---|---|
| `operator_adjusted` | BOOLEAN DEFAULT false | Оператор вручную изменил цену |

**Расширение `orders_order`:**

| Поле | Тип | Назначение |
|---|---|---|
| `delivery_cost` | NUMERIC(18,4) NULL | Стоимость доставки клиенту |
| `delivery_method` | TEXT NULL | СДЭК / Яндекс / самовывоз / … |
| `delivery_paid_by_customer` | BOOLEAN DEFAULT true | Оплачивается клиентом |
| `order_discount_percent` | NUMERIC(5,2) NULL | Скидка на уровне заказа (для прозрачности) |

**Расширение `catalog_supplier`:**

| Поле | Тип | Назначение |
|---|---|---|
| `default_purchase_type` | ENUM `purchase_type` NULL | Подсказка для формулы |

### Структура breakdown JSONB

#### Retail path breakdown (FX purchase example)

```json
{
  "purchase_type": "retail",
  "purchase_cost": 50.00,
  "purchase_currency": "USD",
  "pricing_exchange_rate": 92.50,
  "pricing_rate_id": 42,
  "purchase_cost_rub": 4625.00,
  "weight_kg": 2.5,
  "shipping_per_kg_usd": 17.00,
  "shipping_currency": "USD",
  "shipping_cost_rub": 3931.25,
  "base_cost_rub": 8556.25,
  "margin_percent": 20.0,
  "margin_amount": 1711.25,
  "subtotal": 10267.50,
  "discount_percent": null,
  "discount_amount": 0.0,
  "pre_round_price": 10267.50,
  "rounding_step": 100,
  "final_price": 10300.00
}
```

#### Retail path breakdown (RUB purchase example)

```json
{
  "purchase_type": "retail",
  "purchase_cost": 5000.00,
  "purchase_currency": "RUB",
  "purchase_cost_rub": 5000.00,
  "weight_kg": 2.5,
  "shipping_per_kg_usd": 17.00,
  "shipping_currency": "USD",
  "pricing_exchange_rate": 92.50,
  "pricing_rate_id": 42,
  "shipping_cost_rub": 3931.25,
  "base_cost_rub": 8931.25,
  "margin_percent": 20.0,
  "margin_amount": 1786.25,
  "subtotal": 10717.50,
  "discount_percent": null,
  "discount_amount": 0.0,
  "pre_round_price": 10717.50,
  "rounding_step": 100,
  "final_price": 10800.00
}
```

Примечание: при `purchase_currency = RUB` поля `pricing_exchange_rate` и `pricing_rate_id` всё равно присутствуют в breakdown, если shipping конвертировался через курс. Если ни закупка, ни shipping не требовали конвертации — поля `pricing_exchange_rate` и `pricing_rate_id` отсутствуют в breakdown.

#### Manufacturer path breakdown

```json
{
  "purchase_type": "manufacturer",
  "product_price_fcy": 50.00,
  "currency": "USD",
  "pricing_exchange_rate": 92.50,
  "pricing_rate_id": 42,
  "product_price_rub": 4625.00,
  "logistics": {
    "origin_shipping": 0.0,
    "intl_shipping": 1200.00,
    "kz_to_moscow": 800.00
  },
  "customs_fee": 300.00,
  "intermediary_fee": 500.00,
  "base_cost_rub": 7425.00,
  "margin_percent": 20.0,
  "margin_amount": 1485.00,
  "subtotal": 8910.00,
  "discount_percent": 7.0,
  "discount_amount": 623.70,
  "pre_round_price": 8286.30,
  "rounding_step": 100,
  "final_price": 8300.00
}
```

Оба breakdown содержат все входные параметры и все промежуточные шаги — полная воспроизводимость.

Logistics legs в manufacturer path — вложенный объект. Legs с нулевым значением **не включаются** в объект `logistics` (если origin_shipping = 0 и не указан — его нет в breakdown). Legs, которые указаны явно как 0.00 — включаются (оператор осознанно указал 0).

### Constants в коде модуля `pricing`

Следующие параметры фиксируются как constants в `app/pricing/constants.py`:

```python
DEFAULT_MARGIN_PERCENT = Decimal("20.00")
DEFAULT_ROUNDING_STEP = 100
SMALL_ITEM_ROUNDING_STEP = 10
ROUNDING_THRESHOLD_RUB = Decimal("1000.00")
DEFAULT_SHIPPING_PER_KG_USD = Decimal("17.00")
DEFAULT_SHIPPING_CURRENCY = "USD"
```

При изменении — деплой. Если изменения станут частыми — выносится в `pricing_formula_config` (отдельный ADR).

**Статус констант: temporary code constants с гарантией выносимости.**

Коммерческие параметры (`DEFAULT_MARGIN_PERCENT`, `DEFAULT_SHIPPING_PER_KG_USD`, `ROUNDING_THRESHOLD_RUB` и др.) фиксируются как Python-константы в `app/pricing/constants.py` на старте. Это осознанное упрощение для этапа, когда параметры меняются редко и оператор один.

Архитектурное обязательство: формула pricing **не обращается к константам напрямую**, а получает все параметры через структуру `PriceInput` (Pydantic-схема). Constants используются только как **default values** при создании `PriceInput`. Это означает:

- Формула (pure function) не зависит от источника параметров.
- Вынос констант в таблицу `pricing_formula_config` (отдельный ADR) не требует изменения формулы — только изменения точки, где создаётся `PriceInput`.
- Тесты формулы не зависят от констант — они передают параметры явно.

Триггер для выноса: если оператор меняет параметры чаще 1 раза в квартал, или если появляется потребность в audit trail изменений параметров — создаётся ADR на `pricing_formula_config`.

Паттерн в коде:

```python
# app/pricing/constants.py — defaults, not dependencies
DEFAULT_MARGIN_PERCENT = Decimal("20.00")
DEFAULT_SHIPPING_PER_KG_USD = Decimal("17.00")
DEFAULT_SHIPPING_CURRENCY = "USD"
ROUNDING_THRESHOLD_RUB = Decimal("1000.00")

# app/pricing/schemas.py — PriceInput получает defaults из constants
class RetailPriceInput(BaseModel):
    purchase_cost: Decimal                                     # Цена в оригинальной валюте
    purchase_currency: str = "RUB"                             # RUB / USD / EUR / JPY / CHF
    weight_kg: Decimal
    shipping_per_kg_usd: Decimal = DEFAULT_SHIPPING_PER_KG_USD
    shipping_currency: str = DEFAULT_SHIPPING_CURRENCY
    pricing_exchange_rate: Decimal | None = None                # Обязателен если any FX
    pricing_rate_id: int | None = None                         # Обязателен если any FX
    margin_percent: Decimal = DEFAULT_MARGIN_PERCENT
    discount_percent: Decimal | None = None
    rounding_step: int | None = None  # None = auto by threshold
    # Валидация: если purchase_currency != "RUB" или shipping_per_kg_usd указан,
    # то pricing_exchange_rate и pricing_rate_id обязательны

# app/pricing/service.py — pure function, no imports from constants
def calculate_price(params: RetailPriceInput) -> PriceCalculationResult:
    # uses only params.*, never constants.*
    ...
```

Claude Code при реализации должен следовать этому паттерну: **формула получает параметры, а не читает глобальные переменные**.

## Последствия

**Что становится проще:**

- Формула pricing полностью определена: входные параметры, ветвление, pipeline, output. Claude Code может реализовать модуль `pricing` одним промтом.
- Breakdown JSONB содержит полный audit trail расчёта — любой расчёт воспроизводим.
- Два слоя (planned/actual) чётко разделены — нет путаницы между «почём должно было быть» и «как вышло».
- Скидки и маржа — параметры вызова, а не сложная конфигурация. Оператор контролирует каждый расчёт.
- Rounding policy формализована — больше не ручная привычка.
- Банковская комиссия и overhead — только в fact layer, не загромождают формулу pricing.
- Доставка клиенту — отдельная строка заказа, не смешивается с ценой товара.

**Какие ограничения появляются:**

- Параметры формулы в constants — изменение требует деплоя. Для одного оператора с редкими изменениями — приемлемо.
- Клиентские скидки — параметры вызова, не автоматические. Оператор должен помнить условия клиента и передавать при расчёте. Если это станет проблемой — нужна таблица `pricing_customer_discount`.
- Intermediary fee аллоцируется по товарам пропорционально стоимости — при сильно разнородных товарах в одной закупке это может быть неточно.
- Profit вычисляется query, не хранится — при росте объёмов может потребоваться materialized view.
- Order-level discount аллоцируется пропорционально — при округлении сумма аллоцированных скидок может отличаться от общей на несколько копеек (rounding error). Нужна логика «последняя позиция получает остаток».

**Что придётся учитывать дальше:**

- Налоговые формулы (какие налоги, как считаются) — отдельный ADR.
- Таблица `pricing_customer_discount` — если клиентских условий станет много.
- Таблица `pricing_formula_config` — если параметры формулы начнут часто меняться.
- Profit view / query — реализация при создании модуля `finance`.
- Пересчёт цены при изменении курса для неподтверждённых заказов — отдельная policy.
- Расширение `finance_expense_category` enum: нужно добавить `bank_commission`, `overhead`, `logistics`, `customs`, `intermediary` (если ещё не в ADR-003).

## Что должен сделать Claude Code

1. Создать `app/pricing/constants.py` с дефолтными параметрами формулы: `DEFAULT_MARGIN_PERCENT`, `DEFAULT_ROUNDING_STEP`, `SMALL_ITEM_ROUNDING_STEP`, `ROUNDING_THRESHOLD_RUB`, `DEFAULT_SHIPPING_PER_KG_USD`, `DEFAULT_SHIPPING_CURRENCY`.
2. Создать enum `purchase_type` (`retail`, `manufacturer`) в `app/shared/types.py` (кросс-модульный тип, используется в `pricing` и `catalog`). Импортировать в `app/pricing/models.py` и `app/catalog/models.py`.
3. Добавить в `pricing_price_calculation` поля: `purchase_type`, `pre_round_price`, `rounding_step`, `margin_percent`, `discount_percent`. Поле `customer_id` **не добавлять** (см. примечание в секции «Расширения схемы ADR-003» — FK нарушает DAG зависимостей).
4. Добавить в `orders_order_item` поле `operator_adjusted BOOLEAN DEFAULT false`.
5. Добавить в `orders_order` поля: `delivery_cost`, `delivery_method`, `delivery_paid_by_customer`, `order_discount_percent`.
6. Добавить в `catalog_supplier` поле `default_purchase_type`.
7. Расширить enum `finance_expense_category` значениями `bank_commission`, `overhead`, `customs`, `intermediary` (если их ещё нет). Значение `logistics` уже существует в ADR-003. Примечание: `commission` (из ADR-003) — комиссия поставщика или посредника по закупке. `bank_commission` (из ADR-004) — комиссия банка за поступление денег на счёт. Это разные категории расходов, обе нужны.
8. Создать Alembic-миграцию для всех изменений схемы.
9. Создать `app/pricing/service.py` с реализацией формулы:
   - функция `calculate_retail_base_cost(params) → Decimal`;
   - функция `calculate_manufacturer_base_cost(params) → Decimal`;
   - функция `apply_margin(base_cost, margin_percent) → Decimal`;
   - функция `apply_discount(subtotal, discount_percent) → Decimal`;
   - функция `apply_rounding(price, rounding_step=None) → tuple[Decimal, int]`;
   - функция `calculate_price(params: PriceInput) → PriceCalculation`;
   - все функции — pure, stateless, детерминированные.
10. Создать `app/pricing/schemas.py` с Pydantic-схемами для входных параметров (`RetailPriceInput`, `ManufacturerPriceInput`) и результата (`PriceCalculationResult` с breakdown).
11. Написать юнит-тесты `tests/pricing/test_formula.py`:
    - тест retail path (базовый);
    - тест manufacturer path (базовый);
    - тест manufacturer path со всеми legs;
    - тест manufacturer path с intermediary fee;
    - тест с discount 7%;
    - тест с пониженной маржой;
    - тест rounding < 1000 → step 10;
    - тест rounding >= 1000 → step 100;
    - тест rounding operator override;
    - тест edge case: цена ровно кратна шагу;
    - тест order-level discount allocation пропорционально;
    - тест order-level discount allocation: последняя позиция получает остаток.
12. Обновить `README.md`: добавить описание pricing policy (два пути, pipeline, rounding).
13. Поместить этот ADR в `docs/adr/ADR-004-pricing-profit-policy.md`.

## Что проверить вручную

- Миграция применяется без ошибок (`alembic upgrade head`), откатывается (`alembic downgrade -1 && alembic upgrade head`).
- `\d pricing_price_calculation` показывает новые поля: `purchase_type`, `pre_round_price`, `rounding_step`, `margin_percent`, `discount_percent`. Поле `customer_id` **отсутствует** (убрано — нарушало DAG зависимостей, см. ADR-001).
- `\d orders_order_item` показывает `operator_adjusted BOOLEAN DEFAULT false`.
- `\d orders_order` показывает `delivery_cost`, `delivery_method`, `delivery_paid_by_customer`, `order_discount_percent`.
- `\d catalog_supplier` показывает `default_purchase_type`.
- `\dT+ purchase_type` показывает `retail`, `manufacturer`.
- `\dT+ finance_expense_category` включает `bank_commission`, `overhead`, `logistics`, `customs`, `intermediary`.
- Все юнит-тесты проходят (`pytest tests/pricing/`).
- Формула retail (FX): purchase 50 USD × rate 92.50 = 4625 + weight 2.5 × shipping 17 USD × rate 92.50 = 3931.25 → base 8556.25 → margin 20% = 10267.50 → ceil(10267.50/100) = 10300.
- Формула retail (RUB): purchase 5000 RUB + weight 2.5 × shipping 17 USD × rate 92.50 = 3931.25 → base 8931.25 → margin 20% = 10717.50 → ceil(10717.50/100) = 10800.
- Формула manufacturer: пример из ADR (product 50 USD × 92.50 + legs + margin 20% − discount 7% = 8286.30 → ceil/100 = 8300).
- Rounding: 950 → ceil(950/10)×10 = 950 (кратно). 951 → ceil(951/10)×10 = 960.
- Rounding: 8286.30 → ceil(8286.30/100)×100 = 8300.
- `ruff check app/` и `mypy app/` проходят.
- `/health` по-прежнему работает (регрессия).
