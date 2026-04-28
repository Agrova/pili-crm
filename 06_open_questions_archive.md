# Open Questions Archive — проект ПилиСтрогай

Этот файл — архив **закрытых** (`closed`) и `wontfix` вопросов. Вынесен из
основного `06_open_questions.md`, чтобы активный файл оставался компактным
и быстро читался при регулярном обращении.

Записи здесь сохраняются в первозданном виде для истории — чтобы можно было
найти решение по любому ранее закрытому вопросу. Поиск по заголовку или
дате через `grep`/Ctrl+F.

Записи добавляются сюда в момент закрытия: статус становится `closed` или
`wontfix`, запись **переносится** (не копируется) из активного файла.

---

## Закрытые вопросы

### [2026-04-27] — Расхождение имён vision-моделей в `app/config.py` vs LM Studio + отсутствие модели в `extractor_version`

- **Статус:** closed
- **Закрыт:** 2026-04-27, коммит `19be350` (`fix(media_extract): align vision model IDs with LM Studio + bump extractor version`).
- **Решение:**
  1. `app/config.py`: `MEDIA_EXTRACT_MODEL_PRIMARY = 'qwen/qwen3-vl-30b'`, `MEDIA_EXTRACT_MODEL_FALLBACK = 'qwen/qwen3-vl-8b'` (вместо HF-имён `mlx-community/Qwen3-VL-...`).
  2. `analysis/media_extract/__init__.py`: `MEDIA_EXTRACTOR_VERSION = 'v1.1+qwen3-vl-8b'` (вместо `'v1.0'` без указания модели). Bump до `v1.1` важен — гарантирует что новые записи никогда не пересекаются с smoke-наследием (которого, как выяснилось, и не было — прошлый прогон упал, ничего не записав).
  3. `analysis/media_extract/service.py`: добавлены новые ID `qwen3-vl-30b` и `qwen3-vl-8b` в `_KNOWN_VISION_METHODS`. Старые HF-имена оставлены для обратной совместимости.
  4. `tests/test_media_extract_service.py`: 4 occurrences легаси-имени model_id обновлены на новый формат.
- **Корневая причина:** `ensure_model_loaded` спрашивал у LM Studio модель по точному ID; HF-имена в LM Studio не существовали; функция уходила в 60-секундный поллинг и падала с «Vision model is not loaded». Smoke на chat 5942 после фикса — 113 обработанных messages за 9:36 без abort.
- **Контекст:** обнаружено 2026-04-27 при подготовке боевого прогона media_extract. Записано как Q-2026-04-27-02 в `06_open_questions.md` коммитом `22033b1`. Решение «вариант A — идти как есть» оказалось недостижимым — проблема стала блокером и закрыта вариантом B (фикс перед прогоном).
- **Связано:** ADR-014 (media extraction pipeline), стандарт versioning из памяти проекта.

### [2026-04-27] — Конфликт preflight-записей и полного анализа ADR-011 при совпадении `analyzer_version`

- **Статус:** closed
- **Закрыт:** 2026-04-27, архитектурное решение принято оператором.
- **Решение:** принят **вариант B** — разные `analyzer_version` для preflight и full analysis. Preflight остаётся `'v1.0+qwen3-14b'` (текущие 850 записей не трогаем). Full analysis при следующем запуске будет использовать другую версию (например, `'analysis-v1.0+qwen3-14b'` или эквивалент). На чат может быть две строки в `analysis_chat_analysis`: одна preflight-only (всегда), вторая полная (только для client/possible_client). Запрос «последнее что знаем про чат X» — через `ORDER BY analyzed_at DESC LIMIT 1` или window function (тот же паттерн уже применён в media_extract фильтре по preflight, см. коммит `bce25f4`).
- **Обоснование выбора:**
  - Консистентность с уже принятым подходом версионирования: preflight = `v1.0+qwen3-14b`, media_extract = `v1.1+qwen3-vl-8b` — каждый этап конвейера имеет свою версию, идемпотентность гранулярная, метрики разделены.
  - Низкий cost реализации: ~5-10 строк изменения константы `ANALYZER_VERSION` в `analysis/run.py`. Без миграций, без ослабления constraint'ов.
  - Сохраняется защитный `ck_analysis_chat_analysis_skipped_consistency` — invariant «если skipped, то narrative пустой» остаётся как есть.
  - Поле `preflight_classification` для запроса «отфильтровать клиентов» берётся из preflight-записи без зависимости от full-analysis-записи.
