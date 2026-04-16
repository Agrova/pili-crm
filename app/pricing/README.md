# Module: pricing

**Zone:** Deterministic customer price calculation.

## Entities
- `PriceCalculation` — computed price breakdown for an order item
- `PricingExchangeRate` — exchange rate used for price calculation (may include buffer/markup over bank rate)

## External Connectors
- Exchange rate API (source for PricingExchangeRate)

## Notes
`PricingExchangeRate` is distinct from `BankExchangeRate` (owned by `finance`).
Pricing rate may be set higher than the actual bank rate to buffer against fluctuations.

## Dependencies
- Depends on: `catalog`, `finance`
- Depended on by: `orders`, `api`
