# Running tests

## One-time setup

```bash
./scripts/setup_test_db.sh
```

Then add to your `.env` file:

```
TEST_DATABASE_URL=postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_test
```

## Running the test suite

```bash
pytest
```

Expected: ~340 passed (baseline + 5 safety guard tests). Up to 4 unrelated failures
may remain after hotfix #3 — see `06_open_questions.md` under
"Test failures unrelated to DB protection". They are not blockers and are tracked
as separate mini-tasks.

The safety guard in `tests/conftest.py` refuses to start if `TEST_DATABASE_URL` is not set,
equals `DATABASE_URL`, or does not contain the word `test` — protecting production data.

## Reset test DB

If tests behave inconsistently:

```bash
docker exec pili-crm-postgres-1 psql -U pili -c "DROP DATABASE pili_crm_test"
./scripts/setup_test_db.sh
```

## Two-layer protection (hotfix #3)

Tests that bypass the `db_session` fixture (direct `create_async_engine(settings.database_url)`
or `subprocess.run(["alembic", ...])`) should also land on the test DB, not production.

`pytest_configure` does this via two overrides applied **after** all safety checks pass:

1. **Python level:** `settings.database_url = settings.test_database_url`
   - Affects all tests that read `settings.database_url` from `app.config`.

2. **OS environment level:** `os.environ["DATABASE_URL"] = settings.test_database_url`
   - Affects subprocess-spawned children (alembic, scripts) that inherit env from parent
     and read `DATABASE_URL` directly.

Both overrides happen **only** after `TEST_DATABASE_URL` is verified to be set, distinct from
production `DATABASE_URL`, and contain the substring `test`. If any check fails, pytest exits
with code 2 before reaching the override.
