# Инструкция: первый прогон анализатора Telegram-чатов

> **Окружение:** macOS, везде используется `python3` (не `python`).
> Системный `python` на mac — это Python 2.7, его использование запрещено.

## Предусловия (один раз перед первым прогоном)

### 1. LM Studio с Qwen3-14B

```bash
# Если ещё не установлен — скачать LM Studio с https://lmstudio.ai
# В LM Studio:
#  1. Найти и загрузить модель `qwen2.5-14b-instruct` или `qwen3-14b`
#     (поиск в LM Studio → "qwen 14b")
#  2. Загрузить модель (Load) в выбранном квантовании
#     Рекомендация для Mac: Q4_K_M или Q5_K_M (~9-11 GB RAM)
#  3. Запустить локальный сервер: вкладка "Local Server" → Start Server
#     По умолчанию слушает http://localhost:1234

# Проверить, что сервер ответил:
curl http://localhost:1234/v1/models
# Ожидаемый ответ: JSON с массивом models, у одной из них поле id
# вроде "qwen2.5-14b-instruct"
```

### 2. Окружение проекта

```bash
cd /Users/protey/pili-crm

# Если работаешь в существующем venv — ничего делать не нужно
# (rapidfuzz и httpx уже установлены через pyproject.toml после Task 3).
# Если venv пересоздаёшь — учти, что pip install -e . падает
# (см. open_questions: pyproject build-backend, HIGH priority).
# Используй прямой pip3 install зависимостей.

# Проверить, что зависимости подхватываются:
python3 -c "import rapidfuzz, httpx; print(rapidfuzz.__version__, httpx.__version__)"
# Ожидаемый ответ: что-то вроде 3.14.5 0.27.x
```

### 3. PostgreSQL

```bash
# Контейнер должен быть запущен:
docker ps | grep pili-crm-postgres
# Если не запущен:
cd /Users/protey/pili-crm && docker-compose up -d
```

### 4. FastAPI (опционально для прогона анализатора)

```bash
# В отдельном терминале:
cd /Users/protey/pili-crm
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Для самого прогона анализатора FastAPI не критичен (derive-status
# триггер работает на уровне БД, не FastAPI). Но если планируешь
# сразу проверять результат через Cowork — FastAPI нужен.
```

---

## Первый прогон — на одном чате

### Шаг 1: dry-run для предпросмотра

```bash
cd /Users/protey/pili-crm
python3 analysis/run.py --dry-run --all
```

Что увидишь: список чатов с количеством сообщений, статусом review_status,
наличием связанного клиента. Ничего в БД не пишется.

### Шаг 2: выбрать тестовый чат

Из списка выбрать **малый чат с уже привязанным клиентом**
(`review_status='linked'`, ~50–200 сообщений, есть customer_id).
Это даст полный пайплайн без перегрузки на чанкинге.

```bash
# Можно посмотреть в БД:
docker exec -it pili-crm-postgres-1 psql -U postgres -d pili_crm -c "
  SELECT
    tc.id AS chat_id,
    tc.title,
    tc.review_status,
    COUNT(tm.id) AS messages
  FROM communications_telegram_chat tc
  LEFT JOIN communications_telegram_message tm ON tm.chat_id = tc.id
  WHERE tc.review_status = 'linked'
  GROUP BY tc.id, tc.title, tc.review_status
  HAVING COUNT(tm.id) BETWEEN 50 AND 200
  ORDER BY COUNT(tm.id)
  LIMIT 10
"
```

Выбрать один `chat_id` из вывода.

### Шаг 3: прогон на одном чате

```bash
python3 analysis/run.py --chat-id <ID>
```

В консоли будут логи фаз: chunking → narrative → extract → matching → record + apply.
Время на 100-сообщений ≈ 2–5 минут на Mac без GPU (зависит от Qwen-квантования).

### Шаг 4: проверить результат

```bash
# Что записалось в анализе:
docker exec -it pili-crm-postgres-1 psql -U postgres -d pili_crm -c "
  SELECT id, chat_id, analyzer_version,
         narrative_markdown IS NOT NULL AS has_narrative,
         jsonb_array_length(structured_extract->'orders') AS orders_in_extract,
         skipped_reason
  FROM analysis_chat_analysis
  WHERE chat_id = <ID>
"

# Что применилось к клиенту:
docker exec -it pili-crm-postgres-1 psql -U postgres -d pili_crm -c "
  SELECT customer_id,
         jsonb_array_length(preferences) AS prefs_count,
         jsonb_array_length(incidents) AS incidents_count,
         delivery_preferences IS NOT NULL AS has_delivery
  FROM orders_customer_profile
  WHERE customer_id = (
    SELECT customer_id FROM communications_telegram_chat WHERE id = <ID>
  )
"

# Какие draft-заказы созданы:
docker exec -it pili-crm-postgres-1 psql -U postgres -d pili_crm -c "
  SELECT o.id, o.status, COUNT(oi.id) AS items_count
  FROM orders_order o
  LEFT JOIN orders_order_item oi ON oi.order_id = o.id
  WHERE o.id IN (
    SELECT entity_id FROM analysis_created_entities
    WHERE source_chat_id = <ID> AND entity_type = 'orders_order'
  )
  GROUP BY o.id, o.status
"

# И pending позиции (не нашлось в каталоге):
docker exec -it pili-crm-postgres-1 psql -U postgres -d pili_crm -c "
  SELECT id, items_text, matching_status,
         candidates IS NOT NULL AS has_candidates
  FROM analysis_pending_order_item
  WHERE order_id IN (
    SELECT entity_id FROM analysis_created_entities
    WHERE source_chat_id = <ID> AND entity_type = 'orders_order'
  )
"
```

