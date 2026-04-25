"""ADR-011 Task 3: Qwen3-14B prompts for the analysis pipeline.

All prompts are version-tagged **v1.0** (matches ``ANALYZER_VERSION``
``v1.0+qwen3-14b`` in ``app.analysis.__init__``). Any wording change
here must be accompanied by a bump of ``ANALYZER_VERSION`` so existing
``analysis_chat_analysis`` rows are preserved as history.

Prompts are in Russian — Qwen3-14B handles Russian well, the chat
corpus is Russian, and the operator is Russian-speaking.

Substitution: callers use :func:`render` (plain ``str.replace``) — never
``str.format`` — because prompt bodies contain literal ``{``/``}`` from
JSON examples and the example schemas. Avoiding ``.format`` means
authors don't need to escape braces.

Five prompts exported as module-level constants:

- ``CHUNK_SUMMARY_PROMPT`` — summarise a single chunk of messages.
- ``MASTER_SUMMARY_PROMPT`` — merge chunk summaries into one.
- ``NARRATIVE_PROMPT``      — pass 1, free-form markdown portrait.
- ``STRUCTURED_EXTRACT_PROMPT`` — pass 2, strict JSON by example.
- ``MATCHING_PROMPT``       — catalog matching verdict per order item.

Two variants of the pass-2 prompt are provided:

- ``STRUCTURED_EXTRACT_PROMPT`` — exported default, uses a static
  hand-crafted example (per operator Phase-1 feedback: Qwen3-14B
  handles this better than raw Pydantic JSON Schema).
- ``STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA`` — alternative that embeds
  ``StructuredExtract.model_json_schema()``. Kept for A/B on the first
  real-chat run.
"""

from __future__ import annotations

import json
from typing import Any

from app.analysis.schemas import StructuredExtract

PROMPTS_VERSION = "v1.0"


def render(template: str, **values: Any) -> str:
    """Substitute ``{key}`` placeholders with ``str.replace`` (no escaping).

    ``str.format`` is unsuitable here because prompt bodies contain raw
    ``{``/``}`` from JSON examples; ``str.replace`` is.
    """
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


# ── Message formatting convention (chunking.format_messages_for_prompt) ─────
#
# Messages are fed to CHUNK_SUMMARY_PROMPT formatted as:
#
#   [YYYY-MM-DD HH:MM | id=12345 | from_user] text of the message
#
# The ``id=N`` token is essential — downstream prompts rely on it to
# trace facts back to source messages via ``source_message_ids``.


CHUNK_SUMMARY_PROMPT = """\
Ты помощник владельца магазина ручного столярного инструмента. На вход —
фрагмент переписки в Telegram с клиентом (или группой). Сделай краткое
саммари того, что в этом фрагменте происходит, на русском языке.

Обязательно сохрани:
- telegram_message_id каждого сообщения, в котором упомянут факт. Формат:
  `[id=12345]` рядом с фактом. Эти id нужны для последующей обратной
  привязки к источнику.
- Упомянутые товары (модели, артикулы, бренды) и контекст упоминания
  (интересуется / заказал / получил / вернул / жалуется).
- Обсуждаемые заказы: что, сколько, цена, валюта, упоминание оплаты
  или доставки.
- Факты о клиенте: имя, город, телефон, способ доставки, предпочтения.
- Инциденты: брак, возврат, претензия, договорённость о скидке.
- Сроки и даты, если упомянуты явно.

Пиши в телеграфном стиле, короткими пунктами. Не додумывай: если в
фрагменте чего-то нет — не упоминай. Не делай выводов о том, что было
до или после фрагмента. Если фрагмент пустой или бессмысленный — выведи
строку "пустой фрагмент".

Фрагмент:
{chunk_messages}

Саммари:
"""


MASTER_SUMMARY_PROMPT = """\
Тебе дан список кратких саммари по фрагментам одной переписки с клиентом,
в порядке от самого раннего к самому позднему. Собери из них единое
саммари всей переписки на русском.

Задача — сохранить хронологию и объединить повторяющуюся информацию:
- Обязательно сохраняй telegram_message_id из саммари фрагментов
  в формате `[id=...]` — они нужны для финального JSON.
- Если в нескольких фрагментах упоминается один заказ — собери его
  детали в одном месте.
- Если факт о клиенте (телефон, город, предпочтение) повторяется —
  укажи один раз, опираясь на самое позднее уточнение.
- Сохрани все упомянутые товары, даты, суммы.
- Отметь явные противоречия между фрагментами (например, разные
  номера телефонов), если они есть.

Пиши короткими пунктами, разделами «Клиент», «Заказы», «Доставка»,
«Инциденты», «Прочее». Не пересказывай фрагмент целиком — суммируй.

Саммари фрагментов:
{chunk_summaries}

Мастер-саммари:
"""


