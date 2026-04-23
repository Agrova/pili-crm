# ADR-008: Инвариант «одна цена на SKU на складе» и механизм разрешения ценовых конфликтов

**Статус:** принят
**Дата:** 2026-04-22
**Связанные ADR:** ADR-003 final-ready (warehouse), ADR-004 (pricing policy), ADR-006 (триггеры в PostgreSQL), ADR-007 (stock price привязана к pricing_price_calculation)
**Связанные документы:** `docs/cowork-system-prompt.md` (будет расширен в Пакете 3)

---

## Контекст

ADR-007 ввёл принципиальное решение: `warehouse_stock_item` получил связь с `pricing_price_calculation` через поле `price_calculation_id`. При поступлении товара на склад создаётся расчёт продажной цены, и эта цена фиксируется за единицами на складе — продажа со склада идёт по **зафиксированной** цене, без пересчёта.

Это правильно для первого поступления. Но возникает нетривиальный вопрос: **что происходит при повторном поступлении товара, который уже есть на складе?**

Курс за время между партиями изменился (часто значительно — ₽/$ в проекте колеблется), либо поставщик изменил цену. Расчёт по новой партии даёт другую продажную цену, чем та, что зафиксирована у существующих единиц на складе.

Возможны три позиции:

1. **Молчаливая перезапись** — новая цена заменяет старую. Последствие: старая партия «переоценивается» задним числом, может быть продана дороже или дешевле, чем планировалось при её закупке.
2. **Молчаливое оставление старой** — новая партия присоединяется к старой цене. Последствие: если курс вырос, магазин продаёт новую партию с нулевой или отрицательной маржой.
3. **Явное разрешение оператором** — при расхождении цен система блокируется, оператор принимает осознанное решение.

Решение оператора (Роман) — **позиция 3**. Сформулированное правило: **«цена на SKU на складе должна быть одна»**. Это не процедурное предпочтение, а **инвариант данных**.

Следствие: любое поступление, создающее потенциально вторую цену, — это **конфликт**, который не может быть разрешён автоматически. До разрешения новая партия не попадает в активный складской остаток.

ADR нужен сейчас, потому что:

- ADR-007 ввёл связь stock ↔ pricing_calculation, но не зафиксировал, что делать при конфликте.
- Реализация Пакета 2 ADR-007 (hook при поступлении) упирается в этот вопрос.
- Без формального решения Claude Code реализует поведение по одной из молчаливых моделей — и нарушит инвариант ещё до того, как он будет явно зафиксирован.

---

## Варианты

### Вариант 1. Молчаливая перезапись цены

При каждом поступлении `stock_item.price_calculation_id` перезаписывается новым расчётом, `quantity` суммируется.

- **Плюсы:** простая реализация, нет промежуточных состояний, модель всегда «одна цена на SKU».
- **Минусы:** нарушает бизнес-логику — старая партия, закупленная по выгодному курсу, переоценивается задним числом; оператор теряет контроль над ценообразованием, неочевидные изменения прибыли по ранее заказанным позициям.

### Вариант 2. Молчаливое сохранение старой цены

При конфликте — новая партия входит в stock_item с существующей ценой, расчёт новой цены игнорируется.

- **Плюсы:** тоже просто, прогнозируемо.
- **Минусы:** при росте курса/цен — нулевая или отрицательная маржа на новой партии; оператор не узнаёт, что товар теперь убыточен, до фактической продажи.

### Вариант 3. Stock lots (раздельные партии с разными ценами)

Снять ограничение «одна цена на SKU» на уровне данных; вести несколько `stock_item`-ов (или отдельную таблицу `stock_lot`) для одного товара с разными ценами.

- **Плюсы:** точный учёт себестоимости, возможность FIFO/LIFO.
- **Минусы:** значительное усложнение схемы; требует политики списания; не вписывается в текущий объём (десятки товаров, простые операции); нарушает заявленный инвариант «одна цена на SKU».

