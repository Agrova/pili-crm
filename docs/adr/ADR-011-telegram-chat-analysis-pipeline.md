# ADR-011: Пайплайн анализа Telegram-переписки локальной LLM

**Статус:** draft
**Дата:** 2026-04-24
**Связанные ADR:** ADR-001 v2 (модульный монолит), ADR-003 final-ready (core schema), ADR-009 (Telegram profile schema), ADR-010 (Telegram ingestion pipeline)
**Связанные документы:** `06_open_questions.md`, `tool-gaps.md`, исторический артефакт `/Users/protey/Downloads/tool-shop-crm/`

---

## Контекст

После ADR-010 Задания 1 в БД `pili-crm` импортированы 64 785 сообщений по 303 Telegram-чатам. ADR-010 Задание 2 реализовало очередь модерации — оператор через Cowork присваивает каждому чату `review_status` (linked / new_customer / ignored). К 2026-04-23 оператор обкатал три режима `link_chat_to_customer` на трёх чатах.

**Проблема 1.** Очередь из 303 чатов слишком велика для ручного разбора «в холодную». Оператор не помнит наизусть, кто такой «Сергей» или «Иван» из 2024 года, какие у него были заказы, что он покупал. Принятие решения (ignore/link/new_customer) требует контекста переписки, а MCP-tool `get_unreviewed_chats` возвращает только превью первого и последнего сообщения (`tool-gaps.md`).

**Проблема 2.** Даже когда клиент создан (режим `new_customer`) или привязан (`linked`), JSONB-поля `orders_customer_profile.preferences` / `delivery_preferences` / `incidents` из ADR-009 остаются пустыми. Без них профиль клиента в pili-crm сводится к имени и telegram-идентификаторам.

**Проблема 3.** История заказов клиентов до 2026 существует только в переписке. В `orders_order` находятся 62 заказа seed MVP. Реальная история продаж — в 64 785 Telegram-сообщениях, нигде не структурированная.

**Прошлый опыт.** В `/Users/protey/Downloads/tool-shop-crm/` лежит попытка решить эти проблемы: Python-скрипт `tg_analyze.py` + Qwen3-14B через LM Studio. Результат (апрель 2026) оказался частично рабочим: `tg_scan_results.json` с классификацией 303 чатов — качественно, `topics/*.json` с профилями — чисто, `inbox/*.md` с заказами — дефектно. Три причины дефектов:

- `max_msgs=150` — для крупных чатов (Василий 5513 сообщений) анализировалось <3% истории
- `max_tokens=2048` — JSON-ответы обрезались на середине на насыщенных клиентах
- Жёсткая схема слотов, навязанная Python-скриптом — модель не могла свободно рассуждать о клиенте, заполняла только предусмотренные поля, часто с ошибками типов (поле «телефон» = «9.5, 21, 22» — размеры свёрел из заказа)

**Решение нужно принять сейчас**, потому что:

- Без обогащения профилей ручной разбор 303 чатов упирается в потолок памяти оператора
- Без истории заказов из переписки Finance ledger и любая аналитика строятся на seed MVP, а не на реальности бизнеса
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

### Вариант B — Новый пайплайн с model-driven подходом ✅ выбран

Новый модуль `analysis/` в монолите. Скрипт читает переписку из `communications_telegram_message`, разбивает на иерархические чанки, пропускает через Qwen3-14B двумя проходами: проход 1 — свободный markdown-narrative о клиенте, проход 2 — извлечение структурированного JSON из собственного markdown первого прохода. Результаты складываются в новую таблицу `analysis_chat_analysis` (narrative + structured JSON, immutable) и в JSONB-поля `orders_customer_profile` ADR-009 (с `confidence=suggested`). Извлечённые заказы создаются в `orders_order` со `status=draft`. Оператор верифицирует через Cowork, `suggested` → `manual`, `draft` → `confirmed`.

