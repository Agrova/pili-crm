# План работ по проекту PiliStrogai CRM

**Текущая версия:** v16 (2026-05-05)
**Цель:** провести проект от текущего состояния (полный анализ Telegram-чатов на PC-worker, перезапуск после теплового сбоя Mac) до закрытия `01_scope.md` (включая отложенные «В планах»: Gmail ingestion, finance ledger, Telegram-бот, технический долг).

## История версий

| Версия | Дата | Что изменилось |
|---|---|---|
| v1 | 2026-04-30 утро | Первая ревизия документов — план ближайших действий (B-блок) |
| v2 | 2026-04-30 | Корректировка после `PLAN_2026-04-30_VERIFICATION_REPORT.md`: B1, B2 закрыты; B4 понижен до medium; F2 рассинхронизация подтверждена |
| v3 | 2026-04-30 | Полный план всего проекта (G0–G16, CP1–CP15, спящие риски). Решение «Mac-only, отложить PC-worker» |
| v4 | 2026-04-30 вечер | **(а)** возврат к PC-worker (Mac перегрелся через 1 час); B3 реактивирован, E3 снова активен. **(б)** G0 закрыт, CP1 достигнут. **(в)** Скелеты инициирующих сообщений для всех 17 групп + полные для G1, G2, G3, G4, G5, G7, G8. **(г)** Файл переименован `PLAN_2026-04-30.md` → `PLAN.md` |
| v5 | 2026-04-30 поздний вечер | **(а)** Зафиксирована модель работы: одноразовые Cowork-чаты на группу, без долгоживущих веб-чатов claude.ai. Колонка «Чат» в карте групп переписана под новые роли (Cowork-arch / Cowork-pf / Claude Code / Cowork-operator). Подробности — в `CLAUDE.md` секция «Модель работы». **(б)** Добавлена группа G17 — MCP-tools курса валют (`get_current_exchange_rate`, `set_exchange_rate`) — закрывает реальную причину существования `pricing-context.md` в операторской папке. **(в)** Папка `Documents/Claude/Projects/ПилиСтрогай/` зафиксирована в `CLAUDE.md` как рабочее пространство операторского Cowork-чата (отдельный проект от Architecture Office), не устаревшая. |
| v6 | 2026-04-30 ночь | **(а)** G1 закрыт: ADR-F04 (snapshot цен) принят, файл `docs/adr/ADR-F04-price-snapshot.md`. CP2 достигнут. **(б)** Финансовый контур: 2 из 6 ADR приняты (F04 + F06); G6 (finance ledger) разблокирован по архитектуре. |
| v7 | 2026-04-30 ночь | G2 закрыт: `--chat-id-range`/`--chat-ids` в `analysis/run.py`, `scripts/sync_pc_analyses.sh` (обе таблицы), `docs/runbook_sync_pc_analyses.md`, регрессионные тесты. CP3 достигнут. G3 разблокирован. |
| v8 | 2026-04-30 ночь | Принят ADR-016 (экономия токенов + мобильный capture). Добавлена группа G18 (Desktop live artifacts + Telegram capture-only бот). G14 переопределён как «полный мобильный CRM» — capture-only часть вынесена в G18. CP17 добавлен. Открытый вопрос про измерительный pre-step добавлен в `06_open_questions.md`. |
| v9 | 2026-04-30 ночь | G18 pre-step переведён с ручного журнала на **авто-сбор операционным Cowork**: создан `docs/inbox_measurement.md`, в `cowork-system-prompt.md` добавлен раздел 11 с эвристиками. Период сбора **2026-04-30 — 2026-05-14** (две недели), дата разбора 2026-05-14. Open question по G18 переведён в `in-progress`. |
| v16 | 2026-05-05 | **Починка загрузки промта операционного Cowork.** Обнаружено к 2026-05-05: журнал `docs/inbox_measurement.md` пуст, хотя операционный Cowork активно работал между v9 и v15 (G3/G4/G5/G4.5/G4.6 закрыты, инструменты 16→18→19+ выросли). Корень: `pili-crm/CLAUDE.md` не указывал Cowork читать `docs/cowork-system-prompt.md`, поэтому раздел 11 промта не доходил. Починено: (а) добавлена секция «Системный промт операционного Cowork» в `pili-crm/CLAUDE.md`; (б) создан fallback `Documents/Claude/Projects/ПилиСтрогай/CLAUDE.md`; (в) период сбора G18 pre-step пересмотрен на **2026-05-05 — 2026-05-19**, разбор 2026-05-19; (г) одношаговая проверка загрузки промта добавлена в open question. **Важный системный урок:** обновления `cowork-system-prompt.md` в репо не доходят до Cowork без указателя в CLAUDE.md — этот недосмотр был активен с момента написания промта. |
| v15 | 2026-04-30 | **G4.6 закрыта** — ADR-017 реализован: `_is_actionable_order` фильтр в `apply_analysis_to_customer` (items непустой + status_delivery ∉ {delivered, returned}). Cascade fix E18: `delete_created_entities` удаляет `orders_order` со статусом `draft`/`in_procurement` при force=True. Добавлен счётчик `orders_filtered_historical`. 6 новых тестов (a-f). CP7.5 ✅. G4 разблокирован. Коммит `d73e52e`. |
| v14 | 2026-04-30 | **G4.5 закрыта** — принят ADR-017 (фильтрация исторических orders при apply). Smoke chat 6485 показал: v1.4 by design реконструирует историю заказов, apply трактует это как новые draft → ~64% orders создаются пустыми. Решение: фильтр на стороне `apply_analysis_to_customer` (items непустой + status_delivery ∉ {delivered, returned}). Альтернатива (bump промта v1.5) отложена до триггера. **G4.6 открыта** — реализация фильтра + cascade fix для force=True (E18). G4 → ⏸️ ждёт G4.6. CP7.5 добавлен. 50 пустых draft Яшина (customer_id=1688) удалены вручную через psql. |
| v13 | 2026-04-30 | G5.5 закрыта: `list_draft_orders` + `verify_draft_order` реализованы (18 tools, 6 тестов), `cowork-system-prompt.md` v2.1. CP8.5 достигнут. G4 разблокирован. |
| v12 | 2026-04-30 | G5.5 добавлена: `list_draft_orders` + `verify_draft_order`. Smoke chat 6485 выявил что draft-заказы из анализа недоступны оператору в Cowork. G4 теперь ждёт G5.5. CP8.5 добавлен. `crm-mcp/db.py` получил импорт всех ORM-моделей (фикс NoReferencedTableError). |
| v11 | 2026-04-30 | G5 закрыт: `update_customer`, `update_order`, `apply_pending_analysis` реализованы (16 tools, 17 тестов), `cowork-system-prompt.md` v2.0. CP8 достигнут. G4 разблокирован. |
| v10 | 2026-04-30 ночь | **Скелет G3 и полное инициирующее сообщение скорректированы** по факту G2-сессии: (а) деление Mac/PC отменено — все 386 чатов идут только на PC; (б) реальные chat_id 5941–6790, не 1..386; (в) preflight_classification — отдельная колонка, не поле в structured_extract; (г) smoke на chat 6485 (Вячеслав Яшин / @vyashin86), не 6544; (д) 6544 уже проанализирован (v1.4+qwen3-14b). |

## Как читать этот документ

