# ADR-018 Addendum: Уникальные товары — отдельная сущность `orders_unique_purchase`

**Статус:** принят
**Дата:** 2026-05-06
**Расширяет:** ADR-018 (Калькулятор нового заказа — первый write-артефакт)
**Связанные:** ADR-003 final-ready (схема `orders_order_item`, `procurement_purchase_item`, `warehouse_receipt_item`), ADR-003 addendum-quoted (статус `quoted`), ADR-004 (pricing & profit policy), ADR-008 (stock price invariant), ADR-F01 (выбор веса — draft), ADR-F04 (snapshot цен), G6 (finance ledger), G19 (калькулятор нового заказа)
**Закрывает развилку:** D из ADR-018, запись `2026-05-05` в `docs/schema-gaps.md`

---

## Контекст

ADR-018 принял функциональность калькулятора нового заказа, но решение по развилке D (где живут уникальные товары — eBay-лоты, штучные книги, антиквариат, всё что не каталожный SKU) отложил в отдельный мини-ADR. Этот аддендум закрывает развилку.

В обсуждении 2026-05-06 (Cowork-arch) уточнено два важных факта, которых не было в формулировке развилки D в самом ADR-018:

1. **Уникальный товар физически приезжает в посылке** — вместе с каталожными. Не «сценарий калькулятора и забыли», а полноценный участник складского workflow: `match_shipment` должен его находить, `warehouse_receipt_item` должен фиксировать его прибытие, оператор должен понять кому отдавать.

2. **Посылка как агрегат фактических затрат.** За посылку списываются деньги за доставку (общий shipping в инвойсе форвардера). Стоимость доставки распределяется между товарами внутри посылки по фактическому весу. Это часть **аудита фактических затрат** (Actual layer, ADR-004) и одновременно — момент **актуализации `catalog_product.actual_weight`** для каталожных товаров (расхождение заявленного и фактического веса).

Из этих двух фактов следует, что развилка D — не «куда положить inline-поля для калькулятора», а **«как унифицировать поток каталожный/уникальный товар через order_item → procurement → warehouse»**. Решение распространяется минимум на четыре таблицы.

В самом ADR-018 рассматривались три варианта (D1 inline-поля, D2 отдельная таблица, D3 каталог с флагом `is_unique`). Вариант D3 уже отвергнут в ADR-018 (каталог не должен расти мёртвыми SKU). Реальный выбор — между D1 и D2.

В обсуждении 2026-05-06 D1 рассматривался как достаточный для калькулятора. Отвергнут после уточнения роли посылки: для β-варианта (уникальные товары проходят через `warehouse_receipt_item` ради аудита посылки) D1 потребовал бы дублировать inline-структуру в двух таблицах (`order_item` и `warehouse_receipt_item`) с одинаковыми колонками. Это нарушение нормализации без выгоды. D2 даёт одну таблицу, на которую симметрично ссылаются все участники потока.

---

## Решение

### Архитектурный профиль

| Развилка | Принято |
|---|---|
| Где живёт уникальный товар | **D2** — отдельная таблица `orders_unique_purchase` |
| Уникальный в потоке order → procurement → warehouse | **β** — проходит весь поток как полноценный участник, симметрично каталожному |
| Distribution shipping + `actual_weight` backfill | **зафиксировано как намерение, реализация отдельным ADR** (см. раздел «Намерение по аудиту посылки») |

### Десять зафиксированных пунктов

