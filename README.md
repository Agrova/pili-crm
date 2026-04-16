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

## Quick Start

```bash
cp .env.example .env
docker-compose up -d         # PostgreSQL 16
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# → GET http://localhost:8000/health  →  {"status": "ok"}
```

## Verify

```bash
ruff check .
mypy app/
pytest --collect-only
```