1. Раздел «Карта групп» — таблица 17 групп задач. Каждая группа = один отдельный чат с указанной моделью.
2. Раздел «Текущее состояние» — что закрыто к сегодняшнему дню.
3. Раздел «Спящие риски» — E1–E17, активируются по триггерам.
4. Раздел «Контрольные точки» — CP1–CP15, как закрытие групп ведёт к закрытию проекта.
5. Раздел «Инициирующие сообщения» — скелеты для всех 17 групп + полные тексты для активных 7. Копировать в новый чат при старте группы.

Правила ведения плана и связи с другими файлами проекта — в `CLAUDE.md` секция «Документооборот проекта».

---

## Карта групп задач

Колонка «Чат» — какой тип одноразового Cowork-чата запускать (см. `CLAUDE.md` секция «Модель работы»):
- **Cowork-arch** — архитектурный Cowork-чат (Opus 4.6, для ADR/решений)
- **Cowork-pf** — Cowork-чат для подготовки ТЗ (Sonnet 4.6, бывший Prompt Factory)
- **Cowork-operator** — Cowork-чат с MCP-tools для повседневной работы оператора
- **Claude Code** — CLI-инструмент для непосредственной реализации
- **Hand** — ручная правка оператором (без Claude)

| # | Группа | Назначение | Чат | Модель | Блокирует | Статус |
|---|---|---|---|---|---|---|
| **G0** | Гигиена документации | F1, F2, F3, F4 — закрыть рассинхронизации, перенести `tool-gaps`, обновить `01_scope.md` и `000_ADR_REGISTRY.md` | Cowork-arch (ручная правка) | Sonnet 4.6 + Low | Качество планирования всех остальных групп | ✅ Закрыто 2026-04-30, CP1 |
| **G1** | Архитектурные решения финансового контура | ADR-F04 (snapshot цен) — `highest`. ADR-F06 уже принят 2026-04-30 | Cowork-arch | Opus 4.6 + High | G6 (finance ledger) | ✅ Закрыто 2026-04-30, CP2. ADR-F06 + ADR-F04 приняты |
| **G2** | PC-worker инфраструктура (B3) | `--chat-id-range`/`--chat-ids` в `analysis/run.py` + `scripts/sync_pc_analyses.sh`. Деление 386 чатов: PC 1..193, Mac 194..386. ADR-011 Addendum 2 действует. **Реактивирован после теплового сбоя Mac** | Cowork-pf → Claude Code | Sonnet 4.6 + Medium | G3 (запуск full analysis на 386 чатах) | ✅ Закрыто 2026-04-30, CP3 |
| **G3** | Smoke + полный full analysis на 386 чатах | Перезапустить фоновый. Smoke на chat 6544 (Kristina) с identity quarantine apply. Распределённый прогон | Hand + Cowork-operator | Cowork = Sonnet 4.6 | G5 (tools) → G4 (apply) | ⏳ Ждёт G2 |
| **G4** | Apply результатов через Cowork | review identity quarantine, link chats, apply orders. Оценить vision-template-mismatch (E4) | Cowork-operator | Sonnet 4.6 | — | 🟢 Разблокирован (G4.6 ✅ 2026-04-30) |
| **G4.5** | Архитектурное решение: orders из анализа = drafts vs history | ADR-017: фильтрация исторических orders на стороне `apply_analysis_to_customer`. Выявлено в smoke chat 6485: 26 orders из v1.4 → 50 пустых draft в БД | Cowork-arch (этот чат, 2026-04-30) | Opus 4.6 + Medium | G4.6, G3 part 2 (массовый apply) | ✅ Закрыто 2026-04-30, CP7.5 (архитектурная часть) |
| **G4.6** | Реализация ADR-017 + cascade fix для force=True | Фильтр `_is_actionable_order` в `apply_analysis_to_customer` (отсекает items=пусто и status_delivery ∈ {delivered, returned}). Параллельно — каскад в `delete_created_entities` на orders_order WHERE id IN (...) AND status IN (draft, in_procurement) (E18) | Claude Code | Sonnet 4.6 + Medium | G4, G3 part 2 (массовый apply) | ✅ Закрыто 2026-04-30, CP7.5, коммит `d73e52e` |
| **G5** | Update tools для customer/order + Cowork промт | `update_customer`, `update_order`, `apply_pending_analysis`, расширение `get_unreviewed_chats` (has_analysis + summary). Затем апдейт `cowork-system-prompt.md` под 16 tools | Cowork-pf → Claude Code, потом Cowork-arch (промт) | Sonnet 4.6 + Medium / Opus 4.6 + Medium | G4 (полный apply через Cowork) | ✅ Закрыто 2026-04-30, CP8 |
| **G5.5** | Draft-заказы из анализа: list + verify | `list_draft_orders`, `verify_draft_order`. Выявлено в smoke chat 6485: apply_pending_analysis создаёт 26 draft-заказов, оператор не может их увидеть через Cowork | Cowork-pf → Claude Code | Sonnet 4.6 + Low | G4 (верификация черновиков) | ✅ Закрыто 2026-04-30, CP8.5 |
| **G6** | Finance ledger | Реализация finance модуля по ADR-F04/F06. Schema-миграция, snapshot цен в order_item, хранение валюты | Cowork-pf → Claude Code (несколько подзадач) | Sonnet 4.6 + High | — | ⏳ Ждёт G1 (ADR-F04) |
| **G7** | ADR-007/008 Пакет 3 + ADR-005 mirror live | Пакет 3 — MCP-tools для разрешения ценовых конфликтов + интеграция hooks. Также — выбор библиотеки Google Sheets API и реализация `crm-mcp/mirror/` | Cowork-pf → Claude Code (две задачи) | Sonnet 4.6 + High | — | 🟡 Готов к запуску |
| **G8** | FastAPI launchd autostart (B4) | plist, runbook, `logs/` в `.gitignore`. `/health` endpoint уже есть | Cowork-pf → Claude Code | Sonnet 4.6 + Medium | — | 🟡 QoL, не блокер |
| **G9** | ADR-010 Задание 3 — incremental Telegram | `tg_incremental.py` через Telethon + launchd plist | Cowork-pf → Claude Code | Sonnet 4.6 + Medium | G10 (аналогичная архитектура) | ⏳ В очереди |
| **G10** | Gmail ingestion + автомониторинг (D2) | Gmail → подтверждение заказа → трек → контроль сроков → алерт. Сначала ADR, потом реализация | Cowork-arch → Cowork-pf → Claude Code | Opus 4.6 + High → Sonnet 4.6 + High | — | ⏳ В очереди |
| **G11** | Правила матчинга коммуникаций (D3) | Формализовать `communications_link.target_module` rules | Cowork-arch | Opus 4.6 + Medium | — | ⏳ В очереди |
| **G12** | Технический долг | mypy, ручной status в `create_order`, conftest, маркер `__immutable__` | Cowork-pf → Claude Code | Sonnet 4.6 + Medium | — | 🟡 Готов к запуску |
| **G13** | Admin-tool мержа дубликатов клиентов | По первой реальной коллизии `telegram_id` | Cowork-pf → Claude Code | Sonnet 4.6 + Medium | — (триггер: коллизия) | 💤 По триггеру |
| **G14** | Telegram-бот: полный мобильный CRM (read+write в основные таблицы) | Полный CRM-клиент в Telegram. **Capture-only часть вынесена в G18.2** (ADR-016). G14 активируется только если capture-only окажется недостаточно | Cowork-arch → Cowork-pf → Claude Code | Opus 4.6 + Medium → Sonnet 4.6 + High | — | 💤 По остаточному принципу (после G18.2) |
| **G15** | ADR-008 addendum: процентный порог цен (D4) | Для дорогих позиций (>50 000 RUB) `rounding_step=100` даёт 0.2% → ложные конфликты | Cowork-arch | Opus 4.6 + Medium | — | 💤 По триггеру |
| **G16** | Спящие риски — точечная активация по триггерам | E1–E17 | По месту (Cowork-arch / Cowork-pf) | По месту | — | 💤 По триггерам |
| **G17** | MCP-tools для курса валют | `get_current_exchange_rate`, `set_exchange_rate`. Найдено при разборе `pricing-context.md` (ПилиСтрогай). Курс архитектурно в `pricing_exchange_rate` (ADR-003), но нет tools для его просмотра/обновления через Cowork | Cowork-pf → Claude Code | Sonnet 4.6 + Medium | — (можно объединить с G6 finance) | 🟡 Готов к запуску |
| **G18** | ADR-016: Token economy + Mobile capture | Две подзадачи: **G18.1** Cowork live artifacts (3 desktop-дашборда: pending_orders, customer_lookup, pricing_reference) для рутины без рассуждений. **G18.2** Telegram capture-only бот + таблица `inbox_capture` + 2 MCP-tool. Pre-step: 2 недели авто-сбора через Cowork в `docs/inbox_measurement.md` (см. `06_open_questions.md`) | Cowork-pf → Claude Code (две подзадачи) | Sonnet 4.6 + Medium (обе) | — (G18.1 использует G17 для pricing_reference) | 🟠 Pre-step идёт (2026-05-05 — 2026-05-19, перезапуск после починки промта), разбор 2026-05-19 |

