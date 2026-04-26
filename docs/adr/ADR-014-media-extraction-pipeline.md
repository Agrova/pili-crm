# ADR-014: Media extraction pipeline для Telegram-чатов

**Статус:** draft (заблокирован ADR-015)
**Дата:** 2026-04-26
**Связанные ADR:** ADR-010 (Telegram ingestion), ADR-010 Addendum (reply и медиа-метаданные), ADR-011 (Telegram chat analysis pipeline), ADR-011 Addendum 2 (Mac master + PC worker), ADR-012 (Telegram multiple accounts), ADR-015 (стабилизация медиа-метаданных — pre-requisite)
**Связанные документы:** `06_open_questions.md`

---

## Контекст

При настройке PC-worker'a по ADR-011 Addendum 2 обнаружено расхождение между объёмом Telegram-выгрузки (~1.6 GB на Mac в `~/pili-crm-data/tg-exports/`) и объёмом БД `pili_crm` (~50 MB). Дельта — медиа-файлы (фото, Excel, Word, ссылки), которые анализатор `analysis/run.py` не обрабатывает: он читает только текст из БД и игнорирует медиа-контент.

### Эмпирика

Оператор подтвердил субъективную оценку по 850 unreviewed чатам:
- ~80% медиа-сообщений — **фото** (фото товаров без подписи, фото с подписями, скриншоты маркетплейсов и сайтов поставщиков, таблицы цен в виде картинки).
- Заметная доля — **Excel и Word файлы с заказами**, формируются клиентами в свободной форме.
- Встречаются ссылки на внешние магазины (eBay и подобные ритейлеры по всему миру).
- **Голосовых сообщений нет.**
- **PDF почти нет.**
- Видео и стикеры в импорт не попадают (отсечены ADR-010).

Без обработки медиа narrative-анализ unreviewed чатов получается систематически неполным: типичный сценарий «клиент кидает фото → оператор отвечает текстом» в результате выглядит как «[пропуск] → ответ за 12000» — связь рвётся.

### Ограничения, уже зафиксированные в предыдущих ADR

- ADR-010 Addendum: для каждого медиа-сообщения должны сохраняться нормализованные метаданные (`media_type`, `file_name`, `relative_path`, `file_size_bytes`, `mime_type`).
- ADR-011 Addendum 2: распределение Mac master + PC worker. Mac обрабатывает интерактивные задачи и `apply_analysis_to_customer`. PC — выделенный 24/7 narrative-воркер, использует `--no-apply`. Sync результатов pull-моделью раз в сутки.
- ADR-012 (multiple accounts): один оператор может иметь несколько Telegram-аккаунтов. Каждый чат принадлежит конкретному `owner_account_id`. Conflict resolution — по `(owner_account_id, telegram_chat_id)`. Корень экспортов на Mac: `~/pili-crm-data/tg-exports/+<phone>/`.
- ADR-002: стек Python 3.12+ / SQLAlchemy 2.0 async / PostgreSQL 16.
- Mac-конфигурация: 24 GB unified memory (фактически меньше из-за резервов macOS).
- PC-конфигурация: RTX 3060 Ti 8 GB VRAM (vision-модели крупнее 8B практически не помещаются).
- Текущая текстовая модель: `qwen3-14b` на Mac MLX, `qwen3-8b` на PC.

### Pre-requisite — ADR-015

В рамках диагностики обнаружено, что текущая реализация инжестера (`ingestion/tg_import.py` + `ingestion/parser.py`) **не сохраняет нормализованные медиа-метаданные** в БД, хотя `parser.py` их корректно вычисляет в объекте `ParsedMediaMetadata`. В таблицу пишется только `raw_payload` — сырой dict из Telegram-выгрузки, без `relative_path`. По состоянию на 2026-04-26 в БД 387 сообщений с `media_type` (из 82776), и **0** из них имеют `relative_path`.

Без `relative_path` media_extract не может найти физический файл для обработки. Поэтому **ADR-014 заблокирован ADR-015** «Стабилизация медиа-метаданных в Telegram-инжестере», который должен:
1. Доработать инжестер так, чтобы `ParsedMediaMetadata` записывался в БД.
2. Сделать backfill для уже импортированных сообщений из обоих экспортов.

ADR-014 реализуется только после успешного завершения ADR-015.

