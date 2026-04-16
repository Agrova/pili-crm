# Module: procurement

**Zone:** Supplier purchasing and delivery logistics.

## Entities
- `Purchase` — purchase order placed with a supplier
- `Shipment` — physical shipment from supplier to warehouse
- `TrackingEvent` — tracking update for a shipment

## Dependencies
- Depends on: `catalog`
- Depended on by: `orders`, `warehouse`, `communications`, `finance`, `api`