- **Плюсы:** соответствует проектной архитектуре (pili-crm — источник истины, модульный монолит, confidence-модель ADR-009); модель свободна в рассуждении; полное покрытие истории без лимита на сообщения; чекпоинты и resume для длительных прогонов; инкрементальный режим совместим с ADR-010 Задание 3 (Telethon).
- **Минусы:** больше кода чем Вариант A; двухпроходная модель удваивает время инференса; новые таблицы в модуле `analysis`.

### Вариант C — Отказаться от автоматического анализа, работать через Cowork по одному чату

Не писать скрипт. При ручном разборе чата в Cowork оператор использует `get_chat_messages` (новый tool) для чтения переписки, Claude в Cowork сам обогащает профиль через существующие tools.

- **Плюсы:** минимум инфраструктуры; нулевые затраты на локальную модель.
- **Минусы:** стоимость — 303 чата × длительная сессия с Claude = сотни тысяч токенов API; UX — оператор проводит часы в диалоге с Cowork вместо работы с готовыми данными; не масштабируется.

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
- Три таблицы в БД (одна сущность — один файл модели):
  - `analysis_chat_analysis` — архив результатов анализа
  - `analysis_chat_analysis_state` — чекпоинты для resume
  - `analysis_pending_order_item` — позиции draft-заказов, ожидающие catalog matching оператором

### 2. Таблица `analysis_chat_analysis`

Архив результатов анализа. Одна запись на пару (чат, версия анализатора). Immutable после создания.

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

- UNIQUE `(chat_id, analyzer_version)` — один результат на пару. Повторный прогон той же версии через `ON CONFLICT DO UPDATE`; новая версия создаёт новую запись, старые сохраняются.
- INDEX `chat_id`, INDEX `analyzed_at`

`analysis_chat_analysis` — **архив**, не активный профиль. Активный профиль клиента — это `orders_customer_profile` (ADR-009). Narrative и structured_extract — это доказательная база, хранится навсегда на случай пересмотра.

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
      "description": "Февральский заказ",
      "items": [
        {
          "items_text": "зензубель Veritas 05P44.01",
          "quantity": 1,
          "unit_price": 30600,
          "currency": "RUB",
          "source_message_ids": ["234"]
        }
      ],
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

Семантика статусов:

- `status_delivery`: `ordered` / `shipped` / `delivered` / `returned` / `unknown`
- `status_payment`: `unpaid` / `partial` / `paid` / `unknown`

Платежи **не привязаны** к заказам в extract — в Telegram-переписке привязка часто неявная. Сведение «кто кому должен» — работа оператора при верификации.

### 4. Процесс анализа — четыре фазы

**Фаза 1. Чанкинг.** Скрипт загружает сообщения чата из `communications_telegram_message` (только `text IS NOT NULL`, отсортированные по `sent_at`). Разбивает на чанки фиксированного размера (параметр конфигурации, стартовое значение 300 сообщений). Если чат меньше чанка — один чанк.

**Фаза 2. Иерархическая саммаризация** (только для чатов длиннее одного чанка). Каждый чанк отправляется Qwen с промтом «составь краткое саммари всего важного: факты о клиенте, заказы, платежи, адреса». Саммари чанков объединяются в мастер-саммари тем же способом рекурсивно, пока суммарный объём не уложится в контекстное окно модели.

**Фаза 3. Проход 1 — свободный нарратив.** На вход Qwen подаётся мастер-саммари (или весь чат, если влезает). Промт просит написать свободный markdown-портрет клиента: идентификация, интересы, история заказов, платежей, получений, инциденты. Без навязанной структуры. Результат — в `narrative_markdown`.

**Фаза 4. Проход 2 — извлечение JSON.** На вход Qwen подаётся `narrative_markdown` первого прохода (не сырая переписка). Промт просит заполнить JSON по схеме `structured_extract`. Результат валидируется Pydantic в `app/analysis/schemas.py`. Если валидация падает — retry до 3 раз, после этого запись помечается как failed в `analysis_chat_analysis_state`, чат пропускается, скрипт идёт дальше.

### 5. Catalog matching — модель предлагает, оператор решает

**Ключевой принцип:** скрипт анализатора **не создаёт записей в `catalog_product`**. Создание товара возможно только через действие оператора при верификации draft-заказа в Cowork.

