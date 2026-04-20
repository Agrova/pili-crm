# ADR-003: Схема данных PostgreSQL — ядро (final-ready)

## Контекст

В ADR-001 v2 зафиксирована архитектура (модульный монолит, 8 модулей с явными границами, каждый владеет своими таблицами). В ADR-002 v2 зафиксирован стек (PostgreSQL 16 + SQLAlchemy 2.0 async + Alembic + pgvector, корневой пакет `app/`, управление зависимостями только через `pyproject.toml`).

Теперь нужно зафиксировать **схему данных ядра**: какие таблицы существуют, в каком модуле они живут, какие у них ключи, связи, типы полей, ограничения и индексы. Без этого нельзя начать реализацию ни одного модуля — все они работают с общей БД.

Ограничения и принципы:

- Каждая таблица принадлежит ровно одному модулю. Чужие модули обращаются к её данным только через публичный интерфейс модуля-владельца.
- FK между таблицами разных модулей разрешены (целостность на уровне БД). ORM-relationship между моделями разных модулей запрещён (инкапсуляция на уровне кода).
- БД — источник истины. Все критические расчёты (pricing) детерминированы и используют точные типы (NUMERIC, не FLOAT).
- Схема должна поддерживать: два типа курсов валют (фактический в finance, расчётный в pricing), заявленный и фактический вес товара, связывание коммуникаций с бизнес-сущностями, резервы на складе под заказы клиентов.
- Система рассчитана на одного оператора — права доступа на уровне БД не нужны.
- Зависимости проекта управляются только через `pyproject.toml` (ADR-002 v2). Никаких `requirements.txt` / `requirements-dev.txt`.

### Precondition (состояние репозитория перед запуском Claude Code)

Настоящий ADR-003 в его финальной редакции **уже вручную сохранён** в `docs/adr/ADR-003-postgres-core-schema.md` до запуска Claude Code. Из этого следует:

- Claude Code **не создаёт и не изменяет** файлы в `docs/adr/`. Все три принятых ADR (001 v2, 002 v2, 003) уже находятся на месте.
- Claude Code читает ADR-003 из репозитория как источник истины по схеме данных, но не модифицирует его.
- Проверка `git diff docs/adr/` после работы Claude Code должна быть пуста.
- Единственный документ, который Claude Code правит в процессе, — корневой `README.md` (добавление раздела «Схема данных» со ссылкой на ADR-003).

Решение нужно принять сейчас, потому что от него зависят:

- модели SQLAlchemy в каждом модуле;
- первая миграция Alembic;
- все последующие промты Claude Code по реализации модулей.

### Изменения относительно предыдущих редакций ADR-003

В ходе ревизии внесены правки:

1. Подтверждена конвенция: зависимости — только `pyproject.toml`, без `requirements-dev.txt` (ссылка на ADR-002 v2).
2. Убрано из scope настоящего ADR: экспорт Pydantic-схем через `__init__.py` модулей. Core schema — только SQLAlchemy-модели, типы, enum-ы, FK, constraints, миграция.
3. Уточнена техническая реализация `communications_link.source_id`: вместо одного полиморфного поля — два nullable FK (`email_message_id`, `telegram_message_id`) с CHECK-constraint.
4. Уточнён enum `communications_link_target_module`: `pricing` исключён из списка target-зон.
5. Однозначно зафиксировано направление связи `orders_order_item` → `pricing_price_calculation` (FK из order_item в price_calculation).
6. Добавлена секция «Constraints и индексы».
7. **Добавлена явная precondition** о том, что ADR-файлы сохранены вручную до запуска Claude Code (см. выше).
8. **Уточнено правило timestamps:** `created_at` и `updated_at` присутствуют на **всех таблицах ядра без исключений** (включая immutable-снапшоты и ассоциативные таблицы). Формулировка про «бизнес-таблицы» убрана как двусмысленная.
9. **Разведены по слоям:** `Currency` как Python-тип в `app/shared/types.py`, и `currency_column()` как reusable helper в том же модуле, возвращающий SQLAlchemy-колонку `CHAR(3)` с CHECK-constraint. Это два разных артефакта, используемых в разных контекстах.

## Варианты

### Вариант 1 — Единая схема `public`, таблицы с префиксами модулей

- Описание: все таблицы в одной схеме `public`, имена с префиксом модуля (`catalog_product`, `orders_order`, `pricing_exchange_rate`).
- Плюсы: простая настройка Alembic; автогенерация миграций для всей схемы сразу; FK между модулями работают без дополнительных настроек; визуальная однозначность принадлежности таблицы.
- Минусы: границы модулей не отражены на уровне БД — только в коде и через префиксы; при 40+ таблицах в одной схеме становится много «шума» при `\dt`.