### Вариант 4. Явное разрешение оператором (pending queue + resolve tool)

Конфликт фиксируется как отложенное решение. Новая партия **не входит** в активный складской остаток, пока оператор не выберет способ разрешения. Оператор имеет три варианта: оставить старую цену, применить новую, усреднить по количеству.

- **Плюсы:** сохраняет инвариант, оператор контролирует ценообразование, полный аудит каждого решения, риск «молчаливой ошибки» исключён.
- **Минусы:** новая таблица, новые MCP-tools; требуется дисциплина оператора — забытые конфликты означают «товар на складе, но не виден»; усложнение системного промта Cowork.

---

## Критерии выбора

- Сохранение инварианта «одна цена на SKU на складе».
- Сохранение осознанного контроля оператора над ценообразованием.
- Прозрачность и аудит — по каждой цене stock_item должно быть видно, откуда она взялась.
- Совместимость с ADR-004 (формульный расчёт, никаких «ручных цен»).
- Совместимость с ADR-007 (связь stock_item.price_calculation_id).
- Простота реализации для MVP.
- Разделимость с будущим stock lots (ADR-008 не закрывает путь к лотам в будущем).

---

## Принятое решение

Принят **Вариант 4** со следующими параметрами.

### 1. Инвариант

Для каждой пары `(catalog_product.id, warehouse_stock_item.location)` существует **ровно одна активная продажная цена**, зафиксированная через `warehouse_stock_item.price_calculation_id`. Появление второй цены (через новое поступление с расчётной ценой, отличающейся от существующей) — **конфликт**, требующий явного разрешения оператором.

### 2. Поведение при поступлении

При создании `warehouse_receipt_item` в ходе Пакета 2 реализации ADR-007 запускается hook, действующий по следующему алгоритму:

```
1. Найти procurement_purchase_item через цепочку:
   receipt_item.receipt_id → receipt.shipment_id → shipment.purchase_id
2. Рассчитать новую цену через pricing.service.calculate_price(...)
   с параметрами: unit_cost из purchase_item, currency из purchase,
   текущий курс, дефолтная маржа.
3. Найти stock_item для (product_id, location):

   IF stock_item не существует:
     → Создать stock_item с price_calculation_id = new_calculation.
     → quantity = receipt_item.quantity.
     → receipt_item_id = receipt_item.id.
     → Записать catalog_listing_price (source='purchase', см. ADR-007).

   ELIF stock_item существует AND |new_price - stock_price| <= rounding_step:
     → Объединить молча:
       quantity += receipt_item.quantity,
       receipt_item_id = receipt_item.id (последний).
     → price_calculation_id НЕ меняется (оставляем старый).
     → Записать catalog_listing_price (source='purchase').

   ELSE (конфликт):
     → НЕ обновлять stock_item (ни quantity, ни price_calculation_id).
     → Создать warehouse_pending_price_resolution со ссылками на:
       receipt_item_id, existing_stock_item_id, new_price_calculation_id.
     → Записать catalog_listing_price (source='purchase') — каталожная
       история цен независима от stock-решения и заполняется всегда.
```

**`rounding_step`** — шаг округления из ADR-004 для этого товара (10 RUB для цен <1000, 100 RUB для цен ≥1000). Разница в пределах шага считается «совпадением» — это защита от ложных срабатываний при копеечных колебаниях курса.

### 3. Физика склада до разрешения конфликта

До разрешения:

- `warehouse_receipt_item` существует — физический факт прихода зафиксирован.
- `warehouse_stock_item.quantity` **не увеличен** — новая партия не считается «доступной к продаже».
- `warehouse_pending_price_resolution` существует — конфликт в очереди.
- `catalog_listing_price` записан — каталожная история не блокируется складским конфликтом.

**Следствие для `search_products`:** товар показывается с текущим `stock_item.quantity` (без учёта не-разрешённой партии). Системный промт Cowork обязывает оператора перед ответом клиенту о наличии проверить `pending_price_resolutions` (раздел 4 ниже).

