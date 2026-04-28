"""Subprocess tests for the pytest_configure safety guard in tests/conftest.py."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
_TESTS_DIR = pathlib.Path(__file__).parent


def _run_pytest(
    env_overrides: dict[str, str],
    cwd: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://nobody@nowhere/none"  # defence in depth
    env.pop("TEST_DATABASE_URL", None)
    env.update(env_overrides)
    if cwd is not None:
        # Running from a temp dir without .env; pass project root via PYTHONPATH
        # and use absolute path to tests/ so pytest can still discover conftest.
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{_PROJECT_ROOT}:{existing}" if existing else str(_PROJECT_ROOT)
        pytest_target = str(_TESTS_DIR)
    else:
        pytest_target = "tests/"
    return subprocess.run(
        [sys.executable, "-m", "pytest", pytest_target, "--collect-only", "-q"],
        env=env,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_safety_guard_unset_test_url(tmp_path: pathlib.Path) -> None:
    # TEST_DATABASE_URL not in env → check 1 fires.
    # cwd=tmp_path ensures pydantic-settings cannot re-read .env from the project root.
    r = _run_pytest({}, cwd=tmp_path)
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


_TEST_URL = "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_test"
_NOWHERE = "postgresql://nobody@nowhere/none"


def _run_configure_probe() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = _NOWHERE
    env["TEST_DATABASE_URL"] = _TEST_URL
    code = (
        "import os, sys\n"
        "sys.path.insert(0, '.')\n"
        "from tests.conftest import pytest_configure\n"
        "from app.config import settings\n"
        "class _Cfg: pass\n"
        "pytest_configure(_Cfg())\n"
        "print('SETTINGS_URL=' + settings.database_url)\n"
        "print('ENV_URL=' + os.environ['DATABASE_URL'])\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
    )


def test_pytest_configure_overrides_settings_database_url() -> None:
    """Layer 1: settings.database_url is rebound to test_url after pytest_configure."""
    r = _run_configure_probe()
    assert r.returncode == 0, r.stderr
    assert f"SETTINGS_URL={_TEST_URL}" in r.stdout
    assert f"SETTINGS_URL={_NOWHERE}" not in r.stdout


def test_pytest_configure_overrides_os_environ_database_url() -> None:
    """Layer 2: os.environ['DATABASE_URL'] is rebound — critical for subprocess(alembic)."""
    r = _run_configure_probe()
    assert r.returncode == 0, r.stderr
    assert f"ENV_URL={_TEST_URL}" in r.stdout
    assert f"ENV_URL={_NOWHERE}" not in r.stdout