### Вариант 2 — Отдельная PostgreSQL-схема на каждый модуль

- Описание: схемы `catalog`, `orders`, `procurement`, `warehouse`, `pricing`, `communications`, `finance`. Таблицы: `catalog.product`, `orders.order`, `pricing.exchange_rate`.
- Плюсы: границы модулей зафиксированы на уровне БД; права доступа можно выдавать по схемам (актуально для multi-user в будущем); миграции могут быть организованы по схемам.
- Минусы: сложнее настройка Alembic (`include_schemas=True`, управление `search_path`); FK между схемами требуют явного указания схемы в `ForeignKey`; больше boilerplate в SQLAlchemy-моделях (`__table_args__ = {"schema": ...}`); для одного оператора — избыточно.

### Вариант 3 — Единая схема `public` без префиксов

- Описание: все таблицы в `public` без префиксов (`product`, `order`, `exchange_rate`). Принадлежность модулю фиксируется только через каталог, в котором лежит модель.
- Плюсы: самые короткие имена; минимум boilerplate.
- Минусы: коллизии имён неизбежны (в проекте две `exchange_rate` — BankExchangeRate у finance и PricingExchangeRate у pricing); при чтении БД напрямую непонятно, кому принадлежит таблица.

## Критерии выбора

- **Надёжность:** Вариант 1 и 2 защищают от коллизий имён; Вариант 3 — нет.
- **Простота поддержки:** для одного оператора критически важна читаемость схемы в `psql`. Вариант 1 даёт эту ясность без накладных расходов.
- **Совместимость с AnythingLLM:** AnythingLLM работает через API, схема БД ему не видна. Критерий нейтрален.
- **Простота интеграции с Claude Code:** Вариант 1 — простейший (`__tablename__ = "catalog_product"`). Вариант 2 — дополнительный boilerplate. Вариант 3 — риск коллизий.
- **Масштабируемость:** при выносе модуля в сервис префиксы упрощают миграцию — `catalog_product` превращается в `catalog.product` простым rename. Путь к Варианту 2 не закрыт.

## Принятое решение

**Вариант 1 — единая схема `public`, таблицы с префиксами модулей.**

Формат: `{module}_{entity_snake_case}`. Имена колонок — `snake_case`.

### Общие конвенции

1. **Именование:** таблицы `{module}_{singular_noun}` (`catalog_product`, не `catalog_products`). Колонки `snake_case`.

2. **Первичные ключи:** у всех таблиц — суррогатный `id BIGINT GENERATED ALWAYS AS IDENTITY`. UUID не используем.

3. **Временны́е метки:** `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` и `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` присутствуют на **всех таблицах ядра без исключений**. Это включает:
   - справочные таблицы (catalog_supplier, catalog_product, catalog_product_attribute);
   - операционные таблицы (orders_*, procurement_*, warehouse_*);
   - immutable-снапшоты (pricing_price_calculation — `updated_at` на практике не изменяется, но колонка присутствует для единообразия);
   - журнальные таблицы (finance_ledger_entry, finance_exchange_operation, finance_exchange_rate, procurement_tracking_event);
   - коммуникационные и связующие таблицы (communications_*, включая communications_link).
   
   Обновление `updated_at` — на уровне SQLAlchemy через `onupdate=func.now()`. Все времена в UTC. Реализация — через `TimestampMixin` в `app/shared/base_model.py`, применяемый ко всем моделям без исключений.

4. **Типы:**
   - Деньги: `NUMERIC(18, 4)`.
   - Вес: `NUMERIC(10, 3)` — до тонны с точностью до грамма.
   - Курсы валют: `NUMERIC(18, 8)`.
   - Проценты (markup, tax rate): `NUMERIC(5, 2)`.
   - Текст: `TEXT` (без искусственных `VARCHAR(n)`-ограничений).
   - Валютный код: `CHAR(3)` с CHECK на формат (см. ниже «Currency: Python-тип и column helper»).
   - Enum-поля: PostgreSQL `ENUM` типы, определяются в модуле-владельце.
   - JSON-поля: `JSONB` (не `JSON`).
   - Бинарные данные (raw MIME): `BYTEA`.

