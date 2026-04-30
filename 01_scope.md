# Scope

> **Актуальный план работ:** `docs/PLAN.md`. Содержит полную карту групп G0–G17, рекомендации по моделям и контрольные точки CP1–CP16.

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
| PostgreSQL-триггер derive-status (ADR-006) — B1 ✅ | Prompt Factory for Claude Code |
| Listings + price history, Пакеты 1–2 (ADR-007/008) | Prompt Factory for Claude Code |
| Telegram profile schema — миграция (ADR-009) | Prompt Factory for Claude Code |
| Reply column + media metadata (ADR-010 addendum) | Prompt Factory for Claude Code |
| Telegram historical import (ADR-010 Задание 1) | Prompt Factory for Claude Code |
| Ревизия untracked-файлов в рабочем дереве (9 коммитов, `e8d8773..c2ed6b3`) | Prompt Factory for Claude Code |
| MCP-tools очереди модерации Telegram-чатов (ADR-010 Задание 2) | Prompt Factory for Claude Code |
| Analysis module schema — миграция (ADR-011 Задача 1) | Prompt Factory for Claude Code |
| Analysis service + repository layer (ADR-011 Задача 2) | Prompt Factory for Claude Code |
| Analysis CLI runner — `analysis/run.py` + helpers (ADR-011 Задача 3) | Prompt Factory for Claude Code |
| Batch-commit fix — commit every 10 saves — B2 ✅ (коммит `22d54a0`, 2026-04-28) | Prompt Factory for Claude Code |
| ADR-011 X1 — Identity quarantine MCP-tools (`list_pending_identity_updates`, `apply_identity_update`) (коммит `3723597`, 2026-04-29) | Prompt Factory for Claude Code |
| Media extraction pipeline — ADR-014 (боевой прогон завершён 2026-04-28, 6467 messages) | Prompt Factory for Claude Code |
| ADR-F06 — Хранение валюты в БД (принят 2026-04-30) | Архитектурный штаб |
| ADR-F04 — Snapshot цен при сохранении заказа (принят 2026-04-30, G1 закрыт, CP2) | Архитектурный штаб |
| G2 — PC-worker инфраструктура: `--chat-id-range` в `analysis/run.py` + `scripts/sync_pc_analyses.sh` + runbook (закрыто 2026-04-30, CP3) | Cowork-arch (этот чат) |
| ADR-016 — Token economy + Mobile capture (принят 2026-04-30; G18 добавлен в PLAN.md, реализация ждёт pre-step) | Архитектурный штаб |
| G5 — Update tools + Cowork промт v2.0: `update_customer`, `update_order`, `apply_pending_analysis` (16 tools, 17 тестов), `cowork-system-prompt.md` v2.0 с decision tree (закрыто 2026-04-30, CP8; коммиты `c6e5112`, `704980b`, `dabcf7a`) | Архитектурный штаб (этот чат) |

---

## В работе

| Задача | Чат | Приоритет |
|---|---|---|
| — | — | — |

---

## В планах

> Полная карта с зависимостями — в `docs/PLAN.md`. Ниже — сокращённый реестр групп.

| Группа | Задача | Чат | Приоритет |
|---|---|---|---|
| G3 | Smoke на chat 6544 (Kristina) + полный full analysis на 386 чатах | Оператор + Архитектурный штаб | high (ждёт G2) |
| G4 | Apply результатов анализа через Cowork (identity quarantine, link chats) | Cowork (operator workflow) | high (после G3) |
| G5 | ✅ Закрыто 2026-04-30, CP8 — см. «Сделано» | — | — |
| G6 | Finance ledger — реализация по ADR-F04/F06 | Prompt Factory | high (после G1) |
| G7 | ADR-007/008 Пакет 3 (разрешение ценовых конфликтов + hooks) + ADR-005 mirror live | Prompt Factory | medium |
| G8 | FastAPI launchd autostart (plist + runbook) | Prompt Factory | medium |
| G9 | ADR-010 Задание 3 — `tg_incremental.py` через Telethon + launchd | Prompt Factory | medium |
| G10 | Gmail ingestion + автомониторинг статусов | Архитектурный штаб → Prompt Factory | medium |
| G11 | Правила матчинга коммуникаций | Архитектурный штаб | medium |
| G12 | Технический долг: mypy cleanup, ревизия ручного status, conftest | Prompt Factory | medium |
| G13 | Admin-tool мержа дубликатов клиентов (по первой коллизии telegram_id) | Prompt Factory | low (триггер) |
| G14 | Telegram-бот для мобильного доступа | Архитектурный штаб → Prompt Factory | low |
| G15 | ADR-008 addendum: процентный порог цен для дорогих позиций | Архитектурный штаб | low |
| G16 | Спящие риски E1–E17 — активация по триггерам | По месту | по триггеру |
| G17 | MCP-tools для курса валют | Prompt Factory | medium |
| G18 | ADR-016: Cowork live artifacts + capture-only Telegram-бот (две подзадачи) | Prompt Factory (две подзадачи) | medium (после pre-step) |

---

## Пока не входит

- Полная BI-аналитика
- Сложная мобильная версия
- Массовая многопользовательская ролевая система enterprise-уровня
- Сложная внешняя витрина для клиентов
