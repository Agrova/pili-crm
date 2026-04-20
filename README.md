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
| `catalog` | `catalog_supplier`, `catalog_product`, `catalog_product_attribute` |
| `orders` | `orders_customer`, `orders_customer_profile`, `orders_order`, `orders_order_item` |
| `procurement` | `procurement_purchase`, `procurement_purchase_item`, `procurement_shipment`, `procurement_tracking_event` |
| `warehouse` | `warehouse_receipt`, `warehouse_receipt_item`, `warehouse_stock_item`, `warehouse_reservation` |
| `pricing` | `pricing_exchange_rate`, `pricing_price_calculation` |
| `communications` | `communications_email_thread`, `communications_email_message`, `communications_telegram_chat`, `communications_telegram_message`, `communications_link` |
| `finance` | `finance_ledger_entry`, `finance_expense`, `finance_tax_entry`, `finance_exchange_rate`, `finance_exchange_operation` |

Enum-типы PostgreSQL (10 штук): `catalog_attribute_source`, `orders_order_status`,
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

## Verify

```bash
ruff check .
mypy app/
pytest --collect-only
```
