# ADR-015: Стабилизация медиа-метаданных в Telegram-инжестере

**Статус:** draft
**Дата:** 2026-04-26
**Связанные ADR:** ADR-010 (Telegram ingestion), ADR-010 Addendum (reply и медиа-метаданные), ADR-012 (Telegram multiple accounts), ADR-014 (Media extraction pipeline — зависит от этого ADR)
**Связанные документы:** `06_open_questions.md`, `ingestion/parser.py`, `ingestion/tg_import.py`

---

## Контекст

ADR-010 Addendum от 2026-04-23 зафиксировал требование сохранять для каждого медиа-сообщения нормализованный набор полей: `media_type`, `file_name`, `relative_path`, `file_size_bytes`, `mime_type`. Цель — обеспечить возможность открыть физический файл по относительному пути относительно корня экспорта при последующей обработке (разбор чата оператором, media_extract pipeline и т. д.).

При диагностике в рамках работы над ADR-014 (Media extraction pipeline) обнаружено, что **это требование фактически не выполнено**. Парсер `ingestion/parser.py` корректно вычисляет объект `ParsedMediaMetadata` со всеми нужными полями, однако инжестер `ingestion/tg_import.py` в функции `_import_one_chat` не записывает этот объект в БД. В таблицу `communications_telegram_message` пишется только сырой `raw_payload` (исходный dict сообщения из Telegram-выгрузки), в котором нет ключа `relative_path` — этот ключ существует только в нашей нормализованной модели, не в формате Telegram.

### Реальное состояние БД на 2026-04-26

```
total_with_media:    387
with_relative_path:  0
```

- 387 сообщений из 82776 (~0.5%) имеют `media_type` в `raw_payload` — это типы, у которых Telegram сам пишет ключ `media_type` в JSON (`video_file`, `animation`, `voice_message`).
- **Ноль** сообщений имеют `relative_path` в `raw_payload`.
- **Фото фактически отсутствуют** в выборке: у фото в Telegram-JSON есть только ключ `photo`, но нет `media_type: 'photo'`. Парсер это понимает (`elif "photo" in msg: media_type = "photo"`), но эта нормализация теряется при записи в БД.
- Excel и Word файлы аналогично — у них в Telegram-JSON ключ `file`, без `media_type`.

То есть **реально импортированных медиа-сообщений в БД сильно меньше, чем должно быть** (если считать по нормализованным типам), и ни одно из них не имеет адресации к физическому файлу.

### Источник данных для исправления

Физические медиа лежат на Mac в `~/pili-crm-data/tg-exports/+<phone>/` для каждого аккаунта (см. ADR-012). Внутри каждого аккаунта есть `result.json`, из которого `parse_export` корректно извлекает все ParsedMediaMetadata. То есть исходник для backfill готов и доступен — нужно только переписать его в БД.

### Зачем это нужно сейчас

ADR-014 (Media extraction pipeline) реализуется **только после** ADR-015. Без `relative_path` в БД media_extract не найдёт ни одного физического файла. Аналогично — любая будущая фича, которой нужно открыть файл из чата (например, MCP-tool «показать медиа сообщения N» в Cowork), упрётся в ту же проблему.

### Что не обсуждается в этом ADR

- Содержимое медиа (vision-обработка, парсинг офисных файлов) — это ADR-014.
- Структура корня экспортов и многоаккаунтность — это ADR-012.
- Ручное копирование медиа в `~/pili-crm-media/` (упоминание из ADR-010 Addendum) — этот шаг признаётся устаревшим: реальное хранилище уже находится в `~/pili-crm-data/tg-exports/`, копирование не нужно.

---

## Варианты

### Вариант A — Обогащать `raw_payload` нормализованными полями

При импорте инжестер берёт сырой `msg_dict`, добавляет в него ключи `media_type`, `file_name`, `relative_path`, `file_size_bytes`, `mime_type` (которые есть в `ParsedMediaMetadata`), и пишет всё это как `raw_payload`.

