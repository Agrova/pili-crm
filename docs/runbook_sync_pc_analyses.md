# Runbook: sync_pc_analyses.sh — PC → Mac синхронизация результатов анализа

## Цель

Перенести результаты анализа Telegram-чатов, сгенерированные на PC-worker, в боевую БД на Mac. Скрипт реализует pull-репликацию по ADR-011 Addendum 2 (G2).

**Синхронизируются ОБЕ таблицы:**
- `analysis_chat_analysis` — полные результаты анализа (narrative, structured_extract, matching)
- `analysis_extracted_identity` — контактные данные, извлечённые для карантина (X1)

Только записи с `analyzer_version LIKE '%@pc'` переносятся с PC на Mac.

## Предусловия

- Mac и PC в одной локальной сети (дома вечером).
- PC включён и PostgreSQL доступен по `192.168.1.5:5432` (или другой IP).
- На Mac установлен `pg_dump` и `psql` (доступны через `docker exec` в контейнере).
- Контейнер `pili-crm-postgres-1` запущен на Mac (`docker ps` покажет).

## Запуск

```bash
# Из корня репозитория на Mac
./scripts/sync_pc_analyses.sh
```

Скрипт завершается за 1–5 минут в зависимости от количества записей.

## Переменные окружения

Можно переопределить без изменения скрипта:

```bash
PC_HOST=192.168.1.5   # IP адрес PC в локальной сети
PC_PORT=5432           # порт PostgreSQL на PC (обычно 5432 внутри Docker)
PC_DB=pili_crm         # имя БД на PC
PC_USER=pili           # пользователь Postgres на PC
PC_PASSWORD=pili       # пароль (одинаков на обеих машинах)
MAC_DB=pili_crm        # имя БД на Mac
MAC_USER=pili          # пользователь Postgres на Mac
MAC_CONTAINER=pili-crm-postgres-1  # имя Docker-контейнера на Mac
```

Пример с кастомным IP:

```bash
PC_HOST=192.168.1.12 ./scripts/sync_pc_analyses.sh
```

## Что делает скрипт (пошагово)

1. **Preflight** — проверяет подключение к PC. Если PC недоступен — выходит с ошибкой.
2. **Считает до** — запрашивает количество `@pc` записей в обеих таблицах на PC и Mac.
3. **Экспорт с PC** — `pg_dump --column-inserts --where="analyzer_version LIKE '%@pc'"` для каждой таблицы.
4. **Patch** — заменяет `INSERT INTO ... VALUES (...)` на `INSERT INTO ... VALUES (...) ON CONFLICT DO NOTHING` для идемпотентности.
5. **Импорт на Mac** — `docker exec -i pili-crm-postgres-1 psql ... < file.sql`.
6. **Считает после** — сравнивает количество до и после.
7. **Summary** — выводит, сколько новых строк добавлено, распределение по `analyzer_version`.

## Ожидаемый вывод (первый прогон)

```
[sync] Starting PC → Mac analysis sync (2026-04-30T22:15:00Z)
[sync] PC: pili@192.168.1.5:5432/pili_crm
[sync] Mac container: pili-crm-postgres-1/pili_crm
[sync] Worker filter: analyzer_version LIKE '%@pc'
[sync] PC reachable ✓

[sync] === analysis_chat_analysis ===
[sync] PC has:  193 analysis_chat_analysis rows with @pc
[sync] Mac has: 0 (before sync)

[sync] === analysis_extracted_identity ===
[sync] PC has:  487 analysis_extracted_identity rows with @pc
[sync] Mac has: 0 (before sync)

[sync] Exporting analysis_chat_analysis from PC...
[sync] Exported 2340 lines for analysis_chat_analysis
[sync] Exporting analysis_extracted_identity from PC...
[sync] Exported 1530 lines for analysis_extracted_identity

[sync] Patching INSERTs → INSERT ... ON CONFLICT DO NOTHING ...
[sync] Importing analysis_chat_analysis into Mac...
[sync] Importing analysis_extracted_identity into Mac...

[sync] ════════ Summary ════════
[sync] analysis_chat_analysis:
[sync]   PC total @pc:     193
[sync]   Mac before:       0
[sync]   Mac after:        193
[sync]   New rows added:   193
[sync] analysis_extracted_identity:
[sync]   PC total @pc:     487
[sync]   Mac before:       0
[sync]   Mac after:        487
[sync]   New rows added:   487
[sync] ✅ Sync complete — 193 analyses + 487 identity rows added
```

