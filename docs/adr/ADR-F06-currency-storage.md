# ADR-F06: Хранение валюты и фактического курса обмена

**Статус:** принят
**Дата:** 2026-04-30
**Связанные ADR:** ADR-003 final-ready (core schema), ADR-004 (pricing & profit policy), ADR-001 v2 (модульный монолит)
**Связанные документы:** `05_business_rules.md`, `07_glossary.md`

---

## Контекст

ADR-004 описывает двухслойную модель прибыли (Planned / Actual) и формулы расчёта себестоимости с конвертацией валюты через `pricing_exchange_rate`. Однако три проблемы остались нерешёнными:

**Проблема 1 — `orders_order_item` не хранит закупочную цену в валюте.**
При создании заказа на иностранный товар оператор вводит цену в USD/EUR, но MCP принимает её как рубли — исходная валюта теряется. Кейс-триггер: заказ З-1580, товар Infinity Tools Lock Miter Master Jigs за $64.90 записан как 65 ₽. Плановая прибыль по такому заказу невычислима.

**Проблема 2 — скидка поставщика (промокод) не имеет структурного места в модели.**
При закупке поставщик может дать скидку на конкретный товар. Эта скидка снижает фактическую закупочную цену и увеличивает прибыль. Сейчас негде зафиксировать этот факт — он либо теряется, либо оператор вводит уже скорректированную цену вручную (теряя прозрачность).

**Проблема 3 — нет механизма фактического курса для расчёта Actual Profit.**
В ADR-003 есть `finance_exchange_operation` (факт обмена валюты) и `finance_exchange_rate` (курс). Но нет способа вычислить фактический курс закупки конкретного товара: один обмен покрывает несколько закупок, одна закупка может оплачиваться из нескольких обменов. Прямая привязка обмена к заказу отсутствует.

Что **не обсуждается** в этом ADR: формулы расчёта плановой цены (ADR-004), логика pricing_exchange_rate (ADR-004), структура `finance_expense` (ADR-003).

---

## Варианты

### Развилка A — хранение закупочной цены в `orders_order_item`

#### Вариант A1 — хранить пару (цена в валюте + скидка + валюта) рядом с рублёвой ценой ✅

Добавить три поля на `orders_order_item`:
- `purchase_price_fcy NUMERIC(18,4) NULL` — прайс поставщика в валюте
- `supplier_discount_fcy NUMERIC(18,4) NULL` — скидка поставщика в той же валюте (промокод, акция)
- `purchase_currency CHAR(3) NULL` — валюта закупки (USD, EUR, JPY, CHF)

`unit_price` остаётся рублями — цена клиенту. `net_purchase_fcy = purchase_price_fcy − supplier_discount_fcy` — вычисляемое, не хранится.

- **Плюсы:** исходная цена поставщика сохранена; скидка прозрачна; `unit_price` не меняет семантику; каталог и заказ хранят цену независимо (эталонная vs фактическая сделка).
- **Минусы:** три дополнительных nullable-поля на таблице заказов.

#### Вариант A2 — только рублёвая цена, конвертацию делает оператор

`unit_price` всегда рубли, оператор конвертирует сам перед вводом.

- **Плюсы:** схема не меняется.
- **Минусы:** исходная валюта теряется; Actual Profit невычислим; скидка поставщика невидима.

### Развилка B — фактический курс для Actual Profit

#### Вариант B1 — явная таблица аллокации обменов на закупки

Новая таблица `finance_exchange_allocation`: каждая строка = «из обмена X на позицию заказа Y ушло Z единиц валюты по курсу W».

- **Плюсы:** максимальная точность; полная аудируемость.
- **Минусы:** требует ручной привязки каждого обмена к каждой закупке — оператор не ведёт такой учёт в момент обмена; операционная нагрузка неприемлема.

#### Вариант B2 — средневзвешенный курс по остатку валютного пула ✅

Два независимых пула — USD и EUR. Каждый обмен (`finance_exchange_operation`) пополняет пул. Каждая закупка в валюте расходует пул. Фактический курс закупки = средневзвешенный курс текущего остатка пула на дату закупки.

Пример: обменяли $550 по 90.9 + $500 по 90.0 → пул $1050, средний курс 90.48. Купили товар за $58.41 (net) → себестоимость 58.41 × 90.48 = 5284 ₽. Остаток пула пересчитывается.

Пул вычисляемый: `effective_rate_usd` в момент закупки = `SUM(rub_spent) / SUM(usd_received)` по всем обменам в пуле до даты закупки. Явно хранить остаток не нужно.

- **Плюсы:** не требует ручной привязки; автоматически корректен при нескольких обменах; операционно прозрачен.
- **Минусы:** незначительная погрешность по сравнению с B1 (несущественна для масштаба бизнеса).

#### Вариант B3 — курс ближайшего обмена по дате (текущий подход)

При закупке берётся курс ближайшего обмена той же валюты.

- **Плюсы:** простая реализация.
- **Минусы:** «ближайший» неоднозначен (до или после?); при волатильном рубле погрешность существенна; при двух равноудалённых обменах — недетерминировано.