5. **Currency: Python-тип и column helper (два разных артефакта).**

   В `app/shared/types.py` определяются **два отдельных артефакта**:
   
   - **`Currency`** — Python-тип (type alias `Currency = str` или NewType), используется в Pydantic-схемах и сигнатурах функций на следующем этапе. Валидация формата (3 заглавные буквы) — задача Pydantic-слоя, не SQLAlchemy.
   - **`currency_column(nullable: bool = False, **kwargs)`** — reusable helper-функция, возвращающая SQLAlchemy `mapped_column` типа `CHAR(3)` с именованным CHECK-constraint на формат (`^[A-Z]{3}$`). Используется в `models.py` модулей для всех колонок валютных кодов.
   
   Эти два артефакта не смешиваются: `Currency` — для Python/Pydantic, `currency_column()` — для определения колонок в SQLAlchemy-моделях. CHECK-constraint на уровне БД — свойство колонки, а не типа, и определяется в column helper.
   
   Пример использования в models.py (схематично):
   ```python
   from app.shared.types import currency_column
   
   class FinanceLedgerEntry(Base, TimestampMixin):
       __tablename__ = "finance_ledger_entry"
       id: Mapped[int] = mapped_column(BigInteger, primary_key=True, ...)
       amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
       currency: Mapped[str] = currency_column(nullable=False)
       ...
   ```
   
   Аналогичные helper-функции могут быть созданы для других часто повторяющихся column-паттернов (`money_column`, `weight_column`, `exchange_rate_column`, `percent_column`), но это не обязательно в рамках настоящего ADR — достаточно `currency_column`, остальные можно оставить как прямое `mapped_column(Numeric(...))`.

6. **Внешние ключи между модулями:** разрешены. Правило:
   - FK на уровне БД (`ForeignKey("catalog_product.id")`) — обязателен для целостности.
   - ORM `relationship()` между моделями разных модулей — **запрещён**. Только FK-колонка.
   - Внутри модуля `relationship()` разрешён.

7. **ON DELETE / ON UPDATE:**
   - По умолчанию — `RESTRICT` для ссылок на справочные данные (catalog_product, catalog_supplier, orders_customer).
   - `CASCADE` — для «дочерних» сущностей внутри агрегата (order_item → order, purchase_item → purchase, receipt_item → receipt, email_message → email_thread, telegram_message → telegram_chat).
   - `SET NULL` — для опциональных связей (purchase.order_id, order_item.price_calculation_id).

8. **Индексы:** на все FK — явно (SQLAlchemy не создаёт автоматически). На часто искомые поля — явно (секция «Constraints и индексы» ниже).

9. **Soft-delete** на старте не используется. Статусные поля (`status`, `is_active`) закрывают потребность. Если аудит удалений критичен — отдельный ADR.

10. **Scope настоящего ADR:** только SQLAlchemy-модели, типы (включая `Currency` и `currency_column`), enum-ы, FK, constraints, индексы, первая миграция, архитектурный тест границ. **Вне scope:** Pydantic-схемы публичных интерфейсов, их экспорт через `__init__.py`, сервисы, репозитории, роуты API. Это появится на следующем этапе.

### Карта таблиц по модулям

#### Модуль `catalog`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `catalog_supplier` | Поставщики | `id`, `name TEXT`, `website TEXT NULL`, `contact_info JSONB NULL` |
| `catalog_product` | Товары (справочник) | `id`, `supplier_id FK`, `sku TEXT NULL`, `name TEXT`, `category TEXT NULL`, `declared_weight NUMERIC(10,3) NULL`, `actual_weight NUMERIC(10,3) NULL`, `photo_url TEXT NULL` |
| `catalog_product_attribute` | Характеристики товаров | `id`, `product_id FK`, `key TEXT`, `value TEXT`, `source ENUM` |

Enum `catalog_attribute_source`: `manual`, `parsed`, `supplier`.

#### Модуль `orders`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `orders_customer` | Клиенты | `id`, `name TEXT`, `email TEXT NULL`, `phone TEXT NULL`, `telegram_id TEXT NULL` |
| `orders_customer_profile` | Расширенный профиль клиента | `id`, `customer_id FK UNIQUE`, `addresses JSONB NULL`, `notes TEXT NULL` |
| `orders_order` | Заказы клиентов | `id`, `customer_id FK`, `status ENUM`, `total_price NUMERIC(18,4) NULL`, `currency CHAR(3) NULL` |
| `orders_order_item` | Позиции заказа | `id`, `order_id FK`, `product_id FK → catalog_product`, `quantity NUMERIC(10,3)`, `unit_price NUMERIC(18,4) NULL`, `price_calculation_id FK → pricing_price_calculation NULL` |

Enum `orders_order_status`: `draft`, `confirmed`, `in_procurement`, `in_transit`, `arrived`, `ready_for_pickup`, `delivered`, `cancelled`.