- **Альтернативы (отвергнутые):**
  - **Вариант A** (общий `analyzer_version`, ADR-011 сохраняет preflight-поля при upsert): требует ослабления `ck_analysis_chat_analysis_skipped_consistency` и сложной upsert-логики «не затирать preflight-поля». Защитный invariant полезен — лучше сохранить.
  - **Вариант C** (preflight как отдельная таблица): миграция 850 записей и переписывание `select_pending_chats` без явного выигрыша на текущей фазе проекта. Если когда-то preflight станет сложнее (несколько проходов, разные критерии) — тогда можно отделить.
- **Реализация — НЕ в этой записи.** Архитектурный вопрос «какой вариант» закрыт. Сама реализация (изменение `ANALYZER_VERSION` в `analysis/run.py`, обновление тестов) — отдельная задача в `06_open_questions.md`, которая будет закрыта при подготовке full analysis run на чатах из preflight.
- **Контекст возникновения:** обнаружено 2026-04-27 после боевого preflight на 850 чатах. Запись была создана коммитом `4e1e72d`.

### [2026-04-22] — Процесс: точки остановки в промтах для Claude Code

- **Статус:** resolved by practice
- **Закрыт:** 2026-04-27, по факту накопленного опыта.
- **Решение:** правило зафиксировано в памяти проекта (#11): «Для задач Claude Code с явными СТОПами запрет auto mode должен повторяться в 4 местах промта: prelude, начало каждой фазы, deliverables каждой фазы, постамбула с отчётом о попытках проскока». На реализациях ADR-011 Task 2/3 и последующих задач плотность подтвердила свою эффективность — ни одного проскока STOP-дисциплины. Этот стандарт применяется к каждому ТЗ для Prompt Factory с STOP-фазами, без необходимости отдельного активного вопроса.
- **Контекст возникновения:** в Пакете 2 ADR-007 Claude Code однажды нарушил «СТОП 2», написал сводку вместо кода и продолжил в auto mode. Запись была создана 2026-04-22 для фиксации процессного риска. После коммитов ADR-011 Task 2 (`90da591`) и Task 3 (`e71d09b`) с применением 4-местной плотности — риск устранён.

### [2026-04-26] — `enable_thinking: false` игнорируется LM Studio CUDA backend

- **Закрыт:** hotfix #4, коммит `0f28cc6`. Workaround: суффикс `\n\n/no_think` к каждому user-message в payload (`analysis/llm_client.py`, функция `_inject_no_think`).
- **Подтверждение работы fix:** прогон chat_id=6017 на PC после hotfix #4 (2026-04-26 17:44+):
  - Лог LM Studio: `Reasoned for 0.41 seconds` (vs 70-90 сек до фикса).
  - `reasoning_tokens: 1` (vs 498 до фикса).
  - `reasoning_content: "\n\n"` (vs полный CoT текст до фикса).
- **Симптомы до фикса:** на PC LM Studio (CUDA llama.cpp backend, qwen3-14b GGUF Q4_K_M) параметр `chat_template_kwargs.enable_thinking: false` уходил в payload, но Qwen3 всё равно выполнял reasoning ~70-90 секунд на запрос. На Mac MLX backend (та же версия LM Studio 0.4.12 build 1, та же модель) тот же параметр работал корректно.
- **Решение:** двойной механизм — `enable_thinking: false` оставлен (на Mac работает) + `/no_think` суффикс добавляется к user-messages (работает на любом backend независимо от поддержки `chat_template_kwargs`). Идемпотентность через `if "/no_think" not in content`.
- **Замечание про эффективность:** ожидавшееся ускорение прогона 1.5-2× не достигнуто (27:19 → 24:46, всего 9% сокращение). Reasoning отключён успешно, но основное время на PC съедает не reasoning, а скорость генерации 5 tok/s на CUDA backend (vs 17-25 tok/s на Mac MLX) из-за частичного offload через PCIe shared memory. Hotfix решил проблему, которую был призван решить, но узкое место оказалось в другом. См. ADR-011 Addendum 2 о решении стратегии PC.
- **Связано:** ADR-011 Addendum 2 (PC как worker — принят 2026-04-26).
- **Тесты:** +4 в `tests/analysis/test_llm_client.py` (test_user_messages_get_no_think_suffix, test_user_messages_already_with_no_think_not_duplicated, test_non_user_messages_not_modified, test_chat_template_kwargs_preserved). Базовая линия после хотфикса — 341 passed (с учётом 5 несвязанных fail'ов).

### [2026-04-26] — `ANALYZER_VERSION` не отражает фактическую модель

- **Закрыт:** ADR-011 Addendum 2 (принят 2026-04-26) + коммит `90da591` с CLI-флагами `--worker-tag` и `--no-apply` в `analysis/run.py`.
- **Решение:** формат `analyzer_version` = `<base>@<worker_tag>`, где `base = v1.0+qwen3-14b`, `worker_tag` передаётся CLI-флагом `--worker-tag`. Default `mac` → суффикс **не добавляется** для обратной совместимости с существующими записями в БД. Другие теги (`pc`, `worker-1` и т.п.) → `v1.0+qwen3-14b@<tag>`. Реализовано через `make_analyzer_version()` в `app/analysis/__init__.py`. Старая константа `ANALYZER_VERSION` оставлена как алиас на `ANALYZER_VERSION_BASE` для обратной совместимости с импортами.
- **Контекст:** обнаружено 2026-04-26 при сравнении PC (8b) vs Mac (14b) на chat_id=6017. Pipeline-логика версионирования (PROMPTS_VERSION = версия промтов; ANALYZER_VERSION = версия конвейера) работает корректно — но семантика «конвейер» неявно подразумевает фиксированную модель и фиксированную машину, что нарушается при распределённой эксплуатации Mac+PC. Также мешало прямому A/B-сравнению моделей на одном чате — требовался ручной DELETE между прогонами.
- **Тесты:** +5 в `tests/analysis/test_run_unit.py` (`test_make_analyzer_version_default_mac_no_suffix`, `test_make_analyzer_version_pc_with_suffix`, `test_make_analyzer_version_invalid_tag_raises`, `test_run_cli_worker_tag_validation`, `test_run_no_apply_skips_apply_call`).
- **Альтернативы (отвергнутые):**
  - (а) Авто-определение модели через `GET /v1/models`: отвергнуто, так как проблема не только в модели, но и в машине-источнике (Mac vs PC).
  - (в) Отдельная колонка `model_used`: отвергнуто из-за необходимости миграции при простом флаге.
- **Связано:** ADR-011 Addendum 2, коммит `90da591`.

### [2026-04-23] — Git identity и remote URL — косметика

- **Суть:** в рабочей копии `/Users/protey/pili-crm` `git config` не содержит явной `user.name` / `user.email`, поэтому коммиты идут с auto-generated identity `Roman Ageev <protey@Romans-MacBook-Air.local>`. Также remote URL настроен на lowercase `agrova`, при push GitHub шлёт информационный redirect на `Agrova` (правильный регистр в реальном имени организации).
- **Последствие:** функционально ничего не ломается (GitHub принимает push, аутентификация проходит), но в истории commits авторство выглядит как `@hostname` вместо нормального email, а каждый push печатает редирект-warning.
- **Решение оператора (2026-04-23):** не чинить. Функциональности не добавляет, на работу не влияет. Warning при push — косметика, не мешает.
- **Статус:** wontfix

### [2026-04-22] — `search_products`: закрыто через ADR-007

- **Статус:** closed
- **Решение:** ADR-007. Запись I-3 закрывается реализацией листингов и истории цен. Контракт обогащения `search_products` описан в ADR-007, раздел 7.

### [2026-04-22] — ADR-007: URL листингов при миграции seed-товаров

- **Статус:** closed
- **Решение:** при миграции URL не заполняется, `catalog_product_listing.url` остаётся NULL для всех перенесённых из seed товаров. Оператор заполнит вручную по необходимости. Закрыто в рамках Пакета 1 реализации ADR-007.

### [2026-04-22] — ADR-007: копирование sku при миграции

- **Статус:** closed
- **Решение:** `catalog_product.sku` копируется в `catalog_listing_price.sku_at_source` primary-листинга при миграции. Сохраняет информацию. Закрыто в рамках Пакета 1 реализации ADR-007.

### [2026-04-22] — Синхронизация открытых вопросов из ADR-009 в `06_open_questions.md`

- **Статус:** closed
- **Решение:** обе записи из ADR-009 добавлены в этот файл при подготовке миграции ADR-009 (см. ниже «Стратегия версионирования JSONB-схем» и «Комбинация telegram_id=NULL + telegram_username=NOT NULL»). Закрыто 2026-04-23 после реализации миграции (коммит `e5c6e55`).

### [исторический] — Итоговая схема PostgreSQL

- **Закрыт:** ADR-003 final-ready + ADR-003 Addendum (статусы и derivation rule).

### [исторический] — Формат хранения писем

- **Закрыт:** ADR-003 — raw_mime BYTEA + parsed_body TEXT + headers JSONB.

### [исторический] — Реализация расчётчика цен

- **Закрыт:** ADR-001 v2 (модуль pricing в монолите) + ADR-004 (pricing policy).

### [исторический] — Стек для API и панели управления

- **Закрыт:** ADR-002 — Python 3.12+ / FastAPI / SQLAlchemy 2.0 async / Alembic / PostgreSQL 16.

### [исторический] — Хранение сырых Telegram-выгрузок

- **Закрыт:** ADR-003 — таблицы `communications_telegram_*`.

### [2026-04-22] — `search_products`: контракт обогащения ценами

- **Закрыт:** ADR-007, минимальный фикс реализован в Пакете 1 (LEFT JOIN через primary-листинг). Полный контракт обогащения (массив листингов, current_price) — в Пакете 3.

### [2026-04-22] — I-1: derive-status при недоступном FastAPI

- **Закрыт:** ADR-006 реализован (миграция `4f8fe83398af`). Derivation rule переведена из FastAPI в PostgreSQL-триггер. Физический тест подтвердил: статус заказа обновляется автоматически даже при выключенном FastAPI. MCP-tool `update_order_item_status` упрощён (нет HTTP-вызова). FastAPI-endpoint `/derive-status` deprecated. 126/126 тестов зелёные.

### [2026-04-23] — ADR-009: миграция Telegram profile schema

- **Закрыт:** миграция `6bb45bb3dcb5` применена (коммит `e5c6e55` в `main`). Добавлено:
  - `orders_customer.telegram_username TEXT NULL`
  - `orders_customer_profile`: три JSONB-поля (`preferences`, `delivery_preferences`, `incidents`)
  - `communications_telegram_chat`: `last_imported_message_id TEXT NULL`, `review_status telegram_chat_review_status NULL`
  - Новый enum `telegram_chat_review_status` (4 значения)
  - Partial index `ix_telegram_chat_unreviewed WHERE review_status = 'unreviewed'`
  - Pydantic-схемы в `app/orders/schemas.py` с alias `_v`/`schema_version` и инвариантом ровно одного `is_primary=True` в непустом списке `delivery_preferences`
- 134/134 теста зелёные (126 существующих + 8 новых). Reversibility проверена.
- Подготовлена почва для ADR-010 (Telegram ingestion pipeline) — следующая задача в очереди.

### [2026-04-23] — Ревизия untracked-файлов в рабочем дереве (КРИТИЧНО)

- **Суть:** в рабочем дереве `/Users/protey/pili-crm` обнаружено 66 файлов, которые физически существуют на диске, но никогда не попадали в коммиты. Включают production-код: весь `crm-mcp/`, `app/catalog/repository.py`, `app/orders/service.py`, `app/api/routes/`, `scripts/`, миграции ADR-005/006/007/008, ADR-документы 005/006/007/009/010, большая часть тестов (`tests/catalog/`, `tests/orders/`, `tests/procurement/`, `tests/warehouse/`, `tests/api/`, `tests/test_seed.py`, `tests/pricing/test_weighted_average.py`). Плюс 16 файлов modified (`app/catalog/models.py`, `app/pricing/service.py`, `tests/conftest.py` и др.).
- **Контекст:** техдолг накопился за предыдущие сессии. Claude Code при завершении каждой задачи коммитил только **свои** новые файлы, но не проверял общее состояние рабочего дерева. В результате `git log` показывает 9 коммитов (последний `e8d8773` — Задание 1 ADR-010), но на диске живёт код от 5–6 невидимых git'у пакетов реализации.
- **Почему критично:** репозиторий нельзя заново склонировать и получить рабочий проект — не будет MCP-сервера, репозиториев, сервисов, множества тестов. Любой `git clean -fd` сотрёт всё это без возможности восстановления. Задание 2 ADR-010 (MCP-tools) потрогает `crm-mcp/`, который untracked — это **блокер** для Задания 2.
- **Решение:** разобрать каждый из 66 untracked и 16 modified по одному из трёх исходов — закоммитить (production-код / ADR-документы / тесты), добавить в `.gitignore` (локальные артефакты, .DS_Store и пр.), удалить (забытые экспериментальные файлы). Оформить отдельной задачей для Prompt Factory перед Заданием 2 ADR-010.
- **Чат:** Prompt Factory for Claude Code (отдельное задание на ревизию untracked перед Заданием 2 ADR-010)
- **Приоритет:** high (блокер для Задания 2 ADR-010)
- **Статус:** open

### [2026-04-23] — ADR-010 addendum: reply_to_telegram_message_id column

- **Закрыт:** миграция `c3d94a7f1e82` применена (коммит `8b2206e` в `main`). Добавлено:
  - `communications_telegram_message.reply_to_telegram_message_id TEXT NULL` — first-class поле для reply-сообщений (без FK, связь опциональная при чтении)
  - Partial composite index `ix_telegram_message_reply_to (chat_id, reply_to_telegram_message_id) WHERE reply_to_telegram_message_id IS NOT NULL`
  - Уточнённая таблица фильтрации сообщений: текст + медиа-метаданные импортируются, медиа-файлы только по подтверждению оператора (ADR-010, фаза 3)
  - Медиа-метаданные в `raw_payload`: `media_type`, `file_name`, `relative_path`, `file_size_bytes`, `mime_type`
- 138/138 тестов зелёные (134 существующих + 4 новых). Reversibility проверена.
- Сопутствующий фикс: `tests/test_adr_009_migration.py::test_migration_upgrade_downgrade` переведён с `downgrade -1` на явную ревизию `4f8fe83398af` — тест стал устойчив к будущим миграциям (коммит `9bd2e3b`, отдельный от миграции). Паттерн «явная revision вместо -1» — применять во всех новых тестах миграций.
- Подготовлена почва для Задания 1 ADR-010 (`ingestion/tg_import.py`) — реализовано.

### [2026-04-23] — ADR-010 Задание 1: исторический импорт Telegram Desktop JSON Export

- **Закрыт:** коммит `e8d8773` в `main`. Реализовано:
  - `ingestion/parser.py` — чистый парсер JSON без БД: `ParsedChat`, `ParsedMessage`, `ParsedMediaMetadata`. Фильтр `personal_chat`, приоритет `media_type` > `photo` > `file`, корректная обработка заглушки «File not included».
  - `ingestion/tg_import.py` — оркестратор: один чат = одна транзакция через `engine.begin()`, watermark через `None` sentinel, `ON CONFLICT DO NOTHING` с явным именем constraint, per-chat error handling (битый чат не валит скрипт). CLI: `--input-dir` (дефолт — последняя `DataExport_*` в `~/pili-crm-data/tg-exports/`), `--dry-run`, `--verbose`.
  - `ingestion/README.md` — пошаговая инструкция оператору: как сделать экспорт, куда положить, как запустить dry-run и реальный импорт, что делать при повторных экспортах, troubleshooting.
  - `tests/test_ingestion_tg_import.py` — 8 unit-тестов парсера + 4 integration-теста импортёра, всего 12 новых тестов. Итого 150/150 зелёных.
- Применено всё, что зафиксировано в ADR-010 addendum (reply-column первого класса, media-метаданные подробно).
- **Важный процессный момент:** Claude Code несколько раз нарушал СТОП 2 и СТОП 3 (пересказ вместо полного текста, плейсхолдер `[...]` в фикстурах). Каждый раз возвращался на исправление по требованию. Запись в `06_open_questions.md` «Процесс: точки остановки в промтах для Claude Code» (от 2026-04-22) по-прежнему актуальна — формулировка «показ = полный текст, не сводка» нарушается систематически, нужно продолжать жёсткий контроль.
- Разведка выявила аномалию состояния рабочего дерева: 66 untracked-файлов + 16 modified, включая production-код (`crm-mcp/`, `app/catalog/repository.py` и т.д.) — см. открытый вопрос «Ревизия untracked-файлов в рабочем дереве» выше. Блокер для Задания 2 ADR-010.
- Следующий шаг: (1) ревизия untracked, (2) Задание 2 ADR-010 (MCP-tools `get_unreviewed_chats` + `link_chat_to_customer`).

### [2026-04-23] — Ревизия untracked-файлов в рабочем дереве

- **Закрыт:** 9 коммитов (`d4c952d..c2ed6b3`), диапазон в `origin/main`: `e8d8773..c2ed6b3`. Рабочее дерево полностью чистое (`git status --short` пусто), цепочка миграций Alembic целостная, `pytest` 150/150 зелёный на контрольной точке (commit 7 — ADR-007/008).
- **Состав 9 коммитов:**
  - `d4c952d` chore: add ADR documents to git (10 файлов)
  - `af070bf` chore: add knowledge base documents (6 файлов)
  - `ffa1f0f` chore: add docs and runbooks (4 файла)
  - `122fbbf` feat: add MCP server (crm-mcp) (17 файлов)
  - `d069f16` chore: add utility scripts (seed_mvp + seed test) (3 файла)
  - `7f1a097` feat: ADR-006 — derive-status trigger + status migrations (6 файлов)
  - `5ad9b94` feat: ADR-007/008 — catalog listings, pricing invariant, API routes (39 файлов — контрольная точка, pytest 150 passed)
  - `96c5180` chore: update housekeeping files (4 M + 1 D)
  - `c2ed6b3` chore: gitignore — add data/seed/ to ignored paths
- **Главный эффект:** `git clone https://github.com/Agrova/pili-crm.git` теперь даёт **рабочий проект**. До ревизии свежий клон был бы broken (`alembic upgrade head` падал бы на отсутствующих родителях миграций, `crm-mcp/` отсутствовал бы целиком).
- **Процессные уроки:**
  1. Nestабильное интернет-соединение оператора вызывало эффект «Claude Code скипает точки остановки» — повторная отправка сообщения приводила к двойному исполнению команд на стороне Cowork. Решение: на критичных финальных шагах (commit → push) использовать минимально короткие обмены (хэш + статус), не требовать длинных `git log --stat` / `git show` через рваный канал.
  2. Формулировка СТОП в промтах работает, но Claude Code периодически возвращается к пересказу вместо буквального вывода команд. Систематически приходится возвращать на исправление. Дальнейшие промты должны явно требовать: «показ = буквальный stdout команды, не пересказ, не вердикт, не количественный подсчёт — только данные».
- Разблокировано: Задание 2 ADR-010 (MCP-tools `get_unreviewed_chats` + `link_chat_to_customer`) — следующий шаг.

### [2026-04-23] — ADR-010 Задание 2: MCP-tools для очереди модерации чатов

- **Закрыт:** коммит `895c263` в `main` (диапазон `c2ed6b3..895c263`). 5 файлов, +1143 / -1 строка. 168/168 тестов зелёные (150 прежних + 7 тестов `get_unreviewed_chats` + 11 тестов `link_chat_to_customer`).
- **Реализовано:**
  - `crm-mcp/tools/get_unreviewed_chats.py` (read-tool): возвращает чаты с `review_status='unreviewed'`, sorted by `last_message_at DESC`, с превью первого и последнего текстовых сообщений (≤100 символов, медиа без текста пропускается). Один SQL-запрос с тремя `LEFT JOIN LATERAL` (stats + preview_first + preview_last), без N+1. Partial index `ix_telegram_chat_unreviewed` используется.
  - `crm-mcp/tools/link_chat_to_customer.py` (write-tool): три взаимоисключающих режима — `customer_id` (привязка к существующему), `create_new=True` (создать нового клиента из данных чата), `ignore=True` (пометить как ignored). Атомарная транзакция: всё в одной `session.commit()`, при `SQLAlchemyError` — `session.rollback()`. Pre-validation выбрасывает `ValueError` **до** любых записей в БД.
  - Логика работы с `telegram_id` при linking: если `customer.telegram_id IS NULL` и `telegram_chat_id` не занят — backfill; если занят другим клиентом — preserve + warning + `telegram_id_conflict` в ответе; если у клиента уже другой `telegram_id` — preserve silently. `link_confidence='manual'` для всех `communications_link`.
  - Fallback name при `create_new` с пустым `title`: `"Telegram user {telegram_chat_id}"`.
  - Защита от re-process: чат с `review_status NOT IN (NULL, 'unreviewed')` → `ValueError`, транзакция не начинается.
  - `crm-mcp/tools/__init__.py` — добавлены 2 новых импорта, tools теперь 11.
  - `crm-mcp/IMPROVEMENTS.md` — добавлена секция `2026-04-23 — Added tools` с operational notes для реального использования.
  - `tests/test_mcp_telegram_review.py` — 18 тестов (7 для get_unreviewed_chats + 11 для link_chat_to_customer). Размещение в `tests/` (не в `crm-mcp/`) — согласованное исключение: `pyproject.toml testpaths=["tests"]` требует тесты в общем test suite, прецедент — `tests/test_ingestion_tg_import.py` тестирует код из `ingestion/`.
- **Процессные заметки:**
  - **Модель Opus 4.7 + High effort** (новая сессия Claude Code) дала существенно лучшее качество соблюдения СТОПов по сравнению с Sonnet 4.6. На СТОП 1 Claude Code сам поднял 3 расхождения с промтом (размещение тестов, `link_confidence`, защита от коллизии telegram_id) — которые на Sonnet мы ловили только в ревью после. Подтверждает правило: сложные задачи с множеством СТОПов → Opus + High.
  - Stream idle timeout случился один раз на СТОП 3 (при генерации длинного ответа), продолжилось без потерь через «продолжай».
  - Git identity и remote URL — технические косметические вопросы, вынесены в `06_open_questions.md` low priority.
- Разблокировано: после заливки реальной Telegram-выгрузки (`python -m ingestion.tg_import`) оператор через Cowork сможет разбирать очередь модерации. Следующий шаг: Задание 3 ADR-010 (Telethon incremental + launchd), либо сначала реальная эксплуатация Заданий 1+2 для обкатки.