---

## Критерии выбора

- **Надёжность:** A1 сохраняет факт сделки без потерь. B2 детерминирован и не требует ручных действий.
- **Простота поддержки:** A1 — три nullable-поля, миграция обратно совместима. B2 — вычисляемый пул, никакой отдельной таблицы не нужно.
- **Совместимость с ADR-004:** A1 напрямую даёт `purchase_price_fcy` для `breakdown`. B2 даёт `actual_exchange_rate` для Actual Profit.
- **Операционная реалистичность:** B1 отвергнут — оператор не привязывает обмены к закупкам в момент обмена. B2 работает с тем, как оператор реально ведёт учёт.
- **Масштабируемость:** B2 точен при любом количестве обменов; деградация производительности не ожидается на данном масштабе.

---

## Принятое решение

### Часть 1 — Расширение `orders_order_item`

Добавить три nullable-поля:

| Поле | Тип | Обязателен | Смысл |
|---|---|---|---|
| `purchase_price_fcy` | `NUMERIC(18,4) NULL` | Нет | Прайс поставщика в валюте закупки |
| `supplier_discount_fcy` | `NUMERIC(18,4) NULL` | Нет | Скидка поставщика в той же валюте (промокод, акция) |
| `purchase_currency` | `CHAR(3) NULL` | Нет | Валюта закупки (USD, EUR, JPY, CHF) |

**Инварианты:**
- `purchase_currency` обязателен если заполнено `purchase_price_fcy` — CHECK constraint: `(purchase_price_fcy IS NULL) OR (purchase_currency IS NOT NULL)`
- `supplier_discount_fcy >= 0` — CHECK constraint
- `supplier_discount_fcy < purchase_price_fcy` — CHECK constraint (скидка не может превышать цену)
- Скидка всегда в той же валюте что и `purchase_price_fcy` — единая валюта, отдельного поля не нужно
- `net_purchase_fcy = purchase_price_fcy − COALESCE(supplier_discount_fcy, 0)` — вычисляется в коде, не хранится

**Семантика:**
- `unit_price` (рубли) — цена клиенту, не меняет семантику
- `purchase_price_fcy` — фактическая цена сделки с поставщиком (не эталонная цена из каталога)
- Каталог (`catalog_product`) хранит эталонную цену поставщика; заказ хранит цену конкретной сделки — это разные факты, они живут независимо

### Часть 2 — Вычисляемый фактический курс (пул средневзвешенного курса)

Используется существующая таблица `finance_exchange_operation` (ADR-003). Никакой новой таблицы не добавляется.

**Алгоритм вычисления фактического курса на дату D для валюты CCY:**

```sql
-- Фактический курс пула USD/EUR на дату D
SELECT
    SUM(from_amount) / SUM(to_amount) AS effective_rate
FROM finance_exchange_operation
WHERE to_currency = 'USD'         -- или 'EUR'
  AND operated_at <= D
  AND from_currency = 'RUB'
  AND remaining_balance > 0       -- только непотраченный остаток
```

На практике: сервисный слой `app/finance/` реализует функцию `get_effective_rate(currency: str, as_of: datetime) -> Decimal`, которая вычисляет средневзвешенный курс по всем обменам до указанной даты.

**Два независимых пула:**
- USD-пул: все операции `RUB → USD`
- EUR-пул: все операции `RUB → EUR`
- Кросс-валютные операции (USD → EUR) — вне scope данного ADR

**Расход пула:**
Когда оператор регистрирует закупку позиции заказа в валюте, `app/finance/` вычисляет `effective_rate` на дату закупки и записывает его в `finance_expense` как фактическую стоимость в рублях. Это связывает факт обмена с фактом закупки через общий пул, без явной таблицы-аллокации.

### Что хранится vs что вычисляется

| Данные | Хранится | Вычисляется |
|---|---|---|
| Прайс поставщика | `purchase_price_fcy` | — |
| Скидка поставщика | `supplier_discount_fcy` | — |
| Чистая закупочная цена | — | `purchase_price_fcy − supplier_discount_fcy` |
| Курс обмена (каждая операция) | `finance_exchange_rate.rate` | — |
| Средневзвешенный курс пула | — | `get_effective_rate(currency, date)` |
| Себестоимость в рублях (actual) | `finance_expense.amount` | из `net_purchase_fcy × effective_rate` |

---

## Последствия

### Что становится проще

- Actual Profit по позиции заказа становится вычислимым: `unit_price − net_purchase_rub − logistics_rub − ...`
- Скидки поставщика прозрачны и учитываются в прибыли без ручных корректировок
- MCP `create_order` и будущий `update_order_item` могут принимать цену в валюте и сохранять исходный факт
- Cowork может показывать оператору: «закупочная цена $58.41, курс пула 90.48, себестоимость 5284 ₽»

### Какие ограничения появляются