#### Модуль `procurement`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `procurement_purchase` | Закупки у поставщика | `id`, `supplier_id FK → catalog_supplier`, `order_id FK → orders_order NULL`, `status ENUM`, `total_cost NUMERIC(18,4) NULL`, `currency CHAR(3) NULL` |
| `procurement_purchase_item` | Позиции закупки | `id`, `purchase_id FK`, `product_id FK → catalog_product`, `quantity NUMERIC(10,3)`, `unit_cost NUMERIC(18,4) NULL` |
| `procurement_shipment` | Отправления от поставщика | `id`, `purchase_id FK`, `tracking_number TEXT NULL`, `carrier TEXT NULL`, `shipped_at TIMESTAMPTZ NULL`, `expected_arrival TIMESTAMPTZ NULL` |
| `procurement_tracking_event` | События по треку | `id`, `shipment_id FK`, `event_at TIMESTAMPTZ`, `location TEXT NULL`, `status TEXT`, `raw_payload JSONB NULL` |

Enum `procurement_purchase_status`: `planned`, `placed`, `paid`, `shipped`, `delivered`, `cancelled`.

#### Модуль `warehouse`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `warehouse_receipt` | Поступления на склад | `id`, `shipment_id FK → procurement_shipment`, `received_at TIMESTAMPTZ`, `notes TEXT NULL` |
| `warehouse_receipt_item` | Позиции поступления | `id`, `receipt_id FK`, `product_id FK → catalog_product`, `quantity NUMERIC(10,3)`, `actual_weight_per_unit NUMERIC(10,3) NULL` |
| `warehouse_stock_item` | Текущие остатки | `id`, `product_id FK → catalog_product`, `quantity NUMERIC(10,3)`, `location TEXT NULL` |
| `warehouse_reservation` | Резервы под заказы клиентов | `id`, `order_item_id FK → orders_order_item`, `stock_item_id FK`, `quantity NUMERIC(10,3)`, `reserved_at TIMESTAMPTZ`, `released_at TIMESTAMPTZ NULL` |

#### Модуль `pricing`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `pricing_exchange_rate` | Расчётный курс для клиентских цен | `id`, `from_currency CHAR(3)`, `to_currency CHAR(3)`, `rate NUMERIC(18,8)`, `markup_percent NUMERIC(5,2) NULL`, `valid_from TIMESTAMPTZ`, `source ENUM` |
| `pricing_price_calculation` | Снимок расчёта цены (immutable) | `id`, `product_id FK → catalog_product`, `input_params JSONB`, `breakdown JSONB`, `final_price NUMERIC(18,4)`, `currency CHAR(3)`, `calculated_at TIMESTAMPTZ`, `formula_version TEXT` |

Enum `pricing_exchange_rate_source`: `api`, `manual`.

Примечания:

- Направление связи: `orders_order_item.price_calculation_id` — FK (nullable, ON DELETE SET NULL) → `pricing_price_calculation.id`. Один `price_calculation` может использоваться несколькими `order_item` при идентичных параметрах.
- `pricing_price_calculation` — immutable: поля `input_params`, `breakdown`, `final_price` не меняются после создания (enforced в коде, не в БД). Колонки `created_at`/`updated_at` присутствуют для единообразия (п.3 общих конвенций), хотя `updated_at` на практике не будет изменяться.

#### Модуль `communications`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `communications_email_thread` | Email-треды | `id`, `gmail_thread_id TEXT UNIQUE`, `subject TEXT NULL`, `participants JSONB NULL`, `last_message_at TIMESTAMPTZ NULL` |
| `communications_email_message` | Email-сообщения | `id`, `thread_id FK`, `gmail_message_id TEXT UNIQUE`, `from_address TEXT`, `to_addresses JSONB NULL`, `sent_at TIMESTAMPTZ`, `raw_mime BYTEA NULL`, `parsed_body TEXT NULL`, `headers JSONB NULL` |
| `communications_telegram_chat` | Telegram-чаты | `id`, `telegram_chat_id TEXT UNIQUE`, `chat_type TEXT`, `title TEXT NULL` |
| `communications_telegram_message` | Telegram-сообщения | `id`, `chat_id FK`, `telegram_message_id TEXT`, `from_user_id TEXT NULL`, `sent_at TIMESTAMPTZ`, `text TEXT NULL`, `raw_payload JSONB NULL` |
| `communications_link` | Связь сообщения с бизнес-сущностью | `id`, `email_message_id FK NULL`, `telegram_message_id FK NULL`, `target_module ENUM`, `target_entity TEXT`, `target_id BIGINT`, `link_confidence ENUM` |

Enum `communications_link_target_module`: `catalog`, `orders`, `procurement`, `warehouse`. (Модуль `pricing` исключён — переписка не привязывается к расчётам цен напрямую, только к связанным с ними заказам. Модули `finance`, `communications`, `api` также не являются target.)

