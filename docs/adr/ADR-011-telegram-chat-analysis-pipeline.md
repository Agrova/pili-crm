# ADR-011: Пайплайн анализа Telegram-переписки локальной LLM

**Статус:** accepted (Tasks 1–3 closed 2026-04-24)
**Дата:** 2026-04-24 (создан) / 2026-04-24 (Tasks 1–3 закрыты)
**Связанные ADR:** ADR-001 v2 (модульный монолит), ADR-003 final-ready (core schema), ADR-009 (Telegram profile schema), ADR-010 (Telegram ingestion pipeline)
**Связанные документы:** `06_open_questions.md`, `tool-gaps.md`, `ADR-011-addendum-1.md` (расхождения с реальной схемой), исторический артефакт `/Users/protey/Downloads/tool-shop-crm/`

---

## Контекст

После ADR-010 Задания 1 в БД `pili-crm` импортированы 64 785 сообщений по 303 Telegram-чатам. ADR-010 Задание 2 реализовало очередь модерации — оператор через Cowork присваивает каждому чату `review_status` (linked / new_customer / ignored). К 2026-04-23 оператор обкатал три режима `link_chat_to_customer` на трёх чатах.

**Проблема 1.** Очередь из 303 чатов слишком велика для ручного разбора «в холодную». Оператор не помнит наизусть, кто такой «Сергей» или «Иван» из 2024 года, какие у него были заказы, что он покупал. Принятие решения (ignore/link/new_customer) требует контекста переписки, а MCP-tool `get_unreviewed_chats` возвращает только превью первого и последнего сообщения (`tool-gaps.md`).

**Проблема 2.** Даже когда клиент создан (режим `new_customer`) или привязан (`linked`), JSONB-поля `orders_customer_profile.preferences` / `delivery_preferences` / `incidents` из ADR-009 остаются пустыми. Без них профиль клиента в pili-crm сводится к имени и telegram-идентификаторам. Оператор не знает, что этот клиент любит Veritas, живёт в Краснодаре, ждёт СДЭК на вечер и в марте жаловался на царапину.

**Проблема 3.** История заказов клиентов до 2026 существует только в переписке. В `orders_order` находятся 62 заказа seed MVP. Реальная история продаж — в 64 785 Telegram-сообщениях, нигде не структурированная.

**Прошлый опыт.** В `/Users/protey/Downloads/tool-shop-crm/` лежит попытка решить эти проблемы: Python-скрипт `tg_analyze.py` + Qwen3-14B через LM Studio. Результат (апрель 2026) оказался частично рабочим: `tg_scan_results.json` с классификацией 303 чатов — качественно, `topics/*.json` с профилями — чисто, `inbox/*.md` с заказами — дефектно. Три причины дефектов:

- `max_msgs=150` — для крупных чатов (Василий 5513 сообщений) анализировалось <3% истории
- `max_tokens=2048` — JSON-ответы обрезались на середине на насыщенных клиентах
- Жёсткая схема слотов, навязанная Python-скриптом — модель не могла свободно рассуждать о клиенте, заполняла только предусмотренные поля, часто с ошибками типов (поле «телефон» = «9.5, 21, 22» — размеры свёрел из заказа)

**Решение нужно принять сейчас**, потому что:

- Без обогащения профилей ручной разбор 303 чатов упирается в потолок памяти оператора — минуты на чат превращаются в часы
- Без истории заказов из переписки Finance ledger и любая аналитика (следующие задачи в `01_scope.md`) строятся на seed MVP, а не на реальности бизнеса
- Прошлый артефакт `tool-shop-crm` нельзя переиспользовать напрямую (дефекты), но можно использовать как baseline для нового пайплайна

**Что не обсуждается в этом ADR** (зафиксировано в других):

- Схема `orders_customer_profile.preferences / delivery_preferences / incidents` — ADR-009
- Схема `orders_order` / `orders_order_item` — ADR-003
- Confidence-модель `manual` / `suggested` / `auto` — ADR-003, ADR-009
- `review_status` чатов и MCP-tools очереди модерации — ADR-010
- Обратное решение «не предлагать локальные модели в оперативной архитектуре CRM» — ADR-001 v2 (касается агентской логики с tool-calling, не касается батчевого анализа текста)

## Варианты

