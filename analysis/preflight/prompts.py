"""Preflight prompt — ADR-013 §3."""
# ruff: noqa: E501

from __future__ import annotations

PROMPTS_VERSION = "1.0"

PREFLIGHT_PROMPT_TEMPLATE = """Ты помощник оператора магазина столярных инструментов ПилиСтрогай. На входе — превью Telegram-чата из его личной переписки. Твоя задача: определить, клиент ли это магазина.

Имя чата: {title}
Всего сообщений: {total_messages}
Исходящих от оператора: {outgoing_count}
Входящих: {incoming_count}
Первое сообщение: {first_message_date}
Последнее сообщение: {last_message_date}

Первые сообщения:
{first_5_messages}

Последние сообщения:
{last_5_messages}

Верни JSON строго в формате:
{{
  "classification": "client" | "possible_client" | "not_client" | "family" | "friend" | "service",
  "confidence": "low" | "medium" | "high",
  "reason": "короткое обоснование"
}}

Признаки клиента: спрашивают цены, обсуждают товары, заказы, доставку, инструменты.
Признаки family/friend: личные темы, бытовое общение, без торговли.
Признаки service: автоматические уведомления Telegram, боты, сервисы доставки.
При сомнении — выбирай possible_client с medium confidence."""