1. **Новая таблица `orders_unique_purchase`.** Принадлежит модулю `orders` (по правилам ADR-001 v2 + ADR-003: каждая таблица в одном модуле, имя `{module}_{singular}`). Колонки:
   - `id BIGINT IDENTITY PK`
   - `name TEXT NOT NULL` — название лота как видит оператор («Hayward — Cabinetmaking and Joinery», «eBay лот #123 — антикварный рубанок Stanley №4»)
   - `weight_kg NUMERIC(10, 3) NULL` — фактический вес для расчёта shipping; на момент создания quoted — оценочный, может быть актуализирован при поступлении
   - `purchase_price_fcy NUMERIC(18, 4) NOT NULL` — цена закупки в исходной валюте (например, $35.00)
   - `purchase_currency CHAR(3) NOT NULL` — через `currency_column(nullable=False)` (правило ADR-003)
   - `source_url TEXT NULL` — ссылка на лот (eBay, Yahoo Auctions, конкретный объявление); опциональна
   - `notes TEXT NULL` — свободные заметки оператора («продавец просит до 2026-05-20», «упаковка повреждена при отправке»)
   - `created_at`, `updated_at` — стандартный `TimestampMixin` (правило ADR-003 без исключений)

2. **`orders_order_item` расширяется двумя FK.** Существующая колонка `product_id BIGINT NOT NULL → catalog_product.id` становится `NULLABLE`. Добавляется колонка `unique_purchase_id BIGINT NULL → orders_unique_purchase.id`. CHECK constraint:
   ```sql
   CHECK (
     (product_id IS NOT NULL AND unique_purchase_id IS NULL)
     OR
     (product_id IS NULL AND unique_purchase_id IS NOT NULL)
   )
   ```
   Этот же паттерн применяется в `procurement_purchase_item` и `warehouse_receipt_item` (см. п. 3 и 4). ON DELETE — `RESTRICT` для обоих FK (как сейчас для `product_id`).

3. **`procurement_purchase_item` расширяется тем же паттерном.** `product_id NULLABLE` + новая колонка `unique_purchase_id BIGINT NULL → orders_unique_purchase.id` + аналогичный CHECK. Закупка лота с eBay создаёт `procurement_purchase_item` со ссылкой на `orders_unique_purchase`, не на `catalog_product`. Это означает, что закупка уникального товара отслеживается тем же механизмом, что и каталожного — через `procurement_purchase` со статусами и трекингом.

4. **`warehouse_receipt_item` расширяется тем же паттерном.** `product_id NULLABLE` + `unique_purchase_id BIGINT NULL → orders_unique_purchase.id` + аналогичный CHECK. Когда посылка приходит и оператор фиксирует поступление — каждый товар (каталожный или уникальный) попадает в `warehouse_receipt_item` через свой FK. Это даёт **полный аудит посылки в одной таблице** — никаких обходных путей через комментарии или JSONB.

5. **`warehouse_stock_item` НЕ расширяется.** Уникальный товар **не образует stock** — после получения посылки он сразу же передаётся клиенту (или находится в коробке как назначенный конкретному клиенту лот). Инвентаризация уникального товара бессмысленна: его нельзя «продать кому-то ещё», он привязан к конкретному заказу. ADR-008 stock price invariant применяется только к каталожным товарам — для уникальных он не имеет смысла (нет понятия «средневзвешенная цена партии», лот один и цена одна). На уровне БД это означает, что `warehouse_stock_item.product_id` **остаётся NOT NULL** (как сейчас), и записи о уникальных товарах туда не попадают.

6. **`pricing_price_calculation` НЕ расширяется.** Снимок расчёта цены (ADR-F04, immutable) хранит расчёт через `input_params JSONB` + `breakdown JSONB`. Для уникального товара параметры расчёта (вес, цена закупки в FCY, валюта) сохраняются в `input_params` точно так же, как для каталожного, плюс флаг `is_unique=true` и значение `unique_purchase_id`. FK на `catalog_product` остаётся, но становится **nullable** — потому что для уникального товара его нет. Это минимальное изменение существующей таблицы. CHECK на `pricing_price_calculation` не вводится — корректность связки контролируется на уровне сервиса (`calculate_price` MCP-tool, G19.1).

7. **Калькулятор и `calculate_price` (G19.1) — без изменений в сигнатуре.** Сигнатура из ADR-018 остаётся: `calculate_price(items: list[CalculatePriceItem], exchange_rate=None, customer_id=None)`. `CalculatePriceItem` уже описан в ADR-018 как «каталожный ИЛИ inline-описание»: для каталожного передаётся `product_id`, для уникального — `inline_name`, `inline_weight_kg`, `inline_purchase_price_fcy`, `inline_purchase_currency`, `inline_source_url`. Tool работает с описанием в обоих случаях, в БД ничего не пишет. **Запись** уникального товара в БД делает артефакт **в момент создания quoted** (см. п. 8).

8. **Создание quoted с уникальным товаром — атомарная транзакция в `create_order`.** Артефакт калькулятора передаёт в `create_order` items в том же формате, что и `calculate_price`. На стороне сервиса (внутри одной транзакции):
   1. Для каждого item, описанного inline (без `product_id`): `INSERT INTO orders_unique_purchase (name, weight_kg, purchase_price_fcy, purchase_currency, source_url) RETURNING id` → получаем `unique_purchase_id`.
   2. `INSERT INTO orders_order_item (..., product_id=NULL, unique_purchase_id=<id из шага 1>)`.
   3. Для каталожных позиций: обычный `INSERT INTO orders_order_item (..., product_id=<id>, unique_purchase_id=NULL)`.
   4. Всё в одной транзакции. При сбое на любом шаге — rollback, никаких orphaned `orders_unique_purchase` записей.

   Идемпотентность артефакта (защита от двойного клика «создать quoted», ADR-018) сохраняется — кнопка блокируется до завершения всей транзакции.

9. **`match_shipment` расширяется поиском по двум источникам.** Текущий SQL ищет по `catalog_product.name ILIKE :pat`. После принятия аддендума SQL расширяется UNION-веткой по `orders_unique_purchase.name ILIKE :pat` (или эквивалентной OR-конструкцией с LEFT JOIN на `orders_unique_purchase` через `oi.unique_purchase_id`). Реализация — внутри G19 (новая подзадача G19.7 — «адаптация match_shipment под уникальные товары»). Поведение для оператора не меняется: он вводит названия из коробки, tool возвращает matched/ambiguous/unmatched независимо от того, какой это товар.

10. **`update_order_item_status` для уникального товара работает без изменений.** Статусы позиции (`pending → ordered → shipped → at_forwarder → arrived → delivered`) одни и те же для каталожных и уникальных товаров. Никаких отдельных enum-значений или специальных переходов вводить не нужно. Это означает, что workflow оператора по позициям абсолютно одинаков — он не должен помнить «эта позиция уникальная, для неё другой набор статусов».

### Поток работы с уникальным товаром (full lifecycle)

```
Оператор открывает калькулятор
  └─ добавляет «Hayward — книга, $35, 1.2 kg» как уникальный
       │
       ▼
calculate_price(items=[{inline_name, inline_weight_kg, inline_purchase_price_fcy, ...}], ...)
  возвращает цену клиенту, артефакт показывает
       │
       ▼
оператор: «создать quoted и скопировать»
       │
       ▼
create_order(items=[...], status='quoted')
  внутри транзакции:
    INSERT INTO orders_unique_purchase (name, weight_kg, purchase_price_fcy, ...) RETURNING id
    INSERT INTO orders_order_item (order_id, product_id=NULL, unique_purchase_id=<id>, ...)
       │
       ▼
оператор отправляет текст в Telegram, клиент согласился
       │
       ▼
verify_draft_order или confirm_quote → статус заказа confirmed → in_procurement
       │
       ▼
оператор делает закупку лота на eBay
  create_purchase / update_purchase создаёт procurement_purchase + procurement_purchase_item
  с unique_purchase_id (не product_id)
       │
       ▼
лот отправляется → tracking → at_forwarder → arrived в Москве
  update_order_item_status переводит позицию по той же лестнице, что и каталожные
       │
       ▼
ПОСЫЛКА ПРИХОДИТ — содержит каталожные + уникальные
оператор: match_shipment(['Veritas Shooting Board', 'Hayward Cabinetmaking'])
  SQL ищет в catalog_product.name И в orders_unique_purchase.name
  → matched: Veritas → заказ X (через product_id), Hayward → заказ Y (через unique_purchase_id)
       │
       ▼
оператор фиксирует поступление: warehouse_receipt + warehouse_receipt_item × N
  для каталожных: receipt_item.product_id = ..., unique_purchase_id = NULL
  для уникальных: receipt_item.product_id = NULL, unique_purchase_id = ...
       │
       ▼
[НАМЕРЕНИЕ — не реализуется в G19]
  оператор вводит общий shipping_cost посылки
  система распределяет по фактическому весу
  для каталожных — backfill catalog_product.actual_weight (с подтверждением оператора)
  для уникальных — share shipping cost фиксируется в pricing breakdown позиции
       │
       ▼
update_order_item_status('delivered') для каждой позиции
  derivation rule переводит заказ в delivered (если все позиции доставлены)
```

### Защита и инварианты

- **Целостность связи order_item / procurement_item / receipt_item с уникальным товаром.** CHECK constraint «ровно один из двух FK» гарантирует, что строка в этих таблицах либо ссылается на каталог, либо на `orders_unique_purchase`, либо ни на что (последнее — невозможно, так как CHECK требует один из двух). Это enforced на уровне БД, не зависит от кода.

- **Удаление уникального товара.** ON DELETE на FK `unique_purchase_id` — `RESTRICT` (как для `product_id`). Это значит, что нельзя удалить запись `orders_unique_purchase`, если на неё ссылается хотя бы один `order_item` / `purchase_item` / `receipt_item`. Удаление возможно только если оператор сначала удалил/отвязал все ссылающиеся записи. Для черновых quoted-заказов: `verify_draft_order(reject)` каскадно удаляет `order_item` (CASCADE на FK `order_id`), но запись `orders_unique_purchase` остаётся. Это допустимо: запись «лот, который рассматривался, но заказ не подтвердили» — небольшая, безвредная и может быть полезна для анализа отказов клиентов. Если в практике накопится мусор — отдельная housekeeping-задача (см. открытые вопросы).

- **Иммутабельность семантики `orders_unique_purchase` после создания.** В отличие от `pricing_price_calculation`, таблица **не помечается** маркером `__immutable__`. После создания запись может быть отредактирована — например, при поступлении посылки оператор уточняет фактический `weight_kg`, который изначально был оценочным. `updated_at` обновляется через стандартный `onupdate=func.now()`. Это согласуется с тем, что `orders_unique_purchase` — не снапшот расчёта, а описание лота, эволюционирующее по факту прибытия.

- **Уникальный товар не образует stock.** Жёстко: ни один `INSERT INTO warehouse_stock_item (..., unique_purchase_id, ...)` невозможен — соответствующей колонки нет (см. п. 5). Если в будущем появится потребность держать уникальные товары на складе как inventory (нерасторопно реализованный заказ, лот не успел уехать клиенту неделю), это потребует отдельного расширения `warehouse_stock_item` и нового ADR. До тех пор уникальный товар проходит «receipt_item → сразу к клиенту», без промежуточного stock.

- **`pricing_price_calculation.product_id` NULLABLE — единственное изменение immutable-таблицы.** Колонка делается nullable миграцией. Существующие записи (где `product_id NOT NULL` для каталожных товаров) не затрагиваются, новые записи могут иметь `NULL` для уникальных товаров. ADR-F04 (snapshot цен, immutable-семантика) соблюдается: запись после создания не меняется. Изменение только в том, что **набор корректных записей** расширен — теперь допустимо `product_id IS NULL`, при условии что в `input_params` присутствуют inline-поля и `unique_purchase_id`.

### Намерение по аудиту посылки (НЕ реализуется в G19)

Этот раздел фиксирует **намерение** по distribution shipping и `actual_weight` backfill. Реализация — отдельный ADR (предположительно в G6 finance ledger или addendum к ADR-F04 после принятия ADR-F01).

**Зачем намерение здесь:** workflow посылки — это аргумент за D2 + β (см. контекст). Без явной фиксации куда это идёт дальше, кажется, что мы решили половину задачи. Чтобы не размывать scope этого аддендума (схема уникальных товаров) и одновременно не потерять связку — фиксируем направление.

**Рабочий процесс посылки (намерение):**

1. Оператор фиксирует поступление: `warehouse_receipt` + список `warehouse_receipt_item` (каталожные + уникальные через свои FK).
2. Оператор вводит **общий shipping_cost посылки** — фактическая стоимость доставки от форвардера, в её исходной валюте (USD по практике).
3. Оператор вводит/подтверждает **общий фактический вес посылки** (по инвойсу форвардера) и **фактический вес каждого товара** в посылке (взвешивает или берёт с инвойса).
4. Система распределяет shipping_cost между `warehouse_receipt_item` пропорционально фактическому весу (формула — отдельное решение).
5. Для каждого каталожного товара система **сравнивает фактический вес с `catalog_product.actual_weight`** и предлагает оператору обновить (если расхождение существенное; что считать «существенным» — отдельный вопрос).
6. Для каждого уникального товара фактический вес записывается в `orders_unique_purchase.weight_kg` (UPDATE существующей записи).
7. Распределённая доля shipping per item фиксируется в pricing breakdown позиции (вход в Actual layer ADR-004) — это позволяет потом считать фактическую прибыль по заказу.

**Что в этом намерении ещё не решено (открытые вопросы):**

- **Формула распределения shipping.** По фактическому весу — естественный выбор, но возможны варианты: по объёмному весу (для крупногабаритных лёгких товаров), по стоимости (для дорогих товаров с высокой страховкой), комбинация. Зависит от ADR-F01 (выбор веса) — он сейчас draft.
- **Порог обновления `catalog_product.actual_weight`.** Любое расхождение → вопрос оператору? Или только при отклонении >10%? Или только если `actual_weight IS NULL` (первое заполнение)? Связано с типом товара (для книг вес стабилен, для рубанков может варьироваться).
- **Кто принимает решение об обновлении `actual_weight`.** Авто (с записью в audit log) или с явным подтверждением оператора через Cowork-диалог? Операторская боль здесь минимальна (это не «правка цены», это объективный факт), но структурно решение об обновлении справочника заслуживает явного шага.
- **Где хранится распределённая доля shipping per item.** Расширение `pricing_price_calculation.breakdown` или новая колонка в `warehouse_receipt_item.shipping_share_rub`? Связано с ADR-004 Actual layer.
- **Как пересчитывается прибыль при поступлении посылки.** Pricing snapshot был зафиксирован при создании quoted (по оценочному весу). При поступлении актуальный shipping известен — нужно ли создавать новый `pricing_price_calculation` с пометкой «actual» и фиксировать в `order_item` через дополнительный FK? Или достаточно записи в finance ledger без апдейта pricing-таблицы?

**Где это будет решаться:**

- Запись в `06_open_questions.md` под заголовком «Распределение shipping и backfill actual_weight по факту посылки» с чатом «Архитектурный штаб» и приоритетом medium.
- Привязка к **G6 (finance ledger)** в `docs/PLAN.md` — там это естественно ложится в Actual layer.
- Зависимость от **ADR-F01** (выбор веса) — пока он в draft, формула распределения не финализируется.

**Что зафиксировано как факт сейчас:**

- Схема `warehouse_receipt_item` поддерживает уникальные товары (п. 4) — этого достаточно, чтобы записывать факт поступления.
- Структура `orders_unique_purchase.weight_kg` (NULL допустим) позволяет actualisation — не нужна миграция, когда придёт время.
- `catalog_product.actual_weight` уже существует в схеме (ADR-003) — поле есть, осталось формализовать процесс backfill.

То есть **схема готова** к реализации workflow распределения; **правила** (формулы, пороги, UI оператора) — отдельный шаг.

---

## Что **не** включено в этот аддендум

### Миграция существующих заказов

Все существующие `orders_order_item` имеют `product_id NOT NULL` — после миграции колонка станет `NULLABLE`, но все существующие строки сохранят свои значения. Никаких backfill / data migration не нужно. Новые записи могут иметь `unique_purchase_id`, старые остаются с `product_id`.

### Stock lots для уникальных товаров

Уникальный товар не идёт на `warehouse_stock_item` (п. 5). Если в будущем потребуется хранить уникальные товары на складе (нерасторопно реализованный заказ, лот не успел уехать клиенту неделю — лежит на складе как назначенный лот) — это **отдельный ADR**. Сейчас допущение: уникальный товар приехал → сразу к клиенту, без stock-ступени. Если в практике это допущение нарушается — открыть запись в `06_open_questions.md`.

### Versioning / редактирование уникального товара после поступления

`orders_unique_purchase` редактируется (п. «Защита и инварианты»). История изменений не хранится — `updated_at` показывает последнюю правку, прежние значения теряются. Если в будущем понадобится audit изменений (кто/когда/что менял в описании лота) — отдельный ADR на универсальный audit log (`finance_ledger_entry` или новая таблица `audit_log`). Сейчас это излишне.

### Поиск уникальных товаров через `search_products`

`search_products` MCP-tool ищет в `catalog_product`. Уникальные товары туда не попадают (по решению — не засорять каталог). Если оператору нужно найти «какие уникальные были в заказах за последний месяц» — это **отдельный tool** (`search_unique_purchases`) или расширение `pending_orders` фильтром по типу. Не входит в G19. Открыть запись в `docs/tool-gaps.md` если возникнет реальная потребность.

### Уникальные товары в Google Sheets mirror (ADR-005)

Mirror экспортирует таблицы ядра в Google Sheets раз в сутки. После принятия аддендума появится новая таблица `orders_unique_purchase` — её нужно добавить в список экспортируемых. Это **тривиальная правка** в `crm-mcp/mirror/` (новая вкладка `orders_unique_purchase`), не требует addendum к ADR-005. Реализуется в рамках G19.3 как часть миграции (один лишний пункт в `mirror/__init__.py`).

---

## Что должен сделать Claude Code

Реализация — внутри **G19.3** в `docs/PLAN.md`. Подзадача расширяется по сравнению с тем, что было запланировано в ADR-018 (одна миграция для уникальных товаров): теперь это **четыре связанных изменения** в одной миграции + адаптация существующих MCP-tools.

### G19.3 миграция и модели — детализация

1. **Миграция Alembic `XXXX_unique_products.py`:**

   a. `CREATE TABLE orders_unique_purchase` со всеми колонками из п. 1 решения. Стандартный `id BIGINT IDENTITY PK`, `currency_column(nullable=False)` для `purchase_currency`, `created_at` / `updated_at` через TimestampMixin-паттерн.

   b. `ALTER TABLE orders_order_item`:
      - `ALTER COLUMN product_id DROP NOT NULL`
      - `ADD COLUMN unique_purchase_id BIGINT NULL REFERENCES orders_unique_purchase(id) ON DELETE RESTRICT`
      - `ADD CONSTRAINT ck_orders_order_item_product_xor_unique CHECK (...)` (формула из п. 2)
      - `CREATE INDEX ix_orders_order_item_unique_purchase_id ON orders_order_item (unique_purchase_id) WHERE unique_purchase_id IS NOT NULL`

   c. `ALTER TABLE procurement_purchase_item`: симметрично п. b (drop NOT NULL на product_id, add unique_purchase_id, CHECK, partial index).

   d. `ALTER TABLE warehouse_receipt_item`: симметрично п. b.

   e. `ALTER TABLE pricing_price_calculation`:
      - `ALTER COLUMN product_id DROP NOT NULL`
      - **БЕЗ** добавления FK на `orders_unique_purchase` (см. п. 6 решения — связка через `input_params` JSONB, не FK).

   f. `INDEX` на `orders_unique_purchase` стандартные (на FK от других таблиц, не на саму таблицу — там только PK достаточно для текущих запросов).

2. **SQLAlchemy модели:**

   a. `app/orders/models.py`: новая модель `OrdersUniquePurchase(Base, TimestampMixin)`. Поля по п. 1.

   b. `app/orders/models.py`: `OrdersOrderItem` — `product_id` становится `Mapped[int | None]`, добавляется `unique_purchase_id: Mapped[int | None]`, обновляется `__table_args__` с CHECK.

   c. `app/procurement/models.py`: `ProcurementPurchaseItem` — аналогично п. b.

   d. `app/warehouse/models.py`: `WarehouseReceiptItem` — аналогично п. b.

   e. `app/pricing/models.py`: `PricingPriceCalculation.product_id` → `Mapped[int | None]`.

   **Ограничение из ADR-003:** `relationship()` между моделями разных модулей запрещён. Все новые FK — только на уровне БД (`ForeignKey(...)`), без `relationship()` между `OrdersOrderItem` и `OrdersUniquePurchase`? **Нет** — обе модели в модуле `orders`, поэтому `relationship()` между ними **допустим**. То же для `procurement_purchase_item` (модуль `procurement`) и `warehouse_receipt_item` (модуль `warehouse`) — они в разных модулях от `OrdersUniquePurchase`, поэтому `relationship()` к нему **не делается**, только FK.

3. **Тест границ модулей** (`tests/test_module_boundaries.py`) автоматически проверит правило «relationship только внутри модуля» — добавление новых моделей не должно его сломать.

4. **Обновить публичные `__init__.py`:**
   - `app/orders/__init__.py` — ничего не экспортирует по правилу ADR-003 (на этапе core schema), но при появлении сервисов / Pydantic-схем для уникальных товаров на следующих этапах — экспорт через них.

### G19.1 корректировки — `calculate_price`

Сигнатура остаётся как в ADR-018 + addendum-exchange-rate. Дополнительно:

- `CalculatePriceItem` — Pydantic-схема — поддерживает оба варианта: `{product_id: int}` ИЛИ `{inline_name: str, inline_weight_kg: Decimal, inline_purchase_price_fcy: Decimal, inline_purchase_currency: str, inline_source_url: str | None}`. Валидация: ровно один из двух паттернов в одном элементе списка.
- В возвращаемом результате per-item включается `is_unique: bool` (для информативности UI калькулятора и mirror).
- Pricing-формула — без изменений (ADR-004), та же что для каталожных товаров. Различие только в источнике входных данных (catalog или inline).

### G19.5 корректировки — артефакт `order_calculator.html`

Расширение, описанное в ADR-018:
- В UI добавить кнопку «+ уникальный» рядом с «+ из каталога» (это уже было в эскизе UI ADR-018).
- Форма уникального: name, weight_kg, purchase_price_fcy, purchase_currency (по умолчанию USD), source_url (опц).
- При создании quoted — items в `create_order` передаются с inline-полями, бэкенд (см. п. 8 решения) делает атомарный INSERT `orders_unique_purchase` + `orders_order_item`.

### G19.7 (новая подзадача) — адаптация `match_shipment`

- Расширить SQL поиском по `orders_unique_purchase.name` (см. п. 9 решения).
- В возвращаемом результате per-row добавить `is_unique: bool` (для UI Cowork — оператору полезно понимать, что это лот, не каталожный SKU).
- Тесты — добавить кейс «matched по уникальному товару», «ambiguous между каталожным и уникальным с похожим именем».
- Обновить `cowork-system-prompt.md` (раздел про `match_shipment`) — два предложения о том, что tool ищет по обоим источникам.

### Зеркало Google Sheets (тривиально)

- Добавить вкладку `orders_unique_purchase` в `crm-mcp/mirror/__init__.py` (существующий механизм полной пересборки автоматически экспортирует новую таблицу при добавлении в список).

### Тесты (минимум)

1. **Миграционный тест** — `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` без ошибок. CHECK constraints применяются.
2. **Модельный тест** — создание `OrdersUniquePurchase`, `OrdersOrderItem` с FK на него, попытка создать `OrdersOrderItem` с обоими FK заполненными → IntegrityError (CHECK), попытка создать с обоими NULL → IntegrityError.
3. **`calculate_price` с inline-описанием** — вернёт корректную цену; `is_unique=true` в результате.
4. **`create_order` атомарный** — items с inline-описанием создают `orders_unique_purchase` + `orders_order_item` в одной транзакции; при искусственном сбое после первого INSERT — rollback, нет orphaned записей в `orders_unique_purchase`.
5. **`match_shipment` находит уникальный товар** — создан уникальный товар в quoted-заказе, `match_shipment(['Hayward'])` возвращает matched с `is_unique=true`.
6. **`warehouse_receipt_item` с уникальным товаром** — создаётся запись с `unique_purchase_id`, `product_id=NULL`, CHECK не падает.
7. **`warehouse_stock_item` НЕ принимает уникальный товар** — попытка создать stock_item для уникального товара невозможна, потому что в схеме нет колонки `unique_purchase_id` (это enforced дизайном, не отдельным тестом).

---

## Что проверить вручную

- [ ] `\d orders_unique_purchase` — таблица создана со всеми колонками; `purchase_currency` — `CHAR(3)` с CHECK на формат.
- [ ] `\d orders_order_item` — `product_id` теперь NULLABLE, добавлена колонка `unique_purchase_id`, CHECK constraint `ck_orders_order_item_product_xor_unique` присутствует.
- [ ] `\d procurement_purchase_item` — то же самое.
- [ ] `\d warehouse_receipt_item` — то же самое.
- [ ] `\d pricing_price_calculation` — `product_id` теперь NULLABLE, никаких новых FK.
- [ ] `\d warehouse_stock_item` — без изменений, `product_id` остался NOT NULL.
- [ ] Создание quoted с уникальным товаром через калькулятор — записи появляются в `orders_unique_purchase` и `orders_order_item`, цена считается корректно.
- [ ] `match_shipment` с названием уникального товара — находит позицию через `unique_purchase_id`.
- [ ] `warehouse_receipt_item` с `unique_purchase_id` — создаётся, аудит посылки виден через `SELECT * FROM warehouse_receipt_item WHERE receipt_id = X`.
- [ ] Существующие тесты `tests/test_module_boundaries.py` — проходят (правило «relationship только внутри модуля» не нарушено).
- [ ] `git log` после миграции — есть запись в `000_ADR_REGISTRY.md` про этот аддендум, ADR-018 содержит ссылку на него, schema-gaps.md запись 2026-05-05 закрыта.

---

## Открытые вопросы (после принятия)

- **Q (для G6 / finance ledger):** распределение shipping по фактическому весу — формула, порог обновления `catalog_product.actual_weight`, кто принимает решение, где хранится распределённая доля. Запись в `06_open_questions.md` под заголовком «Распределение shipping и backfill actual_weight по факту посылки», чат — Архитектурный штаб, приоритет medium, зависит от ADR-F01.

- **Q (для оператора, после запуска G19):** появляется ли мусор в `orders_unique_purchase` от отменённых quoted-заказов (рассмотрел лот → клиент отказался → quoted отменён → запись `orders_unique_purchase` осталась). Если объём становится заметным (>50 неиспользуемых записей за месяц) — housekeeping-задача (отдельный tool или расширение `verify_draft_order(reject)` каскадом удалять и `orders_unique_purchase` если на неё нет других ссылок).

- **Q (для оператора, после запуска G19):** нужен ли `search_unique_purchases` MCP-tool для поиска уникальных товаров за период / по клиенту / по поставщику? Активация — реальный запрос оператора. До тех пор обходимся через SQL ad-hoc.

- **Q (потенциальный, малый риск):** stock-ступень для уникальных товаров. Сейчас допущение «уникальный товар приехал → сразу к клиенту». Если в практике лот лежит на складе (например, клиент в отпуске, не может забрать) — открыть отдельный ADR на расширение `warehouse_stock_item` или вспомогательную таблицу `warehouse_unique_holding`.