- Плюсы: схема БД не меняется; `raw_payload` действительно содержит всё, как обещает ADR-010 Addendum.
- Минусы: смешивание сырых данных Telegram и нормализованных — нечисто; запросы вида «найди все сообщения с относительным путём» делаются через JSONB-операторы, медленнее индексов; невозможна чёткая типизация на уровне SQLAlchemy.

### Вариант B — Отдельные колонки в `communications_telegram_message`

Добавить в таблицу сообщений колонки `media_type`, `file_name`, `relative_path`, `file_size_bytes`, `mime_type` (все nullable). Сырой Telegram-блок остаётся в `raw_payload`.

- Плюсы: индексы прямо на колонках; чистое разделение «сырое vs нормализованное»; типизация на уровне SQLAlchemy.
- Минусы: миграция расширяет уже большую таблицу 5 колонками, заполненными только для медиа-сообщений (sparse-данные); смешение разных аспектов сообщения в одной таблице.

### Вариант C — Отдельная таблица `communications_telegram_message_media`

Новая таблица 1-к-1 (или 0-к-1) с полями `message_id FK`, `media_type`, `file_name`, `relative_path`, `file_size_bytes`, `mime_type`.

- Плюсы: чистое разделение; основная таблица сообщений не разрастается; sparse-данные не висят на каждой строке; симметрично к будущей таблице `communications_telegram_message_media_extraction` из ADR-014; возможен JOIN, который и так нужен в media_extract pipeline.
- Минусы: JOIN при чтении (но он симметричен к JOIN на media_extraction, паттерн уже принят).

---

## Критерии выбора

- **Чистота модели данных:** разделение сырого Telegram-блока, нормализованных метаданных и извлечённого контента.
- **Совместимость с ADR-014:** структура должна давать чистую схему JOIN при выборке сообщений в media_extract.
- **Минимизация миграционного риска:** не трогать `communications_telegram_message` если можно обойтись без этого.
- **Не нарушать immutability `communications_telegram_message`:** сырые данные сообщения остаются как импортированы.
- **Производительность:** возможность использовать B-tree индексы вместо JSONB-операторов.

---

## Принятое решение

Принимается **Вариант C** со следующими параметрами.

### 1. Новая таблица `communications_telegram_message_media`

```sql
CREATE TABLE communications_telegram_message_media (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL
        REFERENCES communications_telegram_message(id)
        ON DELETE CASCADE,
    media_type TEXT NOT NULL,
        -- Нормализованный тип из ParsedMediaMetadata.media_type:
        -- 'photo', 'file', 'video_file', 'voice_message', 'sticker',
        -- 'animation', 'audio_file', 'video_message' и т.д.
    file_name TEXT NULL,
    relative_path TEXT NULL,
        -- Относительно корня аккаунта
        -- (~/pili-crm-data/tg-exports/+<phone>/),
        -- БЕЗ префикса +<phone>/.
        -- NULL если файл не был выгружен Telegram'ом
        -- (маркер "(File not included...)").
    file_size_bytes BIGINT NULL,
    mime_type TEXT NULL,
    UNIQUE (message_id)
);

CREATE INDEX ix_telegram_message_media_media_type
    ON communications_telegram_message_media(media_type);

CREATE INDEX ix_telegram_message_media_mime_type
    ON communications_telegram_message_media(mime_type);

CREATE INDEX ix_telegram_message_media_has_relative_path
    ON communications_telegram_message_media(message_id)
    WHERE relative_path IS NOT NULL;
```

Ограничения:

- `ON DELETE CASCADE` — при удалении сообщения запись медиа-метаданных удаляется автоматически.
- `UNIQUE (message_id)` — одна запись на одно сообщение (1-к-1 или 0-к-1).
- `media_type NOT NULL` — если запись создана, тип всегда известен.
- Остальные поля nullable: Telegram может не сохранить имя файла или mime, файл может быть не выгружен (тогда `relative_path IS NULL`).

### 2. Что считать «медиа-сообщением» для записи

Запись в `communications_telegram_message_media` создаётся для **каждого сообщения, у которого `parser.py` вернул не-None `ParsedMediaMetadata`** и которое дошло до БД.

`parser.py` сейчас возвращает `None` (целое сообщение пропускается) для:
- `type == 'service'` — служебные сообщения.
- `media_type in ('voice_message', 'sticker') и text is None` — голосовые и стикеры без текста.

