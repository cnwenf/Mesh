"""``mesh auth`` — PAT/device login, status, logout, and ``mesh version
--verbose`` (cli.md C1–C5, §5.4 version negotiation)."""

from __future__ import annotations

import io
import json
import sys

import click as click_mod
import httpx
import respx
import yaml

from meshcli import API_VERSION, __version__

BASE = "https://mesh.test"
PAT = {"kind": "pat", "token": "mesh_pat_abc123", "prefix": "mesh_pat_ab"}


def _stored_credential(config_dir) -> dict:
    return yaml.safe_load((config_dir / "credentials.yaml").read_text())["hosts"][BASE]


class _TTYStdin:
    def isatty(self) -> bool:
        return True


class TestPatLogin:
    @respx.mock
    def test_login_with_token_stdin_persists_pat(self, run_cli, mesh_env, monkeypatch):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {"email": "dev@mesh.dev"}})
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO("mesh_pat_stdin1\n"))
        # Act
        result = run_cli(["auth", "login", "--with-token"])
        # Assert — probed with the fresh token, then persisted 0600.
        assert result.exit_code == 0
        assert route.calls[0].request.headers["Authorization"] == "Bearer mesh_pat_stdin1"
        stored = _stored_credential(mesh_env)
        assert stored["kind"] == "pat"
        assert stored["token"] == "mesh_pat_stdin1"
        assert "dev@mesh.dev" in result.stderr
        assert "0600" in result.stderr

    @respx.mock
    def test_login_with_empty_stdin_exits_3(self, run_cli, monkeypatch):
        # Arrange
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
        # Act
        result = run_cli(["auth", "login", "--with-token"])
        # Assert
        assert result.exit_code == 3
        assert "no token" in result.stderr

    @respx.mock
    def test_login_on_a_tty_prompts_for_the_token(self, run_cli, monkeypatch):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {"email": "tty@mesh.dev"}})
        )
        monkeypatch.setattr(sys, "stdin", _TTYStdin())
        monkeypatch.setattr(click_mod, "prompt", lambda *args, **kwargs: "mesh_pat_tty1")
        # Act
        result = run_cli(["auth", "login", "--with-token"])
        # Assert
        assert result.exit_code == 0
        assert route.calls[0].request.headers["Authorization"] == "Bearer mesh_pat_tty1"

    @respx.mock
    def test_login_pat_probe_401_exits_2(self, run_cli, monkeypatch):
        # Arrange
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "revoked"}}
            )
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO("mesh_pat_dead\n"))
        # Act
        result = run_cli(["auth", "login", "--with-token"])
        # Assert
        assert result.exit_code == 2

    @respx.mock
    def test_login_token_file(self, run_cli, mesh_env, tmp_path):
        # Arrange
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {"email": "f@mesh.dev"}})
        )
        token_file = tmp_path / "pat.txt"
        token_file.write_text("mesh_pat_file1\n", encoding="utf-8")
        # Act
        result = run_cli(["auth", "login", "--token-file", str(token_file)])
        # Assert
        assert result.exit_code == 0
        assert _stored_credential(mesh_env)["token"] == "mesh_pat_file1"

    @respx.mock
    def test_login_empty_token_file_exits_3(self, run_cli, tmp_path):
        # Arrange
        token_file = tmp_path / "empty.txt"
        token_file.write_text("   \n", encoding="utf-8")
        # Act
        result = run_cli(["auth", "login", "--token-file", str(token_file)])
        # Assert
        assert result.exit_code == 3
        assert "no token" in result.stderr


