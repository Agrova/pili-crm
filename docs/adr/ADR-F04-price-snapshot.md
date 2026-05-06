# ADR-F04: Snapshot цен при сохранении заказа

**Статус:** принят
**Дата:** 2026-04-30
**Связанные ADR:** ADR-003 final-ready (core schema), ADR-004 (pricing & profit policy), ADR-F06 (currency storage)
**Связанные документы:** `docs/PLAN.md` (G1 → G6), `01_scope.md`

---

## Контекст

ADR-004 ввёл `pricing_price_calculation` — immutable snapshot формулы расчёта (`input_params`, `breakdown`, `final_price`, `margin_percent`, `discount_percent` и т. д.) и связал его с `orders_order_item.price_calculation_id`. Также ADR-004 зафиксировал, что `unit_price` — это «approved sale price», цена клиенту, и описал три сценария:

1. цена = расчётной (`unit_price == price_calculation.final_price`, `operator_adjusted = false`);
2. цена изменена оператором (`unit_price ≠ final_price`, `operator_adjusted = true`);
3. цена без расчёта (`price_calculation_id = NULL`, `unit_price` задан вручную).

Между этими формулировками и реальным состоянием схемы есть разрывы, которые нужно закрыть **до** реализации finance ledger (G6), потому что любая ретроспективная аналитика прибыли (Actual Profit, отчёты по марже, аудит сделок) опирается на «то, что было видно оператору в момент подтверждения заказа». Если этот срез не зафиксирован детерминированно, рефакторинг позже потребует миграции исторических данных и реконструкции отсутствующих фактов.

Конкретные проблемы:

**Проблема 1 — `price_calculation_id` может «обнулиться».**
Текущий FK объявлен `ON DELETE SET NULL`. Если по какой-либо причине `pricing_price_calculation` будет удалён или подменён, позиция заказа потеряет связь со своим расчётом — `unit_price` останется, но breakdown, маржа, курс, скидка и формула, по которой получена цена, перестанут быть восстановимы. Это противоречит самой идее immutable snapshot из ADR-004.

**Проблема 2 — третий сценарий (`price_calculation_id = NULL`) безмолвный.**
В текущем `create_order` MCP-tool оператор просто передаёт `price` — расчёт не создаётся, в order_item записывается только `unit_price`, `price_calculation_id` остаётся NULL. На практике это означает, что **большинство существующих заказов не имеют snapshot цены вообще**. ADR-004 формально это допустил «для исключительных случаев», но правило не конкретизировало, какой минимум данных всё равно фиксируется и где он живёт.

**Проблема 3 — `pricing_price_calculation.breakdown` может быть «забыт» как структура.**
JSONB иммутабельность в БД — это инвариант на уровне политики, не на уровне схемы. Сейчас нет ни триггера, ни constraint, защищающих snapshot от UPDATE. ADR-004 описал immutability в тексте, но не в коде. Для финансового контура этого недостаточно: snapshot должен быть тем, на что ledger опирается без ревизии задним числом.

**Проблема 4 — момент создания snapshot не зафиксирован.**
ADR-004 не отвечает на вопрос «когда именно создаётся `pricing_price_calculation`»: при первом расчёте в Cowork (до подтверждения заказа), при создании `orders_order_item`, при переходе `draft → confirmed` или при первом финансовом событии? От этого зависит, какой контекст (курс, скидки, политика) попадает в snapshot.

Что **не обсуждается** в этом ADR: формула расчёта (ADR-004); хранение валюты закупки и фактического курса (ADR-F06); метод налогового учёта (ADR-F02); расположение формул DB vs Python (ADR-F05).

---

## Варианты

### Развилка A — что именно сохраняется в snapshot

#### Вариант A1 — только `final_price` (тонкий snapshot)

Хранить в `orders_order_item` финальную цену и больше ничего. `price_calculation_id` опционален.

- **Плюсы:** минимум полей; быстрая запись.
- **Минусы:** breakdown теряется; Actual Profit невычислим (нечего сравнивать); margin / discount / курс — недоступны для ретроспективы; ADR-004 фактически обнуляется.

#### Вариант A2 — весь `pricing_price_calculation` (полный объект) ✅