Эти сообщения в БД не попадают и не учитываются в новой таблице.

Все остальные медиа-типы — записываются. Это включает: `photo`, `file`, `video_file`, `voice_message с текстом`, `sticker с текстом`, `animation`, `audio_file`, `video_message` и любые другие, что возвращает Telegram.

### 3. Доработка инжестера

В `ingestion/tg_import.py`, функция `_import_one_chat`, после INSERT'а пакета сообщений добавляется параллельный INSERT в новую таблицу:

```python
# После INSERT в communications_telegram_message:
media_values = [
    {
        "message_id": <inserted_message_id>,
        "media_type": msg.media.media_type,
        "file_name": msg.media.file_name,
        "relative_path": msg.media.relative_path,
        "file_size_bytes": msg.media.file_size_bytes,
        "mime_type": msg.media.mime_type,
    }
    for msg in chunk
    if msg.media is not None
]
if media_values:
    await conn.execute(
        pg_insert(CommunicationsTelegramMessageMedia)
        .values(media_values)
        .on_conflict_do_nothing(constraint="<unique_message_id_constraint>")
    )
```

Тонкость: `pg_insert` в `communications_telegram_message` использует `ON CONFLICT DO NOTHING` для идемпотентности, и `RETURNING` нельзя получить для пропущенных строк. Поэтому нужен `RETURNING id, telegram_message_id` от INSERT'а, и затем матчинг `msg → inserted_message_id` по `telegram_message_id` перед формированием `media_values`. Это деталь реализации, описывается в задаче для Claude Code.

`ON CONFLICT DO NOTHING` для media-таблицы обеспечивает идемпотентность при повторном импорте: если запись уже есть — не дублируется.

### 4. Backfill для существующих сообщений

Отдельный скрипт `ingestion/backfill_media_metadata.py`:

```bash
python3 -m ingestion.backfill_media_metadata [--account +PHONE] [--dry-run] [--verbose]
```

Логика:

1. Получить список аккаунтов из БД (`communications_telegram_account` или аналогичной таблицы из ADR-012).
2. Для каждого аккаунта:
   - Найти `result.json` через `find_result_json(account_dir)` (функция уже есть в `tg_import.py`).
   - Запустить `parse_export(json_path)` — получить список `ParsedChat` со всеми сообщениями и медиа.
3. Для каждого `ParsedMessage`, у которого `media is not None`:
   - Найти соответствующее сообщение в БД по `(owner_account_id, telegram_chat_id, telegram_message_id)`.
   - Если найдено — INSERT в `communications_telegram_message_media`, `ON CONFLICT DO NOTHING` (идемпотентность).
   - Если не найдено — лог-warning и пропуск (вариант D.1).
4. По завершении — финальный отчёт:
   - Сообщений с медиа в `result.json`: X
   - Сообщений с медиа в БД (уже было): Y
   - Записей создано в `communications_telegram_message_media`: Z
   - Warning'ов «медиа в JSON, нет в БД»: W

Скрипт идемпотентен: повторный запуск не дублирует записи и не падает.

### 5. Поведение при отсутствии медиа в `result.json` для сообщения из БД

Может возникнуть обратная ситуация: в БД есть сообщение, у которого `raw_payload->>'media_type'` не пустой, но при текущем парсинге `result.json` парсер не возвращает для него `ParsedMediaMetadata`. Это маловероятно, но теоретически возможно (баг прежнего парсера, частично экспортированный JSON и т. п.).

Решение: backfill-скрипт идёт **от** `result.json`, а не **от** БД. То есть он создаёт записи только для тех медиа-сообщений, которые видит в свежем парсе. Сообщения в БД, для которых медиа не нашлось в JSON, остаются без записи в `communications_telegram_message_media` — и трактуются media_extract pipeline (ADR-014) как «медиа есть в `raw_payload`, но недоступно для обработки».

Это вариант **D.1** из обсуждения — логируем warning и пропускаем; не создаём «пустую» запись с `relative_path = NULL`, потому что таблица предназначена для **успешно нормализованных** метаданных.

