# Scope

## Правила работы с этим файлом

- Каждый чат смотрит только свои задачи (по полю "Чат").
- Главный архитектор ("Архитектурный чат Cowork: план работ") просматривает
  весь файл, меняет приоритеты и статусы.
- При реализации задачи — переносить из "В планах" / "В работе" в "Сделано".

---

## Сделано

| Задача | Чат |
|---|---|
| Архитектура и выбор БД (ADR-001–004) | Архитектурный советник CRM ПилиСтрогай |
| Core schema PostgreSQL (26 таблиц) | Архитектурный советник CRM ПилиСтрогай |
| Pricing engine (формула, тесты) | Prompt Factory for Claude Code |
| Seed MVP (36 клиентов, 128 товаров, 62 заказа, 133 позиции) | Prompt Factory for Claude Code |
| MCP-сервер + 9 tools | Prompt Factory for Claude Code |
| Системный промт Cowork | Архитектурный советник CRM ПилиСтрогай |
| Smoke-test runbook | Архитектурный советник CRM ПилиСтрогай |
| Настройка и тестирование Cowork + MCP | Архитектурный чат Cowork: план работ |
| Google Sheets-зеркало БД (ADR-005) | Prompt Factory for Claude Code |
| PostgreSQL-триггер derive-status (ADR-006) | Prompt Factory for Claude Code |
| Listings + price history, Пакеты 1–2 (ADR-007/008) | Prompt Factory for Claude Code |
| Telegram profile schema — миграция (ADR-009) | Prompt Factory for Claude Code |
| Reply column + media metadata (ADR-010 addendum) | Prompt Factory for Claude Code |
| Telegram historical import (ADR-010 Задание 1) | Prompt Factory for Claude Code |

---

## В работе

| Задача | Чат | Приоритет |
|---|---|---|
| Ревизия untracked-файлов в рабочем дереве (блокер для Задания 2 ADR-010) | Prompt Factory for Claude Code | high |

---

## В планах

| Задача | Чат | Приоритет |
|---|---|---|
| ADR-010 Задание 2 — MCP-tools `get_unreviewed_chats` + `link_chat_to_customer` | Prompt Factory for Claude Code | high (после ревизии untracked) |
| ADR-010 Задание 3 — `tg_incremental.py` + launchd | Prompt Factory for Claude Code | medium |
| ADR-007/008 Пакет 3 — MCP-tools для разрешения ценовых конфликтов | Prompt Factory for Claude Code | medium |
| Gmail ingestion | — (назначит главный архитектор) | medium |
| Finance ledger | — | medium |
| Technical debt: mypy cleanup, ревизия ручного status в create_order, conftest для изолированных тестов | Prompt Factory for Claude Code | medium |
| Telegram-бот для мобильного доступа | — | low |

---

## Пока не входит

- Полная BI-аналитика
- Сложная мобильная версия
- Массовая многопользовательская ролевая система enterprise-уровня
- Сложная внешняя витрина для клиентов
