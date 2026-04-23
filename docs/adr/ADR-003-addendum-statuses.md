# ADR-003 Addendum: Статусы заказов и позиций

**Статус:** Принято  
**Дата:** 2026-04-20  
**Расширяет:** ADR-003 final-ready

## Контекст

ADR-003 определил enum `orders_order_status` с 8 значениями. В процессе реализации MVP выявлено:

1. Не хватает гранулярности логистической цепочки: товар проходит через форвардера в США перед отправкой в Москву, и этот этап нужно отслеживать.
2. Позиции заказа (`orders_order_item`) нуждаются в собственном статусе, отдельном от статуса заказа: товары одного заказа могут ехать отдельно и находиться на разных этапах.
3. Статус заказа должен автоматически определяться по статусам его позиций.

## Решение

### Order-level enum `orders_order_status` (заменяет ADR-003)

```
draft → confirmed → in_procurement → shipped_by_supplier 
→ received_by_forwarder → arrived → delivered
+ cancelled (из любого состояния)
```

| Значение | Описание |
|---|---|
| `draft` | Черновик заказа |
| `confirmed` | Заказ подтверждён клиентом |
| `in_procurement` | Заказан у поставщика |
| `shipped_by_supplier` | Поставщик отправил |
| `received_by_forwarder` | Форвардер (США) получил товар |
| `arrived` | Получен на склад в Москве |
| `delivered` | Передан клиенту |
| `cancelled` | Отменён |

**Убраны:** `in_transit` (заменён `shipped_by_supplier`), `ready_for_pickup` (избыточен, покрывается `arrived`).

### Item-level enum `orders_order_item_status` (новый)

```
pending → ordered → shipped → at_forwarder → arrived → delivered
+ cancelled
```

| Значение | Описание | Matching поставки |
|---|---|---|
| `pending` | Нужно заказать у поставщика | ✅ Матчится |
| `ordered` | Заказан у поставщика | ✅ Матчится |
| `shipped` | Поставщик отправил | ✅ Матчится |
| `at_forwarder` | Получен форвардером в США | ✅ Матчится |
| `arrived` | Получен на склад в Москве | ❌ Уже на месте |
| `delivered` | Передан клиенту | ❌ Закрыто |
| `cancelled` | Отменён | ❌ |

### Автоматический статус заказа (derivation rule)

Статус заказа = **самый ранний статус среди его активных (не cancelled) позиций**. Порядок определяется цепочкой item-level enum.

Порядковые веса для сравнения:

| Item status | Вес |
|---|---|
| `pending` | 0 |
| `ordered` | 1 |
| `shipped` | 2 |
| `at_forwarder` | 3 |
| `arrived` | 4 |
| `delivered` | 5 |

**Правило:** `order.status = map_to_order_status(min(weight(item.status) for item in active_items))`.

**Маппинг item → order:**

| min(item status) | → order status |
|---|---|
| `pending` | `in_procurement` |
| `ordered` | `in_procurement` |
| `shipped` | `shipped_by_supplier` |
| `at_forwarder` | `received_by_forwarder` |
| `arrived` | `arrived` |
| `delivered` | `delivered` |

**Исключения:**
- Если ВСЕ позиции `cancelled` — order = `cancelled`.
- Если заказ в `draft` или `confirmed` — статус задаётся вручную (позиций ещё может не быть или они все `pending`).
- Derivation срабатывает при каждом изменении статуса позиции.

**Реализация:** Python-функция в `app/orders/service.py`, вызывается при обновлении статуса позиции. Не триггер в БД — логика в коде.

### Маппинг seed-данных из Excel

| Статус в Excel (позиции) | → Item status |
|---|---|
| Нужно заказать | `pending` |
| Заказан | `ordered` |
| Заказан у поставщика | `ordered` |
| Получен | `delivered` |
| Передан клиенту | `delivered` |

| Статус в Excel (заказ) | → Поведение |
|---|---|
| Новый | Derive from items |
| Заказан у поставщика | Derive from items |
| В пути | Derive from items |
| Получен | `delivered` |
| Передан клиенту | `delivered` |
| Закрыт | `delivered` |

### Миграция

Alembic-миграция должна:
1. Создать новый enum `orders_order_item_status` с 7 значениями.
2. Заменить enum `orders_order_status`: убрать `in_transit`, `ready_for_pickup`, добавить `shipped_by_supplier`, `received_by_forwarder`.
3. Пересопоставить существующие данные: `in_transit` → `shipped_by_supplier`, `ready_for_pickup` → `arrived`.
4. Обновить статусы заказов по derivation rule.

## Последствия

- Статус заказа всегда актуален — не нужно обновлять вручную.
- Позиции заказа можно отслеживать независимо — один заказ, разные этапы.
- Shipment matching использует item-level статусы (pending/ordered/shipped/at_forwarder = матчится).
- Два enum вместо одного — чуть больше кода, но точнее модель.