### Вариант A — Refactor старого скрипта `tg_analyze.py`

Взять `/Users/protey/Downloads/tool-shop-crm/scripts/tg_analyze.py`, починить три дефекта (убрать `max_msgs=150`, поднять `max_tokens`, добавить post-validation), прогнать заново на свежих данных из JSON-экспорта.

- **Плюсы:** минимум работы; знакомый код; быстрый результат.
- **Минусы:** сохраняется жёсткая схема слотов, модель не рассуждает свободно; источник данных остаётся JSON-выгрузка (разрыв с pili-crm как источником истины); нет чекпоинтов и resume; результат не структурирован по confidence и не маппится на схему ADR-009.

### Вариант B — Новый пайплайн с model-driven подходом (выбран)

Новый модуль `analysis/` в монолите. Скрипт читает переписку из `communications_telegram_message`, разбивает на иерархические чанки, пропускает через Qwen3-14B двумя проходами: проход 1 — свободный markdown-narrative о клиенте, проход 2 — извлечение структурированного JSON из собственного markdown первого прохода. Результаты складываются в новую таблицу `analysis_chat_analysis` (narrative + structured JSON, immutable) и в JSONB-поля `orders_customer_profile` ADR-009 (с `confidence=suggested`). Извлечённые заказы создаются в `orders_order` со `status=draft`. Оператор верифицирует через Cowork, `suggested` → `manual`, `draft` → `confirmed`.

- **Плюсы:** соответствует проектной архитектуре (pili-crm — источник истины, модульный монолит, confidence-модель ADR-009); модель свободна в рассуждении; полное покрытие истории без лимита на сообщения; чекпоинты и resume для длительных прогонов; инкрементальный режим совместим с ADR-010 Задание 3 (Telethon); результаты сразу видны в Cowork через существующие MCP-tools с расширением.
- **Минусы:** больше кода чем Вариант A; двухпроходная модель удваивает время инференса; новая таблица `analysis_chat_analysis` добавляет сущность в схему; требуется дисциплина при ревью Pydantic-схем второго прохода.

### Вариант C — Отказаться от автоматического анализа, работать через Cowork по одному чату

Не писать скрипт. При ручном разборе чата в Cowork оператор использует `get_chat_messages` (новый tool) для чтения переписки, Claude в Cowork сам обогащает профиль через существующие tools.

- **Плюсы:** минимум инфраструктуры; нулевые затраты на локальную модель; гибкость — Claude в Cowork рассуждает лучше Qwen.
- **Минусы:** стоимость — 303 чата × длительная сессия с Claude = сотни тысяч токенов API; UX — оператор проводит часы в диалоге с Cowork вместо работы с готовыми данными; не масштабируется — каждый новый клиент требует повторного чтения всей его переписки.

## Критерии выбора

- **Надёжность:** детерминированность расчётов не применима (LLM — вероятностный компонент), но детерминированны чекпоинты, идемпотентность, схема результата
- **Простота поддержки:** модуль `analysis/` изолирован, запускается независимо, не вовлечён в горячий путь CRM
- **Простота интеграции с Claude Code и MCP-сервером:** результаты анализа доступны через MCP-tools, оператор не взаимодействует со скриптом напрямую
- **Совместимость с текущим стеком (ADR-002):** Python 3.12, SQLAlchemy 2.0 async для чтения переписки; LM Studio через HTTP как внешняя зависимость — не влияет на стек CRM
- **Сохранение PostgreSQL как единственного источника истины:** исходные данные читаются из БД, результаты пишутся в БД, промежуточные файлы не являются источником правды
- **Масштабируемость:** инкрементальный режим, watermark по чатам, возможность пересчёта отдельного чата
- **Безопасность данных:** переписка не покидает mac оператора — Qwen3-14B локальна, LM Studio на localhost

## Принятое решение

**Вариант B — новый пайплайн с model-driven подходом.**

### 1. Новый модуль `analysis/`

Добавляется девятый прикладной модуль монолита. По правилам ADR-001 v2 и ADR-003:

- Код в `app/analysis/` (sqlalchemy-модели, сервисы, pydantic-схемы)
- Скрипт запуска в `analysis/` на корневом уровне репозитория (вне `app/`, аналогично `ingestion/` из ADR-010) — `analysis/run.py`
- Одна таблица в БД: `analysis_chat_analysis` (одна сущность — один файл модели, одна миграция)