### 6. Сверка целостности после backfill

В конце backfill-скрипта (и опционально как отдельная команда `--verify`) выполняется проверка:

```sql
SELECT
    (SELECT COUNT(*) FROM communications_telegram_message
       WHERE raw_payload->>'media_type' IS NOT NULL
          OR raw_payload ? 'photo'
          OR raw_payload ? 'file') AS messages_with_media_raw,
    (SELECT COUNT(*) FROM communications_telegram_message_media) AS media_records;
```

Расхождение между этими двумя числами — нормальная ситуация, если в `raw_payload` есть медиа без `relative_path` (файл не выгружен) — тогда `parser.py` всё равно вернёт `ParsedMediaMetadata` с `relative_path = None`, и запись будет создана. Принципиальное расхождение (порядки) — повод для анализа.

### 7. SQLAlchemy-модель

В `app/communications/models.py` добавляется класс:

```python
class CommunicationsTelegramMessageMedia(Base):
    __tablename__ = "communications_telegram_message_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("communications_telegram_message.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(Точные имена импортов — по соглашениям проекта, см. остальные модели в `models.py`.)

### 8. Что остаётся в `raw_payload`

Сырой Telegram-блок остаётся в `raw_payload` без изменений. Это сохраняет immutability `communications_telegram_message`: импортированные данные не модифицируются. Если в будущем понадобится поле, которого нет в нормализованной модели — оно по-прежнему доступно через `raw_payload`.

---

## Последствия

### Что становится проще

- **ADR-014 разблокируется** — media_extract получает чистый источник `relative_path` для каждого медиа-сообщения.
- **Любые будущие фичи, нуждающиеся в физических файлах** (например, MCP-tool «открыть фото в Cowork») — получают единую точку доступа.
- **Закрывается долг ADR-010 Addendum** — нормализованные метаданные действительно сохраняются, как было заявлено.
- **Чистая модель данных** — три уровня: сырое (`raw_payload`), нормализованные метаданные (`message_media`), извлечённое содержимое (`message_media_extraction` из ADR-014). Каждая таблица имеет одну ответственность.

### Какие ограничения появляются

- **Backfill зависит от наличия `result.json` обоих аккаунтов** в `~/pili-crm-data/tg-exports/`. Если файл удалён — backfill для соответствующего аккаунта невозможен.
- **Расширение схемы БД** — одна новая таблица. Незначительное.
- **Дополнительный INSERT при импорте** — на каждое медиа-сообщение. На практике незаметно (~387 медиа на 82776 сообщений в текущих данных, доля медиа невелика; даже при росте доли до десятков процентов — лишний INSERT в одну таблицу не является узким местом).

### Что придётся учитывать дальше

- **Workflow новых импортов** — оператор привычно запускает `python3 -m ingestion.tg_import`, и нормализованные метаданные сохраняются автоматически. Backfill нужен только один раз для существующих данных.
- **Зависимость ADR-014** — после реализации этого ADR разблокируется реализация media_extract pipeline.
- **Сверка после backfill** — глазами проверить отчёт скрипта; если число warning'ов «медиа в JSON, нет в БД» неожиданно велико — повод для отдельного разбора (возможно, инжестер ранее терял какие-то сообщения).

---

## Что должен сделать Claude Code

Реализация разбивается на **две задачи**.

### Задача 1 — Миграция БД и SQLAlchemy-модель

- Alembic-миграция: создание таблицы `communications_telegram_message_media` со всеми колонками, индексами и FK по схеме раздела «1. Новая таблица».
- Класс `CommunicationsTelegramMessageMedia` в `app/communications/models.py` по схеме раздела «7. SQLAlchemy-модель».
- Юнит-тест на модель: создание записи, выборка, каскадное удаление при удалении родительского сообщения.

Безопасность: миграция полностью аддитивная, существующие данные не трогаются. Может быть применена без риска.

### Задача 2 — Доработка инжестера и backfill

Объединённая задача:

**Часть A — Доработка `_import_one_chat` в `tg_import.py`:**
- INSERT'ы в `communications_telegram_message` дополняются `RETURNING id, telegram_message_id`.
- После INSERT'а пакета — построение mapping `telegram_message_id → inserted_message_id`.
- Параллельный INSERT в `communications_telegram_message_media` для всех `msg.media is not None`, через `ON CONFLICT DO NOTHING`.
- Транзакция чата сохраняется единой (вся вставка в одной транзакции, как сейчас).

**Часть B — Скрипт `ingestion/backfill_media_metadata.py`:**
- Точка входа `python3 -m ingestion.backfill_media_metadata`.
- Аргументы: `--account +PHONE` (необязательный фильтр по одному аккаунту), `--dry-run`, `--verbose`, `--verify` (только сверка без записей).
- Логика по разделу «4. Backfill».
- Идемпотентность: повторный запуск не падает и не дублирует.
- Финальный отчёт по разделу «4».
- Структурированное логирование (warnings отдельной категорией).
- Graceful SIGINT.

**Часть C — Регрессионные тесты:**
- Тест: импорт чата с фото → проверка что в `communications_telegram_message_media` создана запись с корректным `relative_path`.
- Тест: импорт чата с xlsx-файлом → запись с `mime_type`, `file_name`.
- Тест: импорт чата с видео-сообщением → запись с `media_type='video_file'`.
- Тест: импорт сообщения без медиа → запись в новой таблице **не создаётся**.
- Тест: повторный импорт того же чата (идемпотентность) — записи в `message_media` не дублируются.
- Тест: backfill на synthetic тестовом `result.json` → корректное наполнение таблицы.
- Тест: backfill на пустом `result.json` → 0 записей, нет ошибок.

---

## Что проверить вручную

- [ ] Alembic-миграция накатывается на чистый dev-stage без ошибок.
- [ ] Alembic-миграция обратима: `alembic downgrade -1` корректно удаляет таблицу.
- [ ] Перед backfill — бэкап БД `pili_crm` (`pg_dump`), точка восстановления зафиксирована.
- [ ] Backfill в режиме `--dry-run` для аккаунта `+77471057849`: показывает корректное число медиа-сообщений из `result.json`, без записи в БД.
- [ ] Backfill боевой для `+77471057849`: записи созданы, отчёт сходится.
- [ ] Backfill боевой для `+79161879839`: то же.
- [ ] Сверка после backfill: `SELECT COUNT(*) FROM communications_telegram_message_media` существенно больше 0; запросы по медиа-типам и mime дают ожидаемое распределение.
- [ ] Вручную проверить 5-10 случайных записей: для каждой `relative_path` указанный файл реально существует на диске по пути `~/pili-crm-data/tg-exports/+<phone>/<relative_path>`.
- [ ] Повторный backfill: 0 новых записей, нет дублей, скрипт не падает.
- [ ] Импорт нового чата (тестовый или реальный новый чат) после доработки инжестера: новая запись и в `communications_telegram_message`, и в `communications_telegram_message_media`.
- [ ] Каскадное удаление: `DELETE FROM communications_telegram_message WHERE id = <X>` — соответствующая запись из `message_media` удалена.
- [ ] После всех проверок — снять статус «заблокирован ADR-015» с ADR-014 и переходить к его реализации.

---

## Открытые вопросы

- **Q (для архитектурного штаба):** имя поля и таблицы для аккаунтов оператора (`communications_telegram_account` или другое) — уточняется по ADR-012 при формировании промта Claude Code. Не блокирует ADR-015 — только деталь реализации.
- **Q (для архитектурного штаба, на будущее):** при последующих обновлениях экспорта Telegram (новые сообщения в существующих чатах) backfill-скрипт можно использовать для дозаливки медиа-метаданных, но workflow должен быть формализован — нужна ли отдельная команда `--incremental` или достаточно повторного полного запуска (он идемпотентен)? Это становится актуальным при ритмичных импортах.
- **Q (вне скоупа этого ADR, для отдельного решения):** как поступать если `relative_path` есть, но физического файла на диске уже нет (например, удалили `result.json` или подкаталог `chats/`)? Сейчас это решается на уровне media_extract (ADR-014) — он логирует warning и переходит к следующему сообщению. Если в БД будет много таких «битых ссылок» — может потребоваться периодическая валидация.
