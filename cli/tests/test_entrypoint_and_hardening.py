"""Entry-point exit-code discipline (main.py / __main__.py), credential-store
fail-closed hardening edges (config.py), the RFC 8628 device-polling branches
(auth.py), and assorted residual branches — the surface no single command
module owns (cli.md §3.4, §5.3, §3.2)."""

from __future__ import annotations

import os
import runpy
import sys

import click
import httpx
import pytest
import respx

from meshcli import config as config_mod
from meshcli import main as main_mod
from meshcli.config import CredentialEntry, CredentialFileError
from meshcli.errors import EXIT_AUTH, CliError

BASE = "https://mesh.test"
WS_UUID = "11111111-1111-1111-1111-111111111111"


class TestEntryPointDiscipline:
    def test_typo_gets_did_you_mean_suggestion(self, run_raw):
        # Act
        result = run_raw(["isue", "list"])
        # Assert — usage errors stay exit 3 and point at the nearest command.
        assert result.exit_code == 3
        assert "Did you mean issue?" in result.stderr

    def test_broken_aliases_do_not_mask_usage(self, run_raw, mesh_env):
        # Arrange — valid YAML but an alias map the loader cannot iterate;
        # alias expansion must degrade to a no-op, not kill the command.
        (mesh_env / "config.yaml").write_text("aliases: just-a-string\n", encoding="utf-8")
        # Act
        result = run_raw(["version"])
        # Assert
        assert result.exit_code == 0

    def test_keyboard_interrupt_exits_130(self, run_raw, monkeypatch):
        # Arrange
        def raise_interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(main_mod.cli, "main", raise_interrupt)
        # Act
        result = run_raw(["anything"])
        # Assert
        assert result.exit_code == 130
        assert "interrupted" in result.stderr

    def test_click_abort_exits_3(self, run_raw, monkeypatch):
        # Arrange
        def raise_abort(*args, **kwargs):
            raise click.Abort

        monkeypatch.setattr(main_mod.cli, "main", raise_abort)
        # Act
        result = run_raw(["anything"])
        # Assert
        assert result.exit_code == 3
        assert "aborted" in result.stderr

    def test_unexpected_exception_exits_1_with_neutral_message(self, run_raw, monkeypatch):
        # Arrange
        def raise_boom(*args, **kwargs):
            raise RuntimeError("secret internals")

        monkeypatch.setattr(main_mod.cli, "main", raise_boom)
        # Act
        result = run_raw(["anything"])
        # Assert — neutral failure surface: class name only, no internals.
        assert result.exit_code == 1
        assert "unexpected failure (RuntimeError)" in result.stderr
        assert "secret internals" not in result.stderr

    def test_command_names_helper_lists_commands(self):
        # Act / Assert
        assert "issue" in main_mod.cli.command_names(None)

    def test_main_module_entry_point(self, mesh_env, monkeypatch):
        # Arrange — `python -m meshcli` routes through the same main().
        monkeypatch.setattr(sys, "argv", ["mesh", "version"])
        # Act / Assert
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("meshcli.__main__", run_name="__main__")
        assert exc.value.code == 0