Snapshot — это вся запись `pricing_price_calculation` с `input_params`, `breakdown`, `final_price`, `formula_version`, `pricing_rate_id` и применённой марж/скидкой. `orders_order_item.price_calculation_id` обязателен (с одним явно заявленным исключением — см. ниже). На уровне БД snapshot защищён от изменений.

- **Плюсы:** полный audit trail; Actual Profit вычислим; margin / discount / курс — все доступны через JOIN; ADR-004 выполняется в исходной идее.
- **Минусы:** обязательное создание `pricing_price_calculation` на каждой позиции (включая «короткие» сделки, где формула не запускалась — для них формируется минимальный manual-snapshot, см. ниже).

#### Вариант A3 — гибрид: денормализованная копия ключевых полей в `orders_order_item`

Дублировать `final_price`, `margin_percent`, `discount_percent`, `pricing_exchange_rate`, `breakdown_summary` на сам `orders_order_item` плюс хранить `price_calculation_id`.

- **Плюсы:** быстрые отчёты без JOIN.
- **Минусы:** дублирование = риск рассинхронизации; усложняет инвариант immutable; ADR-001 v2 запрещает «срезать углы» подобными денормализациями без сильной мотивации; для текущего масштаба (десятки–сотни заказов) JOIN не проблема.

### Развилка B — момент создания snapshot

#### Вариант B1 — при первом расчёте в Cowork, до сохранения заказа

Cowork запускает `pricing.calculate_price` → создаётся `pricing_price_calculation` → оператор обсуждает цену с клиентом → при подтверждении заказа `orders_order_item.price_calculation_id` ссылается на уже созданный расчёт.

- **Плюсы:** сохраняется именно тот snapshot, который видел оператор; формула версионирована (`formula_version`), курс зафиксирован моментом расчёта.
- **Минусы:** в БД появляются «висящие» расчёты, не привязанные ни к какому заказу (если оператор передумал) — но это ожидаемое поведение, не баг (audit trail отказов от продажи).

#### Вариант B2 — при создании `orders_order_item` (sync) ✅

Snapshot создаётся в той же транзакции, что и `orders_order_item`. Если запись пришла из Cowork с уже готовым `price_calculation_id` — он используется. Если оператор задал цену вручную (третий сценарий ADR-004) — создаётся **manual-snapshot**: запись `pricing_price_calculation` со специальной отметкой `formula_version = 'manual'`, `purchase_type = NULL`, `breakdown = {"source": "manual", "operator_price": <unit_price>}`.

Объединяет B1 для нормального flow (расчёт уже есть) и закрывает Проблему 2 для ручных цен — у каждой позиции есть snapshot, даже у ручных.

- **Плюсы:** инвариант «у каждой позиции есть snapshot» становится универсальным; нет особого case в схеме; Actual Profit имеет хотя бы `unit_price` для сравнения.
- **Минусы:** требует расширения enum/конвенции `formula_version`, чтобы различать «формульный» и «ручной» snapshot.

#### Вариант B3 — при переходе заказа в статус `confirmed`

Пока заказ в `draft` — snapshot не создаётся. При подтверждении — фиксируем.

- **Плюсы:** нет «висящих» snapshot для отменённых черновиков.
- **Минусы:** между `draft` и `confirmed` может пройти время — курс изменится, прайс пересчитается; зафиксируется не «то, что видел оператор», а «то, что нашлось в момент confirm». Кейс Воропаева (заказ З-1580) — типичный: `draft` создан, цена согласована с клиентом, через час курс обвалился, при `confirmed` snapshot был бы уже другой.

### Развилка C — где хранится snapshot

#### Вариант C1 — отдельная колонка/JSONB в `orders_order_item`

Денормализовать snapshot прямо в order_item.

- **Плюсы:** один JOIN меньше.
- **Минусы:** дублирование с `pricing_price_calculation`; усложнение immutable-инварианта; нарушение разделения модулей `orders` и `pricing` (ADR-001 v2).

#### Вариант C2 — существующая `pricing_price_calculation` через FK ✅

Никакой новой таблицы. `orders_order_item.price_calculation_id` обязателен (NOT NULL после миграции существующих заказов через бэкфилл manual-snapshot). FK переводится с `ON DELETE SET NULL` на `ON DELETE RESTRICT`.