---

## Рекомендации по моделям

| Тип задачи | Модель | Effort |
|---|---|---|
| Архитектурное решение, ADR, спорные развилки | **Opus 4.6** | High (если решение дорогое в откате — ADR-F04, finance ledger) или Medium (для аддендумов и точечных решений) |
| Реализация по утверждённому ADR (код + тесты + миграция) | **Sonnet 4.6** | High (новый модуль, finance ledger, mirror live, Gmail ingestion) или Medium (точечные tools, plist, документация) |
| Гигиена документации, текстовые правки | Sonnet 4.6 | Low |
| Cowork-промт обновление | Opus 4.6 | Medium (требует понимания всех tools и правил) |
| Operator workflow (apply, review, link) | Cowork = Sonnet 4.6 | — |

**Эмпирическое правило:** если решение принимается один раз и стоит дорого в откате — Opus. Если это перевод уже принятого решения в код — Sonnet.

---

## Текущее состояние (что закрыто к 2026-04-30)

- ADR-001..010 — приняты и реализованы
- ADR-011 (Telegram chat analysis) Задачи 1–3 реализованы
- ADR-011 X1 — identity quarantine MCP-tools (`list_pending_identity_updates`, `apply_identity_update`) — коммит `3723597`, 2026-04-29
- ADR-014 (media extraction) реализован, ADR-015 закрыт, оба перенесены в «Принятые» 2026-04-30
- ADR-F06 — принят 2026-04-30
- Batch-commit fix (B2) — коммит `22d54a0`, 2026-04-28
- ADR-007/008 Пакеты 1–2 — коммит `6f67659`, 2026-04-27
- FastAPI `/health` endpoint — есть в `app/main.py`
- 13 MCP-tools работают
- **G0 закрыт 2026-04-30** — гигиена документации (F1-F4), CP1 достигнут
- **G2 закрыт 2026-04-30** — `--chat-id-range` + `scripts/sync_pc_analyses.sh` + runbook + тесты, CP3 достигнут
- **ADR-016 принят 2026-04-30** — экономия токенов на рутине + мобильный capture. G18 добавлен в карту групп.
- **G18 pre-step запущен 2026-04-30, перезапущен 2026-05-05 после починки** — авто-сбор операционным Cowork в `docs/inbox_measurement.md`, период 2026-05-05 — 2026-05-19, разбор 2026-05-19 (см. `06_open_questions.md`). Корень изначальной поломки — отсутствие указателя в `pili-crm/CLAUDE.md`.

---

## Контрольные точки

| CP | Условие | Закрывает группу | Статус |
|---|---|---|---|
| CP1 | Гигиена документации завершена | G0 | ✅ 2026-04-30 |
| CP2 | ADR-F04 принят | G1 | ✅ 2026-04-30 |
| CP3 | PC-worker инфраструктура: `--chat-id-range` + sync-скрипт | G2 | ✅ 2026-04-30 |
| CP4 | Фоновый full analysis перезапущен и завершён | G3 ч.1 | ⏳ |
| CP5 | Smoke на chat 6544 — identity quarantine применён | G3 ч.2 | ⏳ |
| CP6 | Все 386 чатов прогнаны | G3 ч.3 | ⏳ |
| CP7 | Apply через Cowork завершён (требует G5, G4.6) | G4 | ⏳ |
| CP7.5 | ADR-017 принят, фильтр исторических orders в проде | G4.5 + G4.6 | ✅ G4.5 ✅ 2026-04-30, G4.6 ✅ 2026-04-30 (`d73e52e`) |
| CP8 | Update tools реализованы + Cowork промт обновлён (предшествует G4) | G5 | ✅ 2026-04-30 |
| CP8.5 | Draft-заказы видны и верифицируются в Cowork | G5.5 | ✅ 2026-04-30 |
| CP9 | Finance ledger в проде | G6 | ⏳ |
| CP10 | ADR-007/008 Пакет 3 + Mirror live | G7 | ⏳ |
| CP11 | FastAPI autostart | G8 | ⏳ |
| CP12 | Telegram incremental | G9 | ⏳ |
| CP13 | Gmail ingestion + автомониторинг | G10 | ⏳ |
| CP14 | Правила матчинга формализованы | G11 | ⏳ |
| CP15 | Технический долг закрыт | G12 | ⏳ |
| CP16 | MCP-tools для курса валют (G17) | G17 | ⏳ |
| CP17 | ADR-016 G18: артефакты + capture-бот в проде | G18 | ⏳ |

После CP17 проект достигает состояния «всё из scope, кроме wontfix». G13–G16 активируются по триггерам.

---

## Спящие риски (E-блок)

По умолчанию **не запускать**, ждать триггера активации. После активации каждый риск становится отдельной задачей (промт в Prompt Factory или addendum к ADR в Архитектурном штабе).

