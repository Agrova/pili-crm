# Module: finance

**Zone:** Financial accounting.

## Entities
- `FinancialLedgerEntry` — double-entry ledger record
- `Expense` — operational expense
- `TaxEntry` — tax calculation record (УСН 6%)
- `ExchangeOperation` — actual currency exchange transaction
- `BankExchangeRate` — actual exchange rate from bank statement

## External Connectors
- Bank statements (manual import or API)

## Notes
`BankExchangeRate` records the rate at which money was actually exchanged.
Distinct from `PricingExchangeRate` (owned by `pricing`).

## Dependencies
- Depends on: `orders`, `procurement`
- Depended on by: `pricing`, `api`
