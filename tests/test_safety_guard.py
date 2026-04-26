"""Subprocess tests for the pytest_configure safety guard in tests/conftest.py."""

from __future__ import annotations

import os
import subprocess
import sys


def _run_pytest(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://nobody@nowhere/none"  # defence in depth
    env.pop("TEST_DATABASE_URL", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        env=env,
        capture_output=True,
        text=True,
    )


def test_safety_guard_unset_test_url() -> None:
    # TEST_DATABASE_URL not in env → check 1 fires
    r = _run_pytest({})
    assert r.returncode == 2
    assert "TEST_DATABASE_URL is not set" in r.stdout + r.stderr


def test_safety_guard_test_url_equals_prod_url() -> None:
    # Both URLs set to the same safe value → check 2 (equality) fires before check 3
    same = "postgresql://nobody@nowhere/none"
    r = _run_pytest({"TEST_DATABASE_URL": same, "DATABASE_URL": same})
    assert r.returncode == 2
    assert "REFUSING TO RUN" in r.stdout + r.stderr


def test_safety_guard_test_url_without_test_keyword() -> None:
    # TEST_DATABASE_URL without "test" in name → check 3 fires
    r = _run_pytest({
        "TEST_DATABASE_URL": "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_other",
    })
    assert r.returncode == 2
    assert "does not contain 'test'" in r.stdout + r.stderr
