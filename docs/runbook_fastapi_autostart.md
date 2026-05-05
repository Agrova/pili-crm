# Runbook: FastAPI autostart (launchd)

**Артефакт G8 — CP11**
**Связано:** `06_open_questions.md` запись 2026-04-22, решение принято 2026-04-27 (вариант 1: launchd + health-check).

---

## Что делает этот launchd job

- При логине пользователя автоматически запускает FastAPI (`uvicorn app.main:app --host 0.0.0.0 --port 8000`)
- Если процесс упал — `KeepAlive=true` перезапускает его автоматически
- Логи пишутся в `~/pili-crm/logs/fastapi-stdout.log` и `fastapi-stderr.log`

FastAPI нужен для:
- Derive-status триггер (ADR-006 — но триггер в PostgreSQL, FastAPI не нужен)
- ADR-005: lifespan-хук ежесуточного Google Sheets mirror
- Будущих API-endpoint'ов

---

## Установка (первый раз)

### 1. Скопировать plist в LaunchAgents

```bash
cp ~/pili-crm/com.pilistrogai.fastapi.plist ~/Library/LaunchAgents/
```

### 2. Найти путь к python3

```bash
which python3
```

Типичный результат: `/usr/bin/python3` или `/usr/local/bin/python3` или `/opt/homebrew/bin/python3`.

Проверить что uvicorn доступен:

```bash
python3 -m uvicorn --version
```

Если uvicorn не найден — установить:
```bash
pip3 install uvicorn fastapi
```

### 2. Обновить путь в plist (если нужно)

Открыть файл `~/Library/LaunchAgents/com.pilistrogai.fastapi.plist` и проверить строку с python3:

```xml
<string>/usr/bin/python3</string>
```

Если `which python3` вернул другой путь — заменить `/usr/bin/python3` в plist на правильный (в **обоих** файлах: репо и `~/Library/LaunchAgents/`).

### 3. Загрузить job

```bash
launchctl load ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist
```

### 5. Проверить что запустился

```bash
curl -s http://localhost:8000/health
# Ожидаемый ответ: {"status":"ok"}
```

Или через alias (если добавлен в `.zshrc`):
```bash
crm-status
```

---

## Ежедневная проверка

```bash
curl -s http://localhost:8000/health
```

Если ответ есть — FastAPI жив. Если нет — смотреть логи (см. раздел «Диагностика»).

---

## Остановка

```bash
launchctl unload ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist
```

После этого FastAPI не будет запускаться при логине и не перезапустится автоматически.

---

## Перезапуск

```bash
launchctl unload ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist
launchctl load ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist
```

Или одной командой (macOS 10.11+):
```bash
launchctl kickstart -k gui/$(id -u)/com.pilistrogai.fastapi
```

---

## Временная остановка (без выгрузки из автозагрузки)

```bash
launchctl stop com.pilistrogai.fastapi
```

> **Внимание:** если `KeepAlive=true`, launchd **перезапустит** процесс через несколько секунд. Для полной остановки используй `unload`.

---

## Диагностика

### Посмотреть логи

```bash
# stdout (нормальный вывод uvicorn)
tail -f ~/pili-crm/logs/fastapi-stdout.log

# stderr (ошибки запуска, трейсбеки)
tail -f ~/pili-crm/logs/fastapi-stderr.log
```

### Проверить статус job'а

```bash
launchctl list | grep pilistrogai
```

Вывод: `<PID>  <exit_code>  com.pilistrogai.fastapi`
- PID > 0 и exit_code = `-` → процесс запущен нормально
- PID = `-` и exit_code = `0` → процесс завершился (KeepAlive его скоро перезапустит)
- PID = `-` и exit_code = `1` или другой → crash loop, смотреть stderr

### Процесс не запускается (crash loop)

1. Открыть `fastapi-stderr.log`:
   ```bash
   cat ~/pili-crm/logs/fastapi-stderr.log
   ```

2. **Типичные причины:**

   | Симптом в логе | Причина | Решение |
   |---|---|---|
   | `ModuleNotFoundError: No module named 'uvicorn'` | Неправильный python3 в plist | Обновить путь python3 (см. «Установка», п.1) |
   | `ModuleNotFoundError: No module named 'app'` | Неправильный WorkingDirectory | Проверить путь в plist: должен быть `/Users/protey/pili-crm` |
   | `could not connect to the server` | PostgreSQL не запущен | `docker-compose up -d postgres` |
   | `Address already in use` | Порт 8000 занят другим процессом | `lsof -i :8000` → найти и убить |
   | `could not translate host name` | DATABASE_URL с неверным хостом | Проверить EnvironmentVariables в plist |

3. После правки plist — перезагрузить:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist
   launchctl load ~/Library/LaunchAgents/com.pilistrogai.fastapi.plist
   ```

### Проверка после reboot

После перезагрузки Mac подождать ~10-15 секунд, затем:
```bash
curl -s http://localhost:8000/health
```

---

## Alias crm-status (опционально)

Добавить в `~/.zshrc`:

```bash
alias crm-status='curl -s http://localhost:8000/health && echo "" || echo "FastAPI DOWN"'
```

Применить:
```bash
source ~/.zshrc
```

Использование:
```bash
crm-status
# Ответ: {"status":"ok"}
# или: FastAPI DOWN
```

---

## Файлы

| Файл | Назначение |
|---|---|
| `~/Library/LaunchAgents/com.pilistrogai.fastapi.plist` | launchd job descriptor |
| `~/pili-crm/logs/fastapi-stdout.log` | stdout uvicorn (нормальный вывод) |
| `~/pili-crm/logs/fastapi-stderr.log` | stderr uvicorn (ошибки) |

---

## Связанные документы

- `pili-crm/app/main.py` — FastAPI приложение, endpoint `/health`
- `pili-crm/docs/adr/ADR-005-mirror-google-sheets.md` — lifespan trigger
- `pili-crm/06_open_questions.md` → архив, запись от 2026-04-22 (закрыта G8)
