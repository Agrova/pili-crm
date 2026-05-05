# 000 ADR Registry — проект ПилиСтрогай

Реестр всех архитектурных решений проекта. Источник истины по статусам ADR.

**Соглашения:**
- Нумерация: `ADR-NNN` (три цифры, с ведущими нулями).
- Финансовый контур: `ADR-F01` — `ADR-F06` (отдельная серия; ADR-F06 принят 2026-04-30, остальные draft).
- Принятый ADR неизменен. Изменение = новый ADR со ссылкой `Заменяет: ADR-XXX`.
- Аддендумы (`ADR-XXX-addendum-N`) уточняют принятый ADR без его отмены.
- Статусы: `draft` → `accepted` → `superseded` / `deprecated` / `rejected`.

---

## Принятые ADR (accepted)

| № | Название | Дата | Файл | Примечания |
|---|---|---|---|---|
| ADR-001 v2 | Модульный монолит, Claude Cowork + MCP | 2026-04-22 | `ADR-001-v2-modular-monolith.md` | Заменяет ADR-001 v1 |
| ADR-002 | Python 3.12+ / FastAPI / SQLAlchemy 2.0 / Alembic | 2026-04-22 | `ADR-002-python-fastapi.md` | |
| ADR-003 | Core schema PostgreSQL + статусы заказов | 2026-04-22 | `ADR-003-final-ready-postgres-core-schema.md` | С аддендумом по статусам |
| ADR-004 | Pricing & profit policy | 2026-04-22 | `ADR-004-pricing-profit-policy.md` | Retail/manufacturer paths, rounding, discounts |
| ADR-005 | Mirror Google Sheets | 2026-04-22 | `ADR-005-mirror-google-sheets.md` | Триггер с полной пересборкой раз в сутки |
| ADR-006 | Derive-status PostgreSQL trigger | 2026-04-22 | `ADR-006-derive-status-trigger.md` | Статус заказа автоматически |
| ADR-007 | Catalog listings + price history | 2026-04-22 | *(в core schema)* | |
| ADR-008 | Stock price invariant | 2026-04-22 | `ADR-008-stock-price-invariant.md` | weighted_average при расхождениях |
| ADR-009 | Telegram customer profile schema | 2026-04-23 | `ADR-009-telegram-customer-profile-schema.md` | JSONB поля |
| ADR-010 | Telegram ingestion pipeline | 2026-04-23 | *(в core schema + addendum)* | Исторический + reply column |
| ADR-010 addendum | Reply column + медиа-метаданные | 2026-04-23 | `adr-010-addendum-reply-and-media.md` | |
| ADR-011 | Telegram chat analysis pipeline | 2026-04-24 | `ADR-011-telegram-chat-analysis-pipeline.md` | qwen3 LLM, 6 фаз |
| ADR-011 addendum-1 | Связь чат↔клиент через communications_link | 2026-04-24 | `ADR-011-addendum-1.md` | Расхождение с §5/§7 ADR-011 |
| ADR-011 addendum-2 | Mac master + PC worker | 2026-04-26 | `ADR-011-addendum-2-pc-worker.md` | Распределённый прогон |
| ADR-012 | Telegram multiple accounts | 2026-04-26 | `ADR-012-telegram-multiple-accounts.md` | |
| ADR-013 | Preflight classification | 2026-04-26 | `ADR-013-preflight-classification.md` | |
| ADR-014 | Media extraction pipeline | 2026-04-26 | `ADR-014-media-extraction-pipeline.md` | Разблокирован ADR-015; боевой прогон завершён 2026-04-28 |
| ADR-015 | Telegram media metadata stabilization | 2026-04-26 | `ADR-015-telegram-media-metadata-stabilization.md` | Pre-requisite для ADR-014; закрыт 2026-04-27 |
| ADR-016 | Token economy + Mobile capture | 2026-04-30 | `ADR-016-token-economy-and-mobile-capture.md` | Cowork live artifacts (desktop) + Telegram capture-only бот (mobile). Реализация — G18 |
| ADR-017 | Фильтрация исторических orders при apply LLM-анализа | 2026-04-30 | `ADR-017-filter-historical-orders-on-apply.md` | Отсечение orders без items / с терминальными статусами в `apply_analysis_to_customer`. Реализация — G4.6. Триггер на bump промта v1.5 — `06_open_questions.md` |

---

## Финансовый контур

Шесть ADR-решений финансового контура. ADR-F06 и ADR-F04 приняты 2026-04-30. Оба разблокировали реализацию finance ledger (G6).

| № | Тема | Приоритет | Статус |
|---|---|---|---|
| ADR-F01 | Выбор веса (declared vs actual) в расчётах | high | draft |
| ADR-F02 | Метод налогового учёта | medium | draft |
| ADR-F03 | Распределение общих расходов | medium | draft |
| ADR-F04 | Snapshot цен при сохранении заказа | **highest** | **accepted** (2026-04-30, `ADR-F04-price-snapshot.md`) |
| ADR-F05 | Расположение формул (DB vs Python) | medium | draft |
| ADR-F06 | Хранение валюты в БД | **highest** | **accepted** (2026-04-30, `ADR-F06-currency-storage.md`) |

ADR-F04 принят в группе G1 (CP2 достигнут). Реализация — в группе G6 (finance ledger).

---

## Superseded / Deprecated

| № | Чем заменён |
|---|---|
| ADR-001 v1 | ADR-001 v2 |

---

*Последнее обновление: 2026-04-30 (ADR-017 принят и реализован: G4.5 + G4.6 закрыты, PLAN.md v15, CP7.5 ✅). HEAD origin/main: `59fc9ce`.*
