# ПилиСтрогай CRM

Modular monolith for a single-operator tool shop. Handles orders, procurement, warehouse, pricing, communications, and finance.

## Module Map

| Module | Zone | Key Entities |
|--------|------|--------------|
| `catalog` | Reference data | Product, Supplier, ProductAttribute |
| `orders` | Customer lifecycle | Order, OrderItem, Customer, CustomerProfile |
| `procurement` | Supplier ops + logistics | Purchase, Shipment, TrackingEvent |
| `warehouse` | Stock management | WarehouseReceipt, StockItem, Reservation |
| `pricing` | Price calculation | PriceCalculation, PricingExchangeRate |
| `communications` | Gmail + Telegram | EmailThread, EmailMessage, TelegramChat, TelegramMessage |
| `finance` | Financial accounting | FinancialLedgerEntry, Expense, TaxEntry, ExchangeOperation, BankExchangeRate |
| `api` | HTTP API | Routers for AnythingLLM tool calls + admin panel |
| `shared` | Common types | ID types, base models, utilities |

## Exchange Rate Model

| Type | Owner | Purpose |
|------|-------|---------|
| `BankExchangeRate` | `finance` | Actual rate used in real money exchange (from bank statement) |
| `PricingExchangeRate` | `pricing` | Rate used for customer price calculation (may include markup) |

## Dependency Graph

```
orders       → catalog, pricing, warehouse, procurement
procurement  → catalog
warehouse    → procurement
pricing      → catalog, finance
communications → orders, catalog, procurement, warehouse
finance      → orders, procurement
api          → all modules
shared       ← all modules (no outbound deps)
```

## Principles (ADR-001 v2)

- Public `__init__.py` interfaces only — no cross-module direct SQL
- Single operator — no auth yet
- SQLAlchemy 2.0 async throughout
- Alembic migrations only (no `create_all`)

## Схема данных

Полная спецификация: [`docs/adr/ADR-003-postgres-core-schema.md`](docs/adr/ADR-003-postgres-core-schema.md).

Единая схема `public`, таблицы с префиксами модулей (`{module}_{entity}`).

| Модуль | Таблицы |
|--------|---------|
| `catalog` | `catalog_supplier`, `catalog_product`, `catalog_product_attribute`, `catalog_product_listing`, `catalog_listing_price` |
| `orders` | `orders_customer`, `orders_customer_profile`, `orders_order`, `orders_order_item` |
| `procurement` | `procurement_purchase`, `procurement_purchase_item`, `procurement_shipment`, `procurement_tracking_event` |
| `warehouse` | `warehouse_receipt`, `warehouse_receipt_item`, `warehouse_stock_item`, `warehouse_reservation`, `warehouse_pending_price_resolution` |
| `pricing` | `pricing_exchange_rate`, `pricing_price_calculation` |
| `communications` | `communications_email_thread`, `communications_email_message`, `communications_telegram_chat`, `communications_telegram_message`, `communications_link` |
| `finance` | `finance_ledger_entry`, `finance_expense`, `finance_tax_entry`, `finance_exchange_rate`, `finance_exchange_operation` |

Вьюхи: `v_listing_last_price` (последняя цена по листингу), `v_product_current_price`
(сводка цен по товару: `min_last_price`, `primary_last_price`, `last_observation_at`).

Enum-типы PostgreSQL (13 штук): `catalog_attribute_source`, `catalog_source_kind`,
`catalog_price_source`, `orders_order_status`, `orders_order_item_status`,
`procurement_purchase_status`, `pricing_exchange_rate_source`,
`communications_link_target_module`, `communications_link_confidence`,
`finance_entry_type`, `finance_expense_category`, `finance_tax_type`,
`finance_exchange_rate_source`.

Применение миграций:

```bash
docker-compose up -d postgres
alembic upgrade head
```

## Quick Start

```bash
cp .env.example .env
docker-compose up -d         # PostgreSQL 16
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
# → GET http://localhost:8000/health  →  {"status": "ok"}
```

