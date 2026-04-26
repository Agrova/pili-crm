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

Expected: 340 passed (337 baseline + 3 safety guard tests).

The safety guard in `tests/conftest.py` refuses to start if `TEST_DATABASE_URL` is not set,
equals `DATABASE_URL`, or does not contain the word `test` — protecting production data.

## Reset test DB

If tests behave inconsistently:

```bash
docker exec pili-crm-postgres-1 psql -U pili -c "DROP DATABASE pili_crm_test"
./scripts/setup_test_db.sh
```