NARRATIVE_PROMPT = """\
Тебе дан материал по одной переписке с клиентом магазина ручного
столярного инструмента: либо вся переписка целиком, либо её сводное
саммари. Напиши подробный портрет клиента на русском языке в формате
markdown, с разделами ниже. Это narrative-описание, а не JSON — пиши
живым языком, полными предложениями.

# Клиент
Кто этот клиент: имя, упоминаемый никнейм, телефон, контактные данные.
Как долго клиент в переписке, как с вами связан.

# История взаимодействия
Хронологический рассказ: как начался диалог, какие были ключевые этапы,
чем на данный момент закончился.

# Предпочтения
Что клиент любит, чем интересовался без покупки, каких брендов
придерживается, какие пожелания высказывал.

# Инциденты
Жалобы, возвраты, претензии, проблемные доставки, договорённости о
компенсации. Если таких не было — так и напиши.

# Доставка
Способ доставки (СДЭК / Почта / курьер / самовывоз), город получения,
адрес (если упомянут), предпочтения по времени.

# Заказы
Перечислить заказы в хронологическом порядке: что заказал, количество,
цена, валюта, статус доставки, статус оплаты, даты. Если заказ был
оформлен на словах без уточнения позиций — так и отметь.

Не пытайся додумать статус оплаты или доставки, если в материале об
этом прямо не сказано. В таких случаях указывай «статус неизвестен».

Важно:
- Ничего не додумывай. Если данных нет — напиши «нет данных».
- Не путай клиента и продавца. Продавец — владелец магазина,
  клиент — другая сторона.
- Используй только те факты, что есть в материале.
- Везде, где упоминаешь конкретный факт, заказ, инцидент или
  предпочтение — рядом в квадратных скобках указывай
  telegram_message_id сообщения-источника в формате `[id=12345]` или
  `[id=12345, 67890]` если фактов несколько. Это критично для
  последующей машинной обработки.

Материал:
{chat_history}

Портрет клиента:
"""


# ── Static example for STRUCTURED_EXTRACT_PROMPT (default variant) ──────────
#
# Per operator Phase-1 feedback: a hand-crafted example is easier for
# Qwen3-14B than raw Pydantic JSON Schema. The example mirrors ADR-011
# §3 with a mix of populated fields, ``null`` scalars and ``[]`` arrays
# so Qwen sees all valid shapes.

STRUCTURED_EXTRACT_EXAMPLE: dict[str, Any] = {
    "_v": 1,
    "identity": {
        "name_guess": "Иван Петров",
        "telegram_username": "ivan_wood",
        "phone": "+79161234567",
        "email": None,
        "city": "Москва",
        "confidence_notes": "Имя в первом сообщении, телефон при оформлении отправки",
    },
    "preferences": [
        {
            "product_hint": "Veritas зензубель",
            "note": "Интересовался несколько раз, не купил",
            "source_message_ids": ["123", "456"],
        }
    ],
    "delivery_preferences": {
        "method": "СДЭК",
        "preferred_time": "вечер",
        "notes": None,
    },
    "incidents": [],
    "orders": [
        {
            "description": "Заказ от 15 марта",
            "items": [
                {
                    "items_text": "Рубанок Veritas #5 сталь O1",
                    "quantity": 1,
                    "unit_price": 45000,
                    "currency": "RUB",
                    "source_message_ids": ["789"],
                }
            ],
            "status_delivery": "delivered",
            "status_payment": "paid",
            "date_guess": "2025-03-15",
            "source_message_ids": ["789", "790"],
        }
    ],
    "payments": [],
}


_STRUCTURED_EXTRACT_EXAMPLE_JSON = json.dumps(
    STRUCTURED_EXTRACT_EXAMPLE, ensure_ascii=False, indent=2
)