class TestDeviceLogin:
    @respx.mock
    def test_device_login_happy_path_adopts_workspace(self, run_cli, mesh_env, monkeypatch):
        # Arrange
        respx.post(f"{BASE}/api/v1/auth/device/code").mock(
            return_value=httpx.Response(200, json={"data": {
                "device_code": "dc-secret",
                "user_code": "ABCD-1234",
                "verification_uri": "/device",
                "interval": 1,
                "expires_in": 900,
            }})
        )
        respx.post(f"{BASE}/api/v1/auth/device/token").mock(
            return_value=httpx.Response(200, json={"data": {
                "access_token": "access-1",
                "refresh_token": "mesh_rft_1",
                "scope": "issue:read issue:write",
                "workspace": {"slug": "acme"},
            }})
        )
        import meshcli.commands.auth as auth_mod

        opened: list[str] = []
        monkeypatch.setattr(auth_mod, "try_open", lambda url: opened.append(url) or True)
        # Act
        result = run_cli(["auth", "login"])
        # Assert — session persisted, approval workspace became the default.
        assert result.exit_code == 0
        stored = _stored_credential(mesh_env)
        assert stored["kind"] == "device_session"
        assert stored["token"] == "access-1"
        assert stored["refresh_token"] == "mesh_rft_1"
        assert stored["scopes"] == ["issue:read", "issue:write"]
        assert stored["workspace"] == "acme"
        config = yaml.safe_load((mesh_env / "config.yaml").read_text())
        assert config["workspace"] == "acme"
        assert "ABCD-1234" in result.stderr
        assert "Logged in via device authorization" in result.stderr
        assert "user_code=ABCD-1234" in opened[0]

    @respx.mock
    def test_device_login_without_workspace_skips_adoption(self, run_cli, mesh_env, monkeypatch):
        # Arrange — approval page did not bind a workspace.
        respx.post(f"{BASE}/api/v1/auth/device/code").mock(
            return_value=httpx.Response(200, json={"data": {
                "device_code": "dc", "user_code": "UC", "verification_uri": "/device",
                "interval": 1, "expires_in": 900,
            }})
        )
        respx.post(f"{BASE}/api/v1/auth/device/token").mock(
            return_value=httpx.Response(200, json={"data": {
                "access_token": "access-2", "refresh_token": "mesh_rft_2", "scope": "",
            }})
        )
        import meshcli.commands.auth as auth_mod

        monkeypatch.setattr(auth_mod, "try_open", lambda url: False)
        # Act
        result = run_cli(["auth", "login"])
        # Assert
        assert result.exit_code == 0
        config = yaml.safe_load((mesh_env / "config.yaml").read_text())
        assert "workspace" not in config

    @respx.mock
    def test_device_login_expired_code_exits_2(self, run_cli, monkeypatch):
        # Arrange — expires_in=0 means the polling loop never runs.
        respx.post(f"{BASE}/api/v1/auth/device/code").mock(
            return_value=httpx.Response(200, json={"data": {
                "device_code": "dc", "user_code": "UC", "verification_uri": "/device",
                "interval": 1, "expires_in": 0,
            }})
        )
        token_route = respx.post(f"{BASE}/api/v1/auth/device/token")
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert
        assert result.exit_code == 2
        assert token_route.call_count == 0
        assert "mesh auth login" in result.stderr  # actionable hint

    @respx.mock
    def test_device_login_401_on_grant_request_exits_2(self, run_cli):
        # Arrange — a server-side auth failure on the code request.
        respx.post(f"{BASE}/api/v1/auth/device/code").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "disabled"}}
            )
        )
        # Act
        result = run_cli(["auth", "login", "--no-browser"])
        # Assert
        assert result.exit_code == 2


class TestStatus:
    @respx.mock
    def test_status_not_authenticated_exits_2(self, run_cli):
        # Act
        result = run_cli(["auth", "status"])
        # Assert
        assert result.exit_code == 2
        assert "not authenticated" in result.stderr

    @respx.mock
    def test_status_table_writes_everything_to_stderr(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(200, json={"data": {
                "kind": "pat",
                "prefix": "mesh_pat_ab",
                "name": "ci token",
                "scopes": ["issue:read"],
                "expires_at": "2027-01-01T00:00:00Z",
                "last_used_at": "2026-07-01T00:00:00Z",
            }})
        )
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                200, json={"data": {"email": "dev@mesh.dev", "display_name": "Dev"}}
            )
        )
        # Act
        result = run_cli(["auth", "status"], credential=PAT)
        # Assert — human output stays off stdout entirely.
        assert result.exit_code == 0
        assert result.output == ""
        assert "dev@mesh.dev" in result.stderr
        assert "issue:read" in result.stderr
        assert "2027-01-01" in result.stderr
        assert "2026-07-01" in result.stderr
        assert BASE in result.stderr

    @respx.mock
    def test_status_json_emits_exactly_one_document(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(200, json={"data": {"kind": "pat"}})
        )
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {"email": "j@mesh.dev"}})
        )
        # Act
        result = run_cli(["--output", "json", "auth", "status"], credential=PAT)
        # Assert
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["data"]["authenticated"] is True
        assert payload["data"]["api_url"] == BASE
        assert payload["data"]["api_version"] == API_VERSION
        assert payload["data"]["user"]["email"] == "j@mesh.dev"

    @respx.mock
    def test_status_invalid_credential_exits_2(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "expired"}}
            )
        )
        # Act
        result = run_cli(["auth", "status"], credential=PAT)
        # Assert
        assert result.exit_code == 2
        assert "invalid or expired" in result.stderr

    @respx.mock
    def test_status_server_error_propagates_exit_1(self, run_cli):
        # Arrange — a non-auth failure is NOT swallowed as "not authenticated".
        respx.get(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(
                500, json={"error": {"code": "internal_error", "message": "x"}}
            )
        )
        # Act
        result = run_cli(["auth", "status"], credential=PAT)
        # Assert
        assert result.exit_code == 1


