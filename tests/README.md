# Test isolation strategy

## Approach: rollback per test

Each test runs inside a transaction that is rolled back after the test ends.
This gives full isolation without DDL locks or table truncation.

**Why rollback and not truncate:**
- No DDL lock — truncate acquires an AccessExclusive lock on every table, which
  slows down parallel test runners and can deadlock with FK checks.
- Faster — a single `ROLLBACK` vs N sequential `TRUNCATE` statements.
- Savepoint-compatible — nested savepoints work inside the outer transaction.

**Constraint on repository code:**
Repository functions must call `flush()`, not `commit()`. A `commit()` persists
data outside the wrapping transaction, so the rollback at test teardown cannot
undo it. If a repository function commits, its test will leak state into the
next test.

## Running tests

```bash
# Full suite
python3 -m pytest tests/ -v

# Single file
python3 -m pytest tests/orders/test_create_order.py -v

# Single test
python3 -m pytest tests/orders/test_create_order.py::test_create_order_success -v
```

## Writing a new test

```python
async def test_foo(db_session: AsyncSession) -> None:
    result = await some_repository_fn(db_session, ...)
    assert result.field == expected
    # No cleanup needed — rollback happens automatically.
```

## Prerequisites

Run `scripts/setup_test_db.sh` once to create and seed the test database.
Set `TEST_DATABASE_URL` in your environment (must contain the word "test").