### 4. Новая таблица `warehouse_pending_price_resolution`

| Поле | Тип | NULL | Назначение |
|---|---|---|---|
| id | BIGSERIAL | PK | |
| receipt_item_id | BIGINT | NOT NULL | FK → warehouse_receipt_item.id ON DELETE CASCADE, UNIQUE |
| existing_stock_item_id | BIGINT | NOT NULL | FK → warehouse_stock_item.id ON DELETE RESTRICT |
| new_price_calculation_id | BIGINT | NOT NULL | FK → pricing_price_calculation.id ON DELETE RESTRICT |
| created_at | TIMESTAMPTZ | NOT NULL | DEFAULT now() |

**Constraints:**

- UNIQUE `receipt_item_id` — один receipt_item = один конфликт (дубли невозможны по определению).
- INDEX `existing_stock_item_id` — для обратного поиска «какие конфликты ждут этот stock_item».

**Размещение:** модуль `warehouse` (`app/warehouse/models.py`), класс `WarehousePendingPriceResolution`.

**Lifecycle:**

- Создаётся hook-ом при конфликте.
- Удаляется MCP-tool-ом `resolve_price_resolution` после применения выбора оператора.
- При физическом удалении `warehouse_receipt_item` — каскадно удаляется (но это сценарий починки данных, не штатный).

**Замечание про new_price_calculation_id:** это уже созданный `pricing_price_calculation`, не трогается и не удаляется при разрешении конфликта. Если оператор выбирает `use_new` — этот calculation привязывается к stock_item. Если `keep_old` или `weighted_average` — calculation остаётся в БД, не удаляется (он часть истории расчётов), просто не используется для stock. Это соответствует immutability pricing_price_calculation из ADR-003.

### 5. Три способа разрешения конфликта

MCP-tool `resolve_price_resolution(receipt_item_id, choice)` принимает один из трёх вариантов выбора:

**5.1. `keep_old`**

- Существующий `stock_item.price_calculation_id` — остаётся.
- `stock_item.quantity += receipt_item.quantity`.
- `stock_item.receipt_item_id = receipt_item.id` (последний приход).
- `warehouse_pending_price_resolution` удаляется.
- `new_price_calculation_id` не используется, остаётся в БД как часть истории.

**5.2. `use_new`**

- `stock_item.price_calculation_id = new_price_calculation_id` (из pending).
- `stock_item.quantity += receipt_item.quantity`.
- `stock_item.receipt_item_id = receipt_item.id`.
- `warehouse_pending_price_resolution` удаляется.
- Старый `price_calculation_id` остаётся в БД как часть истории.

**5.3. `weighted_average`**

Средневзвешенная по количеству цена всего остатка:

```
weighted_price = (
  existing_stock.quantity * existing_price +
  receipt_item.quantity   * new_price
) / (existing_stock.quantity + receipt_item.quantity)
```

- Создаётся **новый** `pricing_price_calculation` с:
  - `final_price = weighted_price`,
  - `input_params = {"method": "weighted_average", "existing_stock_item_id": ..., "receipt_item_id": ..., "existing_price": ..., "new_price": ..., "existing_quantity": ..., "new_quantity": ...}`,
  - `breakdown = {"method": "weighted_average", "weighted_price": ...}`,
  - `formula_version = "adr-008-weighted-v1"`,
  - `purchase_type` — **наследуется** от existing_stock.price_calculation.purchase_type.
- `stock_item.price_calculation_id` = id нового расчёта.
- `stock_item.quantity += receipt_item.quantity`.
- `stock_item.receipt_item_id = receipt_item.id`.
- `warehouse_pending_price_resolution` удаляется.
- Старый и new_price_calculation_id остаются в БД как история.

**Замечание к formula_version:** `weighted_average` — **не** формула ADR-004 (retail/manufacturer), поэтому использует отдельный тег версии. Это фиксирует, что расчёт создан через усреднение, а не через стандартный pipeline margin → discount → rounding.