Enum `communications_link_confidence`: `manual`, `auto`, `suggested`.

**Источник связи (`source`):** два nullable FK (`email_message_id`, `telegram_message_id`) с CHECK-constraint «ровно одно заполнено»:

```sql
CHECK (
  (email_message_id IS NOT NULL AND telegram_message_id IS NULL)
  OR
  (email_message_id IS NULL AND telegram_message_id IS NOT NULL)
)
```

Это даёт FK-целостность на source без отдельного enum `source_type`.

**Назначение (`target`):** полиморфно (`target_module`, `target_entity`, `target_id`) без FK. Целостность target обеспечивается модулем `communications`.

#### Модуль `finance`

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `finance_ledger_entry` | Записи финансового журнала | `id`, `entry_at TIMESTAMPTZ`, `entry_type ENUM`, `amount NUMERIC(18,4)`, `currency CHAR(3)`, `description TEXT NULL`, `related_module TEXT NULL`, `related_entity TEXT NULL`, `related_id BIGINT NULL` |
| `finance_expense` | Расходы | `id`, `ledger_entry_id FK UNIQUE`, `category ENUM`, `supplier_id FK → catalog_supplier NULL`, `purchase_id FK → procurement_purchase NULL` |
| `finance_tax_entry` | Налоговые начисления | `id`, `ledger_entry_id FK UNIQUE`, `tax_type ENUM`, `period TEXT`, `base_amount NUMERIC(18,4)`, `tax_amount NUMERIC(18,4)` |
| `finance_exchange_operation` | Валютные операции | `id`, `operated_at TIMESTAMPTZ`, `from_currency CHAR(3)`, `from_amount NUMERIC(18,4)`, `to_currency CHAR(3)`, `to_amount NUMERIC(18,4)`, `bank_exchange_rate_id FK`, `bank TEXT NULL` |
| `finance_exchange_rate` | Фактический курс (BankExchangeRate) | `id`, `from_currency CHAR(3)`, `to_currency CHAR(3)`, `rate NUMERIC(18,8)`, `observed_at TIMESTAMPTZ`, `source ENUM`, `bank TEXT NULL` |

Enum `finance_entry_type`: `income`, `expense`, `transfer`, `exchange`.
Enum `finance_expense_category`: `purchase`, `logistics`, `packaging`, `commission`, `tax`, `other`.
Enum `finance_tax_type`: на этапе core schema — один placeholder `general`, расширяется при реализации модуля finance.
Enum `finance_exchange_rate_source`: `bank_statement`, `manual`.

Полиморфная связь `finance_ledger_entry.related_*` — без FK. Разрешённые `related_module`: `orders`, `procurement`.

#### Модуль `api`

**Не владеет таблицами.** `models.py` для `app/api/` не создаётся.

### Constraints и индексы

Ниже — явные ограничения и индексы. Всё, что не указано, — стандартные: PK на `id`, `NOT NULL` для FK-колонок (где не указано NULL), `created_at`/`updated_at` NOT NULL на всех таблицах.

#### catalog

- `catalog_supplier`
  - UNIQUE `name`.
- `catalog_product`
  - UNIQUE `(supplier_id, sku)` WHERE `sku IS NOT NULL`.
  - INDEX `supplier_id`.
  - INDEX `category`.
  - CHECK `declared_weight > 0 OR declared_weight IS NULL`.
  - CHECK `actual_weight > 0 OR actual_weight IS NULL`.
- `catalog_product_attribute`
  - UNIQUE `(product_id, key)`.
  - INDEX `product_id`.

#### orders

- `orders_customer`
  - UNIQUE `email` WHERE `email IS NOT NULL`.
  - UNIQUE `telegram_id` WHERE `telegram_id IS NOT NULL`.
  - CHECK `email IS NOT NULL OR phone IS NOT NULL OR telegram_id IS NOT NULL`.
- `orders_customer_profile`
  - UNIQUE `customer_id`.
- `orders_order`
  - INDEX `customer_id`.
  - INDEX `status`.
  - INDEX `created_at`.
  - CHECK `total_price >= 0 OR total_price IS NULL`.
  - `currency` — через `currency_column(nullable=True)` (CHECK на формат зашит в helper).
- `orders_order_item`
  - INDEX `order_id`.
  - INDEX `product_id`.
  - INDEX `price_calculation_id` WHERE `price_calculation_id IS NOT NULL`.
  - CHECK `quantity > 0`.
  - CHECK `unit_price >= 0 OR unit_price IS NULL`.

#### procurement

