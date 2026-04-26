# Runbook развёртывания PC как analyzer-worker

## Цель

Подготовить Windows PC (RTX 3060 Ti, 32 GB RAM) к работе как worker-анализатора по ADR-011 Addendum 2:
- Docker Desktop с локальным PostgreSQL 16.
- Subset боевой БД (схема + только telegram-чаты и сообщения).
- Python-окружение для `analysis/run.py`.
- LM Studio с qwen3-14b (уже стоит и работает на PC).
- Sanity-прогон одного чата с `--worker-tag=pc --no-apply`.

После выполнения runbook'а PC готов к боевому пакетному прогону 850 чатов в фоне.

## Предусловия

- На PC: Windows 10/11, RTX 3060 Ti с CUDA, LM Studio с qwen3-14b GGUF Q4_K_M уже работает.
- На Mac: рабочий проект ПилиСтрогай, БД `pili_crm` с 850 чатов / 82776 сообщений, текущий коммит на `origin/main` после ADR-011 Addendum 2 реализации (флаги `--worker-tag` и `--no-apply` доступны).
- Mac и PC в одной локальной сети (`192.168.1.x`).
- На Mac установлен `pg_dump` (поставляется с docker exec, плюс системный psql opt).

## Время выполнения

~2-3 часа суммарно, из которых ~1 час — установка Docker Desktop + WSL2 (если ещё нет).

## Шаги

### Шаг 1 — Установка Docker Desktop на Windows

Если уже стоит — пропустить.

1. Скачать установщик с https://www.docker.com/products/docker-desktop/ — версия для Windows.
2. Запустить установщик. На вопрос «Use WSL 2 instead of Hyper-V» — **да** (рекомендуется).
3. После установки и перезагрузки — открыть Docker Desktop, дождаться зелёного индикатора «Docker is running».
4. Проверка из PowerShell:
   ```powershell
   docker --version
   docker run --rm hello-world
   ```
   Должны быть успехи. Если ошибки про WSL2 — установить https://aka.ms/wsl2kernel и `wsl --update`.

### Шаг 2 — Установка Git, Python 3.12+

Если уже стоят — пропустить.

```powershell
# Git for Windows
winget install --id Git.Git -e

# Python 3.12 (если нет)
winget install --id Python.Python.3.12 -e
```

Проверка:
```powershell
git --version
python --version  # Должно быть Python 3.12.x или новее
```

**Важно:** на PC использовать `python` (не `python3`), это путь Windows. В отличие от Mac, где обязательно `python3`.

### Шаг 3 — Клонирование репозитория

```powershell
# Куда клонируем — на твой выбор. Пример:
cd C:\Users\ageev
git clone https://github.com/Agrova/pili-crm.git
cd pili-crm
```

Проверить, что коммит ADR-011 Addendum 2 реализации присутствует:
```powershell
git log --oneline -5
# Должен видеть коммит "feat(analysis): CLI-флаги --worker-tag и --no-apply..."
```

### Шаг 4 — Запуск PostgreSQL 16 в Docker

В корне репозитория должен быть `docker-compose.yml` (тот же, что используется на Mac).

```powershell
docker-compose up -d
```

Проверить:
```powershell
docker ps
# Должен видеть контейнер pili-crm-postgres-1 на порту 5432:5432
```

### Шаг 5 — Применение миграций

Создать venv для анализатора (отдельно от MCP — на PC он не нужен):
```powershell
python -m venv venv
.\venv\Scripts\activate

# Установить зависимости
pip install -r requirements.txt
# Или если pyproject.toml исправлен (см. open question про build-backend):
# pip install -e .
```

Если `pip install -e .` падает на `setuptools.backends.legacy:build` — установи зависимости напрямую (см. open question от 2026-04-24 про `pyproject.toml`):
```powershell
pip install httpx rapidfuzz pydantic sqlalchemy[asyncio] asyncpg fastapi pydantic-settings alembic python-dotenv
```

Создать `.env` в корне:
```ini
DATABASE_URL=postgresql+asyncpg://pili:pili@localhost:5432/pili_crm
# TEST_DATABASE_URL не нужен — на PC pytest гонять не будем
```