### 6. MCP-tool: `list_pending_price_resolutions`

Возвращает все открытые конфликты с предрасчитанными цифрами для каждого из трёх сценариев. Это **не просто выборка из таблицы**, а обогащённый вывод:

**Вход:** без параметров.

**Выход:** массив объектов, где каждый объект — один конфликт:

```
{
  "receipt_item_id": 42,
  "product": {"id": 17, "name": "Veritas Shooting Board"},
  "source": {"id": 3, "name": "Lee Valley"},
  "existing_stock": {
    "quantity": 3,
    "unit_cost_rub": 10400,
    "unit_price_rub": 12500
  },
  "new_receipt": {
    "quantity": 2,
    "unit_cost_rub": 11500,
    "unit_price_rub": 13800
  },
  "scenarios": {
    "keep_old": {
      "final_unit_price": 12500,
      "total_quantity": 5,
      "total_revenue": 62500,
      "total_cost": 54200,
      "profit_rub": 8300,
      "margin_percent": 15.3
    },
    "use_new": {
      "final_unit_price": 13800,
      "total_quantity": 5,
      "total_revenue": 69000,
      "total_cost": 54200,
      "profit_rub": 14800,
      "margin_percent": 27.3
    },
    "weighted_average": {
      "final_unit_price": 13020,
      "total_quantity": 5,
      "total_revenue": 65100,
      "total_cost": 54200,
      "profit_rub": 10900,
      "margin_percent": 20.1
    }
  }
}
```

**Откуда берутся цифры:**

- `existing_stock.unit_cost_rub` — из `existing_stock.price_calculation.input_params` (гарантируется ADR-004: `base_cost_rub` или `purchase_cost_rub` всегда в input_params).
- `existing_stock.unit_price_rub` — из `existing_stock.price_calculation.final_price`.
- `new_receipt.unit_cost_rub` — из `new_price_calculation.input_params` (тот же источник).
- `new_receipt.unit_price_rub` — из `new_price_calculation.final_price`.
- `total_cost` — `(existing.qty * existing.cost) + (new.qty * new.cost)`, валюта RUB (ADR-004).
- `profit_rub` — `total_revenue - total_cost`.
- `margin_percent` — `(profit_rub / total_cost) * 100` (маржинальность, как в ADR-004), округляется до 0.1%.

**Валюта — рубли.** По ADR-004, прибыль считается в рублях (конвертированные costs, рублёвая выручка).

### 7. MCP-tool: `resolve_price_resolution`

**Вход:**
- `receipt_item_id: int` — идентификатор из pending.
- `choice: str` — один из `'keep_old'`, `'use_new'`, `'weighted_average'`.

**Выход:**
- Новое состояние `stock_item`: `{stock_item_id, product_id, quantity, price_calculation_id, final_price, margin_percent}`.

**Поведение при ошибках:**
- Если `receipt_item_id` не найден в `warehouse_pending_price_resolution` → ошибка `"resolution not pending or already resolved"`.
- Если `choice` не из трёх допустимых → ошибка валидации.
- Операция атомарна: обновление stock_item + удаление pending-записи + (для weighted_average) создание нового pricing_price_calculation — в одной транзакции.

**Подтверждение оператора:** этот tool попадает под правило двух подтверждений из системного промта Cowork (изменение state, влияет на ценообразование).

### 8. Видимость конфликтов в других tools

Чтобы оператор не «забывал» конфликты, другие существующие tools получают сигнал:

- **`pending_orders`** — в выводе добавляется метаблок `pending_actions: {"price_resolutions_count": N}` если N > 0. Оператор видит цифру, может вызвать `list_pending_price_resolutions`.
- **`search_products`** — если у найденного товара есть pending-конфликт, в выводе появляется явная пометка: `"Внимание: pending price resolution на этот товар (receipt_item_id=N)"`.

Это **мягкий** механизм — он не блокирует работу, а напоминает.

