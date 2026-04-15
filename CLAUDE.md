# PiliStrogai CRM - Claude Code Rules

## ADR-001 v2: 8 Modules Modular Monolith
catalog/
orders/
procurement/
warehouse/
pricing/
communications/
finance/
api/
shared/

## ADR-002: Python/FastAPI/SQLAlchemy
- Python 3.12+
- FastAPI
- SQLAlchemy 2.0 async
- Alembic
- Pydantic v2
- APScheduler
- Ruff + mypy + pytest

## Rules
- Public __init__.py interfaces only
- No cross-module direct SQL
- Single operator, no auth yet

## Workflow
Plan → Implement → Verify → Commit

## Verify always
- ruff check --fix .
- pytest --collect-only
- docker-compose up postgres
