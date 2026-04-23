# Current Stack

## Операционное окружение

- macOS
- Python 3.12+
- PostgreSQL 16 в Docker (контейнер pili-crm-postgres-1, порт 5432→5432)
- Docker Compose из /Users/protey/pili-crm

## Бэкенд

- FastAPI ≥ 0.111
- SQLAlchemy 2.0 async
- Alembic ≥ 1.13
- Pydantic v2
- APScheduler ≥ 3.10

## Интерфейс оператора

- Claude Cowork (desktop) — основной интерфейс ежедневной работы
- MCP-сервер crm-mcp/ (server.py) — связь между Cowork и PostgreSQL
- 9 MCP-tools: list_customers, find_customer, create_customer, create_order,
  search_products, pending_orders, add_to_stock, update_order_item_status, match_shipment

## Запуск

FastAPI: `cd /Users/protey/pili-crm && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
(обязательно перед работой с Cowork — нужен для derive-status)

## Архитектурные решения

- ADR-001 v2: модульный монолит, 8 модулей
- ADR-002: Python/FastAPI/SQLAlchemy/Alembic
- ADR-003: core schema PostgreSQL (26 таблиц)
- ADR-003 Addendum: статусы заказов и позиций
- ADR-004: pricing & profit policy