- `procurement_purchase`
  - INDEX `supplier_id`.
  - INDEX `order_id` WHERE `order_id IS NOT NULL`.
  - INDEX `status`.
  - CHECK `total_cost >= 0 OR total_cost IS NULL`.
  - `currency` — через `currency_column(nullable=True)`.
- `procurement_purchase_item`
  - INDEX `purchase_id`.
  - INDEX `product_id`.
  - CHECK `quantity > 0`.
  - CHECK `unit_cost >= 0 OR unit_cost IS NULL`.
- `procurement_shipment`
  - INDEX `purchase_id`.
  - INDEX `tracking_number` WHERE `tracking_number IS NOT NULL`.
- `procurement_tracking_event`
  - INDEX `shipment_id`.
  - INDEX `event_at`.

#### warehouse

- `warehouse_receipt`
  - INDEX `shipment_id`.
- `warehouse_receipt_item`
  - INDEX `receipt_id`.
  - INDEX `product_id`.
  - CHECK `quantity > 0`.
  - CHECK `actual_weight_per_unit > 0 OR actual_weight_per_unit IS NULL`.
- `warehouse_stock_item`
  - UNIQUE `(product_id, location)`.
  - INDEX `product_id`.
  - CHECK `quantity >= 0`.
- `warehouse_reservation`
  - INDEX `order_item_id`.
  - INDEX `stock_item_id`.
  - INDEX `released_at` WHERE `released_at IS NULL`.
  - CHECK `quantity > 0`.

#### pricing

- `pricing_exchange_rate`
  - INDEX `(from_currency, to_currency, valid_from DESC)`.
  - CHECK `rate > 0`.
  - `from_currency`, `to_currency` — через `currency_column(nullable=False)`.
  - CHECK `from_currency <> to_currency`.
  - CHECK `markup_percent >= 0 OR markup_percent IS NULL`.
- `pricing_price_calculation`
  - INDEX `product_id`.
  - INDEX `calculated_at`.
  - CHECK `final_price >= 0`.
  - `currency` — через `currency_column(nullable=False)`.

#### communications

- `communications_email_thread`
  - UNIQUE `gmail_thread_id`.
- `communications_email_message`
  - UNIQUE `gmail_message_id`.
  - INDEX `thread_id`.
  - INDEX `from_address`.
  - INDEX `sent_at`.
- `communications_telegram_chat`
  - UNIQUE `telegram_chat_id`.
- `communications_telegram_message`
  - UNIQUE `(chat_id, telegram_message_id)`.
  - INDEX `chat_id`.
  - INDEX `sent_at`.
- `communications_link`
  - CHECK «ровно одно из `email_message_id` и `telegram_message_id` заполнено» (см. SQL выше).
  - INDEX `email_message_id` WHERE `email_message_id IS NOT NULL`.
  - INDEX `telegram_message_id` WHERE `telegram_message_id IS NOT NULL`.
  - INDEX `(target_module, target_entity, target_id)`.

#### finance

- `finance_ledger_entry`
  - INDEX `entry_at`.
  - INDEX `entry_type`.
  - INDEX `(related_module, related_entity, related_id)` WHERE `related_id IS NOT NULL`.
  - `currency` — через `currency_column(nullable=False)`.
- `finance_expense`
  - UNIQUE `ledger_entry_id`.
  - INDEX `category`.
  - INDEX `supplier_id` WHERE `supplier_id IS NOT NULL`.
  - INDEX `purchase_id` WHERE `purchase_id IS NOT NULL`.
- `finance_tax_entry`
  - UNIQUE `ledger_entry_id`.
  - INDEX `(tax_type, period)`.
  - CHECK `base_amount >= 0`.
  - CHECK `tax_amount >= 0`.
- `finance_exchange_operation`
  - INDEX `operated_at`.
  - INDEX `bank_exchange_rate_id`.
  - CHECK `from_amount > 0`.
  - CHECK `to_amount > 0`.
  - CHECK `from_currency <> to_currency`.
  - `from_currency`, `to_currency` — через `currency_column(nullable=False)`.
- `finance_exchange_rate`
  - INDEX `(from_currency, to_currency, observed_at DESC)`.
  - CHECK `rate > 0`.
  - `from_currency`, `to_currency` — через `currency_column(nullable=False)`.
  - CHECK `from_currency <> to_currency`.

### Сводка FK-связей между модулями