## Pricing Policy (ADR-004)

Полная спецификация: [`docs/adr/ADR-004-pricing-profit-policy.md`](docs/adr/ADR-004-pricing-profit-policy.md). Детали реализации: [`app/pricing/README.md`](app/pricing/README.md).

**Две ветки формулы:**

| Ветка | Базовая стоимость |
|-------|------------------|
| `retail` | `purchase_cost_rub + weight_kg × shipping_per_kg_usd × rate` |
| `manufacturer` | `product_price_fcy × rate + logistics_legs + customs + intermediary` |

**Общий pipeline:** `base_cost → margin (20% default) → discount (опц.) → rounding (ceiling)`

**Rounding:** шаг 10 для цен < 1000 RUB, шаг 100 для цен ≥ 1000 RUB. Operator override допустим.

**Два слоя:**
- *Planned* — immutable snapshot `pricing_price_calculation` с полным breakdown JSONB (модуль `pricing`)
- *Actual* — вычисляется запросом по данным `finance` (фактический курс, банковская комиссия, overhead)

## MVP: shipment matching

Сценарий: оператор сообщает список товаров из пришедшей поставки, а API отвечает
«этот для клиента X по заказу Y, этот без заказа». Используется как tool из
AnythingLLM.

### Подготовка данных

```bash
# источники (не в git — см. .gitignore → data/seed/)
cp "/path/to/CRM late.xlsx" data/seed/CRM_late.xlsx
cp /path/to/tg_scan_results.json data/seed/

docker-compose up -d postgres
alembic upgrade head
pip install -e ".[dev]"

PYTHONPATH=. python3 scripts/seed_mvp.py
```

Скрипт идемпотентен: повторный запуск обнуляет производные таблицы
(`catalog_*`, `orders_*`) и заливает их заново из Excel. 36 клиентов, 62 заказа,
133 позиции, ~128 товаров, ~23 поставщика. Детали — [scripts/README.md](scripts/README.md).

### Endpoints

| Метод | Путь | Назначение |
|-------|------|------------|
| `GET` | `/api/v1/customers` | Список клиентов + долг/кол-во заказов |
| `GET` | `/api/v1/orders/pending` | Заказы с незакрытыми позициями |
| `GET` | `/api/v1/products/search?q=veritas` | Fuzzy-поиск товаров (pg_trgm) |
| `POST` | `/api/v1/shipment/match` | Сопоставление поставки с ожидающими заказами |

`POST /api/v1/shipment/match` принимает `{"items": ["Veritas Shooting Board", ...]}`
и возвращает три секции: `matched` (точное совпадение + приоритет самого раннего
заказа), `ambiguous` (несколько кандидатов, требуется выбор оператора), `unmatched`.
Полная схема — Swagger UI по адресу `/docs`.

### Система статусов (ADR-003 Addendum)

Два независимых enum — статус позиции и статус заказа. Статус заказа вычисляется
автоматически из статусов позиций.

**Item-level** (`orders_order_item_status`):

| Статус | Описание | Shipment matching |
|--------|----------|-------------------|
| `pending` | Нужно заказать | ✅ |
| `ordered` | Заказан у поставщика | ✅ |
| `shipped` | Поставщик отправил | ✅ |
| `at_forwarder` | Получен форвардером (США) | ✅ |
| `arrived` | Получен на склад в Москве | — |
| `delivered` | Передан клиенту | — |
| `cancelled` | Отменён | — |

**Order-level** (`orders_order_status`):

`draft → confirmed → in_procurement → shipped_by_supplier → received_by_forwarder → arrived → delivered`  
`+ cancelled` (из любого состояния)

**Derivation rule** (ADR-006, миграция `4f8fe83398af`):  
`order.status = map(MIN(active_item_statuses))`. Реализован как **PostgreSQL-триггер**
(`AFTER UPDATE OF status / INSERT / DELETE ON orders_order_item`). Срабатывает
атомарно в той же транзакции, что и изменение позиции — статус заказа гарантированно
консистентен независимо от доступности FastAPI.