### Что не обсуждается в этом ADR

- Voice-to-text (нет голосовых).
- PDF-парсинг (почти нет PDF; при необходимости — отдельная задача).
- Web-fetch для ссылок eBay и подобных (нетривиально, антибот-защита, парсеры под каждый сайт — отдельный ADR).
- Объектное хранилище медиа (S3/MinIO) — overkill для MVP.
- VL-обработка на PC — невозможна на 8 GB VRAM для моделей нашего класса.
- Доработка инжестера и backfill метаданных — предмет ADR-015.

---

## Варианты

### Вариант A — Принять как known limitation

Не обрабатывать медиа, в narrative-анализе явно отмечать пропуски.

- Плюсы: ноль работы; не меняем схему и pipeline.
- Минусы: ~80% чатов с медиа дают систематически неполный narrative; это сквозная деградация качества всей системы анализа.

### Вариант B — Обработка vision-моделью только в момент анализа

Анализатор `analysis/run.py` сам подгружает фото при формировании narrative-контекста. Vision-модель работает в той же фазе, что и текстовая.

- Плюсы: одна команда от оператора end-to-end.
- Минусы: повторная обработка медиа при каждом запуске анализа (дорого); жёсткая связка vision и text — нельзя сменить vision-модель без перепрогона всего; PC должен иметь vision-модель и доступ к файлам — невозможно (8 GB VRAM, файлы только на Mac).

### Вариант C — Обработка vision-моделью при ингесте

Инжестер `tg_import.py` прогоняет каждое медиа через vision сразу при импорте.

- Плюсы: однократная обработка.
- Минусы: жёстко привязывает версию vision-модели к моменту импорта; смена модели = реимпорт всей выгрузки; инжестер становится тяжёлым и долгим (раньше был быстрый структурный загрузчик).

### Вариант D — Отдельная фаза `media_extract`, описания в БД, переиспользуются всеми анализами

Самостоятельный CLI-скрипт `analysis/media_extract.py` извлекает текстовые описания из медиа и пишет их в отдельную таблицу. `analysis/run.py` читает описания через JOIN. Vision-обработка — только на Mac. PC получает готовые описания через sync БД (тот же канал, что и для остальных данных).

- Плюсы: однократная обработка; гибкость смены vision-модели (`extractor_version` в БД); сохранение immutability `communications_telegram_message`; PC работает с описаниями как с обычным текстом, файлы на PC не нужны; вписывается в принятое распределение Mac/PC.
- Минусы: новая таблица в схеме; новая утилита управления LM Studio.

---

## Критерии выбора

- **Надёжность:** медиа-описания должны быть детерминированно идентифицируемы по версии модели; перепрогон с новой моделью — простая операция.
- **Простота поддержки:** изоляция vision-фазы от narrative-фазы — два независимых CLI.
- **Сохранение PostgreSQL как источника истины:** описания живут в БД, не в файлах рядом с pipeline.
- **Совместимость с распределением Mac/PC (ADR-011 Addendum 2):** PC должен работать без доступа к файлам Mac.
- **Совместимость со стеком (ADR-002):** только pip-зависимости; локальная vision-модель через LM Studio (как уже работает text); openpyxl / python-docx — стандартные библиотеки.
- **Не ломать immutability `communications_telegram_message`:** описания живут в отдельной таблице, не как колонки исходного сообщения.
- **Совместимость с многоаккаунтностью (ADR-012):** хранение и извлечение должны корректно работать для нескольких `owner_account_id`.

---

## Принятое решение

Принимается **Вариант D** со следующими параметрами.

### 1. Архитектурная схема