Модуль **не добавляется** в список таблиц `02_entities.md` автоматически — обновление этого документа входит в задачи реализации.

### 2. Схема таблицы `analysis_chat_analysis`

| Поле | Тип | Назначение |
|---|---|---|
| `id` | `BIGINT PK` | Суррогатный ключ |
| `chat_id` | `BIGINT FK → communications_telegram_chat ON DELETE CASCADE` | Анализируемый чат |
| `analyzed_at` | `TIMESTAMPTZ NOT NULL` | Момент завершения анализа |
| `analyzer_version` | `TEXT NOT NULL` | Версия скрипта + модели, например `v1.0+qwen3-14b` |
| `messages_analyzed_up_to` | `TEXT NOT NULL` | `telegram_message_id` последнего сообщения, вошедшего в анализ (watermark) |
| `narrative_markdown` | `TEXT NOT NULL` | Результат прохода 1 — свободный markdown от Qwen |
| `structured_extract` | `JSONB NOT NULL` | Результат прохода 2 — структурированный JSON |
| `chunks_count` | `INTEGER NOT NULL` | Сколько чанков потребовалось (1 для малых чатов, много для крупных) |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | Стандартные timestamps ADR-003 |

Constraints:

- UNIQUE `(chat_id, analyzer_version)` — один результат на пару «чат + версия анализатора». Повторный прогон той же версии заменяет запись через `ON CONFLICT DO UPDATE`; новая версия создаёт новую запись, старые сохраняются как история.
- INDEX `chat_id`
- INDEX `analyzed_at`

`analysis_chat_analysis` — **архив**, не активный профиль. Активный профиль клиента — это `orders_customer_profile` (ADR-009). Narrative и structured_extract — это доказательная база, на которой построен профиль, хранится навсегда на случай пересмотра.

### 3. Схема `structured_extract` (результат прохода 2)

Pydantic-модель в `app/analysis/schemas.py`. Все поля nullable — модель может не найти ни одного значения.

```json
{
  "_v": 1,
  "identity": {
    "name_guess": "Сергей Иванов",
    "telegram_username": "s_drilling",
    "phone": "+7...",
    "email": null,
    "city": "Казахстан, Алматы",
    "confidence_notes": "Имя упомянуто в первом сообщении, телефон в переписке про отправку"
  },
  "preferences": [
    {"product_hint": "Veritas зензубель", "note": "Интересовался несколько раз", "source_message_ids": ["123", "456"]}
  ],
  "delivery_preferences": {
    "method": "СДЭК",
    "preferred_time": "вечер",
    "notes": null
  },
  "incidents": [
    {"date": "2025-03-15", "summary": "Царапина на товаре, договорились на скидку 5%", "resolved": true, "source_message_ids": ["789"]}
  ],
  "orders": [
    {
      "description": "Veritas зензубель #5",
      "items_text": "1 шт. зензубель Veritas 05P44.01",
      "amount": 30600,
      "currency": "RUB",
      "status_delivery": "delivered",
      "status_payment": "paid",
      "date_guess": "2025-02-20",
      "source_message_ids": ["234", "235"]
    }
  ],
  "payments": [
    {
      "amount": 30600,
      "currency": "RUB",
      "method": "bank_transfer",
      "date_guess": "2025-02-22",
      "source_message_ids": ["240"]
    }
  ]
}
```

Семантика:

- `status_delivery`: `ordered` / `shipped` / `delivered` / `returned` / `unknown`
- `status_payment`: `unpaid` / `partial` / `paid` / `unknown`
- Платежи **не привязаны** к заказам на этом этапе — в Telegram-переписке привязка часто неявная. Сведение «кто кому должен» — работа оператора при верификации.

### 4. Процесс анализа — четыре фазы

**Фаза 1. Чанкинг.** Скрипт загружает сообщения чата из `communications_telegram_message` (только `text IS NOT NULL`, отсортированные по `sent_at`). Разбивает на чанки фиксированного размера (например, 300 сообщений на чанк — параметр конфигурации). Если чат меньше чанка — один чанк, фаза 2 тривиальна.

