"""Shared fixtures for the command-level test suite.

Every test runs through the real entry point (``meshcli.main.main``) with
``MESH_CONFIG`` pinned to a tmp dir, so config/credential files never touch
the real HOME. The exit-code discipline (0/1/2/3/4/130) lives in ``main()``,
so that is the surface whose return value is asserted — mirroring the
``_run`` helper in test_main.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import yaml

from meshcli import main as main_mod

BASE = "https://mesh.test"
WS_UUID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """No real waits anywhere (http backoff, export polling, device poll)."""
    import meshcli.commands.auth as auth_mod
    import meshcli.commands.data as data_mod
    import meshcli.http as http_mod

    monkeypatch.setattr(http_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(data_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(auth_mod.time, "sleep", lambda _seconds: None)


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    output: str  # stdout — result data only (output discipline §3.5)
    stderr: str


def write_config(config_dir, *, api_url=None, workspace=None, extra=None) -> None:
    data = dict(extra or {})
    if api_url:
        data["current_host"] = api_url
    if workspace:
        data["workspace"] = workspace
    if data:
        data.setdefault("version", 1)
        (config_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def write_credential(config_dir, host: str, entry: dict) -> None:
    path = config_dir / "credentials.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "hosts": {host: entry}}), encoding="utf-8")
    path.chmod(0o600)
    config_dir.chmod(0o700)


def invoke(args, capsys) -> RunResult:
    try:
        rc = main_mod.main(list(args))
    except SystemExit as exc:  # commands may sys.exit directly (auth status)
        rc = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return RunResult(exit_code=rc, output=captured.out, stderr=captured.err)


@pytest.fixture
def mesh_env(tmp_path, monkeypatch):
    """Isolated MESH_CONFIG dir + deterministic key resolution (no MESH_* env)."""
    config_dir = tmp_path / "mesh"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("MESH_CONFIG", str(config_dir))
    for var in ("MESH_API_URL", "MESH_WORKSPACE", "MESH_OUTPUT", "MESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return config_dir


@pytest.fixture
def run_raw(mesh_env, capsys):
    """Invoke `mesh <args>` with an isolated config dir, no files pre-written."""
    return lambda args: invoke(args, capsys)


@pytest.fixture
def run_cli(mesh_env, capsys):
    """Invoke `mesh <args>` against BASE with config (and optional credential)."""

    def _run(args, *, api_url=BASE, workspace=None, credential=None, config=None) -> RunResult:
        write_config(mesh_env, api_url=api_url, workspace=workspace, extra=config)
        if credential is not None:
            write_credential(mesh_env, api_url, credential)
        return invoke(args, capsys)

    return _run