```
┌────────────────────── Mac (master) ──────────────────────┐
│                                                          │
│  ~/pili-crm-data/tg-exports/                             │
│  ├── +77471057849/  (chats/, lists/, result.json)        │
│  └── +79161879839/  (chats/, profile_pictures/, ...)     │
│           │                                              │
│           ▼                                              │
│  python3 -m analysis.media_extract                       │
│  ├─ vision: Qwen3-VL-30B-A3B-Instruct-MLX-4bit           │
│  │  через LM Studio (REST на localhost:1234)             │
│  ├─ xlsx: openpyxl → плоский текст                       │
│  └─ docx: python-docx → плоский текст                    │
│           │                                              │
│           ▼                                              │
│  communications_telegram_message_media_extraction        │
│                                                          │
│  apply_analysis_to_customer (только Mac, по ADR-011 A2)  │
│           ▲                                              │
└───────────┼─────────┬────────────────────────────────────┘
            │         │ pull-sync БД (Addendum 2)
            │         ▼
┌───────────┼──────────────────────────────────────────────┐
│           │            PC (worker)                       │
│           │                                              │
│  python3 -m analysis.run --all --no-apply                │
│  читает messages LEFT JOIN media_extraction              │
│  файлы НЕ нужны                                          │
│           │                                              │
│           ▼                                              │
│  analysis_chat_analysis (worker_tag=pc)                  │
│           │                                              │
└───────────┼──────────────────────────────────────────────┘
            │
            └─ pull-sync БД обратно на Mac (Addendum 2):
               analysis_chat_analysis с PC-результатами
               → apply_analysis_to_customer на Mac
```

Sync — двунаправленный, организован в ADR-011 Addendum 2 и не пересматривается в этом ADR. ADR-014 добавляет одну новую таблицу к синхронизируемому набору.

### 2. Vision-модель

| Параметр | Значение |
|---|---|
| Primary | `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` |
| Fallback | `mlx-community/Qwen3-VL-8B-Instruct-MLX-4bit` |
| Архитектура primary | MoE, 30B параметров total, 3B активны на токен |
| Размер на диске и в памяти (4bit MLX) | ~18.27 GB (primary), ~5.78 GB (fallback) |
| Запуск | LM Studio локально на Mac, OpenAI-совместимый API на `localhost:1234/v1` |

Конкретная модель параметризуется через CLI и переменные окружения, не хардкодится:

```python
# app/config.py
MEDIA_EXTRACT_MODEL_PRIMARY = "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
MEDIA_EXTRACT_MODEL_FALLBACK = "mlx-community/Qwen3-VL-8B-Instruct-MLX-4bit"
MEDIA_EXTRACT_DEFAULT_ENDPOINT = "http://localhost:1234/v1"
```

CLI:

```bash
python3 -m analysis.media_extract --all                       # primary
python3 -m analysis.media_extract --all --use-fallback-model  # 8B при OOM
python3 -m analysis.media_extract --all --model <hf-id>       # произвольная
python3 -m analysis.media_extract --chat-id <id>              # один чат
python3 -m analysis.media_extract --message-id <id>           # одно сообщение
python3 -m analysis.media_extract --all --resume              # после Ctrl+C
```

### 3. Управление LM Studio

Введён модуль `app/llm_studio_control.py` с публичным API:

| Функция | Назначение |
|---|---|
| `list_loaded_models(endpoint)` | Текущий список загруженных моделей |
| `load_model(model_id, endpoint)` | Загрузить указанную модель |
| `unload_model(model_id, endpoint)` | Выгрузить указанную модель |
| `unload_all(endpoint)` | Выгрузить все модели |
| `ensure_model_loaded(model_id, endpoint)` | Гарантировать что загружена и активна именно `model_id` (выгрузить остальное при необходимости); поллинг health-check до готовности |

Связь с LM Studio — REST API (`/api/v0/models/*`).

`analysis/media_extract.py` вызывает `ensure_model_loaded(MEDIA_EXTRACT_MODEL_PRIMARY, ...)` в начале работы. По завершении (или по флагу `--unload-after`) — `unload_all`.

`analysis/run.py` модуль использует тот же механизм для своей text-модели (но это уже работает в текущей реализации — добавление `ensure_model_loaded` остаётся опциональным расширением; в рамках этого ADR обязательно только для `media_extract`).

### 4. Схема БД

Новая таблица:

```sql
CREATE TABLE communications_telegram_message_media_extraction (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL
        REFERENCES communications_telegram_message(id)
        ON DELETE CASCADE,
    extracted_text TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
        -- 'vision_qwen3-vl-30b-a3b'
        -- 'vision_qwen3-vl-8b'
        -- 'xlsx_openpyxl'
        -- 'docx_python_docx'
        -- 'placeholder'  (для file типов, которые не извлекаем)
    extractor_version TEXT NOT NULL,    -- 'v1.0' — версия pipeline
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id)
);

CREATE INDEX ix_media_extraction_method
    ON communications_telegram_message_media_extraction(extraction_method);

CREATE INDEX ix_media_extraction_extractor_version
    ON communications_telegram_message_media_extraction(extractor_version);
```