| № | Запись | Триггер активации | Статус |
|---|---|---|---|
| E1 | Email UNIQUE-конфликт без savepoint | Per-field confidence в LLM Identity. **Снижен:** B1 уже реализовал SAVEPOINT внутри `apply_identity_update` | 💤 |
| E2 | Дубликаты pending при rerun analysis | Реальный шум в Cowork backlog | 💤 |
| E3 | Конкурентность `analysis_chat_analysis_state` | **Активен с v4** — Mac+PC попадут на один chat_id (защита: фиксированное деление в G2) | 🟡 Активен |
| E4 | Vision-template-mismatch (98 записей, 1.5%) | Оценка после full analysis на 386 чатах (G4) | 💤 |
| E5 | Дублирование клиентов по telegram_id | Первая реальная коллизия → активирует G13 | 💤 |
| E6 | Версионирование JSONB-схем (ADR-009) | Первое изменение `_v=1 → _v=2` | 💤 |
| E7 | `telegram_id=NULL + telegram_username=NOT NULL` | Реальный инцидент через очередь модерации | 💤 |
| E8 | Очистка draft-заказов от старых analyzer_version | Несколько итераций промта анализатора | 💤 |
| E9 | ADR-005: критерий перехода на дельта-обновление | Время экспорта >30s или >5 API/мин | 💤 |
| E10 | ADR-005: экспорт audit-таблиц | Появление audit-таблиц | 💤 |
| E11 | `match_shipment` read-only или write | Разовый чат — промт уже готов в истории | 💤 |
| E12 | ADR-007: stock lots | 3 варианта разрешения не покрывают потребность | 💤 |
| E13 | ADR-008: дефолт/ассистент-рекомендация | Накопление статистики выборов оператора | 💤 |
| E14 | Cowork: правила работы с листингами | После G7 (Пакет 3 в работе) | 💤 |
| E15 | ADR-006: side-effects на смену статуса | Первое бизнес-требование на side-effect | 💤 |
| E16 | ADR-010: кириллица в имени plist | G9 (Задание 3 ADR-010) | 💤 |
| E17 | Защитное кодирование PL/pgSQL CASE | Следующая PL/pgSQL-функция | 💤 |
| E18 | `force=True` в `delete_created_entities` не каскадирует на `orders_order` — пустые draft остаются после reapply | **Решено в G4.6** — cascade DELETE в `delete_created_entities` удаляет `orders_order` со статусом `draft`/`in_procurement`. ON DELETE CASCADE на `orders_order_item` и `analysis_pending_order_item` убирает дочерние строки. Коммит `d73e52e`. | ✅ Решён |

---

## Что не входит в этот план

Из `01_scope.md` секция «Пока не входит»:
- Полная BI-аналитика
- Сложная мобильная версия (G14 — это минимальный бот, не полный мобильный клиент)
- Многопользовательская ролевая система enterprise-уровня
- Внешняя витрина для клиентов

---

# Инициирующие сообщения

## Шаблон полного сообщения

```
Группа: G_X — название
Цель: одно предложение
Модель: Sonnet/Opus + Low/Medium/High
Префаза (читать первым): файлы и строки
Зависимости (закрытые группы): G_Y, G_Z
Ожидаемые артефакты: файлы / коммиты / ADR
Критерий готовности: CP_X
Чек-лист закрытия: см. CLAUDE.md «Документооборот проекта»
```

---

## Скелеты для всех 17 групп

### G0 — Гигиена документации ✅ ЗАКРЫТО
Закрыто 2026-04-30. CP1 достигнут.

### G1 — Финансовый контур (ADR-F04) ✅ ЗАКРЫТО
Закрыто 2026-04-30. CP2 достигнут. Принят ADR-F04 (`docs/adr/ADR-F04-price-snapshot.md`, status: accepted): A2+B2+C2+D2 — snapshot = вся `pricing_price_calculation`, создаётся в момент создания order_item (включая manual-snapshot для ручных цен), хранится в существующей таблице, защищён PostgreSQL-триггером от UPDATE структурных полей. Реализация — в G6 (finance ledger).

### G2 — PC-worker инфраструктура (B3)
- **Цель:** `--chat-id-range`/`--chat-ids` в `analysis/run.py` + `scripts/sync_pc_analyses.sh` для распределённого прогона по схеме ADR-011 Addendum 2.
- **Префаза:** `docs/adr/ADR-011-addendum-2-pc-worker.md`, `analysis/run.py` (текущие CLI), commit `90da591`
- **Модель:** Sonnet 4.6 + Medium
- **Артефакт:** PR с CLI-флагами + sync-скрипт + тесты идемпотентности
- **CP:** CP3

### G3 — Smoke + full analysis на 386 чатах
- **Цель:** прогнать 386 client/possible_client чатов через full analysis pipeline на PC, пройти smoke на chat 6485, применить identity quarantine.
- **Префаза:** результат G2, `06_open_questions_archive.md` запись о identity quarantine tools
- **Модель:** оператор-вручную, Cowork = Sonnet 4.6
- **Артефакт:** записи в `analysis_chat_analysis` для 386 чатов (analyzer_version LIKE 'v1.4%@pc')
- **Примечание:** все 386 чатов идут на PC (без деления Mac/PC). Реальные chat_id: 5941–6790. Preflight classification — колонка `preflight_classification` в `analysis_chat_analysis`.
- **CP:** CP4–CP6

### G4 — Apply через Cowork
- **Цель:** identity quarantine review + link chats + apply orders. Параллельно оценить E4.
- **Префаза:** `crm-mcp/IMPROVEMENTS.md` запись от 2026-04-29, `docs/cowork-system-prompt.md`, `docs/adr/ADR-017-filter-historical-orders-on-apply.md`
- **Модель:** Cowork = Sonnet 4.6
- **Артефакт:** обновлённые `orders_customer`, `orders_order` (drafts с реальными items), `communications_link`
- **Зависимость:** G5 ✅, G4.6 ✅
- **Статус:** 🟢 Готов к запуску
- **CP:** CP7

### G4.5 — ADR-017: фильтрация исторических orders ✅ ЗАКРЫТО
Закрыто 2026-04-30. Принят `docs/adr/ADR-017-filter-historical-orders-on-apply.md`. Решение принято в Cowork-arch чате на основе smoke chat 6485 + статистики 4 v1.4-чатов (63.9% orders без items). Реализация — G4.6.

### G4.6 — Реализация ADR-017 + cascade fix для force=True
✅ Закрыто 2026-04-30. Коммит `d73e52e`.
- **Цель:** реализовать `_is_actionable_order` фильтр в `apply_analysis_to_customer`. Параллельно — каскад на `orders_order` при `force=True` (E18).
- **Артефакт:** `app/analysis/service.py` (фильтр + `orders_filtered_historical`), `app/analysis/repository.py` (cascade DELETE `draft`/`in_procurement`), `crm-mcp/tools/apply_pending_analysis.py` (счётчик в выдаче), `tests/analysis/test_apply_filter.py` (6 тестов a-f)
- **Что сделано:** фильтр `_is_actionable_order` отсекает orders без items и с terminal delivery status. Cascade fix: при `force=True` `delete_created_entities` сначала удаляет `orders_order` (status `draft`/`in_procurement`, COUNT journal = 1), ON DELETE CASCADE убирает дочерние строки. Дополнительный статус `in_procurement` потребовался — `add_order_item` автоматически меняет статус с `draft` через trigg. E18 закрыт.
- **CP:** CP7.5

### G5 — Update tools + Cowork промт
- **Цель:** `update_customer`, `update_order`, `apply_pending_analysis` + обновить `cowork-system-prompt.md` под 16 tools.
- **Префаза:** `docs/tool-gaps.md` записи 2026-04-30, `crm-mcp/IMPROVEMENTS.md` запись 2026-04-30, `crm-mcp/tools/create_*.py` (образцы)
- **Модель:** Sonnet 4.6 + Medium → Opus 4.6 + Medium
- **Артефакт:** `crm-mcp/tools/update_*.py`, `crm-mcp/tools/apply_pending_analysis.py`, обновлённый промт
- **CP:** CP8

### G5.5 — Draft-заказы: list + verify
- **Цель:** `list_draft_orders`, `verify_draft_order` — оператор видит и верифицирует черновики из анализа в Cowork.
- **Префаза:** `docs/tool-gaps.md` запись 2026-04-30 (draft-заказы), `app/orders/models.py` (статус draft), `crm-mcp/tools/apply_pending_analysis.py` (образец)
- **Модель:** Sonnet 4.6 + Low
- **Артефакт:** `crm-mcp/tools/list_draft_orders.py`, `crm-mcp/tools/verify_draft_order.py`, обновлённый `cowork-system-prompt.md`
- **Зависимость:** G5 (закрыта)
- **CP:** CP8.5