**Фаза 2. Иерархическая саммаризация** (только для чатов длиннее одного чанка). Каждый чанк отправляется Qwen с промтом «составь краткое саммари всего важного: факты о клиенте, заказы, платежи, адреса». Саммари чанков объединяются в мастер-саммари тем же способом рекурсивно, пока суммарный объём не уложится в контекстное окно модели.

**Фаза 3. Проход 1 — свободный нарратив.** На вход Qwen подаётся мастер-саммари (или весь чат, если влезает). Промт просит написать свободный markdown-портрет клиента: идентификация, интересы, история заказов, платежей, получений, инциденты, любые наблюдения, которые модель считает важными. Без навязанной структуры. Результат — в `narrative_markdown`.

**Фаза 4. Проход 2 — извлечение JSON.** На вход Qwen подаётся `narrative_markdown` первого прохода (не сырая переписка). Промт просит заполнить JSON по схеме `structured_extract`. Поля могут остаться null, если в narrative нет информации. Результат валидируется Pydantic в `app/analysis/schemas.py`. Если валидация падает — retry до 3 раз, после этого запись помечается как failed в `analyzer_state`, чат пропускается, скрипт идёт дальше.

Выход фазы 4 пишется в `analysis_chat_analysis`, параллельно — в `orders_customer_profile` и `orders_order` (см. фазу 5).

### 5. Запись результатов в активные таблицы

**Если у чата `review_status IN ('linked', 'new_customer')`** — есть связанный клиент, ссылка через `communications_telegram_chat.customer_id` (поле добавляется ADR-010 Задание 2, должно уже существовать в схеме):

- `structured_extract.preferences` → `orders_customer_profile.preferences` JSONB с `confidence="suggested"`
- `structured_extract.delivery_preferences` → `orders_customer_profile.delivery_preferences` с `confidence="suggested"`, `is_primary=false` (оператор вручную подтверждает primary)
- `structured_extract.incidents` → `orders_customer_profile.incidents` с `confidence="suggested"`
- `structured_extract.orders[]` → записи в `orders_order` со `status="draft"`, позиции без `product_id` (в `orders_order_item` поле `product_id` NOT NULL — см. раздел «Открытые вопросы»)
- `structured_extract.payments[]` — не записываются автоматически в финансовые таблицы; остаются только в `analysis_chat_analysis.structured_extract`. Финансовые записи (`finance_ledger_entry`, отдельный ADR на finance) оператор создаёт вручную при верификации заказа.

**Если у чата `review_status IN ('unreviewed', 'ignored', NULL)`** — клиента нет, писать некуда. Только `analysis_chat_analysis` заполняется. Оператор, разбирая очередь модерации, видит готовый анализ при принятии решения — и после привязки/создания клиента получает отдельный MCP-tool `apply_analysis_to_customer` для переноса результатов в профиль (см. задачи Claude Code).

### 6. Чекпоинты и resume

Отдельная таблица `analysis_chat_analysis_state` (или одна дополнительная колонка в `analysis_chat_analysis` — на усмотрение Claude Code в рамках задачи; предпочтительна отдельная таблица, чтобы не засорять архив незавершёнными записями):

| Поле | Тип | Назначение |
|---|---|---|
| `chat_id` | `BIGINT PK` | Чат в обработке |
| `stage` | `TEXT` | `chunking` / `chunk_summaries` / `master_summary` / `narrative` / `extract` / `done` / `failed` |
| `chunks_done` | `INTEGER` | Прогресс фазы 2 |
| `chunks_total` | `INTEGER` | Всего чанков для этого чата |
| `partial_result` | `JSONB NULL` | Саммари уже обработанных чанков, markdown первого прохода |
| `failure_reason` | `TEXT NULL` | При `stage='failed'` |
| `updated_at` | `TIMESTAMPTZ` | Для heuristics по hard shutdown |

Гранулярность чекпоинтов — средняя: после каждого завершённого чанка `UPDATE analysis_chat_analysis_state SET ...; COMMIT`. После каждого завершённого чата запись в `analysis_chat_analysis` + удаление из `analysis_chat_analysis_state`.

При запуске `analysis/run.py`:

1. Если есть записи в `analysis_chat_analysis_state` со `stage != 'done'` и `updated_at` старше 10 минут — считаем, что это прерванные сессии. Спрашиваем оператора через CLI: продолжить (resume) или откатить (restart этих чатов).
2. Если есть записи со свежим `updated_at` — другой процесс работает, не запускаемся.
3. Дальше обычный прогон: выбираем чаты по критерию (см. CLI), пропускаем уже завершённые (`chat_id IN analysis_chat_analysis WHERE analyzer_version = CURRENT_VERSION`), идём по одному.

### 7. CLI интерфейс

Скрипт `analysis/run.py`:

| Флаг | Назначение |
|---|---|
| `--chat-id N` | Один чат по id |
| `--chat-ids "1,2,3"` | Список чатов |
| `--all` | Все чаты с `text IS NOT NULL` сообщениями |
| `--since YYYY-MM-DD` | Чаты, где есть сообщения новее даты (для инкрементальных прогонов после Telethon) |
| `--review-status unreviewed` | Чаты с указанным `review_status` |
| `--dry-run` | Показать, сколько чатов и сообщений попадёт, не запускать инференс |
| `--status` | Показать состояние `analysis_chat_analysis_state` — что в работе, что failed |
| `--resume` | После SIGINT — продолжить прерванную сессию. Без флага — спросить в CLI |
| `--force` | Перезапустить анализ для чатов, которые уже есть в `analysis_chat_analysis` с той же версией анализатора |
| `--model-endpoint URL` | Адрес LM Studio, дефолт `http://localhost:1234/v1` |
| `--chunk-size N` | Размер чанка в сообщениях, дефолт 300 |

Ctrl+C: SIGINT → graceful — дождаться окончания текущего вызова LM Studio, зафиксировать `analysis_chat_analysis_state`, выйти с кодом 0. Повторный Ctrl+C — force kill.

### 8. Версионирование

`analyzer_version` в коде — константа в `app/analysis/__init__.py`, инкрементируется при любом изменении промта или схемы `structured_extract`. Формат: `vMAJOR.MINOR+MODEL`, например `v1.0+qwen3-14b`. При смене модели или промта — новая версия, существующие записи не затираются.

### 9. Источник модели

Локальная Qwen3-14B через LM Studio, `http://localhost:1234/v1`, OpenAI-совместимый endpoint. Модель определяется автоматически через `/v1/models`. Без `enable_thinking` (проверено на прошлом артефакте — thinking-режим замедляет без выигрыша в качестве для этой задачи). Никаких агентских сценариев — только синхронные запросы с текстом на входе и текстом на выходе. Решение ADR-001 v2 об отказе от локальных моделей в оперативной архитектуре CRM не нарушается: анализатор — батчевый процесс на mac оператора, не часть горячего пути CRM, не агент.

## Последствия

### Что становится проще

- Оператор при разборе очереди модерации видит готовый портрет клиента из narrative + структурированные поля — решения принимаются в разы быстрее
- Профили клиентов в pili-crm обогащаются автоматически (с `confidence=suggested`) вместо пустых полей
- История заказов из переписки попадает в `orders_order` со `status=draft`, становится видимой в системе
- Инкрементальные прогоны после Telethon incremental (ADR-010 Задание 3) — одна команда `analysis/run.py --since DATE`
- Прошлый опыт `tool-shop-crm` не теряется — `tg_scan_results.json` с классификацией чатов можно загрузить отдельной маленькой задачей для обогащения `review_status` (см. Открытые вопросы)

### Какие ограничения появляются

- Зависимость от LM Studio запущенного на mac оператора. Без LM Studio `analysis/run.py` не запускается. Это локальный инструмент, не production-сервис.
- Время инференса на Mac без GPU — медленно. Полный прогон 303 чатов может занять 10+ часов. Отсюда требование resume и чекпоинтов.
- Галлюцинации Qwen3-14B. Выходная информация имеет `confidence=suggested` и требует ручной верификации оператором. Никакие финансовые операции не создаются автоматически.
- Новая сущность `analysis_chat_analysis` в схеме БД. Вторая таблица `analysis_chat_analysis_state` — временная (чекпоинты), но тоже в схеме.
- Заказы из анализа попадают в `orders_order` без `product_id` в позициях — это нарушает текущую схему `orders_order_item` (NOT NULL FK на `catalog_product`). Требуется решение: либо расширение схемы orders (допуск `product_id=NULL` с `product_name_hint TEXT`), либо отложить запись заказов до матчинга на каталог. См. Открытые вопросы.
- Pydantic-схема `structured_extract` развивается отдельно от ADR-009 JSONB. Синхронизация на семантическом уровне (семантика `preferences` должна соответствовать ADR-009), но не на уровне одного файла схем.