## Ожидаемый вывод (повторный прогон — идемпотентность)

```
[sync] analysis_chat_analysis:
[sync]   PC total @pc:     193
[sync]   Mac before:       193
[sync]   Mac after:        193
[sync]   New rows added:   0
[sync] analysis_extracted_identity:
[sync]   PC total @pc:     487
[sync]   Mac before:       487
[sync]   Mac after:        487
[sync]   New rows added:   0
[sync] ℹ️  No new rows (idempotent run — all PC records already on Mac)
```

## Тест идемпотентности

Запустить скрипт дважды подряд и убедиться, что второй прогон добавляет 0 новых строк:

```bash
./scripts/sync_pc_analyses.sh
# Ожидание: "New rows added: N" (N > 0 если первый раз)

./scripts/sync_pc_analyses.sh
# Ожидание: "No new rows (idempotent run)"
```

## Troubleshooting

### PC недоступен (Cannot connect to PC)

```
[sync] ERROR: Cannot connect to PC at 192.168.1.5:5432. Is PC online?
```

**Проверить:**
1. Mac и PC в одной сети? (`ipconfig getifaddr en0` на Mac, `ipconfig` на PC)
2. Docker Desktop на PC запущен? (зелёный индикатор)
3. Порт 5432 открыт? `nc -zv 192.168.1.5 5432` на Mac

Если IP другой — переопределить: `PC_HOST=192.168.1.X ./scripts/sync_pc_analyses.sh`

### pg_dump: command not found на Mac

`pg_dump` должен быть в системе Mac (не в Docker). Проверить:

```bash
which pg_dump
pg_dump --version
```

Если нет — установить через Homebrew:
```bash
brew install libpq
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
```

### Контейнер pili-crm-postgres-1 не найден

```bash
docker ps | grep postgres
# Если не запущен:
cd ~/pili-crm && docker-compose up -d
```

Если имя контейнера другое — переопределить: `MAC_CONTAINER=my-postgres ./scripts/sync_pc_analyses.sh`

### Ошибка при импорте (FOREIGN KEY violation)

Таблица `analysis_extracted_identity` имеет FK на `orders_customer` (через `customer_id`). Если клиент с таким ID отсутствует на Mac (маловероятно, но возможно при рассинхроне) — будет ошибка при вставке.

**Проверить:**
```sql
-- На Mac
SELECT COUNT(*) FROM analysis_extracted_identity WHERE customer_id IS NOT NULL;
-- Сравнить с PC
```

При наличии FK-ошибок — синхронизировать клиентов вручную или уточнить ситуацию.

### Нет новых записей, хотя PC прогнал чаты

Убедиться, что PC запускал анализатор с `--worker-tag pc`:
```powershell
# На PC
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm -c "
SELECT analyzer_version, COUNT(*) FROM analysis_chat_analysis GROUP BY analyzer_version;"
```

Записи с `@pc` — результат правильного запуска. Записи без суффикса — прогон без `--worker-tag`.

## После sync

После успешного sync все PC-результаты видны в Cowork через MCP-tools:
- `get_unreviewed_chats` — покажет чаты с `@pc` анализами
- `apply_identity_update` — для применения identity quarantine из PC
- `link_chat_to_customer` + `apply_analysis_to_customer` — для создания orders из PC-анализов

## Частота sync

По команде оператора, когда Mac дома (PC доступен по локальной сети). Рекомендовано: раз в сутки вечером. Автоматизации не требуется — pull-модель ручная.
