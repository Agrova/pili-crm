# ADR-009: Расширение схемы для Telegram-профилей клиентов

**Статус:** принят
**Дата:** 2026-04-22
**Связанные ADR:** ADR-001 v2 (модульный монолит), ADR-003 final-ready (core schema), ADR-010 (Telegram ingestion pipeline — готовится)

---

## Контекст

Система хранит клиентов в `orders_customer` и расширенные профили в `orders_customer_profile`. Переписка хранится в `communications_telegram_*`. Связь сообщений с бизнес-сущностями — через `communications_link`.

Три проблемы, которые нужно решить сейчас, до реализации ingestion:

**Проблема 1:** `orders_customer` содержит поле `telegram_id`, но не содержит `telegram_username`. Системный промт Cowork описывает логику deep links с username, но в схеме поля нет — Cowork работает в обход схемы.

**Проблема 2:** `orders_customer_profile` имеет только `notes TEXT` и `addresses JSONB`. Это не масштабируется для фактов, извлечённых из переписки — нет структуры, нет confidence, нет ссылки на источник.

**Проблема 3:** `communications_telegram_chat` не имеет полей для управления ingestion — нет watermark последнего импортированного сообщения и нет статуса разбора чата оператором. Без этого нельзя строить инкрементальный импорт и очередь модерации.

Решения нужно принять сейчас, потому что ADR-010 (ingestion pipeline) зависит от этой схемы — реализация ingestion должна писать в правильные поля.

---

## Варианты

### Вариант A — Минимальный патч

Добавить только `telegram_username` в `orders_customer`. Остальное оставить как есть.

- **Плюсы:** одна маленькая миграция, минимальный риск.
- **Минусы:** не решает проблемы 2 и 3. Ingestion придётся делать без watermark и без очереди модерации — либо писать данные в неподходящие поля, либо переделывать схему второй раз.

### Вариант B — Полное расширение (три таблицы, одна миграция)

Одна миграция закрывает все три проблемы:
- `orders_customer` + `telegram_username`
- `orders_customer_profile` + три JSONB-поля
- `communications_telegram_chat` + `last_imported_message_id` + `review_status`

- **Плюсы:** одна миграция вместо двух-трёх; ADR-010 получает готовую схему; Cowork deep links начинают работать корректно сразу.
- **Минусы:** чуть больше работы сейчас, но все изменения связаны тематически и разумно делать одним пакетом.

---

## Критерии выбора

- **Надёжность:** обе миграции безопасны — только добавление nullable-полей и нового enum. Откат прост.
- **Простота поддержки:** лучше одна осознанная миграция, чем три точечных патча в разное время.
- **Совместимость с Claude Code:** вариант B даёт Claude Code полный контракт для реализации ADR-010 без неожиданных доработок схемы.
- **Масштабируемость:** JSONB-поля в профиле позволяют добавлять новые типы фактов без миграций.

---

## Принятое решение

**Вариант B — полное расширение, одна миграция.**

### Изменение 1: `orders_customer`

Добавить поле:

| Поле | Тип | Назначение |
|---|---|---|
| `telegram_username` | `TEXT NULL` | Username без `@`, например `ivan_petrov`. Отдельно от `telegram_id` — username может меняться, id — нет. |

Уникального constraint не ставим — username может быть временно пустым или совпасть при ошибке импорта, разбираем вручную через очередь модерации.

### Изменение 2: `orders_customer_profile`

Добавить три JSONB-поля:

| Поле | Тип | Назначение |
|---|---|---|
| `preferences` | `JSONB NULL` | Предпочтения по товарам. |
| `delivery_preferences` | `JSONB NULL` | Удобный способ доставки, время, адреса для конкретных случаев. |
| `incidents` | `JSONB NULL` | Проблемные ситуации: жалобы, нерешённые вопросы. |

Поле `notes TEXT` остаётся — для свободных заметок оператора без структуры.

**Структура элемента `preferences`:**
```json
[
  {
    "product_id": 42,
    "note": "хочет синий",
    "source_message_id": "789",
    "confidence": "suggested",
    "extracted_at": "2026-04-22T10:00:00Z"
  }
]
```