class TestLogout:
    @respx.mock
    def test_logout_when_not_logged_in_is_a_noop(self, run_cli):
        # Act
        result = run_cli(["auth", "logout"])
        # Assert
        assert result.exit_code == 0
        assert "Not logged in" in result.stderr

    @respx.mock
    def test_logout_pat_clears_locally_only(self, run_cli, mesh_env):
        # Arrange
        delete_route = respx.delete(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        # Act — PAT logout without --revoke never touches the server.
        result = run_cli(["auth", "logout"], credential=PAT)
        # Assert
        assert result.exit_code == 0
        assert delete_route.call_count == 0
        hosts = yaml.safe_load((mesh_env / "credentials.yaml").read_text())["hosts"]
        assert BASE not in hosts
        assert "remains valid server-side" in result.stderr

    @respx.mock
    def test_logout_pat_revoke_with_yes_revokes_server_side(self, run_cli, mesh_env):
        # Arrange
        route = respx.delete(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        # Act
        result = run_cli(["--yes", "auth", "logout", "--revoke"], credential=PAT)
        # Assert
        assert result.exit_code == 0
        assert route.call_count == 1
        hosts = yaml.safe_load((mesh_env / "credentials.yaml").read_text())["hosts"]
        assert BASE not in hosts

    @respx.mock
    def test_logout_revoke_without_yes_on_non_tty_exits_3(self, run_cli):
        # Arrange — pytest's stdin is not a TTY: confirmation must fail hard.
        delete_route = respx.delete(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        # Act
        result = run_cli(["auth", "logout", "--revoke"], credential=PAT)
        # Assert
        assert result.exit_code == 3
        assert delete_route.call_count == 0
        assert "--yes" in result.stderr

    @respx.mock
    def test_logout_device_session_revokes_and_clears(self, run_cli, mesh_env):
        # Arrange
        route = respx.delete(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        device = {"kind": "device_session", "token": "access-1", "refresh_token": "mesh_rft_1"}
        # Act
        result = run_cli(["auth", "logout"], credential=device)
        # Assert
        assert result.exit_code == 0
        assert route.call_count == 1
        hosts = yaml.safe_load((mesh_env / "credentials.yaml").read_text())["hosts"]
        assert BASE not in hosts

    @respx.mock
    def test_logout_revoke_when_server_already_invalid_still_clears(self, run_cli, mesh_env):
        # Arrange
        respx.delete(f"{BASE}/api/v1/auth/token").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "gone"}}
            )
        )
        # Act — already-dead credential is not an error, just a local clear.
        result = run_cli(["--yes", "auth", "logout", "--revoke"], credential=PAT)
        # Assert
        assert result.exit_code == 0
        assert "already invalid" in result.stderr
        hosts = yaml.safe_load((mesh_env / "credentials.yaml").read_text())["hosts"]
        assert BASE not in hosts


class TestVersion:
    @respx.mock
    def test_version_json_without_verbose(self, run_cli):
        # Act
        result = run_cli(["--output", "json", "version"])
        # Assert
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["data"]["cli_version"] == __version__
        assert payload["data"]["api_version"] == API_VERSION
        assert "server" not in payload["data"]

    @respx.mock
    def test_version_verbose_server_reachable_table(self, run_cli):
        # Arrange — the OpenAPI contract carries the server version; a
        # Deprecation header on the probe must surface the upgrade warning.
        respx.get(f"{BASE}/openapi.json").mock(
            return_value=httpx.Response(
                200, json={"info": {"version": "1.2.3"}}, headers={"Deprecation": "true"}
            )
        )
        # Act
        result = run_cli(["version", "--verbose"])
        # Assert — verbose human output stays on stderr; exit stays 0.
        assert result.exit_code == 0
        assert result.output == ""
        assert f"mesh {__version__} (API {API_VERSION})" in result.stderr
        assert "python" in result.stderr
        assert f"api-url {BASE}" in result.stderr
        assert "server reachable — API version 1.2.3" in result.stderr
        assert "deprecated" in result.stderr

    @respx.mock
    def test_version_verbose_server_reachable_json_shape(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/openapi.json").mock(
            return_value=httpx.Response(200, json={"info": {"version": "1.2.3"}})
        )
        # Act
        result = run_cli(["--output", "json", "version", "--verbose"])
        # Assert
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["server"] == {"reachable": True, "api_version": "1.2.3"}
        assert data["api_url"] == BASE
        assert data["python"]
        assert data["platform"]

    @respx.mock
    def test_version_verbose_server_unreachable_table_still_exits_0(self, run_cli):
        # Arrange — the probe is informational: unreachable is NOT a failure.
        respx.get(f"{BASE}/openapi.json").mock(side_effect=httpx.ConnectError("refused"))
        # Act
        result = run_cli(["version", "--verbose"])
        # Assert
        assert result.exit_code == 0
        assert "server unreachable" in result.stderr

    @respx.mock
    def test_version_verbose_server_unreachable_json_shape(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/openapi.json").mock(side_effect=httpx.ConnectError("refused"))
        # Act
        result = run_cli(["--output", "json", "version", "--verbose"])
        # Assert
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["server"] == {"reachable": False}
