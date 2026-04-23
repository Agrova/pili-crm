# Entities

Полная карта сущностей зафиксирована в ADR-003 (секция «Карта таблиц по модулям»).

30 таблиц, разбитых по 8 модулям с таблицами:

| Модуль | Таблицы |
|---|---|
| `catalog` | catalog_supplier, catalog_product, catalog_product_attribute |
| `orders` | orders_customer, orders_customer_profile, orders_order, orders_order_item |
| `procurement` | procurement_purchase, procurement_purchase_item, procurement_shipment, procurement_tracking_event |
| `warehouse` | warehouse_receipt, warehouse_receipt_item, warehouse_stock_item, warehouse_reservation |
| `pricing` | pricing_exchange_rate, pricing_price_calculation |
| `communications` | communications_email_thread, communications_email_message, communications_telegram_chat, communications_telegram_message, communications_link |
| `finance` | finance_ledger_entry, finance_expense, finance_tax_entry, finance_exchange_operation, finance_exchange_rate |
| `analysis` | analysis_chat_analysis, analysis_chat_analysis_state, analysis_pending_order_item, analysis_created_entities |

Модуль `api` таблиц не имеет.

Статусы заказов и позиций — ADR-003 Addendum.
Расширения схемы (pricing, orders, catalog) — ADR-004.
Модуль `analysis` — ADR-011 (LLM-пайплайн анализа Telegram-переписки).