### Что придётся учитывать дальше

- При изменении промтов или схемы `structured_extract` — инкремент `analyzer_version`. Старые записи остаются как история.
- При смене модели (Qwen на что-то ещё) — тоже инкремент версии.
- MCP-tool `get_unreviewed_chats` (ADR-010 Задание 2) должен быть расширен: при наличии записи в `analysis_chat_analysis` возвращать narrative и ключевые факты в превью. Иначе оператор не увидит результат анализа при разборе очереди.
- UI верификации draft-заказов — новые MCP-tools (`list_draft_orders_for_customer`, `verify_order_item` с редактированием полей, `promote_order_to_confirmed`). Это UX-задача в Cowork.
- Finance ledger (следующая задача в `01_scope.md`) получит доступ к истории заказов клиентов начиная с этого ADR. До этого ledger строился бы на seed MVP.

## Что должен сделать Claude Code

Список задач для Prompt Factory. Каждая задача — отдельный промт, порядок важен (зависимости между задачами).

1. **Миграция схемы.** Новая Alembic-миграция: таблица `analysis_chat_analysis` со всеми полями и constraints (раздел 2), таблица `analysis_chat_analysis_state` (раздел 6). Reversibility проверена. SQLAlchemy-модели в `app/analysis/models.py`. Pydantic-схемы в `app/analysis/schemas.py` для `structured_extract` (раздел 3). Обновление `02_entities.md` — добавить модуль `analysis` в карту таблиц.

2. **Репозиторий и сервис.** Минимальный слой доступа в `app/analysis/repository.py` (CRUD для `analysis_chat_analysis` и `analysis_chat_analysis_state`) и сервисный слой в `app/analysis/service.py` (запись результата анализа в БД + условная запись в `orders_customer_profile` и `orders_order` — см. раздел 5). Граница модуля не нарушается: запись в чужие таблицы — только через публичные интерфейсы `orders` и `communications`.

3. **Скрипт анализатора.** `analysis/run.py` — CLI и оркестрация (раздел 7). Фазы 1–4 (раздел 4). Чекпоинты (раздел 6). Коммуникация с LM Studio через `httpx`. Retry-логика на уровне вызовов модели. Graceful shutdown на SIGINT. Интеграционные тесты с mock LM Studio.

4. **MCP-tool `apply_analysis_to_customer`.** Для сценария, когда чат был `unreviewed` при моменте анализа, анализ записан только в `analysis_chat_analysis`, а потом оператор решил создать/привязать клиента. Tool читает последний `analysis_chat_analysis` для чата, применяет к только что созданному/привязанному клиенту (раздел 5).

5. **Расширение MCP-tool `get_unreviewed_chats`.** Добавить в ответ для каждого чата поля `has_analysis: bool`, `analysis_summary: str | null` (первые ~300 символов narrative), `extracted_name_guess: str | null`. Оператор видит аналитику сразу при разборе очереди.

6. **MCP-tools для работы с draft-заказами.** `list_draft_orders` (с фильтром по customer_id и origin=analysis), `verify_order` (перевод в `confirmed` с редактированием полей), `delete_draft_order` (отклонение false-positive извлечения). Эти tools нужны оператору в Cowork для верификации извлечённых анализом заказов.

Задачи 4–6 могут идти параллельно после Задачи 3. Задача 1 блокирует всё.

## Что проверить вручную