### G6 — Finance ledger
- **Цель:** модуль `app/finance/` по принятым ADR-F04 + ADR-F06. Schema-миграция, snapshot цен, хранение валюты, MCP-tools.
- **Префаза:** `docs/adr/ADR-F04-*.md`, `docs/adr/ADR-F06-currency-storage.md`, `app/finance/README.md`
- **Модель:** Sonnet 4.6 + High (несколько пакетов)
- **CP:** CP9

### G7 — ADR-007/008 Пакет 3 + Mirror live
- **Цель:** Пакет 3 (MCP-tools для конфликтов + hooks) + `crm-mcp/mirror/` по ADR-005.
- **Префаза:** `docs/adr/ADR-007-*.md`, `docs/adr/ADR-008-*.md`, `docs/adr/ADR-005-*.md`
- **Модель:** Sonnet 4.6 + High (две параллельные подзадачи)
- **CP:** CP10

### G8 — FastAPI autostart
- **Цель:** plist + runbook + `logs/` в `.gitignore`. Health endpoint уже есть.
- **Префаза:** `app/main.py`, `06_open_questions_archive.md` запись о решении вариант 1
- **Модель:** Sonnet 4.6 + Medium
- **CP:** CP11

### G9 — Telegram incremental (ADR-010 Задание 3)
- **Цель:** `ingestion/tg_incremental.py` через Telethon + launchd plist для регулярного подхвата.
- **Префаза:** `docs/adr/ADR-010-telegram-ingestion-pipeline.md`, `ingestion/tg_import.py` (образец)
- **Модель:** Sonnet 4.6 + Medium
- **CP:** CP12

### G10 — Gmail ingestion + автомониторинг (D2)
- **Цель:** Gmail → подтверждение заказа → трек → контроль → алерт. Сначала ADR, потом реализация.
- **Префаза:** `06_open_questions.md` записи о Gmail ingestion и автомониторинге
- **Модель:** Opus 4.6 + High → Sonnet 4.6 + High
- **CP:** CP13

### G11 — Правила матчинга коммуникаций (D3)
- **Цель:** формализовать `communications_link.target_module` правила.
- **Префаза:** `docs/adr/ADR-003-final-ready-postgres-core-schema.md`, `06_open_questions.md` запись от 2026-04-22
- **Модель:** Opus 4.6 + Medium
- **CP:** CP14

### G12 — Технический долг
- **Цель:** mypy cleanup, ревизия ручного status в `create_order`, conftest для изолированных тестов, маркер `__immutable__`.
- **Префаза:** `06_open_questions.md` записи о mypy/conftest/create_order
- **Модель:** Sonnet 4.6 + Medium
- **CP:** CP15

### G13 — Admin-tool мержа дубликатов (по триггеру)
- **Цель:** объединение двух клиентов при коллизии `telegram_id`.
- **Префаза:** `06_open_questions.md` запись от 2026-04-23
- **Модель:** Sonnet 4.6 + Medium
- **Триггер:** первая коллизия в `link_chat_to_customer` ответе

### G14 — Telegram-бот (по остаточному принципу)
- **Цель:** минимальный бот для оператора в дороге, read-only через MCP.
- **Префаза:** ADR-001 v2, список MCP-tools
- **Модель:** Opus 4.6 + Medium → Sonnet 4.6 + High
- **Триггер:** реальная мобильная боль

### G15 — ADR-008 addendum (по триггеру)
- **Цель:** процентный порог цен для дорогих позиций.
- **Префаза:** `docs/adr/ADR-008-stock-price-invariant.md`, `06_open_questions.md` запись от 2026-04-22
- **Модель:** Opus 4.6 + Medium
- **Триггер:** первый ложный конфликт на дорогой позиции

### G16 — Спящие риски E1–E17
Активация по таблице E-блока. По месту: Cowork-arch для ADR/аддендумов, Cowork-pf → Claude Code для реализации.

### G17 — MCP-tools для курса валют
- **Цель:** реализовать `get_current_exchange_rate(currency='USD')` и `set_exchange_rate(currency, rate, note?)` MCP-tools. Опционально — обогатить `search_products`/`create_order` отображением применяемого курса.
- **Префаза:** `docs/tool-gaps.md` запись 2026-04-30 о курсе валют, `docs/adr/ADR-003-final-ready-postgres-core-schema.md` (таблица `pricing_exchange_rate`), `docs/adr/ADR-004-pricing-profit-policy.md` (как pricing использует курс)
- **Модель:** Sonnet 4.6 + Medium
- **Артефакт:** `crm-mcp/tools/get_current_exchange_rate.py`, `crm-mcp/tools/set_exchange_rate.py`, тесты
- **CP:** CP16
- **Примечание:** можно объединить с G6 (finance ledger) если решено, что курс — часть finance, или сделать отдельным мини-промтом сейчас

### G18 — Token economy + Mobile capture (ADR-016)
- **Цель:** реализовать ADR-016 двумя независимыми подзадачами: G18.1 — три Cowork live artifact (pending_orders_dashboard, customer_lookup, pricing_reference); G18.2 — Telegram capture-only бот, таблица `inbox_capture`, два MCP-tool (`list_inbox_captures`, `mark_inbox_capture`), launchd plist.
- **Pre-step:** 2 недели авто-сбора через операционный Cowork в `docs/inbox_measurement.md` (период 2026-05-05 — 2026-05-19, эвристики в `cowork-system-prompt.md` раздел 11, загрузка промта через `pili-crm/CLAUDE.md`). Разбор 2026-05-19 в отдельном Cowork-arch чате. Без pre-step можем построить дашборд для несуществующей боли.
- **Префаза:** `docs/adr/ADR-016-token-economy-and-mobile-capture.md` (полный текст, особенно разделы «Что должен сделать Claude Code» и «Что проверить вручную»); `docs/cowork-system-prompt.md` (для русских меток и Telegram deep links — артефакты должны им соответствовать); `ingestion/tg_import.py` (паттерн для Telethon в G18.2); `crm-mcp/tools/get_unreviewed_chats.py` + `link_chat_to_customer.py` (паттерн для двух новых tools).
- **Модель:** Sonnet 4.6 + Medium для обеих подзадач. Реализация по принятому ADR.
- **Артефакты:** `crm-mcp/artifacts/*.html` (3 файла), миграция Alembic для `inbox_capture`, `app/inbox/` модуль, `crm-mcp/tools/list_inbox_captures.py` + `mark_inbox_capture.py`, `ingestion/inbox_bot.py`, `~/Library/LaunchAgents/com.pilistrogai.inbox-bot.plist`, `docs/runbook_inbox_bot.md`, `docs/runbook_artifacts.md`. Обновление `docs/cowork-system-prompt.md` (раздел «Артефакты» и два новых tools).
- **Зависимости:** G18.1 опционально использует G17 для `pricing_reference` (но можно стартовать без него — просто без курса).
- **CP:** CP17
- **Примечание:** G18.2 явно отделён от G14 (полный мобильный CRM). G18.2 — capture-only inbox, никаких write в основные таблицы. G14 активируется ТОЛЬКО если capture-only окажется недостаточно.

---

## Полные инициирующие сообщения для активных групп