Python-функция `app/orders/service.py::derive_order_status` сохранена для
обратной совместимости (seed, unit-тесты).

## MCP-сервер для AnythingLLM

Отдельный пакет [`crm-mcp/`](crm-mcp/README.md) — stdio-сервер по протоколу
MCP, который AnythingLLM запускает как дочерний процесс. Предоставляет 9
инструментов: `match_shipment`, `pending_orders`, `list_customers`,
`search_products`, `add_to_stock`, `update_order_item_status`,
`find_customer`, `create_customer`, `create_order`.

Read-only инструменты читают БД напрямую через SQLAlchemy async (без HTTP-слоя).
Write-инструменты (`create_customer`, `create_order`) также работают через SQLAlchemy
и требуют явного подтверждения оператора перед записью (**правило двух подтверждений**).
`update_order_item_status` обновляет статус позиции; PostgreSQL-триггер (ADR-006)
автоматически пересчитывает статус заказа в той же транзакции. HTTP-вызов
`/derive-status` не используется.

### Сценарии использования

| Что говорит оператор | Инструмент |
|----------------------|------------|
| «Пришла поставка: Veritas, Shapton, Pfeil» | `match_shipment` |
| «Shapton не ожидает ни один клиент» | `add_to_stock` |
| «Veritas передан клиенту» | `update_order_item_status` |
| «Покажи активные заказы» | `pending_orders` |
| «Найди клиента Воропаев» | `find_customer` |
| «Добавь нового клиента Иванов» | `create_customer` + подтверждение |
| «Создай заказ для клиента 42» | `create_order` + подтверждение |

См. [crm-mcp/README.md](crm-mcp/README.md) — подробное описание, схема
инструментов, правило двух подтверждений и инструкция подключения к AnythingLLM.

## Ценообразование при поступлении товара (ADR-007/008)

### Инвариант «одна цена на SKU»

На складе хранится одна `pricing_price_calculation` на каждую позицию (`product_id + location`). При поступлении нового товара система выбирает один из трёх сценариев:

| Ситуация | Действие |
|----------|----------|
| Товара на складе нет | Создаётся `warehouse_stock_item` с ценой нового поступления |
| Цена совпадает (в пределах шага округления) | `quantity` объединяется, цена не меняется |
| Цена отличается | Создаётся `warehouse_pending_price_resolution`, stock_item не меняется |

### Hook-и (Python-функции, не PostgreSQL-триггеры)

**Hook 2a — `on_purchase_delivered(purchase_id, session)`** ([app/procurement/services.py](app/procurement/services.py)):
- Вызывается при переходе `procurement_purchase.status → delivered`
- Записывает `catalog_listing_price` (source='purchase') для каждой позиции с известной ценой
- Идемпотентен: повторный вызов определяется через `delivered_at`

**Hook 2b — `on_warehouse_receipt_item_created(receipt_item_id, session)`** ([app/warehouse/services.py](app/warehouse/services.py)):
- Вызывается после `session.flush()` при создании `warehouse_receipt_item`
- Строит `PricingPriceCalculation` (retail или manufacturer, в зависимости от `supplier.default_purchase_type`)
- Применяет алгоритм ADR-008: создаёт stock_item, объединяет или создаёт pending
- Всегда записывает `catalog_listing_price` (независимо от складского решения)

### Разрешение конфликтов (Пакет 3 — не реализован)

`warehouse_pending_price_resolution` хранит конфликт до явного решения оператора. Три варианта разрешения: `keep_old`, `use_new`, `weighted_average`. Вспомогательная функция `calculate_weighted_price` ([app/pricing/service.py](app/pricing/service.py)) реализована и протестирована.

## Verify

```bash
ruff check app/
mypy app/
pytest tests/ -v
```
