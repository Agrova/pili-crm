# crm-mcp — MCP-сервер для ПилиСтрогай CRM

MCP (Model Context Protocol) stdio-сервер, предоставляющий AnythingLLM
доступ к данным CRM: сопоставление поставки с заказами, просмотр активных
заказов, клиентов, каталога и обновление статусов позиций.

## Архитектура

- **Транспорт:** stdio (AnythingLLM запускает сервер как дочерний процесс).
- **БД:** прямое чтение из PostgreSQL через SQLAlchemy 2.0 async + asyncpg.
  ORM-модели основного приложения не дублируются — используется
  `text()` с сырыми SQL.
- **Логи:** только stderr. stdout зарезервирован под протокол MCP —
  любой `print()` ломает коммуникацию.
- **Зависимость от основного приложения:** только схема БД (ADR-003).

## Инструменты

| Name | Что делает |
|------|------------|
| `match_shipment` | Сопоставляет список товаров из поставки с позициями активных заказов (все кроме `delivered` / `cancelled`). Приоритет — более ранний заказ. Возвращает `matched` / `ambiguous` / `unmatched`. |
| `pending_orders` | Активные заказы (`confirmed` / `in_procurement` / `shipped_by_supplier` / `received_by_forwarder`) с товарами и контактами. Опц. фильтр `customer_name`. |
| `list_customers` | Клиенты + число активных заказов. Опц. фильтр `search`. |
| `search_products` | Поиск в каталоге по ILIKE с поставщиком, весом и остатком склада. |
| `add_to_stock` | Добавляет товар на склад как свободный остаток. Используется для позиций из поставки, которые `match_shipment` вернул в `unmatched`. Повторный вызов **увеличивает** `quantity`, не дублирует запись (UPSERT по `(product_id, location)`). Параметры: `product_name` (обяз.), `quantity` (def. `1.0`), `location` (def. `"склад"`). При нескольких совпадениях по ILIKE возвращает список кандидатов и просит уточнить. |
| `update_order_item_status` | Обновляет статус позиции заказа и автоматически пересчитывает статус заказа (через `POST /api/v1/orders/{id}/derive-status`). Принимает `product_name` и `new_status` — на русском или английском. При нескольких совпадениях возвращает список кандидатов. |
| `find_customer` | Нечёткий поиск клиентов по имени, Telegram-хендлу, телефону или email. Возвращает список кандидатов с оценкой уверенности, телеграм-ссылкой, числом открытых заказов и суммой долга. |
| `create_customer` | Создаёт нового клиента. Параметры: `name` (обяз.), `telegram_id`, `phone`, `email`. Если ни один контакт не указан — генерируется placeholder `@auto_...`. Требует подтверждения оператора перед записью (правило двух подтверждений). |
| `create_order` | Создаёт заказ со списком позиций и опциональной суммой оплаты. Параметры: `customer_id`, `items` (массив `{product_name, price, quantity}`), `paid_amount`. Продукты создаются автоматически если их нет в каталоге. Требует подтверждения оператора перед записью (правило двух подтверждений). |

## Установка и запуск

```bash
cd crm-mcp
cp .env.example .env
# отредактировать DATABASE_URL если нужно
pip install -e .

# прямой запуск (для отладки, ждёт MCP-ввода на stdin)
python3 server.py

# прямая проверка инструментов минуя MCP-протокол
python3 test_tools.py
```

`server.py` не печатает ничего в stdout до первого MCP-сообщения — это
обязательное свойство для stdio-транспорта.

## Подключение к AnythingLLM

1. Открыть **Settings → Agent Skills → MCP Servers**.
2. Нажать иконку настроек (ключ) рядом с MCP Servers.
3. Добавить запись в конфигурацию:

```json
{
  "crm-pilistrogai": {
    "command": "python3",
    "args": ["/Users/protey/pili-crm/crm-mcp/server.py"],
    "env": {
      "DATABASE_URL": "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm"
    }
  }
}
```

Путь в `args` должен быть абсолютным. `env` задаёт `DATABASE_URL` для
конкретной сессии и переопределяет значение из `.env`.

4. Нажать **Refresh** в AnythingLLM.
5. Проверить: написать `@agent покажи активные заказы` — агент должен
   вызвать `pending_orders` и ответить списком.

## Обработка ошибок

Если Postgres недоступен, сервер не падает: инструмент возвращает
TextContent с сообщением «Ошибка при обращении к БД (...). Проверьте, что
Postgres запущен и DATABASE_URL корректен.» и пишет трассировку в stderr.

## Статусы (ADR-003 Addendum)

**Order-level** `orders_order_status`:
`draft → confirmed → in_procurement → shipped_by_supplier → received_by_forwarder → arrived → delivered` + `cancelled`.

**Item-level** `orders_order_item_status`:
`pending → ordered → shipped → at_forwarder → arrived → delivered` + `cancelled`.

Статус заказа вычисляется автоматически через derivation rule:
`order.status = map(min_weight(active_item_statuses))`.

- **pending_orders** фильтрует: `in_procurement`, `shipped_by_supplier`, `received_by_forwarder`.
- **match_shipment** и **update_order_item_status** ищут среди активных позиций:
  `NOT IN (delivered, cancelled)`.