STRUCTURED_EXTRACT_PROMPT = """\
Тебе дан портрет клиента в формате markdown. Извлеки из него
структурированные данные в виде JSON, строго соответствующего формату
ниже.

Правила:
- Выводи **только JSON**, без markdown-обёртки, без префиксов, без
  пояснений. Первый символ ответа — `{`, последний — `}`.
- Поле `_v` всегда равно `1`.
- Все поля nullable. Если данных нет — ставь значение `null` для
  скаляров и `[]` для массивов. Единая семантика: «нет данных =
  null/пусто», без различения «не искал» и «искал и не нашёл».
- Денежные суммы — числа (int или float), без валютного знака внутри.
  Валюта — отдельное поле строкой из трёх букв (RUB, USD, EUR, KZT).
- `source_message_ids` — массив строк с telegram_message_id из
  квадратных скобок портрета. Если для факта id не указан — `null`.
- Не придумывай значения, которых нет в портрете. Лучше `null`, чем
  выдумка.
- Не используй дополнительные поля вне примера ниже — валидатор их
  отбросит.
- Допустимые значения `status_delivery`: ordered, shipped, delivered,
  returned, unknown.
- Допустимые значения `status_payment`: unpaid, partial, paid, unknown.

Пример правильного ответа (с вымышленными данными, формат обязателен):

{example_json}

Портрет клиента:
{narrative}

JSON:
""".replace("{example_json}", _STRUCTURED_EXTRACT_EXAMPLE_JSON)


# ── Alternative pass-2 variant: inject Pydantic JSON Schema ─────────────────
#
# Kept for A/B comparison on first real-chat run. To switch, callers
# import ``STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA`` instead.

_STRUCTURED_EXTRACT_SCHEMA_JSON = json.dumps(
    StructuredExtract.model_json_schema(), ensure_ascii=False, indent=2
)


STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA = """\
Тебе дан портрет клиента в формате markdown. Извлеки из него
структурированные данные в виде JSON, строго соответствующего схеме
ниже.

Правила:
- Выводи **только JSON**, без markdown-обёртки, без префиксов, без
  пояснений. Первый символ ответа — `{`, последний — `}`.
- Поле `_v` всегда равно `1`.
- Все поля nullable. Если данных нет — ставь `null` или `[]`.
- Денежные суммы — числа. Валюта — строка из трёх букв.
- Не используй дополнительные поля вне схемы.

Схема ответа (JSON Schema):
{schema_json}

Портрет клиента:
{narrative}

JSON:
""".replace("{schema_json}", _STRUCTURED_EXTRACT_SCHEMA_JSON)


MATCHING_PROMPT = """\
Тебе дана текстовая формулировка позиции из заказа клиента и список
кандидатов-товаров из каталога магазина. Определи, какому товару из
каталога соответствует позиция.

Помни о вариантах написания:
- Транслитерация: Veritas / Верitas / Веритас — одно и то же.
- Артикулы с/без точки: 05P44 / 05P44.01 / P44 — одна модель.
- Русское vs английское название: «зензубель» / «zenzubel» / «rebate plane».
- Номер модели с/без `#`: #5 / №5 / 5.

Верни **только JSON** строго по формату ниже. Никаких пояснений,
markdown-обёртки, текста вне JSON.

Формат ответа:
{
  "decision": "confident_match" | "ambiguous" | "not_found",
  "product_id": <int или null>,
  "candidate_ids": [<int>, ...] | null,
  "note": "<краткое пояснение, 1-2 предложения, на русском>"
}

Правила:
- `confident_match` — ты уверен, какой именно это товар. В `product_id`
  кладёшь id одного кандидата. `candidate_ids` = null.
- `ambiguous` — подходящих несколько, нельзя выбрать один. В
  `candidate_ids` кладёшь id 2-3 наиболее подходящих вариантов.
  `product_id` = null.
- `not_found` — ни один кандидат не подходит. `product_id` = null,
  `candidate_ids` = null.
- Если список кандидатов пуст или очевидно несовместим с позицией —
  `not_found`.
- В `note` кратко поясни решение: что в позиции совпало / не совпало с
  кандидатами.

Позиция из заказа:
{items_text}

Кандидаты (id, name):
{candidates}

JSON:
"""


__all__ = [
    "PROMPTS_VERSION",
    "render",
    "CHUNK_SUMMARY_PROMPT",
    "MASTER_SUMMARY_PROMPT",
    "NARRATIVE_PROMPT",
    "STRUCTURED_EXTRACT_PROMPT",
    "STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA",
    "STRUCTURED_EXTRACT_EXAMPLE",
    "MATCHING_PROMPT",
]