class TestCredentialStoreHardening:
    def test_config_dir_defaults_to_xdg_home(self, monkeypatch):
        # Arrange
        monkeypatch.delenv("MESH_CONFIG", raising=False)
        # Act / Assert
        from pathlib import Path

        assert config_mod.config_dir() == Path.home() / ".config" / "mesh"

    def test_workspace_resolves_from_per_host_shadow(self):
        # Arrange / Act — hosts[current_host].workspace shadows the top level.
        resolved = config_mod.resolve_key(
            "workspace",
            flag_value=None,
            config={"current_host": BASE, "hosts": {BASE: {"workspace": "shadow"}}},
        )
        # Assert
        assert resolved.value == "shadow"
        assert resolved.source == "file"

    def test_did_you_mean_exact_match(self):
        # Act / Assert
        assert config_mod.did_you_mean("issue", ["issue", "auth"]) == "issue"

    def test_expand_alias_empty_argv(self):
        # Act / Assert
        assert config_mod.expand_alias([], {"a": "b"}) == []

    def test_clear_credential_for_unknown_host_writes_nothing(self, mesh_env):
        # Act
        config_mod.clear_credential("https://other.example.com")
        # Assert — no store file is conjured into existence.
        assert not (mesh_env / "credentials.yaml").exists()

    def test_save_credential_records_expires_at(self, mesh_env):
        # Arrange
        entry = CredentialEntry(
            kind="device_session", token="t", refresh_token="mesh_rft_e",
            expires_at="2027-01-01T00:00:00Z",
        )
        # Act
        config_mod.save_credential(BASE, entry)
        # Assert
        assert config_mod.load_credential(BASE).expires_at == "2027-01-01T00:00:00Z"

    def test_config_unset_api_url_clears_current_host(self, mesh_env):
        # Arrange
        (mesh_env / "config.yaml").write_text(
            f"version: 1\ncurrent_host: {BASE}\n", encoding="utf-8"
        )
        # Act
        config_mod.config_unset("api_url")
        # Assert
        assert "current_host" not in config_mod.load_config_raw()

    def test_atomic_write_cleans_up_temp_on_failure(self, mesh_env, monkeypatch):
        # Arrange — serialization explodes mid-write.
        import yaml as yaml_mod

        def boom(*args, **kwargs):
            raise ValueError("unserializable")

        monkeypatch.setattr(yaml_mod, "safe_dump", boom)
        # Act / Assert — nothing half-written is left behind.
        with pytest.raises(ValueError):
            config_mod.save_config_raw({"version": 1})
        leftovers = [p for p in mesh_env.iterdir() if p.name.startswith(".config.yaml.")]
        assert leftovers == []

    def test_directory_chmod_failure_is_best_effort(self, mesh_env, monkeypatch):
        # Arrange — a filesystem that refuses chmod must not break the write.
        from pathlib import Path as PathCls

        def bad_chmod(self, mode):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(PathCls, "chmod", bad_chmod)
        # Act
        config_mod.save_credential(BASE, CredentialEntry(kind="pat", token="mesh_pat_x"))
        # Assert
        assert (mesh_env / "credentials.yaml").exists()

    def test_symlinked_credential_dir_refused(self, mesh_env, tmp_path, monkeypatch):
        # Arrange — credentials under a symlinked config dir.
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        (link / "credentials.yaml").write_text("version: 1\nhosts: {}\n", encoding="utf-8")
        monkeypatch.setenv("MESH_CONFIG", str(link))
        # Act / Assert — fail closed, exit 2, actionable message.
        with pytest.raises(CredentialFileError) as exc:
            config_mod.load_credentials_raw()
        assert exc.value.exit_code == EXIT_AUTH
        assert "symlink" in exc.value.message

    def test_credential_file_not_owned_by_user_refused(self, mesh_env):
        # Arrange
        path = mesh_env / "credentials.yaml"
        path.write_text("version: 1\nhosts: {}\n", encoding="utf-8")
        mesh_env.chmod(0o700)
        path.chmod(0o600)
        os.chown(path, 12345, 12345)
        try:
            # Act / Assert
            with pytest.raises(CredentialFileError) as exc:
                config_mod.load_credentials_raw()
            assert "not owned by you" in exc.value.message
        finally:
            os.chown(path, os.getuid(), os.getgid())

    def test_credential_dir_not_owned_by_user_refused(self, mesh_env):
        # Arrange
        (mesh_env / "credentials.yaml").write_text("version: 1\nhosts: {}\n", encoding="utf-8")
        mesh_env.chmod(0o700)
        os.chown(mesh_env, 12345, 12345)
        try:
            # Act / Assert
            with pytest.raises(CredentialFileError) as exc:
                config_mod.load_credentials_raw()
            assert "not owned by you" in exc.value.message
        finally:
            os.chown(mesh_env, os.getuid(), os.getgid())

    def test_credential_dir_stat_failure_refused(self, mesh_env, monkeypatch):
        # Arrange
        (mesh_env / "credentials.yaml").write_text("version: 1\nhosts: {}\n", encoding="utf-8")
        real_lstat = os.lstat

        def flaky(path, *args, **kwargs):
            if str(path).endswith("mesh"):  # the parent directory
                raise OSError("stat denied")
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(os, "lstat", flaky)
        # Act / Assert
        with pytest.raises(CredentialFileError) as exc:
            config_mod.load_credentials_raw()
        assert "cannot stat credential directory" in exc.value.message

    def test_credential_file_stat_failure_refused(self, mesh_env, monkeypatch):
        # Arrange
        (mesh_env / "credentials.yaml").write_text("version: 1\nhosts: {}\n", encoding="utf-8")
        mesh_env.chmod(0o700)
        real_lstat = os.lstat

        def flaky(path, *args, **kwargs):
            if str(path).endswith("credentials.yaml"):
                raise OSError("stat denied")
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(os, "lstat", flaky)
        # Act / Assert
        with pytest.raises(CredentialFileError) as exc:
            config_mod.load_credentials_raw()
        assert "cannot stat credential file" in exc.value.message

    def test_dangling_symlink_store_is_treated_as_absent(self, mesh_env):
        # Arrange — a broken symlink where the store would live: validation
        # sees "not a regular existing file" and the loader reads defaults.
        mesh_env.chmod(0o700)
        (mesh_env / "credentials.yaml").symlink_to(mesh_env / "nonexistent-target")
        # Act
        raw = config_mod.load_credentials_raw()
        # Assert
        assert raw["hosts"] == {}


