# scripts/

Утилиты, не входящие в runtime-код модулей. Не импортируются из `app/`.

## `seed_mvp.py` — начальная загрузка из Excel

Грузит `data/seed/CRM_late.xlsx` + `data/seed/tg_scan_results.json` в Postgres.

### Входные файлы

- `data/seed/CRM_late.xlsx` — основная таблица оператора.
  Ожидаемые листы: `Клиенты`, `Заказы`, `Позиции заказа`, `Настройки`.
  Формат колонок — см. заголовки в Excel.
- `data/seed/tg_scan_results.json` — список объектов Telegram-чатов
  (`category`, `client_name`, `name`, ...). Используется для обогащения
  `telegram_id` у клиентов.

### Порядок загрузки

1. `CREATE EXTENSION IF NOT EXISTS pg_trgm` (нужен для fuzzy-поиска).
2. Очистка производных таблиц (`orders_order_item` → `orders_order` →
   `orders_customer_profile` → `orders_customer` → `catalog_product` →
   `catalog_supplier`) — обеспечивает идемпотентность.
3. `catalog_supplier` (уникальные имена из колонки «Поставщик», отсутствие
   → синтетический `Unknown`).
4. `catalog_product` (уникальные `(supplier, name)` из листа позиций).
5. `orders_customer` (36 строк из листа «Клиенты»). У клиентов без контактов
   проставляется placeholder `telegram_id = '@excel_Cxxx'` (ограничение
   `ck_orders_customer_contact` требует email OR phone OR telegram).
6. `orders_order` (62 строки, маппинг Excel-статусов на `OrdersOrderStatus`).
7. `orders_order_item` (133 строки, маппинг статусов на
   `OrdersOrderItemStatus`).
8. Обогащение `telegram_id` из `tg_scan_results.json`: для клиентов с
   `@excel_*` placeholder ищем fuzzy match по фамилии/имени в
   `client_name` scan-записей.

### Запуск

```bash
PYTHONPATH=. python3 scripts/seed_mvp.py
```

Вывод — отчёт с количествами по каждой сущности.

### Статус-маппинг

| Excel → | БД (`orders_order_status`) |
|---|---|
| Новый | `draft` |
| Заказан у поставщика | `in_procurement` |
| В пути | `in_transit` |
| Получен | `delivered` |
| Передан клиенту | `delivered` |
| Закрыт | `delivered` |

| Excel → | БД (`orders_order_item_status`) |
|---|---|
| Нужно заказать | `pending_order` |
| Заказан | `ordered` |
| Заказан у поставщика | `ordered_at_supplier` |
| Получен | `received` |
| Передан клиенту | `delivered_to_customer` |

Pending для shipment matching = `pending_order` ∪ `ordered` ∪ `ordered_at_supplier`.