Записи существуют **только для медиа-сообщений**, для которых выполнен (или явно пропущен через placeholder) extract. Для текстовых сообщений записи не создаются — используется LEFT JOIN при чтении.

### 5. Какие медиа извлекаем

Логика определяется по нормализованным метаданным из ADR-015 (после реализации они должны быть доступны как поля или подполя в БД — конкретное место решается в ADR-015):

| `media_type` / mime | Действие | `extraction_method` |
|---|---|---|
| `photo` | vision | `vision_qwen3-vl-...` |
| `file`, `image/*` | vision | `vision_qwen3-vl-...` |
| `file`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (xlsx), `application/vnd.ms-excel` (xls) | openpyxl → плоский текст | `xlsx_openpyxl` |
| `file`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (docx), `application/msword` (doc) | python-docx → плоский текст | `docx_python_docx` |
| `file`, `application/pdf` | placeholder | `placeholder` |
| `file`, прочее | placeholder | `placeholder` |
| `video_file`, `animation` | placeholder | `placeholder` |
| `voice_message`, `sticker` | в БД не попадают (отсечено ADR-010) | — |

Placeholder-запись содержит:

```
[file: <file_name>, type: <mime_type>, size: <size>]
```

Это сохраняет факт «в этом сообщении был файл такого типа» в narrative-контексте, не теряя временную шкалу диалога.

### 6. Формат `extracted_text`

Единый шаблон для каждого типа.

**Для `photo` и `file image/*`:**

```
[Изображение]
Описание: <2-3 предложения о том, что на фото>
Текст на изображении: <расшифровка текста, если есть; иначе "отсутствует">
```

**Для xlsx:**

```
[Excel-файл: <file_name>]
Лист "<sheet_name>":
<плоское представление таблицы — TSV или markdown>
[следующий лист если есть]
```

**Для docx:**

```
[Word-файл: <file_name>]
<plain text абзацев в исходном порядке>
```

**Для placeholder:**

```
[file: <file_name>, type: <mime_type>, size: <bytes> bytes]
```

### 7. Промт vision-модели

Жёстко зафиксированный промт в `analysis/media_extract/prompts.py` с собственным `PROMPTS_VERSION = '1.0'`:

```
Ты обрабатываешь фотографию из переписки клиента магазина пиломатериалов
и инструментов «ПилиСтрогай».

Опиши, что изображено, в формате:

Описание: 2–3 предложения. Что за объект, в каком состоянии, какие
видны характеристики (материал, размер, форма, особенности).
Если изображение — скриншот или таблица — упомяни это в Описании.

Текст на изображении: дословная расшифровка любого читаемого текста
(в том числе на упаковке, ценниках, шапках таблиц, заголовках экранов).
Если текста нет — напиши "отсутствует".

Не добавляй никакой другой информации, не делай предположений
о намерениях отправителя. Не используй markdown, не оборачивай ответ
в код-блоки.
```

### 8. Изменения в `analysis/run.py`

Минимальные:

1. Запрос за сообщениями чата делает `LEFT JOIN communications_telegram_message_media_extraction me ON me.message_id = m.id`.
2. При формировании текста сообщения для LLM:
   - Если `me.extracted_text IS NULL` и нет `media_type` — обычный текст.
   - Если `me.extracted_text IS NOT NULL` — добавляется к тексту сообщения через разделитель:
     ```
     <message text>
     
     [Прикреплено]:
     <extracted_text>
     ```
   - Если `me.extracted_text IS NULL`, но `media_type IS NOT NULL` (медиа есть, описание ещё не извлечено) — добавляется маркер `[Прикреплено медиа: тип=<media_type>, описание не извлечено]`.

### 9. Где хранятся файлы

Корень экспортов на Mac: **`~/pili-crm-data/tg-exports/`**.

Структура:

```
~/pili-crm-data/tg-exports/
├── +77471057849/             ← аккаунт оператора (по E.164 номеру)
│   ├── chats/                  (157 подпапок чатов)
│   ├── lists/
│   └── result.json
└── +79161879839/             ← второй аккаунт оператора
    ├── chats/
    ├── profile_pictures/
    └── result.json
```