**Этап A — поиск совпадений моделью.** После прохода 2, прежде чем писать позиции заказов в БД, скрипт делает отдельный запрос к Qwen:

> Промт: «Вот список товаров из каталога (id, name, attributes). Вот позиция из заказа: "<items_text>". Найди совпадения. Учитывай разные написания: Veritas/Верitas, 05P44/05P44.01/P44, зензубель/Zenzubel. Если нашёл одно точное совпадение — верни его product_id. Если несколько кандидатов или неуверен — верни список кандидатов и отметь "ambiguous". Если ничего не нашёл — верни "not_found".»

**Этап B — три исхода:**

1. **Уверенное совпадение** (`confident_match`) — позиция записывается в `orders_order_item` с найденным `product_id` и `unit_price` из extract.
2. **Неоднозначность** (`ambiguous`) — позиция записывается в `analysis_pending_order_item` (раздел 6) с кандидатами и текстом из extract. В Cowork оператор выбирает кандидата из списка или решает создать новый товар.
3. **Не найдено** (`not_found`) — позиция записывается в `analysis_pending_order_item` без кандидатов. В Cowork оператор либо находит существующий товар руками, либо создаёт новый.

**Пример неоднозначности:** Qwen нашёл в extract «рубанок Veritas #5», в каталоге два товара — «Рубанок Veritas #5 сталь PM-V11» и «Рубанок Veritas #5 сталь O1». Модель возвращает обоих как кандидатов, оператор при верификации решает, о каком из них шла речь.

**Создание товара оператором при верификации.** В момент, когда оператор нажимает «создать новый» для pending-позиции, в `catalog_product` создаётся запись:

- `name` — из `items_text` (или отредактированное оператором)
- `supplier_id` — подбирается по brand из extract. Если совпадений не найдено — используется запись `catalog_supplier` с именем `Unknown (auto)`, которая seed-ом создаётся в миграции ADR-011. Оператор при желании меняет supplier позже.
- Остальные поля NULL или заполняются оператором в диалоге.

Факт создания фиксируется в `analysis_created_entities` (см. раздел 8) — для возможности массового отката при проблемах с анализатором.

### 6. Таблица `analysis_pending_order_item`

Позиции draft-заказов, ожидающие catalog matching оператором.

| Поле | Тип | Назначение |
|---|---|---|
| `id` | `BIGINT PK` | Суррогатный ключ |
| `order_id` | `BIGINT FK → orders_order ON DELETE CASCADE` | Draft-заказ, к которому относится позиция |
| `items_text` | `TEXT NOT NULL` | Исходный текст позиции из extract |
| `quantity` | `NUMERIC(10,3) NULL` | Количество из extract |
| `unit_price` | `NUMERIC(18,4) NULL` | Цена за единицу из extract |
| `currency` | `CHAR(3) NULL` | Валюта из extract |
| `matching_status` | `ENUM` | `ambiguous` / `not_found` |
| `candidates` | `JSONB NULL` | Для `ambiguous`: массив `[{product_id, confidence_note}]` от модели |
| `source_message_ids` | `JSONB NULL` | Telegram message_ids, откуда взялась позиция |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | Стандартные timestamps |

Enum `analysis_pending_matching_status`: `ambiguous`, `not_found`.

Constraints:

- INDEX `order_id`
- INDEX `matching_status`

**Жизненный цикл записи:**

1. Создаётся скриптом анализатора для каждой позиции с `ambiguous` или `not_found`
2. Оператор в Cowork открывает draft-заказ, видит pending-позиции
3. Оператор делает выбор (существующий товар / создать новый / отменить позицию)
4. По выбору: создаётся запись в `orders_order_item` с реальным `product_id`, запись из `analysis_pending_order_item` **удаляется**
5. Таблица хранит только «живые» pending-позиции; история переходов — в `analysis_created_entities`

### 7. Запись результатов в активные таблицы

**Если у чата `review_status IN ('linked', 'new_customer')`** — есть связанный клиент, ссылка через `communications_telegram_chat.customer_id`:

- `structured_extract.identity` — поля, отсутствующие у клиента (phone, email), проставляются с `confidence="suggested"` в соответствующие JSONB-поля профиля
- `structured_extract.preferences` → `orders_customer_profile.preferences` JSONB с `confidence="suggested"`
- `structured_extract.delivery_preferences` → `orders_customer_profile.delivery_preferences` с `confidence="suggested"`, `is_primary=false`
- `structured_extract.incidents` → `orders_customer_profile.incidents` с `confidence="suggested"`
- `structured_extract.orders[]` → записи в `orders_order` со `status="draft"`:
  - Позиции с `confident_match` → сразу в `orders_order_item` с реальным `product_id`
  - Позиции с `ambiguous` или `not_found` → в `analysis_pending_order_item`
- `structured_extract.payments[]` — не записываются автоматически в финансовые таблицы; остаются только в `analysis_chat_analysis.structured_extract`. Финансовые записи (отдельный ADR на finance) оператор создаёт вручную при верификации заказа.

**Если у чата `review_status IN ('unreviewed', 'ignored', NULL)`** — клиента нет, писать некуда. Только `analysis_chat_analysis` заполняется. Оператор, разбирая очередь модерации, видит готовый анализ при принятии решения — и после привязки/создания клиента получает MCP-tool `apply_analysis_to_customer` для переноса результатов в профиль.

### 8. Таблица `analysis_created_entities`

Журнал сущностей, созданных из данных анализа. Позволяет массово откатить результаты неудачного прогона.

| Поле | Тип | Назначение |
|---|---|---|
| `id` | `BIGINT PK` | Суррогатный ключ |
| `analyzer_version` | `TEXT NOT NULL` | Версия анализатора, инициировавшая создание |
| `source_chat_id` | `BIGINT FK → communications_telegram_chat NULL` | Чат-источник данных |
| `entity_type` | `TEXT NOT NULL` | `catalog_product` / `orders_order` / `orders_order_item` |
| `entity_id` | `BIGINT NOT NULL` | id созданной записи |
| `created_by` | `TEXT NOT NULL` | `analyzer` (автоматически) / `operator` (оператор нажал «создать» при верификации) |
| `created_at` | `TIMESTAMPTZ` | Момент создания |

Constraints:

- INDEX `(analyzer_version, entity_type)`
- INDEX `source_chat_id`

Запись фиксируется:

- Автоматически при создании draft-заказа и `orders_order_item` с `confident_match` (`created_by='analyzer'`)
- В момент, когда оператор в Cowork жмёт «создать новый товар» для pending-позиции (`created_by='operator'`), создавая `catalog_product` и `orders_order_item`

Откат:

```sql
-- Посмотреть, что создал конкретный прогон
SELECT entity_type, COUNT(*) FROM analysis_created_entities
WHERE analyzer_version = 'v1.0+qwen3-14b'
GROUP BY entity_type;

-- Массовое удаление (только автоматически созданного, не трогая верифицированное оператором)
DELETE FROM orders_order WHERE id IN (
  SELECT entity_id FROM analysis_created_entities
  WHERE analyzer_version = 'v1.0+qwen3-14b'
    AND entity_type = 'orders_order'
    AND created_by = 'analyzer'
);
```

### 9. Чекпоинты и resume — таблица `analysis_chat_analysis_state`

| Поле | Тип | Назначение |
|---|---|---|
| `chat_id` | `BIGINT PK` | Чат в обработке |
| `stage` | `TEXT` | `chunking` / `chunk_summaries` / `master_summary` / `narrative` / `extract` / `matching` / `done` / `failed` |
| `chunks_done` | `INTEGER` | Прогресс фазы 2 |
| `chunks_total` | `INTEGER` | Всего чанков для этого чата |
| `partial_result` | `JSONB NULL` | Саммари уже обработанных чанков, markdown первого прохода |
| `failure_reason` | `TEXT NULL` | При `stage='failed'` |
| `updated_at` | `TIMESTAMPTZ` | Для heuristics по hard shutdown |