```
catalog_product          ← orders_order_item
catalog_product          ← procurement_purchase_item
catalog_product          ← warehouse_receipt_item
catalog_product          ← warehouse_stock_item
catalog_product          ← pricing_price_calculation
catalog_supplier         ← catalog_product
catalog_supplier         ← procurement_purchase
catalog_supplier         ← finance_expense

orders_customer          ← orders_customer_profile
orders_customer          ← orders_order
orders_order             ← procurement_purchase        (optional)
orders_order_item        → pricing_price_calculation   (optional, FK nullable)
orders_order_item        ← warehouse_reservation

procurement_purchase     ← procurement_purchase_item
procurement_purchase     ← procurement_shipment
procurement_purchase     ← finance_expense              (optional)
procurement_shipment     ← procurement_tracking_event
procurement_shipment     ← warehouse_receipt

warehouse_receipt        ← warehouse_receipt_item
warehouse_stock_item     ← warehouse_reservation

finance_ledger_entry     ← finance_expense
finance_ledger_entry     ← finance_tax_entry
finance_exchange_rate    ← finance_exchange_operation

communications_email_message    ← communications_link (nullable FK)
communications_telegram_message ← communications_link (nullable FK)
communications_email_thread     ← communications_email_message
communications_telegram_chat    ← communications_telegram_message
```

Полиморфные связи (без FK на target):

- `communications_link.(target_module, target_entity, target_id)` → любая сущность в `catalog`, `orders`, `procurement`, `warehouse`.
- `finance_ledger_entry.(related_module, related_entity, related_id)` → любая сущность в `orders`, `procurement`.

### Миграции

- Первая миграция создаёт все таблицы, enum-ы, constraints и индексы ядра одновременно.
- Последующие миграции — по мере реализации модулей, автогенерация через `alembic revision --autogenerate`.
- `alembic/env.py` импортирует `Base` из `app/shared/base_model.py` и модели из всех 7 модулей с таблицами (catalog, orders, procurement, warehouse, pricing, communications, finance). Модуль `api` не импортируется.

## Последствия

**Что становится проще:**

- Имена таблиц самоописательны: `finance_exchange_rate` однозначно принадлежит finance.
- Коллизии исключены (`pricing_exchange_rate` и `finance_exchange_rate` — разные таблицы).
- Alembic автогенерирует миграции для всей схемы без специальных настроек.
- NUMERIC для денег, весов, курсов исключает ошибки округления.
- `communications_link.source` с двумя FK + CHECK даёт целостность на уровне БД.
- `currency_column()` helper обеспечивает единый формат CHECK-constraint во всех таблицах и убирает дублирование.
- Правило timestamps без исключений — нет споров и отдельных обсуждений для каждой новой таблицы.
- Scope ADR узкий — только core schema, Pydantic-слой отложен.

**Какие ограничения появляются:**

- Префикс в каждом имени таблицы — небольшой overhead в длине запросов.
- Правило «FK да, relationship — нет» требует дисциплины. Закрепляется архитектурным тестом.
- Полиморфные связи на target не имеют FK — целостность только кодом.
- JSONB-поля не валидируются БД — валидация появится с Pydantic-слоем.
- Immutable pricing_price_calculation enforced только в коде.
- `updated_at` на immutable-таблицах (pricing_price_calculation) — «мёртвая» колонка, но единообразие перевешивает.

**Что придётся учитывать дальше:**

- Первая миграция будет большой. Обязательная ручная проверка до применения.
- Pydantic-схемы публичных интерфейсов — следующий этап.
- Мониторинг orphaned полиморфных ссылок — задача модулей-владельцев.
- Эволюция формулы pricing требует миграции старых price_calculation через `formula_version`.
- Enum `finance_tax_type` расширяется при реализации модуля finance.
- Soft-delete и аудит — отдельный ADR при необходимости.

## Что должен сделать Claude Code

1. Создать `app/shared/base_model.py`: `Base(DeclarativeBase)`, `TimestampMixin` (`created_at`, `updated_at` с `onupdate=func.now()`), базовый паттерн `id BIGINT` identity PK. `TimestampMixin` применяется ко **всем** моделям ядра без исключений.
2. Создать `app/shared/types.py`:
   - Python-типы: `Money`, `Weight`, `ExchangeRate`, `Percent`, `Currency` (как type alias / NewType).
   - Reusable column helper `currency_column(nullable: bool = False, **kwargs)` — возвращает SQLAlchemy `mapped_column(CHAR(3), ...)` с именованным CHECK-constraint `^[A-Z]{3}$`.
   - `Currency` как Python-тип и `currency_column()` как SQLAlchemy helper — **два разных артефакта**, не смешивать.
