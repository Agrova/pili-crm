# ADR-010: Telegram Ingestion Pipeline

**Статус:** принят
**Дата:** 2026-04-22
**Связанные ADR:** ADR-001 v2 (модульный монолит), ADR-003 final-ready (core schema), ADR-009 (расширение схемы профилей)

---

## Контекст

У оператора накоплена переписка с клиентами в личном Telegram-аккаунте объёмом ~6 GB. Эти данные содержат историю заказов, договорённостей, адресов доставки и контекст по каждому клиенту. Сейчас они недоступны для CRM: профили клиентов пустые, связь переписки с заказами отсутствует.

Необходимо выстроить pipeline, который:
1. **Фаза 1 (Historical):** разово импортирует всю историческую переписку из Telegram Desktop JSON Export (~6 GB).
2. **Фаза 2 (Incremental):** ежесуточно подтягивает новые сообщения из личных чатов.
3. **Фаза 3 (Review):** позволяет оператору через Cowork разобрать импортированные чаты — связать с клиентами, создать новых, проигнорировать нерелевантные.

**Ограничения:**
- Только личные чаты (1-на-1). Групповые чаты — отдельный сценарий, вне scope этого ADR.
- Медиафайлы: хранится только путь к файлу локально, не BLOB в БД.
- Голосовые сообщения, стикеры, системные сообщения — пропускаются.
- Связь чата с клиентом — только через подтверждение оператора. Никаких автоматических write-операций в `orders_*`.
- Источник истины — PostgreSQL. Telegram — источник входных данных.

---

## Варианты

### Фаза 1: Исторический импорт

#### Вариант H1 — Telegram Desktop JSON Export + Python-скрипт ✅ выбран

Telegram Desktop экспортирует историю чатов в JSON. Разовый Python-скрипт читает JSON и загружает в `communications_telegram_*`.

- **Плюсы:** нет риска блокировки аккаунта; формат JSON хорошо документирован; не требует MTProto-авторизации; идемпотентен через `ON CONFLICT DO NOTHING`.
- **Минусы:** ручной запуск экспорта в Telegram Desktop; ~6 GB нужно обработать за один прогон.

#### Вариант H2 — Telethon: программный дамп через MTProto

- **Отклонён.** Для разового исторического импорта риск блокировки аккаунта при массовом чтении истории не оправдан. Telethon используется только для фазы 2, где нагрузка минимальна.

---

### Фаза 2: Инкрементальный режим

#### Вариант I1 — Telethon + launchd на macOS, раз в сутки ✅ выбран

Python-скрипт с Telethon запускается через launchd раз в сутки. Читает только сообщения новее `last_imported_message_id` по каждому чату.

- **Плюсы:** автоматизация без ручных действий; нагрузка на MTProto минимальная (только дельта); раз в сутки достаточно для текущего масштаба; MacBook обычно включён.
- **Минусы:** session-файл Telethon хранится на macOS — требует защиты; если MacBook выключен в момент запуска, синхронизация сдвигается на следующий день (приемлемо).

#### Варианты I2, I3 — отклонены

I2 (скрипт на телефоне) — сложнее поддерживать среду выполнения. I3 (ручной экспорт) — нет автоматизации при доступном launchd.

---

## Принятое решение

**Фаза 1: H1. Фаза 2: I1. Фаза 3: очередь модерации в Cowork через два новых MCP-tools.**

### Архитектура pipeline

