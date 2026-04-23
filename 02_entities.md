# Entities

Полная карта сущностей зафиксирована в ADR-003 (секция «Карта таблиц по модулям»).

26 таблиц, разбитых по 7 модулям с таблицами:

| Модуль | Таблицы |
|---|---|
| `catalog` | catalog_supplier, catalog_product, catalog_product_attribute |
| `orders` | orders_customer, orders_customer_profile, orders_order, orders_order_item |
| `procurement` | procurement_purchase, procurement_purchase_item, procurement_shipment, procurement_tracking_event |
| `warehouse` | warehouse_receipt, warehouse_receipt_item, warehouse_stock_item, warehouse_reservation |
| `pricing` | pricing_exchange_rate, pricing_price_calculation |
| `communications` | communications_email_thread, communications_email_message, communications_telegram_chat, communications_telegram_message, communications_link |
| `finance` | finance_ledger_entry, finance_expense, finance_tax_entry, finance_exchange_operation, finance_exchange_rate |

Модуль `api` таблиц не имеет.

Статусы заказов и позиций — ADR-003 Addendum.
Расширения схемы (pricing, orders, catalog) — ADR-004.