Гранулярность чекпоинтов — средняя: после каждого завершённого чанка `UPDATE analysis_chat_analysis_state SET ...; COMMIT`. После каждого завершённого чата запись в `analysis_chat_analysis` + удаление из `analysis_chat_analysis_state`.

При запуске `analysis/run.py`:

1. Если есть записи в `analysis_chat_analysis_state` со `stage != 'done'` и `updated_at` старше 10 минут — это прерванные сессии. Спрашиваем оператора через CLI: продолжить (resume) или откатить (restart этих чатов).
2. Если есть записи со свежим `updated_at` — другой процесс работает, не запускаемся.
3. Дальше обычный прогон: выбираем чаты по критерию, пропускаем уже завершённые (`chat_id IN analysis_chat_analysis WHERE analyzer_version = CURRENT_VERSION`), идём по одному.

### 10. CLI интерфейс

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
| `--force` | Перезапустить анализ для чатов, которые уже есть в `analysis_chat_analysis` с той же версией |
| `--model-endpoint URL` | Адрес OpenAI-совместимого endpoint локальной модели, дефолт `http://localhost:1234/v1` (LM Studio) |
| `--chunk-size N` | Размер чанка в сообщениях, дефолт 300 |

Ctrl+C: SIGINT → graceful — дождаться окончания текущего вызова LM Studio, зафиксировать `analysis_chat_analysis_state`, выйти с кодом 0. Повторный Ctrl+C — force kill.

### 11. Версионирование

`analyzer_version` в коде — константа в `app/analysis/__init__.py`, инкрементируется при любом изменении промта или схемы `structured_extract`. Формат: `vMAJOR.MINOR+MODEL`, например `v1.0+qwen3-14b`. При смене модели или промта — новая версия, существующие записи не затираются.

### 12. Источник модели

Локальная Qwen3-14B через LM Studio, `http://localhost:1234/v1`, OpenAI-совместимый endpoint. Модель определяется автоматически через `/v1/models`. Без `enable_thinking` (проверено на прошлом артефакте — thinking-режим замедляет без выигрыша в качестве для этой задачи). Никаких агентских сценариев — только синхронные запросы с текстом на входе и текстом на выходе.

Решение ADR-001 v2 об отказе от локальных моделей в оперативной архитектуре CRM не нарушается: анализатор — батчевый процесс на mac оператора, не часть горячего пути CRM, не агент.

### 13. Сценарий запуска оператором

Разовые подготовительные шаги (один раз на машину):

1. LM Studio установлен и запущен в режиме server (на 1234 порту)
2. Модель Qwen3-14B загружена в LM Studio через UI
3. FastAPI pili-crm и Postgres подняты (только БД строго обязательна, FastAPI для анализатора не нужен)
4. Задачи 1–3 из раздела «Что должен сделать Claude Code» выполнены (модуль `analysis/`, миграция, скрипт)

Запуск анализа (каждый раз, когда надо):

```bash
cd /Users/protey/pili-crm

# Посмотреть, что попадёт в работу
python3 -m analysis.run --all --dry-run

# Запуск на одном чате — проверить качество
python3 -m analysis.run --chat-id 490

# Запуск на всей очереди unreviewed
python3 -m analysis.run --review-status unreviewed

# Или на всём сразу
python3 -m analysis.run --all
```

Паузить:

- Ctrl+C в терминале → скрипт дождётся текущего вызова модели, зафиксирует состояние, выйдет чисто
- Закрыл крышку ноута → чекпоинты в БД переживут сон
- Hard shutdown (сел аккум) → при следующем запуске скрипт увидит прерванные чаты и предложит resume

Продолжить:

```bash
# Показать, что в работе
python3 -m analysis.run --status

# Продолжить прерванную сессию
python3 -m analysis.run --resume
```

Смотреть результаты: через Cowork, в `get_unreviewed_chats` появится `analysis_summary`, оператор видит narrative при разборе. Можно параллельно разбирать уже проанализированные чаты, пока скрипт работает над остальными.

## Последствия

### Что становится проще