Применить миграции:
```powershell
alembic upgrade head
```

Проверить:
```powershell
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm -c "\dt"
# Должны быть все таблицы: communications_telegram_*, analysis_*, orders_*, catalog_*, и т.д.
```

### Шаг 6 — Импорт данных с Mac

**Вариант A (рекомендуется): pg_dump через сеть.**

На **Mac** делаем дамп таблиц `communications_telegram_*`:
```bash
# На Mac
docker exec pili-crm-postgres-1 pg_dump -U pili pili_crm \
  --data-only \
  --table=communications_telegram_chat \
  --table=communications_telegram_message \
  --table=communications_account \
  > ~/Downloads/telegram_data.sql
```

Скопировать файл `telegram_data.sql` на PC. Способ — на твой выбор: scp, AirDrop, USB, общий диск.

На **PC** импортировать:
```powershell
# Скопировать файл внутрь контейнера или импортировать через docker exec -i
type C:\path\to\telegram_data.sql | docker exec -i pili-crm-postgres-1 psql -U pili -d pili_crm
```

**Вариант B (более автоматизированный, если Mac в сети):**
```powershell
# На PC (Mac должен быть в сети по 192.168.1.x)
# Mac IP смотри по `ipconfig getifaddr en0` на Mac

$MAC_IP = "192.168.1.X"  # подставить реальный
$env:PGPASSWORD = "pili"

pg_dump -h $MAC_IP -U pili -d pili_crm `
  --data-only `
  --table=communications_telegram_chat `
  --table=communications_telegram_message `
  --table=communications_account `
  | docker exec -i pili-crm-postgres-1 psql -U pili -d pili_crm
```

(Вариант B требует, чтобы PostgreSQL на Mac был доступен извне Docker — обычно по `pg_hba.conf`. Если по дефолту не работает — Вариант A надёжнее.)

### Шаг 7 — Проверка импорта

```powershell
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm -c "
SELECT 
  (SELECT COUNT(*) FROM communications_telegram_chat) AS chats,
  (SELECT COUNT(*) FROM communications_telegram_message) AS messages,
  (SELECT COUNT(*) FROM communications_account) AS accounts,
  (SELECT COUNT(*) FROM analysis_chat_analysis) AS analyses;
"
```

**Ожидание:**
- chats: 850
- messages: 82776
- accounts: 2
- analyses: 0 (на PC ещё не было прогонов)

Если значения совпадают с боевой Mac БД (за вычетом `analyses`) — импорт успешен.

### Шаг 8 — Проверка LM Studio

LM Studio уже стоит и работает на PC, qwen3-14b загружен. Просто проверить из консоли:

```powershell
curl http://localhost:1234/v1/models
# Должно вернуть JSON с qwen/qwen3-14b в data
```

Если `localhost:1234` отвечает — LM Studio готов принимать запросы от анализатора.

### Шаг 9 — Sanity-прогон chat_id=6017

Это контрольная точка: проверка, что весь pipeline на PC работает.

```powershell
# Сброс на всякий случай (если есть какие-то остатки)
docker exec -it pili-crm-postgres-1 psql -U pili -d pili_crm -c "
DELETE FROM analysis_chat_analysis WHERE chat_id = 6017;
DELETE FROM analysis_chat_analysis_state WHERE chat_id = 6017;
"

# Прогон с флагами worker-tag=pc и no-apply
.\venv\Scripts\activate
$env:DATABASE_URL = "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm"

# Запуск
python -m analysis.run --chat-id 6017 `
  --endpoint http://localhost:1234/v1 `
  --worker-tag pc `
  --no-apply
```