- **Плюсы:** одна таблица — один источник правды; модуль `pricing` владеет snapshot, модуль `orders` ссылается; ADR-001 v2 (модульность) сохраняется.
- **Минусы:** требует бэкфилла для исторических заказов; FK становится строже.

#### Вариант C3 — новая таблица `orders_price_snapshots`

Отдельная таблица, копирующая ключевые поля из `pricing_price_calculation` в момент сохранения заказа.

- **Плюсы:** изоляция от изменений в схеме `pricing`.
- **Минусы:** дублирование таблиц; синхронизация двух источников правды; для одного оператора и текущего масштаба — оверинжиниринг; усложняет реализацию G6.

### Развилка D — иммутабельность snapshot

#### Вариант D1 — иммутабельность только в коде (текущее состояние)

Никаких триггеров; правило «не редактируем JSONB» — соглашение в коде.

- **Минусы:** инвариант не enforced; в БД можно сделать UPDATE через psql; для финансового контура это слабая гарантия.

#### Вариант D2 — PostgreSQL trigger BEFORE UPDATE / DELETE ✅

Триггер на `pricing_price_calculation`: запрещает UPDATE по полям `breakdown`, `final_price`, `input_params`, `purchase_type`, `pre_round_price`, `rounding_step`, `margin_percent`, `discount_percent`, `formula_version`, `pricing_rate_id`. Допускаются только апдейты `customer_id` (для backfill) и `updated_at` (TimestampMixin). DELETE запрещён, если на запись есть FK из `orders_order_item`.

- **Плюсы:** инвариант enforced на уровне БД; защита от ручных правок через psql; согласованность с ADR-006 (derive-status тоже на triggers).
- **Минусы:** немного «хитрой логики» в БД; нужно явно разрешать backfill при миграции (`SET LOCAL` или временное отключение триггера).

#### Вариант D3 — отдельный режим «archive» в схеме (event sourcing)

Каждое изменение пишется новой записью; «текущая» — last по `valid_from`.

- **Плюсы:** полная история изменений.
- **Минусы:** snapshot принципиально неизменяемый — изменений быть не должно; event sourcing бесполезен; усложнение без выгоды.

---

## Критерии выбора

- **Надёжность.** A2 + C2 + D2 дают полный, защищённый snapshot. A1/A3 теряют данные или открывают рассинхронизацию. D1 не защищает от ручных правок — для финансового контура неприемлемо.
- **Простота поддержки.** C2 и D2 не требуют новых таблиц и используют существующие механизмы (PG triggers — уже применяются в ADR-006). Бэкфилл — одноразовая работа в G6.
- **Совместимость с ADR-004.** A2 — буквальная реализация исходного замысла ADR-004 («`pricing_price_calculation` как immutable snapshot»). B2 закрывает дыру третьего сценария ADR-004 без изменения формулы. C2 — продолжение существующего FK. D2 enforced инвариант, который ADR-004 описал текстом.
- **Совместимость с ADR-F06.** ADR-F06 хранит `purchase_price_fcy / supplier_discount_fcy / purchase_currency` на `orders_order_item` для **факта закупки** (Actual). Эти поля **не входят** в snapshot цены — они описывают другую сторону сделки (затраты, не выручку). ADR-F04 фиксирует **plan** (что обещано клиенту); ADR-F06 фиксирует **actual** (что реально потрачено). Конфликта нет — поля ортогональны.
- **Совместимость с G6.** Finance ledger в G6 опирается на `pricing_price_calculation` для Planned Profit и на ADR-F06 поля + `get_effective_rate` для Actual. Если snapshot не enforced — G6 рискует строиться на «иногда есть, иногда нет» — это блокер.
- **Операционная реалистичность.** B2 (snapshot в момент создания order_item) совпадает с реальным флоу Cowork: оператор сначала использует pricing, потом подтверждает заказ. B3 (на confirm) теряет контекст; B1 (только до сохранения заказа) — то же самое, что B2, но без обработки ручных цен.
- **Масштабируемость.** B2 + C2 + D2 не требуют ничего особого до сотен тысяч заказов; на текущем объёме (62 заказа prod) — overhead нулевой.

---

## Принятое решение

Принимаются **A2 + B2 + C2 + D2**. Сводно:

### Часть 1 — Что сохраняется (A2)