- Оператор при разборе очереди модерации видит готовый портрет клиента из narrative + структурированные поля — решения принимаются в разы быстрее
- Профили клиентов в pili-crm обогащаются автоматически (с `confidence=suggested`) вместо пустых полей
- История заказов из переписки попадает в `orders_order` со `status=draft`, становится видимой в системе
- Каталог товаров естественно растёт по мере верификации — не через отдельную импорт-операцию, а как побочный эффект работы с клиентами
- Инкрементальные прогоны после Telethon (ADR-010 Задание 3) — одна команда `analysis/run.py --since DATE`
- Возможность массового отката через `analysis_created_entities` — страховка на случай неудачного прогона

### Какие ограничения появляются

- Зависимость от LM Studio запущенного на mac оператора. Без LM Studio скрипт не запускается. Это локальный инструмент, не production-сервис.
- Время инференса на Mac без GPU — медленно. Полный прогон 303 чатов может занять 10+ часов. Отсюда требование resume и чекпоинтов.
- Галлюцинации Qwen3-14B. Выходная информация имеет `confidence=suggested` и требует ручной верификации оператором. Никакие финансовые операции не создаются автоматически.
- Три новые таблицы в схеме БД: `analysis_chat_analysis`, `analysis_chat_analysis_state`, `analysis_pending_order_item`, плюс `analysis_created_entities` — итого четыре. Каждая в своём файле модели.
- Pydantic-схема `structured_extract` развивается отдельно от JSONB-схем ADR-009. Синхронизация на семантическом уровне, не на уровне одного файла.
- Seed запись `catalog_supplier` с именем `Unknown (auto)` появляется в базе как системная — должна быть явно помечена и при отчётности исключаться из метрик по реальным поставщикам.

### Что придётся учитывать дальше

- При изменении промтов или схемы `structured_extract` — инкремент `analyzer_version`. Старые записи остаются как история.
- При смене модели (Qwen на что-то ещё) — тоже инкремент версии.
- MCP-tool `get_unreviewed_chats` (ADR-010 Задание 2) должен быть расширен: при наличии записи в `analysis_chat_analysis` возвращать narrative и ключевые факты в превью.
- UI верификации draft-заказов в Cowork — новые MCP-tools (`list_draft_orders_for_customer`, `resolve_pending_item`, `verify_order`, `delete_draft_order`).
- Finance ledger (следующая задача в `01_scope.md`) получит доступ к истории заказов клиентов начиная с этого ADR. До этого ledger строился бы на seed MVP.

## Что должен сделать Claude Code

Список задач для Prompt Factory. Каждая задача — отдельный промт, порядок важен (зависимости между задачами).

1. **Миграция схемы.** Alembic-миграция:
   - Новые таблицы `analysis_chat_analysis`, `analysis_chat_analysis_state`, `analysis_pending_order_item`, `analysis_created_entities` с constraints (разделы 2, 6, 8, 9)
   - Новый enum `analysis_pending_matching_status`
   - Seed запись `catalog_supplier` с `name='Unknown (auto)'` (используется разделом 5)
   - SQLAlchemy-модели в `app/analysis/models.py` (одна сущность — один класс, одна таблица)
   - Pydantic-схемы в `app/analysis/schemas.py` для `structured_extract` (раздел 3)
   - Обновление `02_entities.md` — добавить модуль `analysis` в карту таблиц
   - Reversibility проверена

2. **Репозиторий и сервис.** Минимальный слой доступа в `app/analysis/repository.py` (CRUD для всех таблиц модуля) и сервисный слой в `app/analysis/service.py`:
   - Запись результата анализа в `analysis_chat_analysis`
   - Условная запись в `orders_customer_profile` и `orders_order` (раздел 7) через публичные интерфейсы `orders`
   - Регистрация созданных сущностей в `analysis_created_entities` (раздел 8)
   - Граница модуля не нарушается: запись в чужие таблицы — только через публичные интерфейсы `orders` и `communications`

