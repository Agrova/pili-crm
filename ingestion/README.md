# ingestion — Telegram Data Ingestion (multi-account)

Скрипты разового и инкрементального импорта переписки из Telegram-аккаунтов
оператора в таблицы `communications_telegram_chat` / `communications_telegram_message`.

Реализует **Фазу 1 (исторический импорт)** ADR-010 с расширением **ADR-012**
(несколько аккаунтов). Фаза 2 (Telethon инкремент) — Задание 3 ADR-010.

---

## Структура папок на диске (ADR-012 §5)

Корень: `/Users/protey/pili-crm-data/tg-exports/`.

```
/Users/protey/pili-crm-data/tg-exports/
├── +77471057849/                    ← казахстанский аккаунт
│   └── DataExport_2026-04-11/       ← legacy-структура (исходная выгрузка)
│       ├── chats/
│       ├── result.json
│       └── ...
└── +79161879839/                    ← российский аккаунт
    ├── chats/                       ← плоская структура (новый стандарт)
    ├── profile_pictures/
    └── result.json
```

**Правила:**

- Имя папки-аккаунта — `phone_number` в формате E.164 (`+77471057849`).
- **Предпочтительная структура — плоская:** `result.json` и `chats/`
  лежат прямо в `+PHONE/`, без промежуточной `DataExport_YYYY-MM-DD/`.
- **Legacy-структура** (`+PHONE/DataExport_*/result.json`) — поддерживается
  для уже существующих выгрузок. Скрипт берёт **самую свежую** `DataExport_*`,
  если плоский `result.json` отсутствует.
- **Одна выгрузка на аккаунт.** При обновлении — старое содержимое папки
  заменяется новым (см. «Замена выгрузки»).

---

## Как сделать экспорт в Telegram Desktop

1. **Telegram Desktop → ☰ → Settings → Advanced → Export Telegram data**.
2. Настройки:
   - **Personal chats** — включить обязательно.
   - **Bot chats / Private groups / Private channels** — выключить.
   - **Photos / Files / Videos** — по необходимости.
   - **Format: JSON** — обязательно.
3. Положи содержимое выгрузки в **папку аккаунта** с E.164 именем —
   напрямую (плоская структура). Пример:

   ```bash
   mkdir -p /Users/protey/pili-crm-data/tg-exports/+79161879839
   cp -r ~/Downloads/Telegram\ Desktop/* \
         /Users/protey/pili-crm-data/tg-exports/+79161879839/
   ```

---

## Рабочий процесс: первый импорт нового аккаунта

### Шаг 1. Зарегистрируй аккаунт в БД

```bash
python3 -m ingestion.register_account \
  --phone +79161879839 \
  --display-name "Россия (+79161879839)"
```

Exit 0 — аккаунт создан, exit 1 — номер уже зарегистрирован.

### Шаг 2. Убедись, что PostgreSQL запущен

```bash
docker-compose up -d postgres
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm -c "SELECT 1;"
```

### Шаг 3. Dry-run

```bash
# Автодетект — если в ~/pili-crm-data/tg-exports ровно один аккаунт
python3 -m ingestion.tg_import --dry-run

# Или явно
python3 -m ingestion.tg_import \
  --input-dir /Users/protey/pili-crm-data/tg-exports/+79161879839/ --dry-run
```

Выводит список чатов и количество сообщений **без записи в БД**.

### Шаг 4. Запусти импорт

```bash
python3 -m ingestion.tg_import \
  --input-dir /Users/protey/pili-crm-data/tg-exports/+79161879839/
```

Все чаты/сообщения попадут с `owner_account_id` российского аккаунта.
`first_import_at` и `last_import_at` у аккаунта заполняются автоматически;
`telegram_user_id` — из `personal_information.user_id` в JSON, если
в аккаунте пока NULL.

### Шаг 5. Проверь результат

```sql
SELECT owner_account_id, COUNT(*)
  FROM communications_telegram_chat GROUP BY owner_account_id;
SELECT COUNT(*) FROM communications_telegram_message;
SELECT id, phone_number, display_name, first_import_at, last_import_at
  FROM communications_telegram_account ORDER BY id;
```

---

## Добавление нового аккаунта (третьего и далее)

1. Сделай выгрузку в Telegram Desktop с этого аккаунта.
2. Положи содержимое в `+PHONE/` (плоская структура).
3. `python3 -m ingestion.register_account --phone +PHONE --display-name "..."`.
4. `python3 -m ingestion.tg_import --input-dir .../+PHONE/`.

---

## Замена выгрузки (ADR-012 §10)

На диске храним **одну актуальную выгрузку на аккаунт**. БД — источник
правды; JSON-файлы после импорта нужны только как подстраховка до следующей
выгрузки.

```bash
cd /Users/protey/pili-crm-data/tg-exports/+77471057849/
rm -rf chats/ profile_pictures/ result.json DataExport_*/   # снос старого
cp -r ~/Downloads/Telegram\ Desktop/* ./                    # новое содержимое

cd /Users/protey/pili-crm
python3 -m ingestion.tg_import --input-dir \
  /Users/protey/pili-crm-data/tg-exports/+77471057849/
```

Идемпотентность:

- В таблице `communications_telegram_chat` — watermark
  `last_imported_message_id`. Повторный запуск импортирует только сообщения
  с `id > watermark`.
- UNIQUE `(chat_id, telegram_message_id)` + `ON CONFLICT DO NOTHING`
  страхует от дублей даже при расхождении watermark.
- Чаты конфликт-резолвятся по паре `(owner_account_id, telegram_chat_id)` —
  два аккаунта **не схлопнут** свои чаты, даже если Telegram выдал им
  одинаковые `telegram_chat_id`.

---

## Автодетект (без `--input-dir`)

```bash
python3 -m ingestion.tg_import
```

- Сканирует `/Users/protey/pili-crm-data/tg-exports/` на подпапки с именами
  в E.164 формате.
- Ровно один аккаунт → импортирует его. Несколько → отказ с подсказкой
  передать `--input-dir`.
- Внутри папки аккаунта: сначала ищет плоский `result.json`, иначе
  fallback на самую свежую `DataExport_*/`.

---

## Troubleshooting

### `Account <+PHONE> is not registered`

Скрипт не нашёл запись в `communications_telegram_account`. Выполни:

```bash
python3 -m ingestion.register_account --phone +PHONE --display-name "<label>"
```

### `phone mismatch — folder says +X, but result.json says +Y`

Выгрузка положена в неправильную папку аккаунта. Проверь, что имя папки
совпадает с `personal_information.phone_number` из `result.json`
(whitespace нормализуется автоматически, но сам номер должен совпадать).

### `... is not inside an E.164 account directory`

Папка выгрузки лежит не внутри `+PHONE/` обёртки. Создай папку аккаунта
с E.164 именем и перенеси туда содержимое (см. «Структура папок»).

### `Multiple accounts found (..., ...). Pass --input-dir to pick one.`

В корне экспортов больше одного E.164-аккаунта, автодетект неоднозначен.
Передай `--input-dir .../+PHONE/` явно.

### `result.json not found in ...`

В папке аккаунта нет ни плоского `result.json`, ни `DataExport_*/result.json`.
Убедись, что Telegram Desktop завершил экспорт и файлы скопированы.

### `DB not available` / `connection refused`

```bash
docker-compose up -d postgres
```

### `FAILED 'Иван Петров': ...`

Ошибка в одном чате. Остальные продолжают импортироваться. Полный traceback
виден при `--verbose`. Причины обычно — нарушенный JSON отдельного чата или
временная проблема с БД; повторный запуск безопасен (watermark + ON CONFLICT).
