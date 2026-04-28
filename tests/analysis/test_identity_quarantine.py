"""ADR-011 identity quarantine (Q-2026-04-27 X1) — service + integration tests.

Coverage:
- ``extract_identity_to_quarantine`` — writes, skip-empties, null customer,
  analyzer_version, context_quotes (5 tests).
- ``auto_apply_safe_identity_updates`` — confidence gate, NULL-target gate,
  unmappable contact_type, metadata, metrics (6 tests).
- ``apply_analysis_to_customer`` integration — full path with autoapply,
  unreviewed chat (2 tests).
- Force-rerun — duplicates are documented MVP behaviour (1 bonus test).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effect imports: FK targets must be mapped before test setup runs.
import app.catalog.models  # noqa: F401
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
from app.analysis import service
from app.analysis.identity_service import (
    auto_apply_safe_identity_updates,
    extract_identity_to_quarantine,
)
from app.analysis.schemas import Identity, MatchedStructuredExtract
from app.orders.repository import create_customer
from app.orders.service import set_customer_identity_field

# ── seed helpers ────────────────────────────────────────────────────────────


async def _seed_chat(session: AsyncSession, title: str) -> int:
    account_id = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"
    chat_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :t) RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"idq-{title}-{datetime.now(tz=UTC).timestamp()}",
                "t": title,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(chat_id)


async def _seed_message(session: AsyncSession, chat_id: int, body: str) -> int:
    msg_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at, text) "
                "VALUES (:cid, :tmid, :sent, :text) RETURNING id"
            ),
            {
                "cid": chat_id,
                "tmid": f"m-{datetime.now(tz=UTC).timestamp()}",
                "sent": datetime.now(tz=UTC),
                "text": body,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(msg_id)


async def _link_chat_to_customer(
    session: AsyncSession, chat_id: int, customer_id: int
) -> None:
    msg_id = await _seed_message(session, chat_id, f"hi {customer_id}")
    await session.execute(
        text(
            "INSERT INTO communications_link "
            "(telegram_message_id, target_module, target_entity, "
            " target_id, link_confidence) "
            "VALUES (:mid, 'orders', 'orders_customer', :tid, 'manual')"
        ),
        {"mid": msg_id, "tid": customer_id},
    )
    await session.flush()


async def _make_customer(session: AsyncSession, suffix: str) -> int:
    c = await create_customer(
        session, name=f"Identity {suffix}", telegram_id=f"@idq_{suffix}"
    )
    return c.id


async def _set_high_confidence(
    session: AsyncSession, extracted_ids: list[int]
) -> None:
    """Force ``confidence='high'`` on quarantine rows.

    The current LLM emits no per-field confidence so all rows land as
    ``'medium'`` — the auto-apply gate would never fire in tests. This
    helper simulates the future LLM iteration that emits structured
    confidence per field.
    """
    await session.execute(
        text(
            "UPDATE analysis_extracted_identity SET confidence = 'high' "
            "WHERE extracted_id = ANY(:ids)"
        ),
        {"ids": extracted_ids},
    )
    await session.flush()


async def _archive_with_identity(
    session: AsyncSession,
    *,
    chat_id: int,
    identity: Identity,
    analyzer_version: str = "v0.idq+apply",
) -> int:
    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1, identity=identity
    )
    row = await service.record_full_analysis(
        session,
        chat_id=chat_id,
        analyzer_version=analyzer_version,
        messages_analyzed_up_to="1",
        narrative_markdown="n",
        matched_extract=extract,
        chunks_count=1,
    )
    return row.id


# ── extract_identity_to_quarantine (1-5) ────────────────────────────────────


async def test_quarantine_writes_all_identity_fields(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "all_fields")
    customer_id = await _make_customer(db_session, "all_fields")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+all",
        identity_data={
            "phone": "+7 999 111 22 33",
            "email": "k@example.com",
            "telegram_username": "@kristina",
            "city": "Москва",
            "name_guess": "Кристина",
        },
    )
    assert len(ids) == 5

    rows = (
        await db_session.execute(
            text(
                "SELECT contact_type, value, status, confidence "
                "FROM analysis_extracted_identity "
                "WHERE extracted_id = ANY(:ids) ORDER BY contact_type"
            ),
            {"ids": ids},
        )
    ).all()
    types = {r[0] for r in rows}
    # name_guess → 'name' (CHECK contact_type list); confidence_notes is dropped.
    assert types == {"phone", "email", "telegram_username", "city", "name"}
    assert all(r[2] == "pending" for r in rows)
    assert all(r[3] == "medium" for r in rows)


async def test_quarantine_skips_empty_values(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "empty")
    customer_id = await _make_customer(db_session, "empty")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+empty",
        identity_data={
            "phone": "+7 111",
            "email": None,
            "city": "",
            "telegram_username": "   ",  # whitespace-only
        },
    )
    assert len(ids) == 1
    contact_type = (
        await db_session.execute(
            text(
                "SELECT contact_type FROM analysis_extracted_identity "
                "WHERE extracted_id = :id"
            ),
            {"id": ids[0]},
        )
    ).scalar_one()
    assert contact_type == "phone"


async def test_quarantine_with_null_customer(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "null_cust")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=None,
        analyzer_version="v0.idq+nullc",
        identity_data={"phone": "+7 1"},
    )
    assert len(ids) == 1
    cid = (
        await db_session.execute(
            text(
                "SELECT customer_id FROM analysis_extracted_identity "
                "WHERE extracted_id = :id"
            ),
            {"id": ids[0]},
        )
    ).scalar_one()
    assert cid is None


async def test_quarantine_writes_analyzer_version(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "ver")
    customer_id = await _make_customer(db_session, "ver")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="analysis-v1.0+qwen3-14b",
        identity_data={"email": "v@example.com"},
    )
    ver = (
        await db_session.execute(
            text(
                "SELECT analyzer_version FROM analysis_extracted_identity "
                "WHERE extracted_id = :id"
            ),
            {"id": ids[0]},
        )
    ).scalar_one()
    assert ver == "analysis-v1.0+qwen3-14b"


async def test_quarantine_with_context_quotes(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "quotes")
    customer_id = await _make_customer(db_session, "quotes")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+quote",
        identity_data={"phone": "+7 1", "email": "q@example.com"},
        context_quotes={
            "phone": "Запиши телефон +7 1",
            "email": "почта q@...",
        },
    )
    rows = (
        await db_session.execute(
            text(
                "SELECT contact_type, context_quote "
                "FROM analysis_extracted_identity "
                "WHERE extracted_id = ANY(:ids) ORDER BY contact_type"
            ),
            {"ids": ids},
        )
    ).all()
    quotes = {r[0]: r[1] for r in rows}
    assert quotes["phone"] == "Запиши телефон +7 1"
    assert quotes["email"] == "почта q@..."


# ── auto_apply_safe_identity_updates (6-11) ─────────────────────────────────


async def test_autoapply_empty_field_high_confidence(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "auto_high")
    customer_id = await _make_customer(db_session, "auto_high")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+autohi",
        identity_data={"phone": "+7 222"},
    )
    await _set_high_confidence(db_session, ids)

    metrics = await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id, extracted_ids=ids
    )
    assert metrics == {"auto_applied": 1, "kept_pending": 0}

    phone = (
        await db_session.execute(
            text("SELECT phone FROM orders_customer WHERE id = :cid"),
            {"cid": customer_id},
        )
    ).scalar_one()
    assert phone == "+7 222"

    status = (
        await db_session.execute(
            text(
                "SELECT status FROM analysis_extracted_identity "
                "WHERE extracted_id = :id"
            ),
            {"id": ids[0]},
        )
    ).scalar_one()
    assert status == "applied"


async def test_autoapply_empty_field_medium_kept_pending(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "auto_med")
    customer_id = await _make_customer(db_session, "auto_med")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+automed",
        identity_data={"email": "med@example.com"},
    )
    # default confidence='medium' → auto-apply must NOT fire
    metrics = await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id, extracted_ids=ids
    )
    assert metrics == {"auto_applied": 0, "kept_pending": 1}

    email = (
        await db_session.execute(
            text("SELECT email FROM orders_customer WHERE id = :cid"),
            {"cid": customer_id},
        )
    ).scalar_one()
    assert email is None  # column untouched


async def test_autoapply_filled_field_kept_pending(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "auto_filled")
    customer_id = await _make_customer(db_session, "auto_filled")
    await set_customer_identity_field(
        db_session, customer_id, "phone", "+7 OLD"
    )

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+filled",
        identity_data={"phone": "+7 NEW"},
    )
    await _set_high_confidence(db_session, ids)

    metrics = await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id, extracted_ids=ids
    )
    assert metrics == {"auto_applied": 0, "kept_pending": 1}

    phone = (
        await db_session.execute(
            text("SELECT phone FROM orders_customer WHERE id = :cid"),
            {"cid": customer_id},
        )
    ).scalar_one()
    assert phone == "+7 OLD"  # operator-only overwrite path


async def test_autoapply_unmappable_type_kept_pending(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "auto_city")
    customer_id = await _make_customer(db_session, "auto_city")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+city",
        identity_data={"city": "Москва"},
    )
    await _set_high_confidence(db_session, ids)

    metrics = await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id, extracted_ids=ids
    )
    # 'city' has no orders_customer column → always pending
    assert metrics == {"auto_applied": 0, "kept_pending": 1}

    status = (
        await db_session.execute(
            text(
                "SELECT status FROM analysis_extracted_identity "
                "WHERE extracted_id = :id"
            ),
            {"id": ids[0]},
        )
    ).scalar_one()
    assert status == "pending"


async def test_autoapply_writes_metadata(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "auto_meta")
    customer_id = await _make_customer(db_session, "auto_meta")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+meta",
        identity_data={"telegram_username": "@meta"},
    )
    await _set_high_confidence(db_session, ids)

    await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id, extracted_ids=ids
    )
    row = (
        await db_session.execute(
            text(
                "SELECT applied_at, applied_by, applied_action, status "
                "FROM analysis_extracted_identity WHERE extracted_id = :id"
            ),
            {"id": ids[0]},
        )
    ).one()
    applied_at, applied_by, applied_action, status = row
    assert applied_at is not None
    assert applied_by == "auto"
    assert applied_action == "auto_filled_empty"
    assert status == "applied"


async def test_autoapply_returns_metrics(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "auto_metrics")
    customer_id = await _make_customer(db_session, "auto_metrics")

    ids = await extract_identity_to_quarantine(
        db_session,
        chat_id=chat_id,
        customer_id=customer_id,
        analyzer_version="v0.idq+metrics",
        identity_data={
            "phone": "+7 1",                # → high → auto-apply (NULL target)
            "email": "m@example.com",       # stays medium → kept_pending
            "city": "Кушнаренково",         # → high but no column → kept_pending
        },
    )
    by_type: dict[str, int] = {}
    for eid in ids:
        ct = (
            await db_session.execute(
                text(
                    "SELECT contact_type FROM analysis_extracted_identity "
                    "WHERE extracted_id = :id"
                ),
                {"id": eid},
            )
        ).scalar_one()
        by_type[ct] = eid
    await _set_high_confidence(
        db_session, [by_type["phone"], by_type["city"]]
    )

    metrics = await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id, extracted_ids=ids
    )
    assert metrics == {"auto_applied": 1, "kept_pending": 2}


# ── apply_analysis_to_customer integration (12-13) ──────────────────────────


async def test_apply_analysis_writes_identity_to_quarantine_and_autoapplies(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "apply_id")
    customer_id = await _make_customer(db_session, "apply_id")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    analysis_id = await _archive_with_identity(
        db_session,
        chat_id=chat_id,
        identity=Identity(
            phone="+7 555",
            email="apply@example.com",
            city="Уфа",
        ),
    )
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    # All extractions land in quarantine.
    assert result.identities_quarantined == 3
    # Default confidence='medium' → nothing auto-applied yet.
    assert result.identities_auto_applied == 0
    assert result.identities_kept_pending == 3

    quarantine = (
        await db_session.execute(
            text(
                "SELECT contact_type, status, confidence, customer_id "
                "FROM analysis_extracted_identity "
                "WHERE chat_id = :cid ORDER BY contact_type"
            ),
            {"cid": chat_id},
        )
    ).all()
    assert {r[0] for r in quarantine} == {"phone", "email", "city"}
    assert all(r[1] == "pending" for r in quarantine)
    assert all(r[2] == "medium" for r in quarantine)
    assert all(r[3] == customer_id for r in quarantine)

    # Simulate future LLM iteration: bump phone to 'high' and run auto-apply.
    # Email stays medium; city has no orders_customer column.
    phone_id = (
        await db_session.execute(
            text(
                "SELECT extracted_id FROM analysis_extracted_identity "
                "WHERE chat_id = :cid AND contact_type = 'phone'"
            ),
            {"cid": chat_id},
        )
    ).scalar_one()
    await _set_high_confidence(db_session, [phone_id])
    metrics = await auto_apply_safe_identity_updates(
        db_session, customer_id=customer_id
    )
    assert metrics["auto_applied"] == 1
    phone = (
        await db_session.execute(
            text("SELECT phone FROM orders_customer WHERE id = :cid"),
            {"cid": customer_id},
        )
    ).scalar_one()
    assert phone == "+7 555"


async def test_apply_analysis_unreviewed_chat_quarantines_only(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "unreviewed")  # NOT linked

    analysis_id = await _archive_with_identity(
        db_session,
        chat_id=chat_id,
        identity=Identity(phone="+7 unreviewed", name_guess="Кристина"),
        analyzer_version="v0.idq+unrev",
    )
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert result.customer_id is None
    assert result.identities_quarantined == 2
    assert result.identities_auto_applied == 0
    assert result.identities_kept_pending == 2

    rows = (
        await db_session.execute(
            text(
                "SELECT contact_type, value, customer_id "
                "FROM analysis_extracted_identity "
                "WHERE chat_id = :cid ORDER BY contact_type"
            ),
            {"cid": chat_id},
        )
    ).all()
    assert {r[0] for r in rows} == {"phone", "name"}
    name_row = next(r for r in rows if r[0] == "name")
    assert name_row[1] == "Кристина"
    # All rows have customer_id NULL (chat not linked).
    assert all(r[2] is None for r in rows)


# ── Bonus (14): force-rerun documents duplicate behaviour ──────────────────


async def test_apply_analysis_rerun_creates_duplicate_quarantine_rows(
    db_session: AsyncSession,
) -> None:
    """MVP-decision: ``extract_identity_to_quarantine`` never deduplicates.

    On a rerun the same identity value is written again as a fresh
    ``pending`` row. The auto-apply gate keeps the duplicate ``pending``
    because the target column is no longer NULL after the first run.
    Operators see both rows in Cowork; deduplication is out of scope for
    X1 — same value can legitimately surface from multiple chats, so a
    UNIQUE constraint was deliberately omitted.
    """
    chat_id = await _seed_chat(db_session, "rerun")
    customer_id = await _make_customer(db_session, "rerun")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    identity = Identity(phone="+7 RERUN")
    aid1 = await _archive_with_identity(
        db_session,
        chat_id=chat_id,
        identity=identity,
        analyzer_version="v0.idq+rerun",
    )

    # First run — quarantine row, kept_pending (default medium).
    first = await service.apply_analysis_to_customer(
        db_session, analysis_id=aid1
    )
    assert first.identities_quarantined == 1
    assert first.identities_kept_pending == 1

    # Simulate the column being filled (operator action or future high-conf
    # auto-apply) so the rerun's gate sees a non-NULL column.
    await set_customer_identity_field(
        db_session, customer_id, "phone", "+7 RERUN"
    )

    # Rerun — same chat, same analysis_id. Identity-only path doesn't
    # journal anything in analysis_created_entities, so force=True is a
    # no-op here; we pass it to mirror the realistic operator action.
    second = await service.apply_analysis_to_customer(
        db_session, analysis_id=aid1, force=True
    )
    assert second.identities_quarantined == 1
    assert second.identities_auto_applied == 0
    assert second.identities_kept_pending == 1

    # Two quarantine rows now exist for the same chat+contact+value.
    rows = (
        await db_session.execute(
            text(
                "SELECT status FROM analysis_extracted_identity "
                "WHERE chat_id = :cid AND contact_type = 'phone' "
                "AND value = '+7 RERUN' ORDER BY extracted_id"
            ),
            {"cid": chat_id},
        )
    ).all()
    assert len(rows) == 2
    assert {r[0] for r in rows} == {"pending"}
