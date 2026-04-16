# Module: orders

**Zone:** Customer order lifecycle.

## Entities
- `Order` — customer order with status and lifecycle
- `OrderItem` — line item within an order
- `Customer` — customer contact info
- `CustomerProfile` — extended customer data

## Dependencies
- Depends on: `catalog`, `pricing`, `warehouse`, `procurement`
- Depended on by: `communications`, `finance`, `api`