Snapshot цены — это полная запись в существующей таблице `pricing_price_calculation` со всеми её полями (`input_params`, `breakdown`, `final_price`, `formula_version`, `purchase_type`, `pre_round_price`, `rounding_step`, `margin_percent`, `discount_percent`, `customer_id`). Никакой денормализации в `orders_order_item` не делается.

Фактическая цена клиенту хранится в `orders_order_item.unit_price` (как в ADR-004). Связь со snapshot — `orders_order_item.price_calculation_id`. Расхождение между `unit_price` и `final_price` остаётся валидным (operator_adjusted = true).

### Часть 2 — Когда создаётся (B2)

Snapshot создаётся **в той же транзакции, что и `orders_order_item`**. Два пути:

| Путь | Источник | Что попадает в `pricing_price_calculation` |
|---|---|---|
| **Calculated** | Cowork запустил pricing-формулу до подтверждения | `formula_version = '<actual>'`, `breakdown` — полный, `purchase_type` обязателен, входные параметры — реальные. `price_calculation_id` уже есть к моменту вставки order_item. |
| **Manual** | Оператор задал цену вручную (третий сценарий ADR-004) | `formula_version = 'manual'`, `breakdown = {"source": "manual", "operator_price": <unit_price>, "note": <opt>}`, `purchase_type = NULL` (см. часть 5), `pre_round_price = unit_price`, `rounding_step = 1`, `margin_percent = 0`, `discount_percent = NULL`. Snapshot всё равно создаётся, `price_calculation_id` ссылается на него. |

В обоих случаях `orders_order_item.price_calculation_id` — обязательный. Сценария «`price_calculation_id IS NULL`» в schema больше не существует.

### Часть 3 — Где хранится (C2)

Существующая таблица `pricing_price_calculation`. Никакой новой таблицы (`orders_price_snapshots`) не создаётся.

Изменения в `orders_order_item.price_calculation_id`:

- `NOT NULL` (после бэкфилла существующих заказов в G6).
- FK переводится с `ON DELETE SET NULL` на `ON DELETE RESTRICT` — снимок нельзя удалить, пока на него ссылается позиция.

### Часть 4 — Иммутабельность (D2)

PostgreSQL `BEFORE UPDATE OR DELETE` триггер на `pricing_price_calculation`:

- **UPDATE.** Запрещён по «структурным» полям: `input_params`, `breakdown`, `final_price`, `purchase_type`, `pre_round_price`, `rounding_step`, `margin_percent`, `discount_percent`, `formula_version`, `product_id`, `currency`, `calculated_at`. Если `OLD` ≠ `NEW` хотя бы по одному из них — `RAISE EXCEPTION`.
- **UPDATE по `customer_id`** — разрешён (для одноразового backfill в G6, где historical расчёты дополняются ссылкой на клиента из `orders_order`).
- **UPDATE по `updated_at`** — разрешён (TimestampMixin, не структурное поле).
- **DELETE** — запрещён, если существует `orders_order_item.price_calculation_id = OLD.id`. PostgreSQL уже даст `RESTRICT` через FK; триггер дополнительно блокирует прямые DELETE через psql.

Триггер можно временно отключить только в одной миграции (G6, бэкфилл) с явным `SET session_replication_role = replica` в рамках Alembic-миграции. После завершения миграции — обратно. Отдельных backdoor-механизмов нет.

### Часть 5 — Поведение `manual`-snapshot

Чтобы `formula_version = 'manual'` не ломал существующие constraints, делаются точечные правки:

- `pricing_price_calculation.purchase_type` становится **nullable** (`NOT NULL` → `NULL`). CHECK-constraint добавляется: `purchase_type IS NOT NULL OR formula_version = 'manual'`.
- `pricing_price_calculation.margin_percent` остаётся `NOT NULL`, но для manual = 0.00. Существующий CHECK `margin_percent >= 0` — выполняется.
- `pricing_price_calculation.pre_round_price` — для manual равен `unit_price`. Существующий CHECK `pre_round_price >= 0` — выполняется.
- `breakdown` для manual всегда содержит как минимум `{"source": "manual", "operator_price": <number>}`. Опционально — `note` (свободная строка от оператора), `customer_context` (что вспомнил оператор о клиенте).

Конвенция `formula_version`:

