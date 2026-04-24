"""ingestion/register_account.py — register a Telegram account before first import.

CLI:
    python -m ingestion.register_account \
        --phone +79161879839 \
        --display-name "Россия (+79161879839)" \
        [--notes NOTES] [--telegram-user-id ID]

Exit codes:
    0 — account created
    1 — phone already registered or validation error
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.communications.service import create_account, get_account_by_phone
from app.config import settings


async def _register(
    phone: str,
    display_name: str,
    notes: str | None,
    telegram_user_id: str | None,
) -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            existing = await get_account_by_phone(session, phone)
            if existing is not None:
                print(
                    f"Account with phone {phone} already registered "
                    f"(id={existing.id}, display_name={existing.display_name!r}).",
                    file=sys.stderr,
                )
                return 1
            try:
                account = await create_account(
                    session,
                    phone=phone,
                    display_name=display_name,
                    notes=notes,
                    telegram_user_id=telegram_user_id,
                )
            except IntegrityError:
                print(
                    f"Account with phone {phone} already registered.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"Created account id={account.id} display_name={account.display_name!r}"
            )
            return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a Telegram account (ADR-012) before the first import."
    )
    parser.add_argument("--phone", required=True, help="E.164 phone, e.g. +79161879839")
    parser.add_argument(
        "--display-name",
        required=True,
        help='Human-readable label, e.g. "Россия (+79161879839)"',
    )
    parser.add_argument("--notes", default=None, help="Free-form notes")
    parser.add_argument(
        "--telegram-user-id",
        default=None,
        help="Operator's user_id in this account. Auto-filled on first import otherwise.",
    )
    args = parser.parse_args()

    try:
        code = asyncio.run(
            _register(
                phone=args.phone,
                display_name=args.display_name,
                notes=args.notes,
                telegram_user_id=args.telegram_user_id,
            )
        )
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.exit(code)


if __name__ == "__main__":
    main()