### G1 — Финансовый контур ADR-F04 (полное сообщение)

```
Контекст: Я работаю над проектом PiliStrogai CRM. План проекта — pili-crm/docs/PLAN.md. Я запускаю группу G1.

Цель группы G1: принять ADR-F04 (snapshot цен при сохранении заказа). ADR-F06 (хранение валюты в БД) уже принят 2026-04-30 — на него опереться, не переоткрывать.

Префаза (прочитай в этом порядке):
1. pili-crm/docs/adr/000_ADR_REGISTRY.md — секция «Финансовый контур»
2. pili-crm/docs/adr/ADR-F06-currency-storage.md — образец принятого ADR этой серии
3. pili-crm/docs/adr/ADR-004-pricing-profit-policy.md — действующая ценовая политика
4. pili-crm/06_open_questions_archive.md — поискать обсуждения по snapshot/иммутабельности

Модель: Opus 4.6 + High (решение дорогое в откате — рефакторинг snapshot позже потребует миграции исторических заказов)

Чем закончить: создать pili-crm/docs/adr/ADR-F04-price-snapshot.md со статусом accepted. Структура — как в принятых ADR серии F.

Ключевые вопросы для решения:
- Когда именно делается snapshot — при создании order_item или при переходе в статус?
- Что именно сохраняется — final_price или весь price_calculation объект?
- Где хранится — отдельная колонка в orders_order_item или новая таблица orders_price_snapshots?
- Иммутабельность — snapshot можно править вручную или нет?

Чек-лист закрытия:
- [ ] ADR-F04-price-snapshot.md создан, статус accepted
- [ ] 000_ADR_REGISTRY.md — F04 переведён в «Финансовый контур accepted», дата 2026-04-30
- [ ] PLAN.md — статус G1 → ✅, версия плана инкрементируется до v5
- [ ] 01_scope.md — добавить запись о принятии ADR-F04 в «Сделано»
- [ ] CP2 отмечен достигнутым

Реализация ADR-F04 (код, миграция) — это уже G6 (finance ledger), не часть этой группы.
```

### G2 — PC-worker инфраструктура B3 (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G2.

Цель: реализовать инфраструктуру для распределённого прогона analysis/run.py по схеме ADR-011 Addendum 2 (Mac master + PC worker). 386 чатов нужно поделить пополам и прогнать параллельно. Mac перегревается через 1 час непрерывной работы — без PC-worker полный прогон невозможен.

Префаза (читать в этом порядке):
1. pili-crm/docs/adr/ADR-011-addendum-2-pc-worker.md — полный текст. Особое внимание разделам «Технические следствия» и «Sync-скрипт».
2. pili-crm/analysis/run.py — текущие CLI-флаги (--worker-tag, --no-apply из commit 90da591)
3. pili-crm/docs/runbook_pc_deployment.md — что уже развёрнуто на PC
4. pili-crm/06_open_questions.md — запись «ADR-011 Addendum 2» от 2026-04-26: пункты 2 (chat-id-range) и 4 (sync-скрипт)

Модель: Sonnet 4.6 + Medium. Реализация по уже принятому ADR.

Артефакты:
1. CLI-параметры в analysis/run.py:
   - --chat-id-range START..END (включительно)
   - --chat-ids ID1,ID2,... (для добивания остатка)
   - Сочетаются с существующими --worker-tag, --no-apply
   - Тест: запуск с --chat-id-range 1..10 обрабатывает только эти chat_id
2. Скрипт pili-crm/scripts/sync_pc_analyses.sh:
   - Mac пуллит с PC через pg_dump или psql -h <PC_HOST> записи analyzer_version LIKE '%@pc'
   - Синхронизируются ОБЕ таблицы: analysis_chat_analysis И analysis_extracted_identity (X1)
   - Импорт через INSERT ... ON CONFLICT DO NOTHING
   - Логирование: сколько записей пришло, сколько уже было
3. Тест идемпотентности: запустить дважды подряд — второй раз 0 новых записей.

Решения по развилкам (из ADR-011 Addendum 2 — следовать без отклонений):
- Деление 386 чатов: PC = 1..193, Mac = 194..386
- analyzer_version суффикс: @mac (default), @pc (через флаг)
- Sync однонаправленный: Mac тянет с PC
- Конкурентность state — защищена фиксированным делением (E3 как defense-in-depth)

Чек-лист закрытия:
- [ ] CLI-флаги работают, тесты зелёные (regression в tests/analysis/test_run_unit.py)
- [ ] Sync-скрипт прогнан на тестовых данных, идемпотентен
- [ ] Runbook оператора pili-crm/docs/runbook_sync_pc_analyses.md написан
- [ ] 06_open_questions.md — запись ADR-011 Addendum 2, пункты 2 и 4 → ✅
- [ ] PLAN.md — G2 → ✅, CP3 достигнут, инкремент версии
- [ ] 01_scope.md — записи в «Сделано»
- [ ] commit message: «feat(analysis): chat-id-range + sync-pc-analyses (ADR-011 Add-2 G2)»

После закрытия G2 запускается G3 (smoke + полный прогон).
```

### G3 — Smoke + full analysis (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G3 (оператор-вручную).

Важные факты перед стартом (выяснены в G2-сессии 2026-04-30):
- Реальные chat_id в БД: 5941–6790 (не 1..386 — это порядковые номера, не id)
- Деление Mac/PC ОТМЕНЕНО: все 386 client/possible_client чатов идут только на PC
- Preflight classification хранится в колонке `preflight_classification` таблицы `analysis_chat_analysis`
  (analyzer_version = 'v1.0+qwen3-14b'). Запрос 386 чатов:
    SELECT DISTINCT chat_id FROM analysis_chat_analysis
    WHERE analyzer_version = 'v1.0+qwen3-14b'
    AND preflight_classification IN ('client', 'possible_client')
    ORDER BY chat_id;
- Smoke уже начат на chat 6485 (Вячеслав Яшин / @vyashin86) — проверь результат первым делом.
  Chat 6544 (Kristina) уже проанализирован в v1.4+qwen3-14b, smoke на нём не нужен.
- PC доступен для полного прогона. Mac не участвует в прогоне (перегрев).
- --chat-id-range и --review-status взаимоисключающие флаги (один argparse mutex group).
  Чтобы прогнать только непроанализированные: используй --chat-id-range + filter_already_processed
  автоматически пропустит уже готовые.

Цель: прогнать 386 client/possible_client чатов через full analysis pipeline на PC, применить результаты через identity quarantine в Cowork.

Префаза (читать в этом порядке):
1. pili-crm/docs/PLAN.md — раздел «Спящие риски» E3, E4
2. pili-crm/06_open_questions_archive.md — запись о identity quarantine tools
3. pili-crm/crm-mcp/IMPROVEMENTS.md — записи 2026-04-29 о quarantine tools (operational notes)
4. pili-crm/docs/runbook_sync_pc_analyses.md — порядок синхронизации PC→Mac

Модель: оператор-вручную; Cowork = Sonnet 4.6 при apply identity quarantine

Шаги:
1. Проверить smoke на chat 6485:
   - Убедиться что analysis/run.py завершил chat 6485 (SELECT ... WHERE chat_id=6485 AND analyzer_version LIKE 'v1.4%')
   - Sync на Mac: bash scripts/sync_pc_analyses.sh
   - В Cowork: list_pending_identity_updates(customer_id=<id Вячеслава Яшина>) или найти через find_customer
   - Применить: apply_identity_update(extracted_id=..., action='overwrite') для каждой записи
   - Сверить с @vyashin86 что данные корректны
2. Полный прогон на PC (все 386 client/possible_client чатов):
   - Получить список chat_id запросом выше (или сохранить в файл)
   - На PC (nohup, 24/7): python3 -m analysis.run --chat-id-range 5941..6790 --worker-tag pc --no-apply
     (filter_already_processed пропустит preflight-only записи и уже готовые)
   - Мониторинг: SELECT COUNT(*) FROM analysis_chat_analysis WHERE analyzer_version LIKE 'v1.4%@pc';
3. Sync раз в сутки (или после завершения прогона): bash scripts/sync_pc_analyses.sh
4. В Cowork после sync — apply identity quarantine для готовых чатов (get_unreviewed_chats + list_pending_identity_updates)

Чек-лист закрытия:
- [ ] SELECT COUNT(*) FROM analysis_chat_analysis WHERE analyzer_version LIKE 'v1.4%@pc' = 386 (≥ 386 c учётом chat 6544 и 6485)
- [ ] Smoke на chat 6485 — identity Вячеслава применена
- [ ] Все pending identity записи по проанализированным чатам обработаны
- [ ] PLAN.md — G3 → ✅, CP4–CP6 достигнуты, инкремент версии
- [ ] 01_scope.md — запись в «Сделано»
- [ ] G4 готов к запуску

После CP6 запускается G5 (реализация tools), затем G4 (apply через Cowork).
```