- `formula_version = 'manual'` — manual snapshot.
- `formula_version = 'pricing-vN.M'` — формульный, где `N.M` соответствует семантической версии модуля `pricing` (ADR-004).
- При изменении формулы — инкремент minor; при изменении breakdown-схемы — major.

### Часть 6 — Что хранится vs что вычисляется

| Данные | Хранится | Вычисляется |
|---|---|---|
| Финальная цена клиенту | `orders_order_item.unit_price` | — |
| Snapshot формулы | `pricing_price_calculation.*` | — |
| Связь позиция ↔ snapshot | `orders_order_item.price_calculation_id` (NOT NULL) | — |
| Расхождение цены и расчёта | `orders_order_item.operator_adjusted` | — |
| Тип snapshot (calculated/manual) | `pricing_price_calculation.formula_version` ∈ `{'manual', 'pricing-vN.M'}` | — |
| Planned Profit по позиции | — | из `breakdown.base_cost_rub`, `unit_price`, скидок |
| Actual Profit | — | из ADR-F06 (`purchase_price_fcy`, `effective_rate`) |

---

## Последствия

### Что становится проще

- ADR-004 наконец имеет полную имплементационную спецификацию: «у каждой позиции есть snapshot, snapshot защищён от изменений».
- G6 (finance ledger) строится на надёжном инварианте: `JOIN orders_order_item → pricing_price_calculation` всегда возвращает строку.
- Аудит цен и маржи становится тривиальным запросом без NULL-ветвлений.
- Обработка manual-цен (которые сейчас составляют почти весь prod — 62 заказа без snapshot) приводится к единому формату.
- Триггер D2 защищает от случайных правок через psql, что особенно важно при ручном обслуживании БД оператором.

### Какие ограничения появляются

- В G6 нужно реализовать бэкфилл: для всех существующих `orders_order_item` с `price_calculation_id IS NULL` создать manual-snapshot с `breakdown = {"source": "manual", "operator_price": unit_price, "backfill": true, "backfill_date": "2026-04-30"}`. Только после бэкфилла можно ставить `NOT NULL` и менять FK на `RESTRICT`.
- MCP-tool `create_order` обязан создавать snapshot. Это +1 INSERT на каждую позицию заказа — для текущего масштаба незаметно.
- При ошибке в формуле, требующей пересчёта исторических цен, придётся создавать **новый** snapshot и менять `price_calculation_id` (это разрешено триггером D2 — он защищает поля `pricing_price_calculation`, а не FK на стороне `orders_order_item`). Это явный, аудируемый процесс — не ручной UPDATE breakdown.
- `purchase_type` становится nullable — это расширение ADR-004 (там было `NOT NULL`). Обоснование: ADR-004 не предусмотрел manual-сценарий явно, но допустил его текстом; F04 закрывает разрыв.

### Что придётся учитывать дальше

- **G6 (finance ledger).** Реализация бэкфилла + миграция FK + триггер immutability + расширение `create_order` MCP-tool. Это группа, на которую ADR-F04 разблокирован.
- **G5 (`update_order_item` MCP-tool).** Обновление цены позиции = создание нового `pricing_price_calculation` (manual или calculated) и переключение `price_calculation_id`. Старый snapshot остаётся в БД как исторический след (его нельзя удалить — на него теперь нет FK, но и удалять незачем).
- **ADR-F02 (метод налогового учёта).** Налоговая база Actual Profit опирается на snapshot — связка ADR-F02 ↔ ADR-F04.
- **Отчёты Planned vs Actual.** Опираются на breakdown JSONB. Если в будущем потребуется изменить структуру breakdown — нужен `formula_version` major-инкремент и явная обработка старых snapshot.

---

## Что должен сделать Claude Code

Реализация — часть **G6 (finance ledger)**, не текущей группы G1. Здесь зафиксирован фронт работ; разбиение на промты — ответственность Prompt Factory при запуске G6.

### Задача 1 — Миграция Alembic: расширение `pricing_price_calculation`

```sql
-- 1.1 Сделать purchase_type nullable
ALTER TABLE pricing_price_calculation
    ALTER COLUMN purchase_type DROP NOT NULL;

-- 1.2 CHECK: либо purchase_type есть, либо это manual-snapshot
ALTER TABLE pricing_price_calculation
    ADD CONSTRAINT chk_purchase_type_or_manual
    CHECK (purchase_type IS NOT NULL OR formula_version = 'manual');
```