Ожидание (30 минут):
- В логе LM Studio: `Reasoned for 0.41 seconds` (после hotfix #4) или меньше — reasoning отключён.
- В терминале — несколько строк `INFO httpx: HTTP Request: POST ...`, в конце `done=1 failed=0`.
- В терминале **отсутствует** строка про apply (`apply_analysis_to_customer skipped` или подобное).

Проверка результата:
```powershell
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm -c "
SELECT chat_id, analyzer_version, LENGTH(narrative_markdown) AS chars,
  jsonb_array_length(structured_extract->'orders') AS orders,
  jsonb_array_length(structured_extract->'preferences') AS prefs,
  jsonb_array_length(structured_extract->'incidents') AS incs,
  analyzed_at
FROM analysis_chat_analysis WHERE chat_id = 6017;
"
```

**Ожидание:**
- `analyzer_version` = `v1.0+qwen3-14b@pc` (с суффиксом `@pc`).
- narrative chars > 1500.
- chunks_count = 1.

Если `analyzer_version` без суффикса `@pc` — флаг `--worker-tag` не сработал. Проверить, что коммит ADR-011 Addendum 2 реализации присутствует в `git log` (см. шаг 3).

### Шаг 10 — Готовность к боевому прогону

Если шаги 1-9 прошли — PC готов к боевой эксплуатации.

**Команда боевого прогона** (по 50 чатов за итерацию, например, или сразу все):
```powershell
# Все unreviewed-чаты:
python -m analysis.run --all `
  --review-status unreviewed `
  --endpoint http://localhost:1234/v1 `
  --worker-tag pc `
  --no-apply

# Или конкретный диапазон:
# python -m analysis.run --chat-ids 6017,6018,6019,... `
#   --endpoint http://localhost:1234/v1 `
#   --worker-tag pc `
#   --no-apply
```

Грубая оценка времени: 25 минут × 850 чатов = 354 часа = ~15 суток.

PC работает 24/7, можно оставить в фоне. Следить по `analysis_chat_analysis` count.

## Sync скрипт (на Mac)

Когда вечером Mac в сети с PC — оператор тянет свежие результаты:

```bash
# На Mac, в репо ПилиСтрогай
./scripts/sync_pc_analyses.sh
```

(скрипт реализуется отдельной задачей, сейчас можно сделать вручную через `pg_dump --where ...` с PC)

## Troubleshooting

### LM Studio не отвечает

```powershell
curl http://localhost:1234/v1/models
# Если ошибка — открыть LM Studio UI, проверить что server включен (значок справа сверху)
```

### Docker Postgres не запускается

```powershell
docker ps -a  # покажет все контейнеры, в том числе остановленные
docker logs pili-crm-postgres-1  # логи
docker-compose down && docker-compose up -d  # перезапуск
```

### Анализатор падает с DATABASE_URL ошибкой

Проверить env:
```powershell
echo $env:DATABASE_URL
# Должно быть postgresql+asyncpg://pili:pili@localhost:5432/pili_crm
```

Если пустое — установить через:
```powershell
$env:DATABASE_URL = "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm"
```

Или прописать в `.env` файл в корне проекта.

### Прогон отваливается по сети между Python и LM Studio

Это известная проблема (на Mac тоже наблюдалась — см. hotfix #4 контекст). retry-механизм в `analysis/llm_client.py` обработает большинство случаев. Если каждый запрос отваливается — проверить, что LM Studio не выгрузил модель (Settings → Hardware → Max idle TTL = 60 минут по умолчанию, может надо увеличить).

### `apply_analysis_to_customer` ошибка на PC

Не должна случиться при `--no-apply`. Если случилась — флаг не передан корректно. Проверить, что в команде запуска есть `--no-apply` буквально (не `--no_apply` и не `--noapply`).

## После завершения боевого прогона

Когда `analysis_chat_analysis` на PC содержит ~850 записей с `analyzer_version = 'v1.0+qwen3-14b@pc'`:

1. На Mac запустить sync-скрипт.
2. Через Cowork оператор делает review unreviewed-чатов.
3. Через Cowork tool `link_chat_to_customer` (или эквивалент) вызывает `apply_analysis_to_customer` для linked чатов — на Mac.
4. Драфт-orders создаются в боевой БД.

Это закрывает полный цикл ADR-011 + Addendum 2.

## Эволюция (фаза 2)

Когда удобно (отдельный шаг):
- Установить Telegram Desktop на PC.
- Делать выгрузки чатов прямо на PC.
- Запускать `ingestion/tg_import.py` локально на PC.
- Sync направления Mac → PC становится не нужен.
