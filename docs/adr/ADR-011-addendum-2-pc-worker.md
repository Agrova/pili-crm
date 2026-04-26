# ADR-011 — Addendum 2: Распределённая обработка через PC-worker

**Дата:** 2026-04-26
**Статус:** accepted
**Связан с:** ADR-011 (Telegram chat analysis pipeline), ADR-011 Addendum 1
**Заменяет:** —

## Контекст

После реализации ADR-011 эмпирически выявлены следующие ограничения боевого прогона анализатора на 850 unreviewed-чатах:

1. **Тепловое ограничение Mac** (MacBook Air, пассивное охлаждение). При непрерывной нагрузке на Qwen3-14B MLX начинается throttling через ~1 час. Реальное «полезное окно» Mac — 30–45 минут чистой генерации в сутки. Боевой прогон 850 чатов на Mac не помещается в разумные сроки.
2. **Эксперимент с PC + qwen3-14b CUDA** (2026-04-26, hotfix #4 включил `/no_think`):
   - Время на чат: ~25 минут (vs Mac ~5 минут).
   - Качество: narrative заметно короче (2504 vs 3981 chars), структура сравнима (1/1/2 vs 1/1/1 для orders/preferences/incidents).
   - Стабильность: 24/7 работа без throttling возможна.
3. **Эксперимент с PC + qwen3-8b CUDA** показал недостаточное качество для боевых прогонов: фрагментирует заказы, дублирует incidents, галлюцинирует delivery method. Решение — не использовать.
4. **Сетевая доступность Mac** ограничена: оффлайн ночью, в течение рабочего дня (оператор в офисе). 24/7 Mac как DB host исключён без VPN, а VPN — overkill для текущей фазы.

Боевой прогон 850 чатов на Mac primary не реалистичен. Нужно решение, переносящее основную нагрузку на PC, при этом сохраняющее Mac как source of truth для боевых данных.

## Решение

Принимается схема **«Mac master + PC worker»** с **pull-репликацией результатов** анализа в одну сторону (PC → Mac).

### Архитектурная диаграмма

```
┌────────────────────────────┐                  ┌────────────────────────────┐
│            MAC             │                  │            PC              │
│   (master, source-of-truth)│                  │  (analyzer worker, 24/7)   │
│                            │                  │                            │
│  pili_crm                  │   pull раз/день  │  pili_crm                  │
│  (полная БД, все таблицы)  │  ◄──────────────│  (subset: только chats     │
│                            │  analysis_chat_  │   + messages + analyses)   │
│                            │  analysis        │                            │
│  Cowork + MCP-сервер       │   @pc записи     │  analysis/run.py           │
│  apply_analysis_to_customer│                  │  --worker-tag pc           │
│  Telegram-выгрузки         │   push snapshot  │  --no-apply                │
│   (фаза 1 addendum)        │  ──────────────►│  LM Studio (qwen3-14b)     │
│                            │  schema + chats  │                            │
└────────────────────────────┘                  └────────────────────────────┘

         │                                                   │
         │  Обе машины:                                      │
         │  git pull origin main                             │
         │  alembic upgrade head                             │
         └────────────────────────────────────────────────────┘
                        Общий git как источник схемы
```

### Ключевые принципы

1. **Mac хранит полную БД.** PC хранит подмножество: только `communications_telegram_*` (входные данные для анализатора) + анализаторские таблицы (`analysis_chat_analysis*`).
2. **PC выполняет только LLM-фазы анализатора** (preflight, chunk_summary, master_summary, narrative, structured_extract, matching). Запись результата в `analysis_chat_analysis` — на PC. Фаза `apply_analysis_to_customer` (создание draft orders, обновление customer profile) на PC **не выполняется** — она будет применена на Mac под контролем оператора через Cowork.
3. **Pull-модель.** Mac инициирует sync в удобное время (вечером, когда дома и PC доступен по локальной сети). Никакого VPN, push'a с PC, или 24/7 онлайна Mac не требуется.
4. **Идентификация worker через `analyzer_version`.** Каждый прогон тегируется машиной: `v1.0+qwen3-14b@mac`, `v1.0+qwen3-14b@pc`. Это:
   - Снимает конфликт UNIQUE `(chat_id, analyzer_version)` при merge.
   - Делает явным в БД, какая машина какой результат сгенерировала.
   - Сохраняет идемпотентность: тот же чат, та же машина → тот же `analyzer_version` → конфликт обнаруживается на уровне БД.
5. **Миграции схемы — из git.** Обе машины применяют миграции независимо через `alembic upgrade head` после `git pull`. Никакой репликации DDL.

### Решения по развилкам (краткое резюме)

- **D1 (формат `analyzer_version`):** суффикс `@<worker_tag>`. Worker tag передаётся через CLI флаг `--worker-tag` или env `ANALYZER_WORKER_TAG`. Default = `mac` (обратная совместимость).
- **D2 (sync filter):** Mac тащит с PC `analysis_chat_analysis` где `analyzer_version LIKE '%@pc'` и `(chat_id, analyzer_version)` отсутствует на Mac (`LEFT JOIN ... WHERE mac.id IS NULL`).
- **D3 (направление sync):** Mac инициирует pull. Технически — `pg_dump` или `psql -h 192.168.1.5` с фильтром, импорт в Mac БД с `ON CONFLICT DO NOTHING`. Никакой автоматики, ручной запуск скрипта оператором.
- **D4 (что реплицируем):** только `analysis_chat_analysis`. Не реплицируем `analysis_chat_analysis_state` (рабочее состояние PC, Mac не нужно), `analysis_created_entities` (на PC всегда пусто из-за `--no-apply`).
- **D5 (Mac → PC, входные данные):**
  - **Фаза 1 (сейчас):** разовый snapshot `communications_telegram_*` с Mac на PC при развёртывании. После каждой новой Telegram-выгрузки оператор вручную пушит дельту (свежие чаты + сообщения) на PC.
  - **Фаза 2 (в будущем, когда удобнее):** Telegram-выгрузка переносится с Mac на PC напрямую. Инжестер запускается на PC, sync в направлении Mac → PC становится не нужен. Этот переход — отдельный инкрементальный шаг, не блокер для развёртывания.
- **D6 (миграции):** обе машины используют один git. После `git pull origin main` каждая машина применяет `alembic upgrade head` у себя локально.

## Технические следствия

### Изменения в коде (отдельная задача через Prompt Factory)

1. **`analysis/run.py` — новый CLI-флаг `--worker-tag`** с типом `str`, default `mac`. При запуске результирующий `analyzer_version` формируется как `f"{ANALYZER_VERSION_BASE}@{worker_tag}"`. Принимает только `[a-z0-9-]+` (защита от случайных значений).
2. **`analysis/run.py` — новый CLI-флаг `--no-apply`**. Когда установлен, после записи `analysis_chat_analysis` пропускается вызов `apply_analysis_to_customer`. Используется на PC.
3. **`app/analysis/__init__.py` — рефакторинг `ANALYZER_VERSION`:**
   - `ANALYZER_VERSION_BASE = "v1.0+qwen3-14b"` — версия конвейера без worker.
   - Полная версия формируется в момент использования из `BASE` + worker_tag из CLI/env.
   - Обратная совместимость: если `--worker-tag` не указан и нет env-переменной, default `mac` обеспечивает идентичность с текущим поведением для существующих записей в БД.

### Sync-скрипт `scripts/sync_pc_analyses.sh`

Запускается на Mac. Тянет с PC новые анализы.

```bash
#!/usr/bin/env bash
# Простая pull-репликация PC → Mac

set -euo pipefail

PC_HOST="${PC_HOST:-192.168.1.5}"
PC_DB="${PC_DB:-pili_crm}"
PC_USER="${PC_USER:-pili}"
MAC_DB="${MAC_DB:-pili_crm}"
MAC_USER="${MAC_USER:-pili}"

# Получить список новых analyses от PC
echo "[sync] fetching analyses with @pc tag from $PC_HOST..."

PGPASSWORD=pili pg_dump \
  -h "$PC_HOST" -U "$PC_USER" -d "$PC_DB" \
  --data-only \
  --table=analysis_chat_analysis \
  --column-inserts \
  > /tmp/pc_analyses.sql

# Импортировать с защитой от дублей
echo "[sync] applying to Mac DB..."
docker exec -i pili-crm-postgres-1 psql -U "$MAC_USER" -d "$MAC_DB" \
  < /tmp/pc_analyses.sql 2>&1 | grep -v "duplicate key" || true

# Подтверждение
echo "[sync] checking diff..."
docker exec pili-crm-postgres-1 psql -U "$MAC_USER" -d "$MAC_DB" -c "
SELECT analyzer_version, COUNT(*) FROM analysis_chat_analysis 
GROUP BY analyzer_version;"
```

(финальная версия скрипта — в отдельной задаче через Prompt Factory; здесь — концепт-уровень)

### Развёртывание PC (отдельный runbook оператора)

1. Установить Docker Desktop на Windows + WSL2.
2. Клонировать `https://github.com/Agrova/pili-crm.git`.
3. Запустить `docker-compose up -d` (та же конфигурация, что на Mac).
4. Применить миграции: `alembic upgrade head`.
5. Импортировать снапшот `communications_telegram_*` с Mac.
6. Установить Python 3.12+, зависимости анализатора.
7. Установить LM Studio, загрузить qwen3-14b GGUF Q4_K_M.
8. Прогнать sanity check: `python3 -m analysis.run --chat-id <test> --endpoint http://localhost:1234/v1 --worker-tag pc --no-apply`.

(детальный runbook — отдельный документ)

## Альтернативы (отвергнутые)

### A1: Distributed pipeline по фазам

Идея: лёгкие фазы (preflight, matching) на Mac, тяжёлые (narrative, extract) на PC.

**Отвергнуто:** требует переархитектуры pipeline, передачу состояния между фазами через БД, новый ADR на 2-3 недели работы. Для 850 чатов overkill.

### A2: PC как DB host

Идея: PostgreSQL переезжает на PC, Mac обращается по сети.

**Отвергнуто:** Mac в офисе оффлайн, требует VPN (Tailscale/WireGuard) для доступа из офиса, ломает паттерн «Mac source of truth». Усложняет архитектуру без выигрыша.

### A3: Облачный PostgreSQL (Supabase/Neon/Railway)

Идея: БД в управляемом облаке.

**Отвергнуто на этапе MVP:** добавляет latency на каждый запрос (50-200 ms), зависимость от интернета, безопасность реальных клиентских данных в облаке требует отдельной проработки. Возможный шаг в будущем при существенном росте объёма данных.

### A4: Multi-master replication

Идея: Mac и PC оба пишут в любую таблицу, конфликты разрешаются.

**Отвергнуто:** конфликты неизбежны (Mac обновляет customer #52 одновременно с PC, пишущим analyses). Решение конфликтов — сложная отдельная задача. Pull-only модель в одну сторону существенно проще.

## Критерии приёмки

- [ ] PC поднят с Docker Desktop + Postgres + miграциями.
- [ ] Snapshot `communications_telegram_*` импортирован на PC (chats=850, messages=82776).
- [ ] CLI флаги `--worker-tag` и `--no-apply` реализованы и протестированы.
- [ ] `ANALYZER_VERSION` в БД для PC-прогонов имеет формат `v1.0+qwen3-14b@pc`.
- [ ] Sanity-прогон chat_id=6017 на PC: запись с `@pc`, время ~25 минут.
- [ ] Sync-скрипт `scripts/sync_pc_analyses.sh` корректно тянет с PC на Mac записи `@pc` без дублей.
- [ ] Mac имеет в БД одновременно `@mac` (свои прогоны) и `@pc` (от PC), оба видны в Cowork для review/apply.

## Open questions (привязка к active)

- **`ANALYZER_VERSION` не отражает фактическую модель** (open question от 2026-04-26): закрывается этим addendum. Решение D1 ставит формат `v1.0+qwen3-14b@<tag>`. Запись переносится в архив после реализации CLI-флага.
- **PC + 14b CUDA для боевых пакетных прогонов:** статус меняется с «непригоден» на «пригоден в режиме worker». Запись (если будет добавлена) учитывает этот контекст.

## Эволюция (фаза 2)

Когда удобно (не блокер для текущей реализации):
- Telegram-выгрузка переносится на PC.
- Инжестер `ingestion/tg_import.py` запускается на PC.
- Sync направления Mac → PC отпадает.
- Mac → PC sync остаётся только для случаев, когда оператор хочет что-то перепрогнать на PC из существующих данных.