- [ ] Миграция создаёт обе таблицы и все constraints; `downgrade -1` откатывает чисто
- [ ] На пустом `analysis_chat_analysis` запуск `analysis/run.py --all --dry-run` показывает ожидаемое число чатов и сообщений
- [ ] Прогон `analysis/run.py --chat-id N` на одном малом чате (из имеющихся 303): запись в `analysis_chat_analysis` появилась, narrative заполнен, `structured_extract` валиден по Pydantic
- [ ] Прогон на одном крупном чате (5000+ сообщений): чанкинг проходит, запись появляется, `chunks_count > 1`, narrative отражает полную историю (можно сверить с памятью оператора)
- [ ] Ctrl+C в середине крупного чата: запись в `analysis_chat_analysis_state` со `stage != 'done'`, повторный запуск с `--resume` продолжает с чекпоинта
- [ ] Повторный запуск без `--force` пропускает уже обработанные чаты
- [ ] Для чата с `review_status='linked'` после анализа — `orders_customer_profile` клиента содержит `preferences` с `confidence='suggested'`; `orders_order` содержит новые записи со `status='draft'`
- [ ] Для чата с `review_status='unreviewed'` — только `analysis_chat_analysis` заполнен, в `orders_*` ничего нового
- [ ] MCP-tool `get_unreviewed_chats` возвращает `has_analysis=true` и summary для проанализированных чатов
- [ ] Один цикл ручной верификации: оператор видит draft-заказ в Cowork, открывает, правит, verify — заказ переходит в `confirmed`
- [ ] Ссылки в ADR и обновление `02_entities.md`, `01_scope.md` (перевод задачи «Реальная заливка» в «Сделано» когда применимо)

## Открытые вопросы

Передаются в `06_open_questions.md` с указанием чата для разрешения.

- **Q (Архитектурного штаба) — ✅ ЗАКРЫТ через ADR-011 Task 3 (2026-04-24):** `orders_order_item.product_id` NOT NULL FK на `catalog_product`. Анализатор не умеет матчить позиции на каталог — в переписке «Veritas зензубель #5» это текст, не SKU. **Принято решение (3): предварительный fuzzy-matching через `rapidfuzz` (token_set_ratio) + LLM-arbitration через Qwen для неоднозначных случаев.** Параметры: top-20 кандидатов, confident-threshold ≥ 85 с margin ≥ 15 от второго. Ambiguous и not_found позиции пишутся в новую таблицу `analysis_pending_order_item` (не в `orders_order_item`), что сохраняет инвариант NOT NULL FK. Реализация: `analysis/matching.py`, коммит `6a1bbb9`.
- **Q (для Архитектурного штаба):** как обогатить существующий `review_status` автоматической классификацией. `tg_scan_results.json` из `tool-shop-crm` содержит классификацию 303 чатов (175 client / 112 unknown / 8 friend / 4 service / 1 family) с хорошей точностью. Можно загрузить одной маленькой задачей для Prompt Factory и использовать при разборе очереди. Но нужно решить: менять `review_status` enum (добавить `friend` / `family` / `service` как значения), или добавить отдельное поле `auto_classification TEXT` с оригинальными метками классификатора. Первое путает семантику (review_status — статус решения оператора, не классификатор), второе чище.
- **Q (для Prompt Factory при реализации задачи 3) — ✅ ЗАКРЫТ через ADR-011 Task 3 (2026-04-24):** точное значение параметра `chunk_size`. **Принято: дефолт 300 сообщений, конфигурируется через `--chunk-size N`.** Эмпирический подбор остаётся актуальным — после первых прогонов на крупных чатах оператор может скорректировать дефолт.
- **Q (для Архитектурного штаба):** policy повторных прогонов при обновлении `analyzer_version`. Если вышла новая версия промта — пересчитывать все чаты автоматически или только по явной команде `--version-upgrade`? Старые результаты хранить вечно или удалять после N версий? **Связанный частный случай:** policy очистки накопившихся draft-заказов от устаревших версий — отдельный вопрос в `06_open_questions.md` (low priority).
- **Q (для Архитектурного штаба при появлении следующего ADR на Finance):** платежи из `structured_extract.payments` — должны ли автоматически порождать `finance_ledger_entry` после верификации заказа, или это отдельное действие оператора? Сейчас ADR-011 явно не создаёт финансовых записей.

---

## Реализация — состояние на 2026-04-24

### Закрытые задачи