### Задача 2 — Бэкфилл manual-snapshot для существующих позиций

```sql
-- 2.1 Создать manual-snapshot для каждой order_item с price_calculation_id IS NULL
INSERT INTO pricing_price_calculation (
    product_id, input_params, breakdown, final_price, currency,
    calculated_at, formula_version, purchase_type,
    pre_round_price, rounding_step, margin_percent, discount_percent,
    customer_id
)
SELECT
    oi.product_id,
    jsonb_build_object('source', 'manual', 'backfill', true),
    jsonb_build_object(
        'source', 'manual',
        'operator_price', oi.unit_price,
        'backfill', true,
        'backfill_date', '2026-04-30'
    ),
    COALESCE(oi.unit_price, 0),
    'RUB',
    o.created_at,
    'manual',
    NULL,
    COALESCE(oi.unit_price, 0),
    1,
    0,
    NULL,
    o.customer_id
FROM orders_order_item oi
JOIN orders_order o ON o.id = oi.order_id
WHERE oi.price_calculation_id IS NULL;

-- 2.2 Привязать backfilled snapshot к позициям
-- (см. промт реализации — нужен корреляционный UPDATE)
```

### Задача 3 — Миграция Alembic: усиление FK `orders_order_item.price_calculation_id`

```sql
-- Проверка: ни одной NULL не осталось
-- (ассерт в миграции)

ALTER TABLE orders_order_item
    ALTER COLUMN price_calculation_id SET NOT NULL;

-- Пересоздать FK с RESTRICT
ALTER TABLE orders_order_item
    DROP CONSTRAINT orders_order_item_price_calculation_id_fkey;

ALTER TABLE orders_order_item
    ADD CONSTRAINT orders_order_item_price_calculation_id_fkey
    FOREIGN KEY (price_calculation_id)
    REFERENCES pricing_price_calculation(id)
    ON DELETE RESTRICT;
```

### Задача 4 — PostgreSQL trigger immutability

```sql
CREATE OR REPLACE FUNCTION pricing_price_calculation_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (OLD.input_params       IS DISTINCT FROM NEW.input_params)
        OR (OLD.breakdown          IS DISTINCT FROM NEW.breakdown)
        OR (OLD.final_price        IS DISTINCT FROM NEW.final_price)
        OR (OLD.purchase_type      IS DISTINCT FROM NEW.purchase_type)
        OR (OLD.pre_round_price    IS DISTINCT FROM NEW.pre_round_price)
        OR (OLD.rounding_step      IS DISTINCT FROM NEW.rounding_step)
        OR (OLD.margin_percent     IS DISTINCT FROM NEW.margin_percent)
        OR (OLD.discount_percent   IS DISTINCT FROM NEW.discount_percent)
        OR (OLD.formula_version    IS DISTINCT FROM NEW.formula_version)
        OR (OLD.product_id         IS DISTINCT FROM NEW.product_id)
        OR (OLD.currency           IS DISTINCT FROM NEW.currency)
        OR (OLD.calculated_at      IS DISTINCT FROM NEW.calculated_at)
        THEN
            RAISE EXCEPTION
                'pricing_price_calculation is immutable (id=%): structural fields cannot be updated',
                OLD.id;
        END IF;
        RETURN NEW;
    END IF;
    -- DELETE — RESTRICT уже сработает через FK; если позиции нет, разрешаем
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pricing_price_calculation_immutable
    BEFORE UPDATE OR DELETE ON pricing_price_calculation
    FOR EACH ROW EXECUTE FUNCTION pricing_price_calculation_immutability();
```

Бэкфилл (Задача 2) выполняется **до** создания триггера; сам триггер не должен мешать миграции.

### Задача 5 — Обновление MCP-tool `create_order`

- Принимать `price_calculation_id` (если Cowork уже запустил pricing-формулу) или `price` + `manual_note`.
- Если `price_calculation_id` передан — использовать его, валидировать что `product_id` совпадает.
- Если передана только `price` — создавать manual-snapshot в той же транзакции и записывать его id в `orders_order_item.price_calculation_id`.
- Никогда не оставлять `price_calculation_id = NULL`.

### Задача 6 — SQLAlchemy-модель и Pydantic-схемы

