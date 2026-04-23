# IMPROVEMENTS.md — эргономика MCP-tools

**Назначение:** фиксация замечаний по существующим MCP-tools проекта ПилиСтрогай. Сюда пишутся наблюдения, которые касаются удобства, формата вывода, параметров, обработки ошибок и производительности существующих tools.

**Куда НЕ писать сюда:**
- Пробелы в модели данных (нет поля / статуса / сущности) → `docs/schema-gaps.md`
- Отсутствие самого tool-а для типовой операции → `docs/tool-gaps.md`

См. decision tree в `docs/cowork-system-prompt.md`, раздел 8.

**Путь решения:** промт в Prompt Factory for Claude Code → правка кода в `crm-mcp/`.

---

## Формат записи

```markdown
## YYYY-MM-DD — короткий заголовок

- **Severity:** low | medium | high
- **Источник:** tool-name / сценарий / конкретная операция
- **Проблема:** что именно не работает / неудобно
- **Сценарий:** конкретный пример из практики (1-2 предложения)
- **Предложение:** как можно исправить
- **Статус:** open
- **Связанные решения:** (заполняется позже — ADR-XXX / промт в Prompt Factory)
```

### Severity

- **high** — блокирует работу оператора.
- **medium** — работу не блокирует, но требует обходного пути.
- **low** — косметика, неудобство.

---

## Пример записи (удалить при первой реальной записи)

## 2026-04-22 — `pending_orders` не поддерживает группировку по статусу

- **Severity:** low
- **Источник:** `pending_orders`
- **Проблема:** tool возвращает плоский список заказов без возможности группировки на стороне сервера. При большом количестве заказов Cowork вынужден группировать на лету.
- **Сценарий:** оператор попросил «покажи открытые заказы, сгруппированные по статусу». Cowork сделал группировку вручную, но это работает нестабильно при >20 заказах.
- **Предложение:** добавить параметр `group_by: "status" | "customer" | None`.
- **Статус:** open
- **Связанные решения:** —

---
## Записи

<!-- Новые записи добавлять ниже в хронологическом порядке (сверху — старые, снизу — новые). -->

## 2026-04-22 — `update_order_item_status` не пересчитывает статус заказа при недоступном FastAPI

- **Severity:** high
- **Источник:** `update_order_item_status` / сценарий смены статуса позиции
- **Проблема:** при недоступном FastAPI статус позиции обновляется в БД, но derivation rule (автопересчёт статуса заказа) не срабатывает. Оператор получает предупреждение, но статус заказа остаётся устаревшим — расхождение между позицией и заказом.
- **Сценарий:** позиция Veritas Shooting Board в заказе З-271 переведена в `ordered`, но статус заказа не изменился — FastAPI был недоступен. Требуется ручной вызов `derive-status` после перезапуска сервера.
- **Предложение:** реализовать derive-status непосредственно в слое MCP (или в триггере PostgreSQL), чтобы пересчёт статуса заказа не зависел от доступности FastAPI.
- **Статус:** done
- **Связанные решения:** ADR-006, миграция `4f8fe83398af` (derive_order_status PL/pgSQL + три AFTER-триггера на orders_order_item). HTTP-вызов из MCP убран, derivation атомарна в БД.

## 2026-04-22 — `create_order` не проверяет существование клиента заранее

- **Severity:** medium
- **Источник:** `create_order` / сценарий создания заказа
- **Проблема:** Cowork показывает сводку подтверждения и выполняет `create_order` без предварительной проверки, что клиент с указанным id существует. Ошибка «клиент не найден» прилетает только после подтверждения оператора — лишний шаг.
- **Сценарий:** оператор передал id=999999, Cowork показал сводку, оператор подтвердил — MCP вернул ошибку. Можно было поймать раньше.
- **Предложение:** добавить в `create_order` валидацию клиента на стороне MCP с понятной ошибкой до записи. Также Cowork должен делать `find_customer` по id перед формированием сводки, если id передан явно.
- **Статус:** triaged
- **Связанные решения:** Prompt Factory, Пакет α

## 2026-04-22 — `search_products` не возвращает цену товара

- **Severity:** medium
- **Источник:** `search_products`
- **Проблема:** ответ содержит название, поставщика, вес и остаток на складе, но не содержит цену (`unit_price` / `final_price`). Нельзя быстро ответить клиенту на вопрос «сколько стоит?» без отдельного поиска по заказам.
- **Сценарий:** оператор ищет Veritas Shooting Board через `search_products` — видит 20 шт. на складе, но цену узнать не может.
- **Предложение:** добавить в ответ поля `stock_price_rub` (из `stock_item.price_calculation.final_price`) и массив `listings` с историей цен по листингам. Контракт обогащения зафиксирован в ADR-007, раздел 7.
- **Статус:** in-progress
- **Связанные решения:** ADR-007 + ADR-008 (Пакеты 1–2 реализованы, Пакет 3 в очереди)

## 2026-04-22 — `list_customers` не возвращает дату регистрации и не поддерживает сортировку

- **Severity:** medium
- **Источник:** `list_customers`
- **Проблема:** tool не возвращает поле `created_at` (дату создания клиента) и не поддерживает фильтрацию или сортировку по дате. Невозможно ответить на вопрос «покажи клиентов, зарегистрированных в 2019 году».
- **Сценарий:** оператор запросил клиентов за 2019 год. MCP не вернул дату регистрации ни для одного клиента — запрос не выполним.
- **Предложение:** добавить поле `created_at` в ответ, а также параметры `created_after` / `created_before` для фильтрации по периоду. Также полезна сортировка по дате (`sort_by: "created_at"`, `order: "desc"`).
- **Статус:** triaged
- **Связанные решения:** Prompt Factory, Пакет α

## 2026-04-23 — Added tools: get_unreviewed_chats, link_chat_to_customer (ADR-010 Task 2)

Two new tools implementing Phase 3 of ADR-010:
- `get_unreviewed_chats`: moderation queue for imported Telegram chats
- `link_chat_to_customer`: resolve a chat (link / create / ignore)

Total tools: 11.

Known operational notes (to watch during real use):
- Chat title may be None — tool uses "Telegram user {id}" stub; operator
  should rename via future tool or manual DB edit if needed.
- telegram_id collision on link-to-existing: if the chat's telegram_chat_id
  is already held by a different customer, the tool does NOT overwrite
  — it logs a warning and returns `telegram_id_conflict` in the response
  so Cowork can tell the operator "linked, but telegram_id is already with
  customer A — possible duplicate?".
- telegram_id mismatch on same customer (customer already has a different
  telegram_id): tool preserves existing value, logs warning, no conflict
  field in response.
- get_unreviewed_chats does not paginate beyond `limit` — if >50 chats
  await review, default limit=50 may hide some; explicit higher limit works.
- Reject re-processing: chat with review_status NOT IN (NULL, 'unreviewed')
  raises ValueError. Re-link of an already-linked chat is a separate
  admin operation (future tool).