### G4 — Apply через Cowork (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G4 (Cowork operator workflow).

Цель: применить результаты full analysis (G3) в боевую БД через Cowork. Параллельно оценить качество vision-template-mismatch (E4).

Префаза:
1. pili-crm/docs/cowork-system-prompt.md
2. pili-crm/crm-mcp/IMPROVEMENTS.md — operational notes для всех 16 tools (после G5)
3. pili-crm/docs/PLAN.md — спящий риск E4

Модель: Cowork = Sonnet 4.6

Важно: G5 закрыта. Доступны tools: apply_pending_analysis, расширенный get_unreviewed_chats (has_analysis + summary), update_customer, update_order.

Известные чаты для smoke-проверки в первую очередь:
- chat 6485 (@vyashin86) → клиент Слава Яшин (id=1688). Чат уже привязан (2026-04-30).
  Анализ прогнан с --no-apply → apply_pending_analysis(6485) создаст identity в карантин.
  Проверить: имя «Вячеслав» vs «Слава», телефон, прочие контакты.
- chat 6544 (Kristina) → уже проанализирован в v1.4+qwen3-14b ранее.
  Проверить list_pending_identity_updates — применить или отклонить записи.
  Внимание: name overwrite заменит placeholder-имя реальным — подтвердить явно.

Workflow:
1. Smoke на известные чаты (6485, 6544) — убедиться что pipeline работает end-to-end
2. Для каждого нерешённого chat (get_unreviewed_chats(limit=50)):
   - get_unreviewed_chats показывает has_analysis + summary — видно что уже проанализировано
   - Если есть похожий клиент → link_chat_to_customer(chat_id, customer_id)
   - Если нет → link_chat_to_customer(chat_id, create_new=True или ignore=True)
3. После привязки чата → apply_pending_analysis(chat_id):
   - Применяет уже готовый анализ к клиенту (identity → карантин, orders → draft)
   - Если анализа нет — пропустить, чат уйдёт в следующий прогон PC
4. Для каждого linked клиента:
   - list_pending_identity_updates(customer_id) → видеть quarantine
   - apply_identity_update(extracted_id, action='overwrite'|'reject'|'add_as_secondary')
   - Внимание: name overwrite критичен (NOT NULL); email_unique_collision — структурированная ошибка
5. Применить orders/preferences через стандартный workflow

Параллельно — оценка E4:
- SELECT COUNT(*) FROM communications_telegram_message_media_extraction WHERE extraction_method = 'vision-template-mismatch' (~98)
- Проверить выборку: повлиял ли маркер [VISION_TEMPLATE_MISMATCH:...] на качество identity-extraction?
- Если шум — открыть запись для G7 или отдельный промт-фактори

Чек-лист закрытия:
- [ ] Все 386 чатов имеют review_status ≠ NULL и ≠ unreviewed
- [ ] Все pending identity записи обработаны (status ≠ pending для записей по 386 чатам)
- [ ] E4 оценена: 💤 (терпимо) или открыт промт на починку
- [ ] PLAN.md — G4 → ✅, CP7 достигнут, инкремент версии
- [ ] 01_scope.md — apply результатов в «Сделано»
```

### G5 — Update tools + Cowork промт (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G5 (две подзадачи в двух чатах).

Цель: реализовать update_customer, update_order и apply_pending_analysis MCP-tools. Затем — обновить cowork-system-prompt.md под 16 tools (13 текущих + 3 новых).

Префаза для подзадачи 1 (реализация tools):
1. pili-crm/docs/tool-gaps.md — записи 2026-04-30 (update_customer, update_order, apply_pending_analysis)
2. pili-crm/crm-mcp/IMPROVEMENTS.md — запись 2026-04-30 (link_chat_to_customer + apply)
3. pili-crm/crm-mcp/tools/create_customer.py, create_order.py — образцы стиля
4. pili-crm/crm-mcp/tools/update_order_item_status.py — пример write-tool с derive-status интеграцией
5. pili-crm/app/analysis/service.py — apply_analysis_to_customer (уже реализован, force=True поддерживается)

Модель подзадачи 1: Sonnet 4.6 + Medium

Артефакты подзадачи 1:
- crm-mcp/tools/update_customer.py: update_customer(customer_id, name?, phone?, telegram_id?, email?). Валидация: customer_id существует, telegram_id не конфликтует (структурированный ответ как в link_chat_to_customer). Email UNIQUE — savepoint как в apply_identity_update.
- crm-mcp/tools/update_order.py: scope первой итерации — добавление позиций (items_to_add). Удаление и price_adjustments — отложить во вторую итерацию, явно зафиксировать в IMPROVEMENTS.md.
- crm-mcp/tools/apply_pending_analysis.py: apply_pending_analysis(chat_id) — ищет последний завершённый analysis_chat_analysis для чата, вызывает apply_analysis_to_customer(force=True), возвращает identities_quarantined, orders_created и т.д. Ошибка если чат не привязан к клиенту (structured error как в apply_identity_update).
- 6-8 тестов на edge cases.

Префаза для подзадачи 2 (Cowork промт):
1. pili-crm/docs/cowork-system-prompt.md
2. pili-crm/crm-mcp/IMPROVEMENTS.md — operational notes для всех 16 tools

Модель подзадачи 2: Opus 4.6 + Medium

Артефакты подзадачи 2:
- Обновлённый docs/cowork-system-prompt.md с описанием update_customer, update_order, apply_pending_analysis, list_pending_identity_updates, apply_identity_update
- Decision tree: update vs create, update_order vs update_order_item_status, когда звать apply_pending_analysis (после link_chat_to_customer)

Чек-лист закрытия:
- [ ] Tests зелёные, ruff/mypy чисто
- [ ] tool-gaps.md — записи 2026-04-30 переведены в done
- [ ] Cowork промт обновлён, обкатан (smoke: link_chat → apply_pending_analysis → list_pending_identity_updates)
- [ ] PLAN.md — G5 → ✅, CP8 достигнут, инкремент версии
- [ ] 01_scope.md — записи в «Сделано»
```

