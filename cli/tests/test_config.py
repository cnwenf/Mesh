"""Config + credential store (cli.md §2.2, C6/C26/C27, §5.3 fail-closed)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from meshcli import config as cfg
from meshcli.config import (
    CredentialEntry,
    CredentialFileError,
    expand_alias,
    parse_env_bool,
    resolve_key,
)
from meshcli.errors import EXIT_AUTH, EXIT_VALIDATION, CliError


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own MESH_CONFIG dir + clean env."""
    monkeypatch.setenv("MESH_CONFIG", str(tmp_path / "mesh"))
    for var in ("MESH_TOKEN", "MESH_API_URL", "MESH_WORKSPACE", "MESH_OUTPUT"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "mesh"


# --- fail-closed credential permissions (§5.3) ----------------------------------


def _write_creds(mode: int, content: dict | None = None) -> Path:
    path = cfg.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(yaml.safe_dump(content or {"hosts": {}}))
    os.chmod(path, mode)
    return path


def test_credential_file_0600_loads(isolated_config):
    _write_creds(0o600, {"hosts": {"h": {"kind": "pat", "token": "mesh_pat_x"}}})
    entry = cfg.load_credential("h")
    assert entry is not None and entry.token == "mesh_pat_x"


@pytest.mark.parametrize("mode", [0o644, 0o604, 0o640, 0o606, 0o666])
def test_credential_file_group_other_access_refused(isolated_config, mode):
    _write_creds(mode)
    with pytest.raises(CredentialFileError) as exc:
        cfg.load_credential("h")
    assert exc.value.exit_code == EXIT_AUTH
    assert "chmod" in (exc.value.hint or "")


def test_credential_dir_loose_perms_refused(isolated_config):
    path = _write_creds(0o600)
    os.chmod(path.parent, 0o755)
    with pytest.raises(CredentialFileError) as exc:
        cfg.load_credential("h")
    assert "chmod 700" in (exc.value.hint or "")


def test_credential_symlink_refused(isolated_config):
    isolated_config.mkdir(parents=True, exist_ok=True)
    real = isolated_config / "real.yaml"
    real.write_text("hosts: {}")
    path = cfg.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.symlink_to(real)
    with pytest.raises(CredentialFileError) as exc:
        cfg.load_credential("h")
    assert "symlink" in exc.value.message


def test_save_credential_atomic_and_0600(isolated_config):
    cfg.save_credential(
        "https://mesh.example.com",
        CredentialEntry(kind="pat", token="mesh_pat_abc", prefix="mesh_pat_abc"),
    )
    path = cfg.credentials_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    # No temp litter left behind.
    assert not [p for p in path.parent.iterdir() if p.name.startswith(f".{path.name}.")]


def test_mesh_token_env_short_circuits_file(isolated_config, monkeypatch):
    # With MESH_TOKEN set, the (even malformed-permission) file is NOT read.
    monkeypatch.setenv("MESH_TOKEN", "mesh_pat_ci")
    entry = cfg.load_credential("whatever-host")
    assert entry is not None and entry.token == "mesh_pat_ci" and entry.kind == "pat"


# --- precedence chain (flag > env > file > default) ------------------------------


def test_precedence_flag_wins(isolated_config, monkeypatch):
    monkeypatch.setenv("MESH_OUTPUT", "json")
    cfg.config_set("output", "table")
    assert resolve_key("output", flag_value="json").source == "flag"
    assert resolve_key("output", flag_value="json").value == "json"


def test_precedence_env_over_file(isolated_config, monkeypatch):
    cfg.config_set("workspace", "from-file")
    monkeypatch.setenv("MESH_WORKSPACE", "from-env")
    resolved = resolve_key("workspace", flag_value=None)
    assert resolved.value == "from-env" and resolved.source == "env"


def test_precedence_file_over_default(isolated_config):
    cfg.config_set("workspace", "acme")
    resolved = resolve_key("workspace", flag_value=None)
    assert resolved.value == "acme" and resolved.source == "file"


def test_precedence_default(isolated_config):
    resolved = resolve_key("output", flag_value=None)
    assert resolved.value == "table" and resolved.source == "default"


def test_env_empty_string_treated_as_unset(isolated_config, monkeypatch):
    """Empty env value falls through — never an 'empty invalid' error (C6)."""
    monkeypatch.setenv("MESH_WORKSPACE", "")
    cfg.config_set("workspace", "file-ws")
    resolved = resolve_key("workspace", flag_value=None)
    assert resolved.value == "file-ws" and resolved.source == "file"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("On", True),
        ("0", False), ("false", False), ("No", False), ("off", False),
    ],
)
def test_env_bool_parsing(raw, expected):
    assert parse_env_bool(raw) is expected


def test_env_bool_invalid():
    with pytest.raises(CliError) as exc:
        parse_env_bool("maybe")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_output_enum_validation(isolated_config, monkeypatch):
    monkeypatch.setenv("MESH_OUTPUT", "yaml")
    with pytest.raises(CliError) as exc:
        resolve_key("output", flag_value=None)
    assert exc.value.exit_code == EXIT_VALIDATION


# --- config set/get/unset/list (C6) ----------------------------------------------


def test_config_unset_restores_default(isolated_config):
    cfg.config_set("workspace", "acme")
    assert resolve_key("workspace", flag_value=None).source == "file"
    cfg.config_unset("workspace")
    resolved = resolve_key("workspace", flag_value=None)
    assert resolved.source == "default"


def test_config_list_all_marks_sources(isolated_config, monkeypatch):
    cfg.config_set("workspace", "acme")
    monkeypatch.setenv("MESH_OUTPUT", "json")
    rows = {r["key"]: r for r in cfg.config_list_all()}
    assert rows["workspace"]["source"] == "file"
    assert rows["output"]["source"] == "env"
    assert rows["api_url"]["source"] == "default"


def test_config_set_rejects_insecure_key(isolated_config):
    with pytest.raises(CliError) as exc:
        cfg.config_set("insecure", "true")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_config_set_rejects_userinfo_proxy(isolated_config):
    with pytest.raises(CliError) as exc:
        cfg.config_set("proxy", "http://user:pass@proxy.corp:3128")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_config_get_unknown_key(isolated_config):
    with pytest.raises(CliError) as exc:
        cfg.config_get("bogus")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_api_url_maps_to_current_host(isolated_config):
    cfg.config_set("api_url", "https://mesh.corp.com")
    raw = cfg.load_config_raw()
    assert raw["current_host"] == "https://mesh.corp.com"
    assert resolve_key("api_url", flag_value=None).value == "https://mesh.corp.com"


# --- aliases (C27: single-level sugar) -------------------------------------------


def test_alias_expands_with_positional_passthrough():
    aliases = {"co": "issue create", "ls": "issue list"}
    assert expand_alias(["co", "--title", "X"], aliases) == ["issue", "create", "--title", "X"]
    assert expand_alias(["ls", "--all"], aliases) == ["issue", "list", "--all"]


def test_alias_single_level_no_recursion():
    """a→b, b→a: expanding `a` yields `b ...` — the result is NOT re-expanded."""
    aliases = {"a": "b", "b": "a"}
    assert expand_alias(["a", "x"], aliases) == ["b", "x"]


def test_alias_no_match_passthrough():
    assert expand_alias(["issue", "list"], {"co": "issue create"}) == ["issue", "list"]


def test_did_you_mean():
    assert cfg.did_you_mean("isue", ["issue", "project", "member"]) == "issue"
    assert cfg.did_you_mean("zzzzzzz", ["issue"], max_distance=2) is None
