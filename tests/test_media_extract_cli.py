"""ADR-014 Task 5: tests for analysis/media_extract/cli.py."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.media_extract import cli as cli_mod
from analysis.media_extract import service as service_mod
from analysis.media_extract.service import (
    ExtractionResult,
    ExtractorKind,
    PendingMediaMessage,
)
from app.llm_studio_control import LMStudioTimeoutError

SEED_PHONE = "+77471057849"


# ── shared fixture-helpers (mirror the service test file) ─────────────────


async def _seed_account_id(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT id FROM communications_telegram_account "
                    "WHERE phone_number = :phone"
                ),
                {"phone": SEED_PHONE},
            )
        ).scalar_one()
    )


async def _seed_chat(session: AsyncSession, account_id: int, tag: str) -> int:
    cid = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :title) RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"tg-cli-{tag}-{datetime.now(tz=UTC).timestamp()}",
                "title": f"cli test {tag}",
            },
        )
    ).scalar_one()
    await session.flush()
    return int(cid)


async def _seed_photo_message(
    session: AsyncSession, chat_id: int, *, tag: str
) -> int:
    mid = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at) "
                "VALUES (:cid, :tg, NOW()) RETURNING id"
            ),
            {
                "cid": chat_id,
                "tg": f"cli-msg-{tag}-{datetime.now(tz=UTC).timestamp()}",
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO communications_telegram_message_media "
            "(message_id, media_type, mime_type, file_name, "
            " relative_path, file_size_bytes) "
            "VALUES (:mid, 'photo', 'image/jpeg', 'p.jpg', "
            "        'chats/x/p.jpg', 1024)"
        ),
        {"mid": mid},
    )
    await session.flush()
    return int(mid)


def _make_args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = dict(
        all=False,
        chat_id=None,
        message_id=None,
        model=None,
        use_fallback_model=False,
        endpoint="http://localhost:1234/v1",
        regenerate=False,
        dry_run=False,
        verbose=False,
        batch_size=10,
        unload_after=False,
        classification=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ── tests ──────────────────────────────────────────────────────────────────


async def test_cli_dry_run_does_not_write_to_db(
    db_session: AsyncSession,
    capsys,
) -> None:
    aid = await _seed_account_id(db_session)
    chat = await _seed_chat(db_session, aid, tag="dry")
    msg = await _seed_photo_message(db_session, chat, tag="dry")

    args = _make_args(chat_id=chat, dry_run=True)

    fake_factory = _patch_session_factory(db_session)
    with (
        fake_factory,
        patch.object(
            cli_mod, "ensure_model_loaded", new=AsyncMock()
        ) as ensure_mock,
        patch.object(service_mod, "_vision_extract_image", new=AsyncMock()) as vis_mock,
    ):
        rc = await cli_mod.main(args)

    assert rc == 0
    ensure_mock.assert_not_called()  # dry-run skips LM Studio
    vis_mock.assert_not_called()

    written = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM communications_telegram_message_media_extraction "
                "WHERE message_id = :mid"
            ),
            {"mid": msg},
        )
    ).scalar()
    assert written == 0

    out = capsys.readouterr().out
    assert "Saved to DB:          0 (dry-run)" in out
    assert f"--chat-id {chat}" in out


async def test_cli_chat_id_filter(db_session: AsyncSession) -> None:
    aid = await _seed_account_id(db_session)
    chat_a = await _seed_chat(db_session, aid, tag="A")
    chat_b = await _seed_chat(db_session, aid, tag="B")
    msg_a = await _seed_photo_message(db_session, chat_a, tag="A-img")
    msg_b = await _seed_photo_message(db_session, chat_b, tag="B-img")

    args = _make_args(chat_id=chat_a, dry_run=True)

    seen_message_ids: list[int] = []

    real_decide = service_mod.decide_extractor

    def _spy_decide(msg: PendingMediaMessage) -> ExtractorKind:
        seen_message_ids.append(msg.message_id)
        return real_decide(msg)

    fake_factory = _patch_session_factory(db_session)
    with (
        fake_factory,
        patch.object(cli_mod, "decide_extractor", side_effect=_spy_decide),
    ):
        rc = await cli_mod.main(args)

    assert rc == 0
    assert msg_a in seen_message_ids
    assert msg_b not in seen_message_ids


async def test_cli_lm_studio_timeout_returns_exit_2(
    db_session: AsyncSession,
    capsys,
) -> None:
    aid = await _seed_account_id(db_session)
    chat = await _seed_chat(db_session, aid, tag="timeout")
    await _seed_photo_message(db_session, chat, tag="timeout")

    args = _make_args(chat_id=chat, dry_run=False)

    fake_factory = _patch_session_factory(db_session)
    with (
        fake_factory,
        patch.object(
            cli_mod,
            "ensure_model_loaded",
            new=AsyncMock(side_effect=LMStudioTimeoutError("never appeared")),
        ),
    ):
        rc = await cli_mod.main(args)

    assert rc == 2
    err = capsys.readouterr().err
    assert "is not loaded in LM Studio" in err
    assert "load it manually" in err


async def test_cli_progress_summary_correct(
    db_session: AsyncSession,
    capsys,
) -> None:
    aid = await _seed_account_id(db_session)
    chat = await _seed_chat(db_session, aid, tag="sum")
    msg_id = await _seed_photo_message(db_session, chat, tag="sum")

    args = _make_args(chat_id=chat, dry_run=False, batch_size=5)

    fake_text = "[Изображение]\nОписание: x\nТекст на изображении: y"

    # Substitute extract_image_or_fail to bypass the on-disk file check —
    # our seeded relative_path doesn't point at a real file.
    async def _fake_extract_image(
        msg: PendingMediaMessage, *_args: object, **_kw: object
    ) -> ExtractionResult:
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=fake_text,
            extraction_method="vision_qwen3-vl-30b-a3b",
        )

    # CLI commits per batch; with the rollback fixture we patch commit
    # to a no-op so the writes flushed to the test session stay visible.
    fake_factory = _patch_session_factory(db_session)
    with (
        fake_factory,
        patch.object(cli_mod, "ensure_model_loaded", new=AsyncMock()),
        patch.object(
            cli_mod, "extract_image_or_fail", side_effect=_fake_extract_image
        ),
        patch.object(db_session, "commit", new=AsyncMock()),
    ):
        rc = await cli_mod.main(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Processed:            1" in out
    assert "vision:             1" in out
    assert "Saved to DB:          1" in out

    stored = (
        await db_session.execute(
            text(
                "SELECT extraction_method "
                "FROM communications_telegram_message_media_extraction "
                "WHERE message_id = :mid"
            ),
            {"mid": msg_id},
        )
    ).scalar()
    assert stored == "vision_qwen3-vl-30b-a3b"


async def test_crash_resume_commits_per_batch_size(
    db_session: AsyncSession,
) -> None:
    """Simulate a process crash mid-chat: verify that the first COMMIT_BATCH_SIZE
    saves are durable and a restart picks up exactly where the crash left off.

    Scenario:
    - Seed 25 photo messages in one chat.
    - First run: _vision_extract_image raises RuntimeError on the 12th call
      (after 10 successful saves have been committed and 1 more flushed but
      not yet committed). The CLI returns rc=1.
    - The real session is committed here to simulate what would have been
      durable before the crash (only the explicit commit(s) count).
    - Second run (skip_existing=True): processes remaining messages.
    - Final check: exactly 25 rows in DB, no duplicates.
    """
    aid = await _seed_account_id(db_session)
    chat = await _seed_chat(db_session, aid, tag="crash")
    msg_ids = [
        await _seed_photo_message(db_session, chat, tag=f"crash-{i}")
        for i in range(25)
    ]
    await db_session.flush()

    call_count = 0
    # Tracks commits that _actually_ happened (real session.commit calls).
    committed_rows: list[int] = []

    async def _fake_extract(
        msg: PendingMediaMessage, *_a: object, **_kw: object
    ) -> ExtractionResult:
        nonlocal call_count
        call_count += 1
        if call_count == 12:
            raise RuntimeError("simulated crash")
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text="[photo]",
            extraction_method="vision_test",
        )

    original_commit = db_session.commit

    async def _tracking_commit() -> None:
        await original_commit()
        # Count how many extraction rows exist after this commit.
        from sqlalchemy import text as sa_text
        n = (
            await db_session.execute(
                sa_text(
                    "SELECT COUNT(*) FROM communications_telegram_message_media_extraction "
                    "WHERE message_id = ANY(:ids)"
                ),
                {"ids": msg_ids},
            )
        ).scalar()
        committed_rows.append(int(n))

    args_run1 = _make_args(chat_id=chat, dry_run=False, batch_size=100)

    fake_factory = _patch_session_factory(db_session)
    with (
        fake_factory,
        patch.object(cli_mod, "ensure_model_loaded", new=AsyncMock()),
        patch.object(cli_mod, "extract_image_or_fail", side_effect=_fake_extract),
        patch.object(db_session, "commit", side_effect=_tracking_commit),
    ):
        rc = await cli_mod.main(args_run1)

    # CLI should have aborted (rc=1) when the 12th call crashed.
    assert rc == 1

    # Exactly COMMIT_BATCH_SIZE rows were committed before the crash.
    # (10 writes → commit; 11th write started → crash before next commit)
    from analysis.media_extract.cli import COMMIT_BATCH_SIZE
    assert committed_rows and committed_rows[-1] == COMMIT_BATCH_SIZE, (
        f"expected {COMMIT_BATCH_SIZE} durable rows after crash, got {committed_rows}"
    )

    # ── Second run: restart from the point of durability ─────────────────
    call_count = 0
    committed_rows.clear()

    async def _fake_extract_ok(
        msg: PendingMediaMessage, *_a: object, **_kw: object
    ) -> ExtractionResult:
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text="[photo]",
            extraction_method="vision_test",
        )

    args_run2 = _make_args(chat_id=chat, dry_run=False, batch_size=100)

    with (
        fake_factory,
        patch.object(cli_mod, "ensure_model_loaded", new=AsyncMock()),
        patch.object(cli_mod, "extract_image_or_fail", side_effect=_fake_extract_ok),
        patch.object(db_session, "commit", side_effect=_tracking_commit),
    ):
        rc2 = await cli_mod.main(args_run2)

    assert rc2 == 0

    from sqlalchemy import text as sa_text2
    total = (
        await db_session.execute(
            sa_text2(
                "SELECT COUNT(*) FROM communications_telegram_message_media_extraction "
                "WHERE message_id = ANY(:ids)"
            ),
            {"ids": msg_ids},
        )
    ).scalar()
    assert int(total) == 25, f"expected 25 total rows after resume, got {total}"


# ── helpers ────────────────────────────────────────────────────────────────


class _SessionFactoryProxy:
    """Async-context manager that yields the supplied session and ignores
    enter/exit so the real db_session fixture controls rollback."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _patch_session_factory(session: AsyncSession):  # type: ignore[no-untyped-def]
    """Patch ``cli_mod.async_session_factory`` so the CLI uses the test session.

    Returned object is a context manager — use as ``with _patch_session_factory(s):``.
    """
    return patch.object(
        cli_mod,
        "async_session_factory",
        new=lambda: _SessionFactoryProxy(session),
    )


# Silence the unused ``StringIO`` import in older lint configs.
_ = StringIO