### G5.5 — Draft-заказы (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G5.5.

Цель: реализовать два MCP-tool для работы с draft-заказами из анализа.
Выявлено в smoke chat 6485: apply_pending_analysis создал 26 draft-заказов,
но оператор не может их увидеть через Cowork — pending_orders фильтрует только confirmed+.

Префаза:
1. pili-crm/docs/tool-gaps.md — запись 2026-04-30 (draft-заказы)
2. pili-crm/app/orders/models.py — enum OrderStatus (draft есть), структура OrderItem
3. pili-crm/crm-mcp/tools/apply_pending_analysis.py — образец стиля tool-а
4. pili-crm/crm-mcp/tools/pending_orders.py — образец format_text для заказов

Модель: Sonnet 4.6 + Low

Артефакты:
- crm-mcp/tools/list_draft_orders.py:
    list_draft_orders(customer_id?) — заказы со status=draft, с позициями.
    Фильтр по клиенту опциональный. Read-only, без подтверждения.
    Показывать: order_id, display_id (З-XXX), customer_name, created_at, позиции (название, цена, кол-во).
- crm-mcp/tools/verify_draft_order.py:
    verify_draft_order(order_id, action: "confirm"|"reject") — confirm переводит в status=confirmed,
    reject удаляет заказ и позиции. Требует подтверждения оператора (write-tool).
- Обновить cowork-system-prompt.md: добавить оба tool-а, описать workflow верификации.
- 4-6 тестов на edge cases (нет draft-заказов, неверный order_id, повторный confirm).

Критерий готовности (CP8.5):
- list_draft_orders возвращает черновики клиента Слава Яшин (id=1688) — ~26 заказов
- verify_draft_order confirm/reject работает корректно
- Tests зелёные, ruff/mypy чисто

Чек-лист закрытия:
- [ ] Tests зелёные, ruff/mypy чисто
- [ ] tool-gaps.md — запись draft-заказы → done
- [ ] cowork-system-prompt.md обновлён
- [ ] PLAN.md — G5.5 → ✅, CP8.5 достигнут, инкремент версии
- [ ] 01_scope.md — запись в «Сделано»
```

### G7 — ADR-007/008 Пакет 3 + Mirror live (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G7 (две параллельные подзадачи).

Цель: Пакет 3 ADR-007/008 (MCP-tools + интеграция hooks) + crm-mcp/mirror/ по ADR-005.

Префаза для подзадачи 1 (Пакет 3):
1. pili-crm/docs/adr/ADR-007-listings-and-price-history.md (или раздел в core schema)
2. pili-crm/docs/adr/ADR-008-stock-price-invariant.md
3. pili-crm/06_open_questions.md (или archive) — записи о hooks on_purchase_delivered / on_warehouse_receipt_item_created (HIGH — без них инвариант ADR-008 не работает)
4. Запись о маркере __immutable__ для моделей

Модель подзадачи 1: Sonnet 4.6 + High

Артефакты подзадачи 1:
- MCP-tools: list_pending_price_resolutions, resolve_price_resolution(resolution_id, action='keep_old'|'use_new'|'weighted_average')
- Обогащение search_products: stock_price_rub из stock_item.price_calculation.final_price + массив listings
- Атрибут __immutable__ = True на моделях, рефактор _IMMUTABLE_MODELS на использование маркера
- Интеграция hooks в той же транзакции: смена procurement_purchase.status → on_purchase_delivered; создание warehouse_receipt_item → on_warehouse_receipt_item_created
- Тесты: разрешение конфликта, __immutable__, hook вызывается

Префаза для подзадачи 2 (Mirror):
1. pili-crm/docs/adr/ADR-005-mirror-google-sheets.md
2. pili-crm/06_open_questions.md — выбор библиотеки (gspread vs google-api-python-client)
3. pili-crm/app/main.py — текущий lifespan

Модель подзадачи 2: Sonnet 4.6 + High

Артефакты подзадачи 2:
- Решение по библиотеке (зафиксировать с обоснованием)
- Модуль crm-mcp/mirror/ с экспортом всех таблиц (полная пересборка раз в сутки)
- Lifespan-триггер в FastAPI: при старте — экспорт, далее APScheduler раз в 24 часа
- Тесты на маппинг таблица → вкладка

Чек-лист закрытия G7:
- [ ] Hooks интегрированы и протестированы end-to-end
- [ ] Mirror работает: записи появляются в Google Sheets после смены данных
- [ ] open_questions: выбор библиотеки, hooks, __immutable__ → закрыты в архиве
- [ ] IMPROVEMENTS.md запись о search_products без цены → done
- [ ] PLAN.md — G7 → ✅, CP10 достигнут, инкремент версии
- [ ] 01_scope.md — записи в «Сделано»
```

### G8 — FastAPI autostart (полное сообщение)

```
Контекст: PiliStrogai CRM. План — pili-crm/docs/PLAN.md. Запускаю G8.

Цель: автозапуск FastAPI при старте macOS через launchd. /health endpoint уже реализован.

Префаза:
1. pili-crm/06_open_questions_archive.md (или active) — запись от 2026-04-22 о решении вариант 1 (launchd + health-check, принято 2026-04-27)
2. pili-crm/app/main.py — убедиться, что /health работает
3. pili-crm/ingestion/ или pili-crm/docs/ — посмотреть, есть ли уже plist (для tg-incremental, G9)

Модель: Sonnet 4.6 + Medium

Артефакты:
1. plist ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist (ASCII, без кириллицы):
   - RunAtLoad=true, KeepAlive=true
   - WorkingDirectory=/Users/<user>/pili-crm
   - ProgramArguments: python3 + -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   - StandardOutPath/StandardErrorPath в logs/fastapi-*.log
   - EnvironmentVariables — DATABASE_URL либо .env через pydantic-settings
2. logs/ в репо, в .gitignore
3. Runbook pili-crm/docs/runbook_fastapi_autostart.md:
   - Установка/остановка/перезапуск через launchctl
   - Где смотреть логи
   - Что делать если не запускается
   - Опционально — alias crm-status

Чек-лист закрытия:
- [ ] plist в ~/Library/LaunchAgents/, launchctl load без ошибок
- [ ] После reboot Mac — FastAPI запустился, curl http://localhost:8000/health = ok
- [ ] Если процесс убить — KeepAlive перезапустил
- [ ] Runbook написан и проверен
- [ ] open_question от 2026-04-22 → закрыт в архиве
- [ ] PLAN.md — G8 → ✅, CP11 достигнут, инкремент версии
- [ ] 01_scope.md — запись в «Сделано»
```

---

## Развёртывание скелетов в полные сообщения (для G6, G9–G17)

Когда подходит время запуска одной из этих групп:

1. Открой одноразовый **Cowork-arch** чат (Opus 4.6 + Medium, ~15-20 минут)
2. Дай ему контекст: «Развёрни скелет группы G_X из `pili-crm/docs/PLAN.md` в полное инициирующее сообщение по шаблону из `CLAUDE.md` секция Документооборот → Шаблон инициирующего сообщения. Проверь актуальность скелета — возможно, контекст изменился (закрылись зависимости, появились новые ADR). Также проверь, какой тип Cowork-чата нужен для запуска (Cowork-arch / Cowork-pf / Cowork-operator).»
3. Cowork-arch выдаёт готовое сообщение к копированию.
4. Опционально — обновить план, добавив развёрнутое сообщение в раздел «Полные инициирующие сообщения».