### 9. Системный промт Cowork — обновления

В Пакете 3 (реализация tools) системный промт Cowork расширяется:

- **Раздел 3 (правило двух подтверждений):** добавляется `resolve_price_resolution` (любое значение `choice`).
- **Раздел 7 (работа с MCP-tools):** новый подраздел «Разрешение ценовых конфликтов». Правила:
  - Оператор перед ответом клиенту о наличии товара проверяет `list_pending_price_resolutions`.
  - Если товар в списке — Cowork сообщает клиенту «товар есть, но требуется уточнение цены, перезвоним», фиксирует запрос в заметке, оператор разрешает конфликт, только потом коммуницирует клиенту финальную цену.
- **Раздел 8 (аудит MCP):** если оператор раз за разом выбирает один и тот же `choice` (например, всегда `weighted_average`) — это потенциальный сигнал для `IMPROVEMENTS.md` (возможно, нужен дефолтный выбор или ассистент-рекомендация).

---

## Последствия

### Что становится проще

- Инвариант «одна цена на SKU» жёстко выдержан — никогда две цены в stock_item.
- Оператор никогда не продаёт «переоценённый задним числом» товар — старая партия сохраняет свою цену, если он не принял явное решение иначе.
- Аудит работает полностью — каждая цена в stock_item выводима через `price_calculation_id` на конкретное решение (первое поступление / разрешение конфликта типа N).
- `weighted_average` формализует одну из трёх реальных эвристик оператора.
- Путь к stock lots в будущем не закрыт: `warehouse_pending_price_resolution` — отдельная сущность, её можно эволюционировать, основная схема не связана с этим механизмом.

### Какие ограничения появляются

- Новая таблица и два новых MCP-tools.
- Усложнение workflow оператора при поступлениях — теперь не только приёмка, но и (иногда) разрешение.
- Risk of forgotten resolutions — товар физически на складе, но невидим как наличный. Mitigation: видимость в `pending_orders` и `search_products`.
- `weighted_average` создаёт `pricing_price_calculation` с нестандартным `formula_version` — требует отдельного трактования в profit-аналитике (если будут сводки по маржинальности, нужно различать расчёты по ADR-004 и усреднённые).
- Системный промт Cowork усложняется — оператор должен знать три сценария разрешения и когда какой применять.

### Что придётся учитывать дальше

- При появлении первого реального кейса, когда оператор хочет **другую** цену, не из трёх (например, «цена по новой партии плюс наценка за срочность») — возвращаемся к вопросу: добавлять `recalculate` или переходить к stock lots.
- При росте частоты конфликтов — пересматриваем порог совпадения (сейчас `rounding_step`, возможно потребуется процентный порог, например «5% разницы — не конфликт»).
- При появлении policy «не выбирать одну цену, а учитывать лоты» — это уже новый ADR (stock lots).
- Когда `pending_orders` и `search_products` начинают часто мусорить предупреждениями о конфликтах — это сигнал, что Роман получает много поступлений между разрешениями. Возможно, нужен SLA «разрешить конфликт в течение N часов с момента поступления» — но это политика, не архитектура.

---

## Что должен сделать Claude Code

Передаётся в Prompt Factory for Claude Code отдельными промтами.

**Пакет 2b (схема + hook, совместно с Пакетом 2a по procurement):**

1. Миграция Alembic: создать таблицу `warehouse_pending_price_resolution` с FK, индексами, UNIQUE.
2. SQLAlchemy-модель `WarehousePendingPriceResolution` в `app/warehouse/models.py`.
3. Hook при создании `warehouse_receipt_item` (в `app/warehouse/services.py`): реализация алгоритма из раздела 2 принятого решения.
4. Функция `calculate_weighted_price(existing_stock_item, new_price_calculation, new_quantity)` в `app/pricing/service.py` — возвращает Decimal, детерминированная, pure, tested.
5. Создание `pricing_price_calculation` с `formula_version = "adr-008-weighted-v1"` при выборе `weighted_average` — отдельная функция в `pricing/service.py`.
6. Тесты (`tests/warehouse/test_receipt_hook.py`):
   - первое поступление → создаётся stock_item, без pending;
   - поступление с совпадающей ценой (в пределах rounding_step) → quantity суммируется, pending не создаётся;
   - поступление с конфликтной ценой → quantity не меняется, pending создаётся;
   - каскадное удаление: DELETE receipt_item удаляет pending;
   - UNIQUE receipt_item_id: повторное создание pending на тот же receipt_item блокируется.