`relative_path` (после реализации ADR-015) сохраняется в БД **относительно корня аккаунта** — то есть без префикса `+<phone>/`. Например: `chats/chat_180/photos/photo_2024-05-01.jpg`.

Абсолютный путь к файлу строится склейкой:

```
absolute_path = TELEGRAM_EXPORTS_ROOT / f"+{account_phone}" / relative_path
```

где:
- `TELEGRAM_EXPORTS_ROOT` — переменная окружения, по умолчанию `~/pili-crm-data/tg-exports/`.
- `account_phone` — берётся через JOIN на таблицу аккаунтов (см. ADR-012, точное имя поля и таблицы — там) по `chat.owner_account_id`.

`media_extract` никогда не передаёт файлы на PC. PC работает только с описаниями из БД.

Mac-only обработка снимает вопросы синхронизации файлов между Mac и PC: PC получает только нормализованный текст (`extracted_text`) через тот же sync БД, что и для основного narrative-анализа.

### 10. Идемпотентность и резюме

`media_extract` идемпотентен:

- Перед обработкой сообщения проверяется наличие записи в `media_extraction` для этого `message_id`.
- Если запись существует и `extractor_version` совпадает с текущим — пропуск.
- Если `extractor_version` отличается — действие зависит от флага: по умолчанию пропуск (логика «не перезатирать прошлое»); с `--regenerate` — DELETE + INSERT.

Поддерживается `--resume` после Ctrl+C: graceful SIGINT, текущее сообщение завершается, далее выход.

### 11. Версионирование

| Что | Где | Что значит |
|---|---|---|
| `MEDIA_EXTRACTOR_VERSION` | `analysis/media_extract/__init__.py` | Версия pipeline в целом, пишется в БД (`extractor_version`). При смене модели или промта — инкремент. Стартовое значение `'v1.0'`. |
| `PROMPTS_VERSION` | `analysis/media_extract/prompts.py` | Версия только текстов промтов. При несовпадении с `MEDIA_EXTRACTOR_VERSION` — сигнал к перепрогону с новым `MEDIA_EXTRACTOR_VERSION`. Аналог паттерна из ADR-011. |

---

## Последствия

### Что становится проще

- **Восстановление контекста медиа в narrative-анализе** — основной positive impact. Сценарий «клиент шлёт фото → оператор отвечает» становится корректно интерпретируемым.
- **Гибкость смены vision-модели** — `extractor_version` в БД позволяет точечно перепрогнать.
- **Изоляция фаз pipeline** — vision-обработка отделена от narrative-анализа; можно прогонять независимо, тестировать независимо, переисполнять независимо.
- **Отсутствие изменений в инжестере** в рамках этого ADR — фикс инжестера происходит в ADR-015, ADR-014 опирается на стабильные метаданные.
- **Mac-only обработка медиа** — снимает вопросы синхронизации файлов между Mac и PC.

### Какие ограничения появляются

- **Vision-обработка только на Mac.** Если Mac недоступен надолго — новые медиа не обрабатываются. Боевой прогон 850 чатов на PC становится возможен только после полного прогона `media_extract` на Mac.
- **Память Mac впритык.** Qwen3-VL-30B-A3B занимает ~18 GB из ~24 GB unified memory. Во время прогона `media_extract` параллельный запуск других тяжёлых процессов на Mac (Cowork с большой историей, Docker с тяжёлыми контейнерами) может вызвать swap или OOM. Fallback на 8B модель (~6 GB) предусмотрен.
- **Скорость обработки.** На 850 чатах с десятками тысяч медиа-сообщений первый прогон `media_extract` займёт значительное время (часы — точная оценка после первого замера на 10–20 чатах).
- **Качество vision на нишевых сценариях.** Скриншоты с международных маркетплейсов могут содержать текст на разных языках; качество распознавания зависит от модели. Возможный компромисс качества покрывается возможностью перепрогона.
- **Excel/Word в свободной форме.** Структурный парсинг даёт плоский текст; LLM-narrative должен сам понять, что это позиции заказа. Это снижает точность относительно структурированного извлечения, но соответствует реальности (клиенты пишут как умеют).
- **Зависимость от ADR-015.** Без реализации ADR-015 этот ADR не может быть реализован — `media_extract` не сможет найти физические файлы.

### Что придётся учитывать дальше

