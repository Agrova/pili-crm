# ADR-002: Python / FastAPI / SQLAlchemy

**Статус:** Принято  
**Дата:** 2026-04-15

## Решение

| Компонент | Выбор | Версия |
|-----------|-------|--------|
| Язык | Python | 3.12+ |
| HTTP-фреймворк | FastAPI | ≥ 0.111 |
| ORM | SQLAlchemy async | 2.0 |
| Миграции | Alembic | ≥ 1.13 |
| Валидация | Pydantic v2 | ≥ 2.7 |
| Планировщик | APScheduler | ≥ 3.10 |
| Линтер | Ruff | ≥ 0.4 |
| Типизация | mypy (strict) | ≥ 1.10 |
| Тесты | pytest + pytest-asyncio | ≥ 8.2 |

## Правила

- SQLAlchemy 2.0 async везде, `create_all` не использовать — только Alembic
- `alembic.ini`: `sqlalchemy.url` пустой, URL инжектируется через `env.py` из `app.config.settings`
- Pydantic v2: `model_config = SettingsConfigDict(...)` вместо `class Config`
- Тесты: `asyncio_mode = "auto"` в `pytest.ini_options`