- **update_order_item_status** после обновления item вызывает
  `POST http://localhost:8000/api/v1/orders/{id}/derive-status` для
  пересчёта статуса заказа.

### Маппинг русских статусов → enum

| Фраза оператора | → enum |
|-----------------|--------|
| «заказан», «заказан у поставщика» | `ordered` |
| «отправлен», «отправлен поставщиком» | `shipped` |
| «получен форвардером», «у форвардера», «склад США» | `at_forwarder` |
| «получен», «получен на склад», «пришёл» | `arrived` |
| «передан клиенту», «выдан», «забрал» | `delivered` |
| «отменён», «отмена» | `cancelled` |

## Связка match_shipment → add_to_stock

Товары из поставки, которые не ожидает ни один клиент, попадают в
`unmatched` ответа `match_shipment`. Каждая запись unmatched содержит
поле `suggested_action` с готовым вызовом `add_to_stock`:

```json
{
  "input_item": "Higo No Kami",
  "reason": "Не найден в ожидающих заказах",
  "suggested_action": {
    "action": "add_to_stock",
    "product_name": "Higo No Kami"
  }
}
```

В текстовом блоке под unmatched-позицией подсказка видна оператору:

```
❌ Без заказа (1):
  • «Higo No Kami» — Не найден в ожидающих заказах
    → чтобы добавить на склад: add_to_stock('Higo No Kami')
```

LLM может вызвать `add_to_stock` автоматически или после подтверждения
оператором.

## Примеры вызовов update_order_item_status

```
# Оператор: «Veritas Shooting Board передан клиенту»
update_order_item_status(product_name="Veritas Shooting Board", new_status="delivered")

# Оператор: «стамеска Pfeil получена на склад»
update_order_item_status(product_name="стамеска Pfeil", new_status="получен на склад")

# Если несколько совпадений — уточнить заказ:
update_order_item_status(product_name="стамеска", new_status="arrived", order_id=173)

# Русский статус с именем клиента:
update_order_item_status(product_name="Shapton", new_status="передан клиенту",
                         customer_name="Хаустов")
```

Ответ содержит два статуса: `old_item_status → new_item_status` и
`old_order_status → new_order_status` (пересчитанный автоматически).

## Правило двух подтверждений

Инструменты `create_customer` и `create_order` **необратимо изменяют данные**.
Перед их вызовом агент обязан:

1. Показать оператору итоговые параметры запроса (имя, товары, цены, оплата).
2. Получить явное подтверждение («да», «ок», «создай», «подтверждаю»).
3. Только после подтверждения вызвать инструмент.

Если оператор написал одним сообщением «создай клиента и заказ» — это **не считается
подтверждением**. Агент должен вывести сводку и подождать.

## Примеры вызовов find_customer / create_customer / create_order

```
# Поиск клиента по имени
find_customer(query="Воропаев")
# → список: id, name, telegram_link, pending_orders_count, total_debt, confidence

# Поиск по Telegram-хендлу
find_customer(query="@alexvab")

# Создание нового клиента (после подтверждения)
create_customer(name="Новый Клиент", telegram_id="@new_client", phone="+79161234567")

# Создание заказа (после подтверждения)
create_order(
    customer_id=42,
    items=[
        {"product_name": "Veritas Jack Plane", "price": 28500, "quantity": 1},
        {"product_name": "Лезвие PMV-11", "price": 4200, "quantity": 1}
    ],
    paid_amount=10000
)
# → order_id, order_display (З-123), total, paid, debt, status, items
```

## Добавка в системный промт AnythingLLM

Скопируй этот текст в **Settings → AI Providers → System Prompt** в AnythingLLM:

---

```
У тебя есть доступ к MCP-серверу CRM ПилиСтрогай. Используй инструменты для управления заказами.

Когда оператор называет товар и действие — вызывай соответствующий инструмент:

• Пришла поставка, нужно сопоставить с заказами → match_shipment
• Товар не ожидает ни один клиент → add_to_stock
• Статус товара изменился («передан клиенту», «получен на склад», «у форвардера» и т.д.) → update_order_item_status
• Показать активные заказы → pending_orders
• Найти клиента → find_customer (или list_customers для полного списка)
• Найти товар в каталоге → search_products
• Добавить нового клиента → create_customer (с подтверждением)
• Создать заказ → create_order (с подтверждением)

Для update_order_item_status:
- Принимаешь название товара и новый статус (русский или английский)
- Если нашлось несколько позиций — показываешь список и просишь уточнить заказ или клиента
- После успешного обновления сообщаешь старый и новый статус позиции, а также пересчитанный статус заказа

Статусы позиции (item): pending, ordered, shipped, at_forwarder, arrived, delivered, cancelled
Русские варианты: «заказан», «отправлен», «у форвардера», «получен», «передан клиенту», «отменён»

Статусы заказа (order) вычисляются автоматически из статусов позиций — не нужно менять их вручную.

ВАЖНО — правило двух подтверждений:
Перед вызовом create_customer или create_order ты ОБЯЗАН:
1. Показать оператору итоговые данные (имя клиента, товары, цены, сумма оплаты).
2. Явно попросить подтверждение.
3. Только после явного «да», «ок», «создай», «подтверждаю» — вызвать инструмент.
Одно сообщение с запросом «создай клиента и заказ» НЕ является подтверждением.
```
