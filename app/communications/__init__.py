"""Модуль communications — письма Gmail и сообщения Telegram.

Хранение (raw и parsed), нормализация, связывание (linking)
с бизнес-сущностями всех модулей: клиентами, заказами,
товарами, поставщиками, закупками, складскими событиями.
Ключевые сущности: EmailThread, EmailMessage, TelegramChat, TelegramMessage.
Внешние коннекторы: Gmail API, Telegram API/export.
"""

__all__: list[str] = []