3. Создать `app/shared/__init__.py` с экспортом `Base`, `TimestampMixin`, Python-типов и `currency_column`. Это общая инфраструктура, экспорт допустим на этапе core schema.
4. В каждом из 7 модулей (catalog, orders, procurement, warehouse, pricing, communications, finance) создать `models.py` со всеми таблицами по секции «Карта таблиц». Имена: `{module}_{singular}`, PK `id`, все модели наследуют `TimestampMixin`.
5. **Не создавать** `app/api/models.py` — модуль `api` не владеет таблицами.
6. **Не экспортировать** Pydantic-схемы через `__init__.py` модулей в рамках этого ADR. В `__init__.py` модулей оставить текущий docstring и `__all__ = []`.
7. Использовать `currency_column()` для всех колонок валютных кодов во всех моделях.
8. Определить enum-типы PostgreSQL в models.py модулей-владельцев: `catalog_attribute_source`, `orders_order_status`, `procurement_purchase_status`, `pricing_exchange_rate_source`, `communications_link_target_module`, `communications_link_confidence`, `finance_entry_type`, `finance_expense_category`, `finance_tax_type`, `finance_exchange_rate_source`.
9. Реализовать все FK из «Сводки FK-связей» с ON DELETE / ON UPDATE правилами. **Запрет:** `relationship()` между моделями разных модулей.
10. Реализовать все UNIQUE, INDEX, CHECK constraints из секции «Constraints и индексы» через `__table_args__`.
11. В `communications_link` — два nullable FK (`email_message_id`, `telegram_message_id`) и CHECK «ровно одно заполнено».
12. В `orders_order_item` — FK `price_calculation_id` → `pricing_price_calculation.id` (nullable, ON DELETE SET NULL).
13. Обновить `alembic/env.py`: импортировать `Base` из `app.shared.base_model`, импортировать модели из 7 модулей (не из `api`), `target_metadata = Base.metadata`.
14. Сгенерировать первую миграцию: `alembic revision --autogenerate -m "initial core schema"`. Провести ручную проверку по чек-листу: таблицы, префиксы, NUMERIC, enum-ы, FK, constraints, индексы, наличие `created_at`/`updated_at` на **всех** таблицах.
15. Применить миграцию: `alembic upgrade head`. Проверить откат: `alembic downgrade base && alembic upgrade head`.
16. Написать `tests/test_module_boundaries.py`: через рефлексию SQLAlchemy metadata для каждой модели собрать `relationship()` и убедиться, что target относится к тому же модулю (сравнение по префиксу `__tablename__`).
17. Обновить корневой `README.md`: добавить раздел «Схема данных» со ссылкой на ADR-003 и краткой картой таблиц.
18. **Не создавать и не изменять** файлы в `docs/adr/`. ADR-001 v2, ADR-002 v2, ADR-003 уже находятся там (см. Precondition).

## Что проверить вручную

- `\dt` в psql — все таблицы с префиксами модулей.
- `\d catalog_product` — `declared_weight` и `actual_weight` оба `NUMERIC(10,3)`, nullable.
- `\d pricing_exchange_rate` и `\d finance_exchange_rate` — разные таблицы, pricing с `markup_percent`, finance с `bank`.
- `\d orders_order_item` — FK `price_calculation_id` → `pricing_price_calculation.id` существует, nullable, ON DELETE SET NULL.
- `\d communications_link` — `email_message_id` и `telegram_message_id` оба nullable, CHECK «ровно одно заполнено» присутствует.
- `\dT+` — все enum-типы. `communications_link_target_module` содержит только `catalog`, `orders`, `procurement`, `warehouse`.
- Проверить, что `created_at` и `updated_at` присутствуют на **всех** таблицах ядра без исключений (включая `pricing_price_calculation`, `communications_link`, `procurement_tracking_event`, `finance_ledger_entry`).
- Проверить, что все колонки валютных кодов — `CHAR(3)` с CHECK-constraint на формат.
- Проверить, что `Currency` (Python-тип) и `currency_column()` (SQLAlchemy helper) — два разных артефакта в `app/shared/types.py`, не объединены в один «гибридный тип».
- `alembic downgrade base && alembic upgrade head` — работает в обе стороны.
- `pytest tests/test_module_boundaries.py` — проходит.
- Денежные поля `NUMERIC(18,4)`, веса `NUMERIC(10,3)`, курсы `NUMERIC(18,8)`.
- `app/api/models.py` **не существует**.
- `__init__.py` модулей не содержит экспорта Pydantic-схем (docstring + `__all__ = []`).
- `git diff docs/adr/` пуст — ADR-файлы не изменены (см. Precondition).
- В репозитории нет `requirements.txt` и `requirements-dev.txt`.
- `/health` по-прежнему возвращает `{"status": "ok"}`.
- `ruff check app/` и `mypy app/` проходят без новых ошибок.
