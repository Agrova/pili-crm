# ingestion — Telegram Data Ingestion

Скрипты разового и инкрементального импорта переписки из личного Telegram-аккаунта
в таблицы `communications_telegram_chat` / `communications_telegram_message`.

Реализует **Фазу 1 (исторический импорт)** ADR-010.
Фаза 2 (инкрементальный, Telethon) — Задание 3 ADR-010.

---

## Как сделать экспорт в Telegram Desktop

1. Открой **Telegram Desktop** (macOS/Windows/Linux).
2. В любом диалоге нажми **☰ → Settings → Advanced → Export Telegram data**.
3. Настройки экспорта:
   - **Account information** — можно включить (не влияет на импорт).
   - **Personal chats** — **включить обязательно**.
   - **Bot chats**, **Private groups**, **Private channels** — выключить
     (скрипт их игнорирует, но включение увеличит объём и время экспорта).
   - **Photos**, **Files**, **Videos** — включить, если нужны медиафайлы локально.
     Без этого `relative_path` для медиа будет `null` в `raw_payload`.
   - **Format: JSON** — **обязательно**. Machine-readable format.
   - **Size limit** — оставить без ограничений.
4. Нажми **Export** и дождись завершения.  
   Для ~6 GB переписки экспорт занимает 10–30 минут в зависимости от скорости диска.
5. По завершении Telegram Desktop предложит открыть папку.  
   Путь по умолчанию:  
   `~/Downloads/Telegram Desktop/DataExport_YYYY-MM-DD/`
6. Убедись, что внутри папки есть файл `result.json`.  
   Его размер для 300+ личных чатов — порядка 30–50 МБ (текст без медиа)
   или несколько ГБ (с медиа).

---

## Что сделать после завершения экспорта

Выполни шаги по порядку.

### 1. Скопируй папку экспорта в рабочее место

```bash
mkdir -p ~/pili-crm-data/tg-exports
cp -r ~/Downloads/Telegram\ Desktop/DataExport_YYYY-MM-DD \
      ~/pili-crm-data/tg-exports/
```

Скрипт по умолчанию ищет последнюю папку `DataExport_*` в
`~/pili-crm-data/tg-exports/`. Если оставить экспорт в `~/Downloads`,
передавай путь явно через `--input-dir`.

### 2. Убедись, что PostgreSQL запущен

```bash
docker-compose up -d postgres
# проверка
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm -c "SELECT 1;"
```

### 3. Dry-run — проверь, что будет импортировано

```bash
python3 -m ingestion.tg_import --dry-run
```

Вывод покажет список чатов и количество сообщений **без записи в БД**.
Убедись, что количество чатов совпадает с ожидаемым (303 для текущего экспорта).

### 4. Запусти импорт

```bash
python3 -m ingestion.tg_import
```

Прогресс выводится построчно:

```
[1/303] Иван Петров: 87 messages
[2/303] Анна Сидорова: 143 messages
...
=== Import complete (42.3s) ===
Chats   : total=303  new=303  updated=0  failed=0
Messages: inserted=65096  skipped=0
```

Если нужен подробный лог (debug уровень):

```bash
python3 -m ingestion.tg_import --verbose
```

Если папка экспорта не в стандартном месте:

```bash
python3 -m ingestion.tg_import --input-dir /path/to/DataExport_2026-04-11
```

### 5. Проверь результат

```sql
-- количество чатов и сообщений
SELECT COUNT(*) FROM communications_telegram_chat;
SELECT COUNT(*) FROM communications_telegram_message;

-- все чаты должны иметь review_status = 'unreviewed'
SELECT review_status, COUNT(*)
FROM communications_telegram_chat
GROUP BY review_status;

-- watermark заполнен у всех чатов
SELECT COUNT(*) FROM communications_telegram_chat
WHERE last_imported_message_id IS NULL;
-- должно вернуть 0
```

---

## Что делать при повторном экспорте через N месяцев

Скрипт **идемпотентен**: повторный запуск на том же или расширенном экспорте
не создаёт дублей.

Механизм:

- `last_imported_message_id` в таблице `communications_telegram_chat` — watermark.
  При следующем запуске импортируются только сообщения с `id > watermark`.
- Уникальный constraint `(chat_id, telegram_message_id)` + `ON CONFLICT DO NOTHING`
  страхует от дублей даже если watermark по какой-то причине не сработал.

### Порядок действий

1. Сделай новый экспорт в Telegram Desktop (те же настройки).
2. Скопируй в `~/pili-crm-data/tg-exports/` — новая папка `DataExport_YYYY-MM-DD`.
3. Запусти:

```bash
python3 -m ingestion.tg_import
```

Скрипт автоматически возьмёт **последнюю** папку `DataExport_*` по алфавитной
сортировке (хронологически последнюю, если имена содержат дату).

Вывод покажет `updated=303  inserted=<только новые сообщения>`.

### Фаза 2 (автоматический инкремент)

После настройки `ingestion/tg_incremental.py` (Задание 3 ADR-010) ручной
повторный экспорт потребуется только при потере session-файла Telethon.
До тех пор — ручной экспорт раз в несколько месяцев для полноты истории.

---

## Troubleshooting

### `result.json not found in ...`

Папка экспорта не содержит `result.json`. Убедись:
- Экспорт завершился полностью (Telegram Desktop показал «Export complete»).
- Передан правильный путь: `--input-dir /path/to/DataExport_YYYY-MM-DD`
  (папка, а не сам `result.json`).

### `No DataExport_* directory found in ~/pili-crm-data/tg-exports/`

Стандартная папка не существует или пуста. Передай путь явно:

```bash
python3 -m ingestion.tg_import \
  --input-dir ~/Downloads/Telegram\ Desktop/DataExport_2026-04-11
```

### `DB not available` / `connection refused`

PostgreSQL не запущен:

```bash
docker-compose up -d postgres
```

### `FAILED 'Иван Петров': ...`

Ошибка при импорте одного чата. Этот чат пропущен, остальные продолжают
импортироваться. Полный traceback виден при `--verbose`.

Частые причины:
- Нарушена целостность JSON для конкретного чата (редко, но бывает при
  прерванном экспорте).
- Временная проблема с соединением с БД — запусти скрипт повторно;
  watermark защитит от дублей.

### Импорт завис на одном чате

Очень большой чат (10 000+ сообщений) может занять несколько секунд.
Прогресс-строка выводится в начале обработки чата, а не по завершении.
Подожди — или запусти с `--verbose` для подробного лога.

### `UnicodeDecodeError`

`result.json` должен быть в UTF-8. Telegram Desktop всегда создаёт UTF-8,
но если файл был перемещён через Windows-утилиты — возможна перекодировка.
Проверь: `file result.json` должен показать `UTF-8 Unicode text`.

### Повторный запуск добавил 0 сообщений, хотя в Telegram есть новые

Значит, новые сообщения вышли **после** даты экспорта. Сделай новый экспорт
или дождись настройки Фазы 2 (Telethon, Задание 3 ADR-010).