- **Скоуп прогона.** Боевой прогон 850 чатов в `analysis/run.py` на PC должен запускаться только после полного `media_extract` на Mac и sync БД.
- **Эмпирическая валидация качества.** После первого прогона `media_extract` на 10–20 чатах нужно глазами проверить выборку описаний. Если 30B-A3B даёт неудовлетворительное качество — переключение на 8B или гибрид с облачным vision.
- **Файлы непропарсимых типов** (pdf, прочие file mime) — со временем накопится статистика, какие mime реально встречаются. На основании этого решается, нужен ли отдельный ADR для PDF-парсинга.
- **Web-fetch ссылок.** Ссылки на eBay и подобные пока остаются в исходном тексте сообщения как URL без извлечения. При недостатке контекста — отдельный ADR.
- **Многоаккаунтность (ADR-012).** Один и тот же клиент может писать на разные аккаунты оператора. На уровне `media_extract` это прозрачно — обработка идёт на уровне сообщений. На уровне narrative — `analysis/run.py` уже работает на уровне чатов, и применение результатов к клиенту через `apply_analysis_to_customer` уже корректно поддерживает многие чаты на одного клиента.

---

## Что должен сделать Claude Code

Реализация разбивается на задачи. Каждая задача — потенциальный отдельный промт для Prompt Factory. **Все задачи стартуют после успешного завершения ADR-015.**

### Задача 1 — Утилита управления LM Studio

Создать `app/llm_studio_control.py` с функциями `list_loaded_models`, `load_model`, `unload_model`, `unload_all`, `ensure_model_loaded`. Связь — REST API LM Studio. Поллинг health-check после загрузки. Юнит-тесты с моком HTTP.

### Задача 2 — Миграция БД

Alembic-миграция: новая таблица `communications_telegram_message_media_extraction` с колонками и индексами по схеме раздела «Принятое решение / 4. Схема БД». SQLAlchemy-модель в `app/communications/models.py`.

### Задача 3 — Парсеры офисных файлов

`analysis/media_extract/office.py` с функциями `extract_xlsx(path) -> str` и `extract_docx(path) -> str`, вывод по шаблонам раздела «6. Формат extracted_text». Юнит-тесты на синтетических xlsx и docx.

### Задача 4 — Vision-обработчик

`analysis/media_extract/vision.py` с функцией `extract_image(path, model_id, endpoint) -> str`. Использует промт из `analysis/media_extract/prompts.py` (`PROMPTS_VERSION = '1.0'`). Возвращает строку по шаблону «Изображение / Описание / Текст на изображении». Парсинг ответа модели с `_strip_json_fence`-подобной защитой от markdown-обёртки (паттерн из ADR-011 hotfix). Юнит-тест с моком LM Studio API.

### Задача 5 — CLI `analysis/media_extract.py`

Точка входа `python3 -m analysis.media_extract`:
- Аргументы: `--all`, `--chat-id`, `--message-id`, `--model`, `--use-fallback-model`, `--endpoint`, `--regenerate`, `--resume`, `--unload-after`, `--dry-run`.
- В начале: `ensure_model_loaded(...)` для vision (только если есть медиа типа image; для чисто xlsx/docx-сессии — пропуск).
- Селектор сообщений: фильтр по нормализованным `media_type`/`mime_type` (после ADR-015), отсев уже обработанных по `extractor_version`.
- Резолв абсолютного пути: JOIN на чат + аккаунт → `TELEGRAM_EXPORTS_ROOT / +{phone} / relative_path`.
- Цикл: для каждого сообщения определить роутинг по `media_type` + mime, вызвать соответствующий обработчик, записать в БД.
- Идемпотентность: проверка существующей записи перед обработкой.
- Graceful SIGINT: завершить текущее сообщение, выйти.
- Прогресс-бар (tqdm) и логирование.

### Задача 6 — Расширение `analysis/run.py`

Добавить LEFT JOIN на `communications_telegram_message_media_extraction` в выборке сообщений чата. Изменить формирование текста сообщения для LLM по правилам раздела «8. Изменения в analysis/run.py». Регрессионные тесты: чат без медиа (поведение не изменилось), чат с медиа-описаниями (описания подмешаны в контекст), чат с медиа без описаний (маркер «не извлечено» добавлен).

### Задача 7 — Runbook оператора