class TestErrorModelResiduals:
    @respx.mock
    def test_403_interactive_session_required_hints_reauth(self, run_cli):
        # Arrange — a 403 that demands recent active authentication.
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/agents").mock(
            return_value=httpx.Response(403, json={"error": {
                "code": "forbidden",
                "message": "stale session",
                "details": {"reason": "interactive_session_required"},
            }})
        )
        # Act
        result = run_cli(
            ["--workspace", WS_UUID, "agent", "list"],
            credential={"kind": "pat", "token": "mesh_pat_stale"},
        )
        # Assert
        assert result.exit_code == EXIT_AUTH
        assert "reauth" in result.stderr

    def test_cli_error_str_is_the_message(self):
        # Act / Assert
        assert str(CliError("boom", exit_code=1)) == "boom"


class TestDevicePollingBranches:
    """RFC 8628 token-poll responses (cli.md §3.2): pending keeps polling,
    slow_down widens the interval, denied/expired end the flow with the
    auth-exclusive exit code, anything else re-raises untouched."""

    def _code_routes(self, *poll_responses):
        respx.post(f"{BASE}/api/v1/auth/device/code").mock(
            return_value=httpx.Response(200, json={"data": {
                "device_code": "dc", "user_code": "UC", "verification_uri": "/device",
                "interval": 1, "expires_in": 900,
            }})
        )
        route = respx.post(f"{BASE}/api/v1/auth/device/token")
        route.side_effect = list(poll_responses)
        return route

    @staticmethod
    def _poll_error(code: str) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": code, "message": code}}
        )

    @respx.mock
    def test_authorization_pending_keeps_polling_until_success(self, run_cli, mesh_env):
        # Arrange — first poll: pending; second: approved.
        route = self._code_routes(
            self._poll_error("authorization_pending"),
            httpx.Response(200, json={"data": {
                "access_token": "access-p", "refresh_token": "mesh_rft_p", "scope": "",
            }}),
        )
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert
        assert result.exit_code == 0
        assert route.call_count == 2
        assert "Logged in via device authorization" in result.stderr

    @respx.mock
    def test_slow_down_widens_interval_and_continues(self, run_cli):
        # Arrange
        route = self._code_routes(
            self._poll_error("slow_down"),
            httpx.Response(200, json={"data": {
                "access_token": "access-s", "refresh_token": "mesh_rft_s", "scope": "",
            }}),
        )
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert — the flow survived the slow_down and completed.
        assert result.exit_code == 0
        assert route.call_count == 2

    @respx.mock
    def test_access_denied_exits_2(self, run_cli):
        # Arrange
        self._code_routes(self._poll_error("access_denied"))
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert — terminal, auth-exclusive, actionable.
        assert result.exit_code == EXIT_AUTH
        assert "denied" in result.stderr
        assert "mesh auth login" in result.stderr

    @respx.mock
    def test_expired_token_exits_2(self, run_cli):
        # Arrange
        self._code_routes(self._poll_error("expired_token"))
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert
        assert result.exit_code == EXIT_AUTH
        assert "expired" in result.stderr

    @respx.mock
    def test_unknown_poll_error_is_reraised_untouched(self, run_cli):
        # Arrange — an unrecognized code keeps its own exit mapping (400 → 3).
        self._code_routes(self._poll_error("invalid_grant"))
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert
        assert result.exit_code == 3


class TestAgentExecutionsLimit:
    @respx.mock
    def test_executions_limit_param_forwarded(self, run_cli):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/executions").mock(
            return_value=httpx.Response(200, json={"data": [{
                "id": "e-1", "status": "succeeded", "trigger_kind": "manual",
                "created_at": "T0", "finished_at": "T1",
            }]})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "agent", "executions", "ag-1", "--limit", "7"
        ])
        # Assert
        assert result.exit_code == 0
        params = route.calls[0].request.url.params
        assert params["agent_id"] == "ag-1"
        assert params["limit"] == "7"