```
Фаза 1: Historical (разово)
────────────────────────────────────────────────────────
Telegram Desktop → «Экспорт истории чатов» → JSON (~6 GB)
→ ingestion/tg_import.py
   ├── фильтр: только chat_type = 'personal_chat'
   ├── фильтр сообщений: правила ниже
   ├── INSERT ... ON CONFLICT DO NOTHING
   ├── last_imported_message_id = max(telegram_message_id) по чату
   └── review_status = 'unreviewed' для всех импортированных чатов

Фаза 2: Incremental (ежесуточно, 02:00)
────────────────────────────────────────────────────────
launchd (macOS)
→ ingestion/tg_incremental.py (Telethon)
   ├── только личные чаты
   ├── для каждого чата: сообщения after last_imported_message_id
   ├── INSERT ... ON CONFLICT DO NOTHING
   └── обновление last_imported_message_id

Фаза 3: Review (через Cowork, в удобном темпе)
────────────────────────────────────────────────────────
MCP: get_unreviewed_chats()
→ список чатов с превью
→ оператор принимает решение по каждому:

   linked       → MCP: link_chat_to_customer(chat_id, customer_id=X)
                  → review_status = 'linked'
                  → communications_link для сообщений чата
                  → обновление telegram_id / telegram_username клиента

   new_customer → MCP: link_chat_to_customer(chat_id, create_new=True)
                  → create_customer (имя из title, telegram_id из чата)
                  → review_status = 'new_customer'
                  → communications_link для сообщений чата

   ignored      → MCP: link_chat_to_customer(chat_id, ignore=True)
                  → review_status = 'ignored'
```

### Правила фильтрации сообщений

**Импортируем:**

| Тип | Условие |
|---|---|
| Текст | `text IS NOT NULL AND text != ''` |
| Фото | только если оператор подтвердил при разборе чата |
| Документы | только если оператор подтвердил при разборе чата |

**Пропускаем всегда:**

| Тип | Причина |
|---|---|
| `media_type = 'voice_message'` | Нет ценности без транскрипции |
| `media_type = 'sticker'` | Нет текстового содержания |
| `type = 'service'` | Системные события (вступление, переименование и т.п.) |
| Сообщения из групповых чатов | Только личные чаты в scope |

### Watermark и идемпотентность

- `last_imported_message_id` (TEXT) в `communications_telegram_chat` — Telegram message_id последнего успешно импортированного сообщения чата.
- Обновляется **после** успешной записи всего батча сообщений чата. При ошибке в середине — watermark не сдвигается, следующий запуск переимпортирует с того же места.
- Дедупликация: UNIQUE constraint `(chat_id, telegram_message_id)` + `ON CONFLICT DO NOTHING`.
- Результат: скрипт можно запускать повторно без побочных эффектов.

### MCP-tool: `get_unreviewed_chats` (read)

Возвращает чаты с `review_status = 'unreviewed'`, отсортированные по `last_message_at DESC`.

**Формат ответа:**
```json
[
  {
    "chat_id": 42,
    "telegram_chat_id": "123456789",
    "title": "Иван Петров",
    "message_count": 87,
    "first_message_at": "2023-05-12T14:30:00Z",
    "last_message_at": "2026-03-01T09:15:00Z",
    "preview_first": "Привет, интересует пила...",
    "preview_last": "Спасибо, всё отлично!"
  }
]
```

Выполняется без подтверждения (read-only).

### MCP-tool: `link_chat_to_customer` (write)

**Параметры:**
```
chat_id:     int            — ID записи в communications_telegram_chat
customer_id: int | None     — ID существующего клиента (режим linked)
create_new:  bool = False   — создать нового клиента из данных чата
ignore:      bool = False   — пометить чат как ignored
```

Ровно один из `customer_id`, `create_new`, `ignore` должен быть задан — иначе ошибка валидации.

**Требует подтверждения оператора** (правило двух подтверждений, системный промт Cowork).

**Выполняет атомарно в одной транзакции:**
1. Обновляет `review_status` чата.
2. При `create_new`: создаёт `orders_customer` с именем из `title` чата и `telegram_id` из чата.
3. Создаёт записи `communications_link` для всех сообщений чата (`link_confidence = 'auto'`, `target_module = 'orders'`, `target_entity = 'orders_customer'`).
4. Если `telegram_id` или `telegram_username` клиента пустые — заполняет из данных чата.

### Медиафайлы

**Хранение:** локальная папка `~/pili-crm-media/telegram/`. Путь фиксируется в `.env` как `TELEGRAM_MEDIA_PATH`.

**В БД:** только метаданные в `raw_payload JSONB` сообщения:
```json
{
  "media_type": "photo",
  "file_path": "telegram/photos/photo_2024-05-01_123456.jpg",
  "file_size_bytes": 245120
}
```

**Решение о сохранении:** при разборе чата оператор видит список медиафайлов и явно подтверждает нужность. По умолчанию импортируется только текст.

