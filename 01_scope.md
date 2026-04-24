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
| Ревизия untracked-файлов в рабочем дереве (9 коммитов, `e8d8773..c2ed6b3`) | Prompt Factory for Claude Code |
| MCP-tools очереди модерации Telegram-чатов (ADR-010 Задание 2) | Prompt Factory for Claude Code |
| Analysis module schema — миграция (ADR-011 Задача 1) | Prompt Factory for Claude Code |
| Multi-account Telegram support — миграция и сервис (ADR-012 Задача 1) | Prompt Factory for Claude Code |
| Preflight classification schema — миграция (ADR-013 Задача 1) | Prompt Factory for Claude Code |
| Preflight import from tg_scan_results.json (ADR-013 Задача 2) | Prompt Factory for Claude Code |
| Multi-account Telegram ingestion — tg_import rewrite + register_account (ADR-012 Задача 2) | Prompt Factory for Claude Code |

---

## В работе

| Задача | Чат | Приоритет |
|---|---|---|
| — | — | — |

---

## В планах

| Задача | Чат | Приоритет |
|---|---|---|
| Реальная заливка Telegram-выгрузки + обкатка Заданий 1+2 ADR-010 | Оператор (без Claude Code) | high |
| ADR-010 Задание 3 — `tg_incremental.py` через Telethon + launchd plist | Prompt Factory for Claude Code | medium |
| Системный промт Cowork — обновление под 11 tools (включая очередь модерации) | Архитектурный штаб | medium (после обкатки) |
| ADR-007/008 Пакет 3 — MCP-tools для разрешения ценовых конфликтов | Prompt Factory for Claude Code | medium |
| Admin-tool для мержа дублирующихся клиентов (при первой коллизии telegram_id) | Prompt Factory for Claude Code | low (при появлении) |
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
