# ADR-001 v2: Модульный монолит

**Статус:** Принято  
**Дата:** 2026-04-15

## Контекст

ПилиСтрогай — магазин инструментов, один оператор. Нужна CRM для управления заказами,
закупками, складом, ценообразованием, коммуникациями и финансами.

Интеллектуальным интерфейсом служит Claude (Cowork на десктопе, в будущем — Telegram-бот для мобильного доступа), связь с системой — через MCP-сервер. Источником истины — реляционная БД PostgreSQL.

## Решение

Модульный монолит: 8 функциональных модулей + shared в одном Python-процессе.

## Модули

```
catalog/          — справочник товаров и поставщиков
orders/           — заказы клиентов
procurement/      — закупки и логистика
warehouse/        — склад
pricing/          — расчёт цены клиенту
communications/   — Gmail + Telegram
finance/          — финансовый учёт
api/              — единый HTTP API: эндпоинты для MCP-сервера, внешних интеграций и для будущей панели управления
shared/           — общие типы и утилиты
```

## Граф зависимостей

```
orders       → catalog, pricing, warehouse, procurement
procurement  → catalog
warehouse    → procurement
pricing      → catalog, finance
communications → orders, catalog, procurement, warehouse
finance      → orders, procurement
api          → все модули
shared       ← все модули (исходящих зависимостей нет)
```

## Критерии выбора

- **Совместимость с Claude / MCP:** Claude обращается к системе через MCP-сервер (stdio transport). Модульный монолит предоставляет MCP-tools, маппящиеся на публичные интерфейсы модулей — Claude работает с набором tools, без необходимости знать о внутренней структуре.

## Правила

- Публичный интерфейс только через `__init__.py`
- Никаких прямых SQL-запросов между модулями
- Один оператор, авторизации пока нет

## Курсы валют — строго два типа

| Тип | Владелец | Назначение | Источник |
|-----|----------|------------|----------|
| `BankExchangeRate` | `finance` | Фактический курс обмена (реальные деньги) | Банковская выписка |
| `PricingExchangeRate` | `pricing` | Расчётный курс для цены клиенту (может включать наценку) | API курсов + правило наценки |

## Ключевые сущности по модулям

### catalog
- Product, Supplier, ProductAttribute

### orders
- Order, OrderItem, Customer, CustomerProfile

### procurement
- Purchase, Shipment, TrackingEvent

### warehouse
- WarehouseReceipt, StockItem, Reservation

### pricing
- PriceCalculation, PricingExchangeRate

### communications
- EmailThread, EmailMessage, TelegramChat, TelegramMessage

### finance
- FinancialLedgerEntry, Expense, TaxEntry, ExchangeOperation, BankExchangeRate

## Внешние коннекторы

| Модуль | Коннектор |
|--------|-----------|
| catalog | Парсинг сайтов поставщиков |
| communications | Gmail API, Telegram API/export |
| pricing | API курсов валют |
| finance | Банковские выписки |
