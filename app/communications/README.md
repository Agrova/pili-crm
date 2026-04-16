# Module: communications

**Zone:** Gmail and Telegram ingestion, storage, and linking.

## Entities
- `EmailThread` — Gmail thread
- `EmailMessage` — individual email message (raw + parsed)
- `TelegramChat` — Telegram chat/channel
- `TelegramMessage` — individual Telegram message

## External Connectors
- Gmail API (OAuth 2.0)
- Telegram API / export

## Responsibilities
- Store raw and parsed messages
- Normalize message content
- Link messages to business entities: customers, orders, products, suppliers, purchases, warehouse events

## Dependencies
- Depends on: `orders`, `catalog`, `procurement`, `warehouse`
- Depended on by: `api`