- **Task 1** — Schema для модуля `analysis` (4 таблицы + state-чекпоинты). Коммиты `db2618f`, `ccc9513`, `6e29202`. Преколумны для preflight-импорта (Task 1 ADR-013) добавлены отдельной миграцией.
- **Task 2** — Сервисный + репозиторный слой `app/analysis/`: `record_full_analysis`, `record_skipped_analysis`, `apply_analysis_to_customer`, чекпоинты state, `MultipleCustomersForChatError`. Коммиты `597199a`, `3a9aa32`, `7ea8a07`, `9c632b0`. Расхождения с этим ADR задокументированы в `ADR-011-addendum-1.md`.
- **Task 3** — CLI runner `analysis/run.py` + helper modules (`analysis/chunking.py`, `analysis/llm_client.py`, `analysis/prompts.py`, `analysis/matching.py`, `analysis/state_check.py`). Коммиты `6a1bbb9`, `69f6527`, `8766caf`. Найден и пофикшен баг `_strip_json_fence` (markdown-fence parsing), две регрессионные тестовые проверки.

### Задачи в очереди

- **Task 4** — MCP tools: `apply_analysis_to_customer` (точечное применение через Cowork), расширение `get_unreviewed_chats` (возвращает `has_analysis` + summary).
- **Task 5** — MCP tool `start_analysis_run` (опционально — запуск через Cowork без терминала).
- **Task 6** — MCP tools оркестрации drafts: `list_draft_orders`, `resolve_pending_item`, `verify_order`, `delete_draft_order`. Понадобится после первых ~30–50 прогонов, когда накопится достаточно draft-заказов.

### Уточнённые параметры реализации (Task 3)

**Catalog matching (раздел 5):**

- Библиотека: `rapidfuzz` (>=3.6)
- Scorer: `fuzz.token_set_ratio` (устойчив к порядку слов и частичным совпадениям)
- Top-N кандидатов: 20
- **Confident-threshold:** ≥ 85 — если только один кандидат проходит порог с разрывом ≥ 15 пунктов от второго кандидата (margin rule), позиция помечается `confident_match` без обращения к LLM.
- **Discard-threshold:** < 40 — кандидаты ниже этого порога не попадают в top-20.
- Атрибуты товаров (`catalog_product_attribute`) в первой версии **не подмешиваются** в строку поиска — только `catalog_product.name`.

**Логика выбора:**

1. 0 кандидатов прошли threshold 40 → `not_found` (LLM не вызывается).
2. 1 кандидат проходит threshold 85 с margin ≥ 15 → `confident_match` (LLM не вызывается).
3. Иначе → промт `MATCHING_PROMPT` в Qwen с top-N кандидатов; LLM возвращает `confident_match` (1 id) / `ambiguous` (2–3 id) / `not_found`.

**Версионирование (раздел 11) — две версии:**

- `ANALYZER_VERSION` (в `app/analysis/__init__.py`) — версия конвейера в целом, пишется в `analysis_chat_analysis.analyzer_version` и используется для идемпотентности и отката (`apply_analysis_to_customer`). Текущее значение: `v1.0+qwen3-14b`.
- `PROMPTS_VERSION` (в `analysis/prompts.py`) — версия именно текстов промтов. При любом изменении формулировки промта инкремент обязателен. Текущее значение: `v1.0`. При несовпадении `PROMPTS_VERSION` с версией, под которой был сделан анализ — это сигнал для repeated-run'а с инкрементом `ANALYZER_VERSION`.

### Ключевые числа после Task 3

- Тестов всего в проекте: **330 passed / 0 skipped / 0 failed**
- Тестов модуля `analysis`: **87** (по итогам Task 1–3)
- Зависимости: добавлены `rapidfuzz>=3.6` и `httpx>=0.27` (поднят из dev в core)
- Alembic head: `208c6dd6037b`
- Готовность к первому реальному прогону: **готов**, требуется только LM Studio + Qwen3-14B на хосте оператора.

### CLI команды для оператора

```bash
python analysis/run.py --dry-run --all                  # предпросмотр без записи
python analysis/run.py --chat-id N                      # один чат
python analysis/run.py --review-status unreviewed       # очередь модерации
python analysis/run.py --resume                         # после Ctrl+C
python analysis/run.py --force --chat-id N              # переанализ той же версии
python analysis/run.py --prompt-variant schema|example  # A/B test промта extract
python analysis/run.py --status                         # текущее состояние state
python analysis/run.py --chunk-size 200                 # переопределение чанка
```

См. `ingestion/README.md` (или эквивалент) для пошаговой инструкции оператору.