- `purchase_currency` обязателен при заполнении `purchase_price_fcy` — MCP должен валидировать
- Функция `get_effective_rate` требует что `finance_exchange_operation` ведётся аккуратно — «мусорные» обмены исказят курс
- Кросс-валютные закупки (товар в JPY при пуле из USD/EUR) — вне scope, требуют отдельного ADR при появлении

### Что придётся учитывать дальше

- При реализации `update_order_item` MCP-tool: если меняется `purchase_price_fcy`, пересчитать `finance_expense` для этой позиции
- `get_effective_rate` должна быть идемпотентной — вызов дважды с одними параметрами даёт одинаковый результат
- Отчёт «Actual Profit» (будущий ADR) будет читать `purchase_price_fcy`, `supplier_discount_fcy`, `purchase_currency` и вызывать `get_effective_rate`

---

## Что должен сделать Claude Code

### Задача 1 — Миграция Alembic: расширение `orders_order_item`

Добавить три nullable-поля и два CHECK-constraints:

```sql
ALTER TABLE orders_order_item
  ADD COLUMN purchase_price_fcy  NUMERIC(18,4) NULL,
  ADD COLUMN supplier_discount_fcy NUMERIC(18,4) NULL,
  ADD COLUMN purchase_currency   CHAR(3)       NULL;

ALTER TABLE orders_order_item
  ADD CONSTRAINT chk_purchase_currency_required
    CHECK ((purchase_price_fcy IS NULL) OR (purchase_currency IS NOT NULL)),
  ADD CONSTRAINT chk_supplier_discount_non_negative
    CHECK (supplier_discount_fcy >= 0 OR supplier_discount_fcy IS NULL),
  ADD CONSTRAINT chk_supplier_discount_lt_price
    CHECK (supplier_discount_fcy IS NULL OR purchase_price_fcy IS NULL
           OR supplier_discount_fcy < purchase_price_fcy);
```

Существующие строки не затрагиваются (все поля NULL).

### Задача 2 — Сервисная функция `get_effective_rate`

В `app/finance/service.py` реализовать:

```python
async def get_effective_rate(
    session: AsyncSession,
    currency: str,          # 'USD' или 'EUR'
    as_of: datetime,
) -> Decimal | None:
    """
    Средневзвешенный курс пула на дату as_of.
    Возвращает None если обменов до as_of не было.
    Формула: SUM(from_amount_rub) / SUM(to_amount_fcy)
    по всем finance_exchange_operation WHERE to_currency = currency
    AND operated_at <= as_of AND from_currency = 'RUB'.
    """
```

Покрыть тестами: пустой пул → None; один обмен; два обмена (проверить средневзвешенность); дата раньше первого обмена → None.

### Задача 3 — Обновление SQLAlchemy-модели и Pydantic-схем

- `app/orders/models.py`: добавить три поля в `OrdersOrderItem`
- `app/orders/schemas.py`: добавить поля в `OrderItemCreate`, `OrderItemRead`, `OrderItemUpdate`
- Валидация в схеме: если `purchase_price_fcy` передан, `purchase_currency` обязателен

### Задача 4 — Обновление MCP-tool `create_order`

- Принимать опциональные поля `purchase_price_fcy`, `supplier_discount_fcy`, `purchase_currency` в каждой позиции заказа
- Валидировать: если передана цена в валюте — валюта обязательна
- Отображать в сводке подтверждения: «Товар: X, Цена: $64.90 (скидка: −$6.49, итого: $58.41), курс пула: 90.48, себестоимость: ~5284 ₽»

---

## Что проверить вручную

- [ ] Миграция применяется без ошибок: `alembic upgrade head`
- [ ] Существующие позиции заказов не затронуты: `SELECT id, purchase_price_fcy FROM orders_order_item LIMIT 10` → все NULL
- [ ] CHECK constraint срабатывает: попытка вставить `purchase_price_fcy = 64.90` без `purchase_currency` → ошибка
- [ ] CHECK constraint срабатывает: попытка вставить `supplier_discount_fcy = 70.00` при `purchase_price_fcy = 64.90` → ошибка
- [ ] `get_effective_rate('USD', now())` возвращает корректное значение при наличии обменов в `finance_exchange_operation`
- [ ] Заказ З-1580 (Воропаев) обновлён вручную через psql: `purchase_price_fcy = 64.90`, `supplier_discount_fcy = 6.49`, `purchase_currency = 'USD'`, `unit_price` пересчитан в рублях

---

## Открытые вопросы

- **Q (для Архитектурного штаба):** `orders_order_item` сейчас не имеет поля `origin_shipping_fcy` (доставка до форвардера). В кейсе Воропаева это $12.90. По ADR-004 это нога `origin_shipping` в `pricing_price_calculation.breakdown` — но если `price_calculation_id = NULL` (заказ создан вручную без расчёта), эта нога нигде не хранится. Нужно ли добавить `origin_shipping_fcy` на `orders_order_item` или решать через обязательный `price_calculation`?

- **Q (для Архитектурного штаба):** Кросс-валютные операции (USD → EUR или JPY-закупки при пуле в USD) — вне scope данного ADR. При появлении таких случаев потребуется отдельный ADR на конвертацию между пулами.
