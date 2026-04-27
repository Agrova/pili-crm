"""ADR-013 Task 3: tests for analysis/preflight/cli.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.preflight import PREFLIGHT_VERSION
from analysis.preflight import cli as preflight_cli
from analysis.preflight.cli import build_parser, main
from app.llm_studio_control import LMStudioTimeoutError

SEED_PHONE = "+77471057849"


# ── seed helpers (shared with service tests) ───────────────────────────────


async def _seed_account(session: AsyncSession) -> tuple[int, str]:
    aid = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = :phone"
            ),
            {"phone": SEED_PHONE},
        )
    ).scalar_one()
    op_uid = "12345-operator-cli"
    await session.execute(
        text(
            "UPDATE communications_telegram_account "
            "SET telegram_user_id = :uid WHERE id = :aid"
        ),
        {"uid": op_uid, "aid": aid},
    )
    await session.flush()
    return int(aid), op_uid


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


async def _seed_message(
    session: AsyncSession, chat_id: int, *, from_user_id: str | None, text_value: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO communications_telegram_message "
            "(chat_id, telegram_message_id, from_user_id, sent_at, text) "
            "VALUES (:cid, :tg, :fuid, NOW(), :text)"
        ),
        {
            "cid": chat_id,
            "tg": f"cli-msg-{datetime.now(tz=UTC).timestamp()}",
            "fuid": from_user_id,
            "text": text_value,
        },
    )
    await session.flush()


# ── session-factory + commit patches so CLI uses db_session ────────────────


class _SessionFactoryProxy:
    """Async-context manager that yields the test session, ignoring close."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _patch_session_factory(session: AsyncSession):
    return patch.object(
        preflight_cli,
        "async_session_factory",
        new=lambda: _SessionFactoryProxy(session),
    )


def _patch_commit():
    """The CLI commits per chat — under the test session that would break
    the conftest rollback. Replace commit with flush."""
    return patch(
        "sqlalchemy.ext.asyncio.AsyncSession.commit",
        new=AsyncMock(),
    )


# ── 15. dry-run does not call LLM ──────────────────────────────────────────


async def test_cli_dry_run_does_not_call_llm(db_session: AsyncSession) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "dry")
    await _seed_message(db_session, chat, from_user_id=op_uid, text_value="hi")

    parser = build_parser()
    args = parser.parse_args(["--chat-id", str(chat), "--dry-run"])

    complete_mock = AsyncMock()
    with (
        _patch_session_factory(db_session),
        patch.object(
            preflight_cli, "ensure_model_loaded", new=AsyncMock()
        ) as ensure_mock,
        patch(
            "analysis.preflight.cli.LMStudioClient", autospec=True
        ) as client_cls,
    ):
        client_cls.return_value.complete = complete_mock
        client_cls.return_value.aclose = AsyncMock()
        rc = await main(args)

    assert rc == 0
    complete_mock.assert_not_called()
    ensure_mock.assert_not_called()


# ── 16. --chat-id filter ───────────────────────────────────────────────────


async def test_cli_chat_id_filter(db_session: AsyncSession) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat_a = await _seed_chat(db_session, aid, "fa")
    chat_b = await _seed_chat(db_session, aid, "fb")
    await _seed_message(db_session, chat_a, from_user_id=op_uid, text_value="hi a")
    await _seed_message(db_session, chat_b, from_user_id=op_uid, text_value="hi b")

    parser = build_parser()
    args = parser.parse_args(["--chat-id", str(chat_a), "--dry-run"])

    captured: list[int] = []
    from analysis.preflight import service as svc
    real_select = svc.select_pending_chats

    async def spy(session, **kw):
        out = await real_select(session, **kw)
        captured.extend(out)
        return out

    with (
        _patch_session_factory(db_session),
        patch.object(preflight_cli, "ensure_model_loaded", new=AsyncMock()),
        patch.object(preflight_cli, "select_pending_chats", side_effect=spy),
    ):
        rc = await main(args)

    assert rc == 0
    assert chat_a in captured
    assert chat_b not in captured


# ── 17. LMStudio timeout → exit 2 ──────────────────────────────────────────


async def test_cli_lm_studio_timeout_exit_2() -> None:
    parser = build_parser()
    args = parser.parse_args(["--all"])

    with patch.object(
        preflight_cli,
        "ensure_model_loaded",
        new=AsyncMock(side_effect=LMStudioTimeoutError("nope")),
    ):
        rc = await main(args)

    assert rc == 2


# ── 18. empty chat → archive without LLM call ──────────────────────────────


async def test_cli_handles_empty_chat(db_session: AsyncSession) -> None:
    aid, _ = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "empty")  # no messages

    parser = build_parser()
    args = parser.parse_args(["--chat-id", str(chat)])

    complete_mock = AsyncMock()
    with (
        _patch_session_factory(db_session),
        _patch_commit(),
        patch.object(preflight_cli, "ensure_model_loaded", new=AsyncMock()),
        patch(
            "analysis.preflight.cli.LMStudioClient", autospec=True
        ) as client_cls,
    ):
        client_cls.return_value.complete = complete_mock
        client_cls.return_value.aclose = AsyncMock()
        rc = await main(args)

    assert rc == 0
    complete_mock.assert_not_called()

    row = (
        await db_session.execute(
            text(
                "SELECT skipped_reason, preflight_classification "
                "FROM analysis_chat_analysis "
                "WHERE chat_id = :cid AND analyzer_version = :ver"
            ),
            {"cid": chat, "ver": PREFLIGHT_VERSION},
        )
    ).one()
    assert row.skipped_reason == "empty"
    assert row.preflight_classification == "not_client"


# ── 19. progress summary includes counts ───────────────────────────────────


async def test_cli_progress_summary(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "sum")
    await _seed_message(db_session, chat, from_user_id=op_uid, text_value="hi sum")

    parser = build_parser()
    args = parser.parse_args(["--chat-id", str(chat)])

    with (
        _patch_session_factory(db_session),
        _patch_commit(),
        patch.object(preflight_cli, "ensure_model_loaded", new=AsyncMock()),
        patch(
            "analysis.preflight.cli.LMStudioClient", autospec=True
        ) as client_cls,
    ):
        client_cls.return_value.complete = AsyncMock(
            return_value=(
                '{"classification": "client", "confidence": "high", '
                '"reason": "ok"}'
            )
        )
        client_cls.return_value.aclose = AsyncMock()
        rc = await main(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Preflight summary:" in out
    assert "Total pending:" in out
    assert PREFLIGHT_VERSION in out
    assert "client:" in out