---

## Что считать успехом первого прогона

- [ ] Скрипт завершился без ошибок (return code 0)
- [ ] В `analysis_chat_analysis` появилась запись с непустым `narrative_markdown`
- [ ] `structured_extract` валидный JSON со схемой (Pydantic не упал)
- [ ] У клиента в `orders_customer_profile.preferences` появились записи с `confidence='suggested'`
- [ ] Если в чате обсуждались заказы — есть строки в `orders_order` (status='draft') и `orders_order_item` для confident-позиций
- [ ] Для не-найденных позиций — записи в `analysis_pending_order_item`

## Что делать, если что-то пошло не так

### Скрипт падает на стадии chunking

- Сообщения чата плохо извлекаются → проверить, что `tc.id = <ID>` существует в БД
- `chunk_size` слишком большой → попробовать с меньшим:
  ```bash
  python3 analysis/run.py --chat-id <ID> --chunk-size 200
  ```

### Скрипт падает на LM Studio

- Проверить `curl http://localhost:1234/v1/models`
- Проверить, что модель загружена в LM Studio (UI → Local Server → есть имя модели)
- Таймауты → значит Qwen долго думает; повторить с меньшим chunk:
  ```bash
  python3 analysis/run.py --chat-id <ID> --chunk-size 150
  ```

### Pydantic не валидирует extract

- Скрипт сделает 3 retry, потом mark_failed чата
- Посмотреть на сырой ответ Qwen — возможно, схема промта не пробивается
- Запустить с `--prompt-variant schema` для A/B сравнения:
  ```bash
  python3 analysis/run.py --chat-id <ID> --prompt-variant schema
  ```

### Все позиции not_found

- Каталог не подхватился → проверить SELECT count из `catalog_product`:
  ```bash
  docker exec -it pili-crm-postgres-1 psql -U postgres -d pili_crm -c \
    "SELECT COUNT(*) FROM catalog_product"
  # Ожидаемо: 128
  ```
- Threshold слишком высокий → пока не трогать, накапливать данные для эмпирической настройки

### Прерывание (Ctrl+C) и продолжение

- Один Ctrl+C → graceful shutdown, текущий HTTP-вызов в LM Studio дождётся ответа, состояние сохранится в `analysis_chat_analysis_state`
- Повторный Ctrl+C в течение 5 секунд → force kill (exit 130)
- Возобновить:
  ```bash
  python3 analysis/run.py --resume
  ```

---

## После успешного первого прогона

1. **Прогон на одном крупном чате** — выбрать чат с >1000 сообщений, проверить, что чанкинг и иерархическая саммаризация работают:
   ```bash
   python3 analysis/run.py --chat-id <BIG_ID>
   ```

2. **Прогон с прерыванием** — Ctrl+C в середине, потом:
   ```bash
   python3 analysis/run.py --chat-id <ID> --resume
   ```
   Проверить, что resume работает корректно.

3. **Боевой прогон**:
   ```bash
   python3 analysis/run.py --all
   # или партиями:
   python3 analysis/run.py --review-status unreviewed
   ```
   Учти: на 303 чата с Qwen3-14B на Mac без GPU оценочно 10+ часов. Запускать в режиме «фоном на ночь» или партиями по 30–50 чатов с Ctrl+C-resume.

### Дополнительные команды CLI

```bash
# Проверить текущее состояние state-таблицы (что в работе/прервано):
python3 analysis/run.py --status

# Перезапустить уже обработанный чат с откатом draft-заказов:
python3 analysis/run.py --chat-id <ID> --force

# Только чаты, обработанные после определённой даты:
python3 analysis/run.py --since 2026-04-01

# Несколько конкретных чатов:
python3 analysis/run.py --chat-ids "1,2,3"

# Свой LM Studio endpoint (если поднял на другом порту):
python3 analysis/run.py --chat-id <ID> --model-endpoint http://localhost:5000/v1
```

---

## Когда нужны Tasks 4–6 ADR-011

Скрипт `analysis/run.py` — CLI-инструмент для самого оператора. Tasks 4–6 — это MCP tools, чтобы не лезть в psql каждый раз для оркестрации. Пока:

- **Task 4** (`apply_analysis_to_customer` MCP) — сейчас apply делается автоматически в run.py; tool нужен, если хочешь сначала прогнать анализ через `--no-apply`, посмотреть результаты в Cowork, потом точечно применять. **Полезно для аккуратного rollout первых 50 чатов.**
- **Task 5** (`start_analysis_run` MCP) — запуск анализа из Cowork без терминала. **Удобство, не блокер.**
- **Task 6** (MCP tools для оркестрации drafts) — `list_draft_orders`, `resolve_pending_item`, `verify_order`, `delete_draft_order`. **Понадобится, когда прогон даст 100+ draft-заказов и pending items, разбираться вручную в БД станет неудобно.**

Реалистичный план: первые 5–10 чатов прогнать через CLI, понять качество, потом начать Task 4 (или Task 6, если pending items станут проблемой раньше).
