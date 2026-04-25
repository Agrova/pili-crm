# ADR-011 Addendum-1: расхождения с реальной схемой, обнаруженные при Task 2

**Статус:** принят
**Дата:** 2026-04-24
**Связанные ADR:** ADR-011 (базовый), ADR-010 (ingestion), ADR-009 (profile schema)
**Коммиты реализации Task 2:** `597199a..9c632b0`

---

## Контекст

При реализации ADR-011 Task 2 (сервисный слой `app/analysis/`) Claude Code обнаружил два расхождения между ADR-011 и фактической схемой / моделью данных. Оба расхождения закрыты в реализации Task 2, addendum документирует принятые решения.

---

## Расхождение 1: связь чат↔клиент

### Что написано в ADR-011

Разделы 5 и 7 многократно ссылаются на поле `communications_telegram_chat.customer_id` как источник связи чата с клиентом:

> есть связанный клиент, ссылка через `communications_telegram_chat.customer_id` (поле добавляется ADR-010 Задание 2, должно уже существовать в схеме)

### Реальное состояние схемы

Поле `communications_telegram_chat.customer_id` **не существует** (проверено в `app/communications/models.py` и миграциях от ADR-010 и ранее). Связь чат↔клиент реализована через таблицу `communications_link`:

```
chat → communications_telegram_message → communications_link (
    target_module='orders',
    target_entity='orders_customer',
    target_id=<customer_id>
)
```

Это многоходовая связь (three-table join): чат связан с клиентом через сообщения, у каждого из которых может быть один или несколько линков на сущности orders.

### Принятое решение в Task 2

Добавлен helper в `app/communications/service.py`:

```python
async def get_customer_for_chat(session, chat_id: int) -> int | None:
    """Возвращает customer_id, связанный с чатом через communications_link.
    
    - None, если привязка отсутствует (chat ↔ no customer)
    - int, если ровно один клиент
    - raise MultipleCustomersForChatError(chat_id, customer_ids), если >= 2 клиента
    """
```

Сервис `apply_analysis_to_customer` использует этот helper, ловит `MultipleCustomersForChatError` и возвращает `AnalysisApplicationResult` с заполненным полем `ambiguous_customer_ids: list[int] | None`, при этом ничего не применяя к orders/catalog.

### Последствия для следующих задач ADR-011

- **Задача 3** (`analysis/run.py`) — при принятии решения о применении результатов к клиенту зовёт тот же helper через сервис; ничего нового не требуется.
- **Задача 4** (`apply_analysis_to_customer` MCP-tool) — передаёт `ambiguous_customer_ids` обратно в Cowork; оператор вручную выбирает целевого клиента.
- **Задача 5** (расширение `get_unreviewed_chats`) — не затронуто.
- **Задача 6** (`list_draft_orders`, `resolve_pending_item`, `verify_order`, `delete_draft_order`) — работа с `orders_order`, связь с чатом идёт через `analysis_created_entities.source_chat_id`, поле `customer_id` на чате не требуется.

### Альтернатива, отвергнутая при Task 2

Можно было добавить колонку `orders_customer_id` на `communications_telegram_chat` с бэкфилом из `communications_link`. Отвергнуто:

- Задача 2 и так расширяет scope (добавление helper-функций в `app/orders/service.py`). Миграция с бэкфилом 850 чатов — отдельная архитектурная работа.
- Решение «одна связь = один столбец» упрощает модель, но противоречит уже выбранному в ADR-010 паттерну `communications_link` как унифицированного индирекшна «коммуникация ↔ любая сущность».
- При >=2 клиентах на один чат (реальный edge case) отдельное поле `customer_id` всё равно потребовало бы disambiguation-логики — выигрыш минимален.

### Связанный open question

Возможная будущая работа: **если** при эксплуатации `get_customer_for_chat` окажется, что 3-table-join медленный (>50мс) на реальных объёмах — ввести кеширующую колонку `chat.primary_customer_id` с триггером. До этого момента — решение через `communications_link` достаточно.

---

## Расхождение 2: identity updates из `structured_extract.identity`

### Что написано в ADR-011

Раздел 7:

> `structured_extract.identity` — поля, отсутствующие у клиента (phone, email), проставляются с `confidence="suggested"` в соответствующие JSONB-поля профиля

### Проблема

Identity-поля (`phone`, `email`, `city`, `telegram_username`) живут не в JSONB-полях `OrdersCustomerProfile.preferences/incidents/delivery_preferences`, а как **отдельные колонки на базовой таблице `OrdersCustomer`** (ADR-003 core schema + ADR-009 addendum с `telegram_username`).

Формулировка ADR-011 «в соответствующие JSONB-поля профиля» не имеет целевого места применения.

### Решение для Task 2

**Identity updates пропущены в Task 2**, в коде `apply_analysis_to_customer` проставлен `TODO`-комментарий со ссылкой на `06_open_questions.md`. Счётчик `identity_fields_updated` в `AnalysisApplicationResult` не добавлен.

### Почему отложено

Identity updates касаются **колонок на `OrdersCustomer`**, не JSONB. Это требует отдельной политики:

- **Overwrite**: анализатор имеет право переписать существующий `phone`/`email` у клиента, если нашёл другой
- **Only-if-absent**: анализатор заполняет только пустые поля, не трогая существующие
- **Suggest-only**: вообще не трогать `OrdersCustomer`, складывать identity-hints в новое поле `OrdersCustomerProfile.suggested_identity` (потребует миграцию)

Этот выбор — содержательное архитектурное решение, не техническая деталь. Принимать его по ходу реализации Task 2 было некорректно.

### Дальнейшие шаги

Вопрос зафиксирован в `06_open_questions.md` (запись от 2026-04-24 про identity updates). Резолюция — отдельный **Addendum-2 к ADR-011** или полноценный мини-ADR по политике identity-updates. Приоритет — medium (без identity часть ценности анализа теряется, но pipeline работает).

---

## Статус ADR-011 после Task 2

- **Task 1** — закрыта (коммиты `db2618f`, `ccc9513`, `6e29202`)
- **Task 2** — закрыта (коммиты `597199a`, `3a9aa32`, `7ea8a07`, `9c632b0`)
- **Tasks 3-6** — в очереди

Следующий блокер: **Task 3** — `analysis/run.py` (скрипт анализатора с LM Studio). Task 3 может начинаться — все сервисные контракты для записи и применения результатов готовы.

---

## История изменений

- **2026-04-24**: первичное принятие Addendum-1 (эта запись). Связано с завершением реализации ADR-011 Task 2.