- `app/orders/models.py`: убрать `nullable=True` у `price_calculation_id`; обновить `ondelete` с `SET NULL` на `RESTRICT`.
- `app/pricing/models.py`: `purchase_type` → `Mapped[PricingPurchaseType | None]`, `nullable=True`.
- `app/pricing/schemas.py`: добавить отдельную фабрику `PricingPriceCalculation.manual_snapshot(product_id, unit_price, customer_id, note=None)`.

### Задача 7 — Тесты

- Триггер immutability: попытка `UPDATE pricing_price_calculation SET breakdown = '{}'::jsonb` → `RAISE EXCEPTION`.
- Триггер immutability: разрешённый UPDATE customer_id (backfill).
- FK RESTRICT: `DELETE FROM pricing_price_calculation WHERE id = X` где X используется в order_item → отказ.
- Manual-snapshot: `create_order` с `price` без `price_calculation_id` → создаётся snapshot с `formula_version = 'manual'`.
- Calculated-snapshot: `create_order` с `price_calculation_id` → используется существующий, не создаётся новый.
- Запрос Actual Profit (минимальный): `JOIN orders_order_item → pricing_price_calculation` возвращает строки для всех позиций.

---

## Что проверить вручную

- [ ] Миграция применяется без ошибок: `alembic upgrade head`
- [ ] После бэкфилла: `SELECT COUNT(*) FROM orders_order_item WHERE price_calculation_id IS NULL` → 0
- [ ] После миграции FK: `\d orders_order_item` показывает `price_calculation_id BIGINT NOT NULL` и FK с `ON DELETE RESTRICT`
- [ ] Триггер срабатывает: `UPDATE pricing_price_calculation SET final_price = 999 WHERE id = (SELECT id FROM pricing_price_calculation LIMIT 1)` → ошибка
- [ ] Триггер не мешает разрешённому: `UPDATE pricing_price_calculation SET customer_id = X WHERE id = Y AND customer_id IS NULL` → успех
- [ ] DELETE заблокирован: попытка удалить snapshot, на который ссылается order_item, → ошибка от FK
- [ ] `create_order` с ручной ценой создаёт manual-snapshot: после успешного вызова `SELECT formula_version FROM pricing_price_calculation WHERE id = (SELECT price_calculation_id FROM orders_order_item WHERE id = <new_item_id>)` → `'manual'`
- [ ] CHECK `chk_purchase_type_or_manual` срабатывает: попытка вставить `purchase_type IS NULL` с `formula_version != 'manual'` → ошибка
- [ ] Кейс заказа З-1580 (Воропаев): после G6 должен иметь привязанный manual-snapshot с `operator_price = unit_price`, `backfill = true`

---

## Открытые вопросы

- **Q (для G6):** при бэкфилле для исторических заказов — какой `created_at` ставить в `pricing_price_calculation.calculated_at`? Решение по умолчанию: `orders_order.created_at` родительского заказа (выше в Задаче 2 уже зафиксировано). Альтернатива — дата миграции — отвергнута: теряется информация «это историческая ручная цена», а не «снэпшот, сделанный в день миграции».

- **Q (для G6 / ADR-F02):** должен ли `formula_version = 'manual'` участвовать в Actual Profit? Решение: да, через `breakdown.operator_price` (равно `unit_price`). Margin в таком snapshot = 0, поэтому Planned Profit для manual-позиций тоже 0 — оператор сам взял на себя выбор цены.

- **Q (для G5 — `update_order_item` MCP-tool):** при обновлении цены — создаётся новый snapshot (новая запись `pricing_price_calculation`) и `price_calculation_id` переключается. Старый snapshot остаётся в БД, но FK на него больше нет — он становится «orphan». Нужен ли клин-ап orphan-снэпшотов или пусть копятся? Решение по умолчанию: пусть копятся (audit trail отказов от цены); ревизия — при росте таблицы свыше 10 000 строк (отдельный ADR).

- **Q (для будущего ADR-F05 — расположение формул):** ADR-F05 будет решать, остаётся ли pricing-формула в Python или уезжает в DB-функции. Если в DB — manual-snapshot становится «вырожденным случаем» формулы (margin = 0, rounding = 1). Если в Python — остаётся как сейчас. ADR-F04 совместим с обоими исходами.