3. **Скрипт анализатора.** `analysis/run.py`:
   - CLI и оркестрация (раздел 10)
   - Фазы 1–4 (раздел 4)
   - Catalog matching через Qwen (раздел 5, этап A и B)
   - Чекпоинты (раздел 9)
   - Коммуникация с LM Studio через `httpx`
   - Retry-логика на уровне вызовов модели (3 попытки, экспоненциальный backoff)
   - Graceful shutdown на SIGINT
   - Интеграционные тесты с mock LM Studio

4. **MCP-tool `apply_analysis_to_customer`.** Для сценария, когда чат был `unreviewed` при моменте анализа, анализ записан только в `analysis_chat_analysis`, а потом оператор решил создать/привязать клиента. Tool читает последний `analysis_chat_analysis` для чата, применяет к только что созданному/привязанному клиенту (раздел 7).

5. **Расширение MCP-tool `get_unreviewed_chats`.** Добавить в ответ для каждого чата поля `has_analysis: bool`, `analysis_summary: str | null` (первые ~300 символов narrative), `extracted_name_guess: str | null`. Оператор видит аналитику сразу при разборе очереди.

6. **MCP-tools для работы с draft-заказами и pending-позициями:**
   - `list_draft_orders` (с фильтром по customer_id, возвращает orders со status=draft, в каждом — список pending-позиций)
   - `resolve_pending_item` (для pending-позиции: выбрать product_id из кандидатов / выбрать другой существующий товар / создать новый товар через диалог supplier/name — и переместить в `orders_order_item`)
   - `verify_order` (перевод draft-заказа в `confirmed` после обработки всех pending-позиций)
   - `delete_draft_order` (отклонение false-positive извлечения)
   
   Все write-операции — под правилом двух подтверждений (системный промт Cowork, раздел 7).

Задачи 4–6 могут идти параллельно после Задачи 3. Задача 1 блокирует всё.

## Что проверить вручную

- [ ] Миграция создаёт все четыре таблицы и все constraints; `downgrade -1` откатывает чисто
- [ ] Seed `catalog_supplier 'Unknown (auto)'` создан миграцией, при downgrade удалён
- [ ] На пустом `analysis_chat_analysis` запуск `analysis/run.py --all --dry-run` показывает ожидаемое число чатов и сообщений
- [ ] Прогон `analysis/run.py --chat-id N` на одном малом чате: запись в `analysis_chat_analysis` появилась, narrative заполнен, `structured_extract` валиден по Pydantic
- [ ] Прогон на одном крупном чате (5000+ сообщений): чанкинг проходит, запись появляется, `chunks_count > 1`, narrative отражает полную историю
- [ ] Ctrl+C в середине крупного чата: запись в `analysis_chat_analysis_state` со `stage != 'done'`, повторный запуск с `--resume` продолжает с чекпоинта
- [ ] Повторный запуск без `--force` пропускает уже обработанные чаты
- [ ] Для чата с `review_status='linked'` после анализа — `orders_customer_profile` клиента содержит `preferences` с `confidence='suggested'`; `orders_order` содержит новые записи со `status='draft'`
- [ ] Catalog matching: позиция с уверенным совпадением создаёт `orders_order_item` с реальным `product_id`; неоднозначная — создаёт запись в `analysis_pending_order_item` с `matching_status='ambiguous'` и непустым `candidates`
- [ ] В Cowork через MCP оператор может разрешить pending-позицию: создание нового товара приводит к записи в `catalog_product` + `orders_order_item` + двум записям в `analysis_created_entities`
- [ ] Для чата с `review_status='unreviewed'` — только `analysis_chat_analysis` заполнен, в `orders_*` и `catalog_*` ничего нового
- [ ] MCP-tool `get_unreviewed_chats` возвращает `has_analysis=true` и summary для проанализированных чатов
- [ ] Один цикл ручной верификации от начала до конца: анализатор создал draft-заказ с pending-позициями → оператор в Cowork разрешил все pending → verify → заказ в `confirmed`, позиции в `orders_order_item`, pending-таблица пустая
- [ ] Обновление `02_entities.md` и `01_scope.md` (перенос задач в «Сделано»)