**Структура `delivery_preferences`:**
```json
{
  "method": "СДЭК",
  "preferred_time": "вечер",
  "source": "manual"
}
```

**Структура элемента `incidents`:**
```json
[
  {
    "date": "2026-03-15",
    "summary": "Товар пришёл с царапиной, договорились на скидку",
    "resolved": true,
    "source_message_id": "1042"
  }
]
```

**Confidence-значения для JSONB-фактов:**

| Значение | Смысл |
|---|---|
| `manual` | Оператор зафиксировал явно |
| `suggested` | Система предложила, оператор не подтвердил |
| `auto` | Зарезервировано, на старте не используется |

### Изменение 3: `communications_telegram_chat`

Добавить два поля:

| Поле | Тип | Назначение |
|---|---|---|
| `last_imported_message_id` | `TEXT NULL` | Telegram message_id последнего импортированного сообщения. Watermark для инкрементального режима. NULL = чат ещё не импортировался. |
| `review_status` | `telegram_chat_review_status NULL` | Статус разбора чата оператором. NULL = чат создан вручную, не из ingestion. |

**Новый enum `telegram_chat_review_status`:**

| Значение | Смысл |
|---|---|
| `unreviewed` | Импортирован, оператор ещё не смотрел |
| `linked` | Привязан к существующему клиенту |
| `new_customer` | Создан новый клиент по этому чату |
| `ignored` | Оператор решил не обрабатывать (спам, не клиент) |

NULL означает «чат создан вручную, не из ingestion» — чтобы не ломать существующие записи.

---

## Последствия

### Что становится проще

- Cowork deep links работают через схему, а не в обход неё.
- ADR-010 (ingestion pipeline) получает готовую схему без доработок.
- Факты из переписки хранятся структурированно с источником и confidence.
- Очередь модерации чатов реализуется как простой запрос: `WHERE review_status = 'unreviewed'`.
- При появлении бота (канал 1) `telegram_id` и `telegram_username` уже в схеме — дополнительных миграций не потребуется.

### Какие ограничения появляются

- JSONB-поля профиля не имеют FK-целостности на `source_message_id` — намеренно (ADR-003: полиморфные ссылки без FK). Целостность обеспечивается на уровне сервиса.
- `telegram_username` не уникален в БД — возможны временные дубли при импорте. Разбираются через очередь модерации.

### Что придётся учитывать дальше

- При появлении новых типов фактов из переписки — добавлять ключи в существующие JSONB-поля, не новые колонки.
- Системный промт Cowork обновить: добавить правило работы с `telegram_username` (приоритет перед `telegram_id` для deep links).
- При реализации бота (канал 1) — `telegram_id` и `telegram_username` уже в схеме, дополнительных миграций не потребуется.

---

## Что должен сделать Claude Code

Одна задача для Prompt Factory: **миграция Alembic**.

Добавить в существующую схему:

1. Колонка `orders_customer.telegram_username TEXT NULL`
2. Колонка `orders_customer_profile.preferences JSONB NULL`
3. Колонка `orders_customer_profile.delivery_preferences JSONB NULL`
4. Колонка `orders_customer_profile.incidents JSONB NULL`
5. Новый PostgreSQL enum `telegram_chat_review_status` со значениями: `unreviewed`, `linked`, `new_customer`, `ignored`
6. Колонка `communications_telegram_chat.last_imported_message_id TEXT NULL`
7. Колонка `communications_telegram_chat.review_status telegram_chat_review_status NULL`

Все колонки nullable — существующие строки не затрагиваются.

Индекс на `communications_telegram_chat.review_status` — для запросов очереди модерации.

---

## Что проверить вручную

- `orders_customer` с существующими записями после миграции — `telegram_username` равен NULL, строки не сломаны
- `orders_customer_profile` — три новых поля равны NULL у существующих профилей
- `communications_telegram_chat` — `last_imported_message_id` и `review_status` равны NULL у существующих чатов
- Enum создан корректно: `SELECT enum_range(NULL::telegram_chat_review_status);`
- Индекс на `review_status` присутствует: `\d communications_telegram_chat`