Документ `docs/runbook_media_extract.md`:
- Подготовка LM Studio: скачать Qwen3-VL-30B-A3B-Instruct-4bit из LM Studio Hub.
- Перед прогоном: закрыть тяжёлые приложения, проверить свободную память.
- Команда `python3 -m analysis.media_extract --all` (или `--chat-id N` для теста).
- Что делать при OOM: `--use-fallback-model`.
- Как запустить sync БД на PC после завершения media_extract.
- Как валидировать качество описаний (выборочный SELECT, ручной просмотр).

---

## Что проверить вручную

- [ ] ADR-015 реализован: `relative_path`, `media_type`, `mime_type` сохранены в БД для всех медиа-сообщений (после backfill).
- [ ] LM Studio запущен на Mac, Qwen3-VL-30B-A3B-Instruct-4bit скачан, доступен на `localhost:1234/v1`.
- [ ] `app/llm_studio_control.ensure_model_loaded` корректно загружает vision-модель и выгружает text-модель, если та была загружена.
- [ ] Тестовый прогон `python3 -m analysis.media_extract --chat-id <chat_with_photos> --dry-run` показывает корректную выборку медиа без записи в БД.
- [ ] Резолв абсолютного пути работает корректно для обоих аккаунтов (`+77471057849` и `+79161879839`): файл найден, открыт, обработан.
- [ ] Прогон `--chat-id <chat_with_photos>` на 1 чате с фото даёт корректные записи в `communications_telegram_message_media_extraction` с `extraction_method = 'vision_qwen3-vl-30b-a3b'`.
- [ ] Прогон на чате с xlsx-файлом даёт корректную плоскую табличную расшифровку.
- [ ] Прогон на чате с docx-файлом даёт корректные абзацы.
- [ ] Сообщение с PDF-файлом получает placeholder-запись, не падает.
- [ ] Сообщение с непопадающим под обработку типом (например, archive) получает placeholder-запись.
- [ ] Идемпотентность: повторный запуск той же команды без `--regenerate` пропускает уже обработанные сообщения.
- [ ] `--regenerate` — корректно перезатирает существующие записи.
- [ ] Ctrl+C во время прогона — graceful, текущее сообщение завершается, в БД консистентное состояние.
- [ ] `analysis/run.py` корректно подмешивает `extracted_text` в контекст narrative-анализа.
- [ ] sync БД Mac → PC переносит записи `media_extraction`.
- [ ] PC-прогон `analysis.run --all --no-apply` использует описания медиа без обращения к файлам.
- [ ] sync БД PC → Mac переносит результаты narrative-анализа обратно (по ADR-011 Addendum 2); apply_analysis_to_customer на Mac применяет результаты к клиентам.
- [ ] Глазами проверить 10–20 описаний фото товаров — оценить качество.
- [ ] Глазами проверить 5–10 описаний скриншотов маркетплейсов — оценить чтение текста.

---

## Открытые вопросы

- **Q (для архитектурного штаба):** ограничение по доступной памяти на Mac (24 GB фактически меньше) может потребовать перехода на fallback-модель Qwen3-VL-8B. Решение принимается после первого замера: запуск 30B-A3B на 10 чатах, измерение фактического memory footprint и стабильности. Если 30B-A3B не работает стабильно — переход на 8B как primary.
- **Q (для отдельного ADR в будущем):** обработка ссылок на внешние магазины (eBay и подобные) — нужен ли web-fetch и парсинг продуктовых страниц, или достаточно оставить URL в тексте.
- **Q (для отдельного ADR в будущем):** PDF-парсинг — пока почти нет PDF в чатах, placeholder покрывает; при росте доли PDF — переоценить.
- **Q (для архитектурного штаба):** интеграция с Cowork — нужно ли давать оператору в Cowork-интерфейсе возможность просмотреть `extracted_text` рядом с сообщением чата при review? (Сейчас MCP отдаёт оператору только текст; описания медиа можно добавить в выдачу `read_chat_messages`-подобных tools.) Решается отдельно при доработке MCP.
- **Q (для архитектурного штаба):** отсутствие `000_ADR_REGISTRY.md` на диске. Системная инструкция упоминает реестр как ведущийся документ, но он фактически не создан. Не блокирует ADR-014, но требует отдельного решения о восстановлении или снятии требования.
