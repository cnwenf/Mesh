"""Run docs/specs/validation/schema_r2_validation.sql on a disposable PG 16.

Acceptance: the 144 canonical assertions PASS and the script exits 0.
The script uses psql meta-commands, so it is executed with the real ``psql``
binary — exactly like the CI job (``psql -v ON_ERROR_STOP=1 -f ...``). When the
host has no psql (dev laptops), the cached ``postgres:16`` image provides it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.parse

import pytest

from tests.conftest import REPO_ROOT, SPECS_DIR, get_test_database_url

pytestmark = pytest.mark.e2e

VALIDATION_SQL = SPECS_DIR / "validation" / "schema_r2_validation.sql"
VALIDATION_DB = "mesh_validation_ci"
EXPECTED_PASS_COUNT = 144


def _conn_parts() -> tuple[str, str, str, str]:
    url = get_test_database_url().replace("postgresql+asyncpg://", "postgresql://")
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.username or "postgres",
        parsed.password or "",
        parsed.hostname or "127.0.0.1",
        str(parsed.port or 5432),
    )


def _run_psql(database: str, *args: str) -> subprocess.CompletedProcess:
    user, password, host, port = _conn_parts()
    env = {**os.environ, "PGPASSWORD": password}
    psql_args = ["psql", "-h", host, "-p", port, "-U", user, "-d", database, *args]
    if shutil.which("psql"):
        return subprocess.run(psql_args, env=env, capture_output=True, text=True, timeout=300)
    # Dev fallback: the postgres client from the cached postgres:16 image.
    docker_args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-e",
        f"PGPASSWORD={password}",
        "-v",
        f"{REPO_ROOT}:/work",
        "postgres:16",
        *psql_args,
    ]
    return subprocess.run(docker_args, env=env, capture_output=True, text=True, timeout=300)


def _validation_sql_path() -> str:
    if shutil.which("psql"):
        return str(VALIDATION_SQL)
    return "/work/docs/specs/validation/schema_r2_validation.sql"


def test_schema_r2_validation_runs_144_assertions_green():
    drop = _run_psql("postgres", "-c", f"DROP DATABASE IF EXISTS {VALIDATION_DB} WITH (FORCE)")
    assert drop.returncode == 0, drop.stderr
    create = _run_psql("postgres", "-c", f"CREATE DATABASE {VALIDATION_DB}")
    assert create.returncode == 0, create.stderr

    try:
        result = _run_psql(
            VALIDATION_DB, "-v", "ON_ERROR_STOP=1", "-f", _validation_sql_path()
        )
        # ON_ERROR_STOP=1: any failing assertion (RAISE EXCEPTION) exits non-zero.
        assert result.returncode == 0, f"validation script failed:\n{result.stderr[-4000:]}"

        combined = result.stderr + result.stdout
        # Each assertion emits exactly one "NOTICE:  PASS ..." line. Failures are
        # raised as EXCEPTION (aborting under ON_ERROR_STOP, caught by the exit
        # code above), so this FAIL tripwire only guards against future PASS copy
        # that happens to contain "FAIL" — hence the anchored NOTICE:\s+FAIL match
        # instead of a broad substring.
        passes = re.findall(r"NOTICE:\s+PASS ", combined)
        fails = [
            line for line in combined.splitlines() if re.search(r"NOTICE:\s+FAIL", line)
        ]
        assert not fails, f"validation reported failures: {fails[:5]}"
        assert len(passes) == EXPECTED_PASS_COUNT, (
            f"expected {EXPECTED_PASS_COUNT} PASS assertions, got {len(passes)}"
        )
    finally:
        # Drop the scratch DB so long-lived dev hosts do not accumulate residue.
        # CI runs on a disposable service container, so this is a no-op there
        # (the next run also starts from DROP DATABASE IF EXISTS).
        _run_psql("postgres", "-c", f"DROP DATABASE IF EXISTS {VALIDATION_DB} WITH (FORCE)")