7. Тесты `tests/pricing/test_weighted_average.py`:
   - базовый расчёт средневзвешенной;
   - edge case: одинаковые цены (результат = любой из исходных);
   - edge case: нулевое существующее количество — должен быть невозможен по инварианту, но тест должен падать корректно.

**Пакет 3 (MCP-tools, в рамках общего Пакета 3 ADR-007):**

8. MCP-tool `list_pending_price_resolutions()` с обогащённым выводом по разделу 6.
9. MCP-tool `resolve_price_resolution(receipt_item_id, choice)` по разделу 7.
10. Обновление `pending_orders`, `search_products` для показа meta-блока с количеством конфликтов.
11. Обновление системного промта Cowork по разделу 9.

---

## Что проверить вручную

- [ ] Миграция `warehouse_pending_price_resolution` применяется на чистой БД и с seed MVP.
- [ ] Downgrade корректно удаляет таблицу.
- [ ] Первое поступление товара X (новый товар, не было на складе) → `stock_item` создан, `pending_price_resolution` отсутствует.
- [ ] Второе поступление с **совпадающей** ценой → `stock_item.quantity` увеличился, `pending_price_resolution` пуст.
- [ ] Второе поступление с **конфликтной** ценой → `stock_item.quantity` не изменился, запись в `pending_price_resolution` создана, `catalog_listing_price` записан.
- [ ] `list_pending_price_resolutions()` возвращает три сценария с корректными числами (верифицируется на конкретных данных).
- [ ] `resolve_price_resolution(id, 'keep_old')` → `stock_item` обновлён, pending удалён.
- [ ] `resolve_price_resolution(id, 'use_new')` → `stock_item.price_calculation_id` изменился, pending удалён.
- [ ] `resolve_price_resolution(id, 'weighted_average')` → создан новый `pricing_price_calculation`, привязан к stock, pending удалён.
- [ ] Удаление `receipt_item` (CASCADE) удаляет pending автоматически.
- [ ] Попытка создать вторую pending-запись на тот же `receipt_item` — блокируется UNIQUE constraint.
- [ ] `search_products` показывает пометку о pending-конфликте по затронутому товару.
- [ ] `pending_orders` показывает `price_resolutions_count` > 0 при наличии неразрешённых.

---

## Открытые вопросы (передаются в `06_open_questions.md`)

- **Q (архитектурный чат, будущий ADR):** при частом выборе оператором одного и того же варианта — добавить ли дефолт или ассистент-рекомендацию в `resolve_price_resolution`. Критерий — по IMPROVEMENTS.md / статистике выборов.
- **Q (архитектурный чат, будущий ADR):** порог совпадения цен сейчас = `rounding_step`. При росте частоты конфликтов — пересмотреть, возможно на процентный порог (5% разницы не считать конфликтом).
- **Q (архитектурный чат, будущий ADR на stock lots):** первый реальный случай, когда три варианта разрешения не покрывают потребность → триггер для ADR на stock lots.
- **Q (Prompt Factory):** место размещения `calculate_weighted_price` — в `app/pricing/service.py` или в `app/warehouse/services.py`. Рекомендация — pricing (это расчёт цены), но окончательно определяется при реализации.
- **Q (системный промт Cowork, Пакет 3):** формулировки правил работы оператора при pending-конфликтах, обновление раздела 3 и 7 промта.