### Session-файл Telethon

- Хранится в `ingestion/.telethon_session`.
- Добавить в `.gitignore`: `ingestion/*.session`
- Права: `chmod 600 ingestion/.telethon_session`
- При потере — повторная авторизация через номер телефона.

---

## Последствия

### Что становится проще

- Вся история переписки с клиентами доступна в Cowork через поиск по `communications_telegram_message.text`.
- Профили клиентов наполняются историческим контекстом.
- Оператор разбирает очередь чатов в удобном темпе — pipeline не блокирует ежедневную работу.
- Новые сообщения появляются в БД каждое утро без ручных действий.

### Какие ограничения появляются

- Session-файл Telethon на macOS — не коммитить, защитить правами.
- Если MacBook выключен ночью — инкремент сдвигается на следующие сутки. Для текущего масштаба приемлемо.
- Медиафайлы (~6 GB) хранятся локально без резервного копирования из коробки (Time Machine покрывает если настроен).
- Разбор очереди чатов — ручная работа оператора.

### Что придётся учитывать дальше

- Групповые чаты для поиска новых клиентов — отдельный ADR.
- При росте объёма сообщений — GIN-индекс на `to_tsvector(text)` для полнотекстового поиска.
- Telegram-бот (канал 1) использует ту же схему `communications_telegram_*` — конфликтов нет, координация при появлении бота.
- Резервное копирование `~/pili-crm-media/telegram/` решить отдельно.

---

## Что должен сделать Claude Code

Пять задач для Prompt Factory (в порядке выполнения):

**Задача 1: `ingestion/tg_import.py`**
Скрипт разового импорта Telegram Desktop JSON Export. Параметры CLI: `--input-dir` (путь к папке экспорта), `--dry-run` (статистика без записи). Логика: только `personal_chat`, фильтрация по типам, `INSERT ... ON CONFLICT DO NOTHING`, установка `last_imported_message_id` и `review_status = 'unreviewed'`. Вывод: прогресс-бар по чатам, итоговая статистика.

**Задача 2: `ingestion/tg_incremental.py`**
Скрипт инкрементального импорта через Telethon. Параметры CLI: `--session` (путь к session-файлу), `--dry-run`. Логика: по каждому чату из БД запросить сообщения после watermark, записать, обновить `last_imported_message_id`. Вывод: лог по каждому чату.

**Задача 3: MCP-tool `get_unreviewed_chats`**
Read-tool в `crm-mcp/server.py`. Запрос: `WHERE review_status = 'unreviewed' ORDER BY last_message_at DESC`. Превью: первые 100 символов первого и последнего сообщения через subquery.

**Задача 4: MCP-tool `link_chat_to_customer`**
Write-tool в `crm-mcp/server.py`. Атомарная транзакция: обновление `review_status` + `communications_link` + опциональный `create_customer` + обновление `telegram_id`/`telegram_username`.

**Задача 5: launchd plist**
Файл `ingestion/com.pilistrогай.tg-incremental.plist` для автозапуска `tg_incremental.py` ежесуточно в 02:00. Инструкция по установке в README: `cp ... ~/Library/LaunchAgents/ && launchctl load ...`.

---

## Что проверить вручную

- После исторического импорта: кол-во чатов и сообщений в БД не меньше чем в Telegram Desktop export stats
- `last_imported_message_id` заполнен у всех импортированных чатов
- `review_status = 'unreviewed'` у всех импортированных чатов
- Групповые чаты отсутствуют в `communications_telegram_chat`
- Голосовые сообщения и стикеры отсутствуют в `communications_telegram_message`
- Повторный запуск `tg_import.py` не создаёт дублей (COUNT до и после одинаков)
- `get_unreviewed_chats` возвращает корректный список с превью
- `link_chat_to_customer` с `customer_id`: `review_status = 'linked'`, `communications_link` создан
- `link_chat_to_customer` с `create_new=True`: новый клиент в `orders_customer` с `telegram_id`
- `link_chat_to_customer` с `ignore=True`: чат исчез из очереди `get_unreviewed_chats`
- launchd: `launchctl list | grep pilistrогай` показывает задачу; после 02:00 лог содержит запись о запуске
