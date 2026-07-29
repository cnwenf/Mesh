"""Workspace + platform commands: project/member/agent/execution/runtime,
shell completion, local config, and the best-effort browser launch
(cli.md C8–C15/C22, §3.1 mapping table, §4.2).

Each command is driven through the real entry point (conftest ``run_cli`` /
``run_raw``); all HTTP is respx-mocked and ``--web`` browser launches are
stubbed so nothing leaves the sandbox.
"""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest
import respx

import meshcli.browser as browser_mod
from meshcli.errors import EXIT_INTERRUPTED

BASE = "https://mesh.test"
WS_UUID = "11111111-1111-1111-1111-111111111111"


# --- project ---------------------------------------------------------------------


class TestProject:
    @respx.mock
    def test_list_prints_table(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/projects").mock(
            return_value=httpx.Response(200, json={"data": [
                {"key": "WEB", "name": "Website", "status": "active", "health": "ok"},
            ]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "project", "list"])
        # Assert
        assert result.exit_code == 0
        assert "WEB" in result.output and "Website" in result.output

    @respx.mock
    def test_list_all_follows_cursor(self, run_cli):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/projects")
        route.side_effect = [
            httpx.Response(200, json={"data": [{"key": "A"}], "next_cursor": "c1"}),
            httpx.Response(200, json={"data": [{"key": "B"}]}),
        ]
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json",
                          "project", "list", "--all"])
        # Assert
        assert result.exit_code == 0
        assert [row["key"] for row in json.loads(result.output)["data"]] == ["A", "B"]
        assert route.call_count == 2

    @respx.mock
    def test_get_one(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/projects/p-1").mock(
            return_value=httpx.Response(200, json={"data": {"key": "WEB", "name": "Site"}})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json",
                          "project", "get", "p-1"])
        # Assert
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["key"] == "WEB"

    @respx.mock
    def test_get_web_opens_browser_without_data_call(self, run_cli, monkeypatch):
        # Arrange — stub the browser launch; no API request should happen.
        opened: list[str] = []
        import meshcli.commands.issue as issue_mod

        monkeypatch.setattr(issue_mod, "try_open", lambda url: opened.append(url) or True)
        get_route = respx.get(f"{BASE}/api/v1/projects/p-1")
        # Act
        result = run_cli(["--workspace", WS_UUID, "project", "get", "p-1", "--web"])
        # Assert
        assert result.exit_code == 0
        assert opened == [f"{BASE}/projects/p-1"]
        assert result.output == ""  # --web produces no result data
        assert get_route.call_count == 0

    @respx.mock
    def test_create_sends_body_and_idempotency_key(self, run_cli):
        # Arrange
        route = respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/projects").mock(
            return_value=httpx.Response(200, json={"data": {"key": "WEB", "name": "Site"}})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "project", "create",
                          "--name", "Site", "--key", "WEB", "--description", "d"])
        # Assert
        assert result.exit_code == 0
        request = route.calls[0].request
        assert json.loads(request.content) == {"name": "Site", "key": "WEB", "description": "d"}
        assert request.headers["Idempotency-Key"]

    def test_create_missing_required_flag_is_usage_error(self, run_cli):
        # Act — --key omitted
        result = run_cli(["--workspace", WS_UUID, "project", "create", "--name", "X"])
        # Assert
        assert result.exit_code == 3

    @respx.mock
    def test_list_auth_failure_exits_2(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/projects").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "project", "list"],
                         credential={"kind": "pat", "token": "mesh_pat_dead"})
        # Assert
        assert result.exit_code == 2


# --- member ----------------------------------------------------------------------


class TestMember:
    @respx.mock
    def test_list_roster(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/members").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "m-1", "member_type": "human", "role": "owner", "display": "Ada"},
            ]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "member", "list"])
        # Assert
        assert result.exit_code == 0
        assert "m-1" in result.output and "Ada" in result.output

    @respx.mock
    def test_list_jq_projects_each_row(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/members").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "m-1", "role": "owner"}, {"id": "m-2", "role": "member"},
            ]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json",
                          "--jq", ".[] | .id", "member", "list"])
        # Assert
        assert result.exit_code == 0
        assert result.output.splitlines() == ['"m-1"', '"m-2"']


# --- agent -----------------------------------------------------------------------


class TestAgent:
    @respx.mock
    def test_list_agents(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/agents").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "a-1", "name": "Builder", "role_tag": "dev",
                 "lifecycle_status": "active"},
            ]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "agent", "list"])
        # Assert
        assert result.exit_code == 0
        assert "Builder" in result.output

    @respx.mock
    def test_executions_filters_by_agent(self, run_cli):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/executions").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "e-1", "status": "succeeded", "trigger_kind": "manual"},
            ]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json",
                          "agent", "executions", "a-1"])
        # Assert
        assert result.exit_code == 0
        assert route.calls[0].request.url.params["agent_id"] == "a-1"
        assert json.loads(result.output)["data"][0]["id"] == "e-1"


# --- execution -------------------------------------------------------------------


class TestExecution:
    @respx.mock
    def test_get_one(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/executions/e-1").mock(
            return_value=httpx.Response(200, json={"data": {"id": "e-1", "status": "running"}})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json",
                          "execution", "get", "e-1"])
        # Assert
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["status"] == "running"

    @respx.mock
    def test_get_web_opens_browser(self, run_cli, monkeypatch):
        # Arrange
        opened: list[str] = []
        import meshcli.commands.issue as issue_mod

        monkeypatch.setattr(issue_mod, "try_open", lambda url: opened.append(url) or True)
        # Act
        result = run_cli(["--workspace", WS_UUID, "execution", "get", "e-1", "--web"])
        # Assert
        assert result.exit_code == 0
        assert opened == [f"{BASE}/executions/e-1"]

    @respx.mock
    def test_logs_history_mode_prints_lines(self, run_cli):
        # Arrange — non-follow mode uses the one-shot REST history endpoint.
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/executions/e-1/logs").mock(
            return_value=httpx.Response(200, json={"data": {"lines": [
                {"stream": "stdout", "offset": 0, "line": "hello", "ts": "T0"},
                {"stream": "stderr", "offset": 1, "line": "boom", "ts": "T1"},
            ]}})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "execution", "logs", "e-1"])
        # Assert — history lines go to stdout (the result channel).
        assert result.exit_code == 0
        assert result.output.splitlines() == ["T0 hello", "T1 boom"]

    @respx.mock
    def test_logs_follow_dispatches_to_sse_until_end(self, run_cli, monkeypatch):
        # Arrange — isolate the SSE layer; assert the follow wiring + offset.
        import meshcli.commands.execution as execution_mod

        seen: dict = {}

        def fake_follow(client, *, workspace_id, execution_id, start_offset, timestamps):
            seen.update(workspace_id=workspace_id, execution_id=execution_id,
                        start_offset=start_offset, timestamps=timestamps)
            return 7

        monkeypatch.setattr(execution_mod, "follow_logs", fake_follow)
        # Act
        result = run_cli(["--workspace", WS_UUID, "execution", "logs", "e-1",
                          "--follow", "--offset", "3", "--no-timestamps"])
        # Assert
        assert result.exit_code == 0
        assert seen == {"workspace_id": WS_UUID, "execution_id": "e-1",
                        "start_offset": 3, "timestamps": False}

    @respx.mock
    def test_logs_follow_ctrl_c_exits_130(self, run_cli, monkeypatch):
        # Arrange — a Ctrl-C inside the follow loop maps to exit 130.
        import meshcli.commands.execution as execution_mod

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(execution_mod, "follow_logs", interrupted)
        # Act
        result = run_cli(["--workspace", WS_UUID, "execution", "logs", "e-1", "--follow"])
        # Assert
        assert result.exit_code == EXIT_INTERRUPTED

    @respx.mock
    def test_cancel_with_yes_posts_cancel(self, run_cli):
        # Arrange
        route = respx.post(
            f"{BASE}/api/v1/workspaces/{WS_UUID}/executions/e-1:cancel"
        ).mock(return_value=httpx.Response(200, json={"data": {"id": "e-1", "status": "cancelled"}}))
        # Act
        result = run_cli(["--workspace", WS_UUID, "execution", "cancel", "e-1", "--yes"])
        # Assert
        assert result.exit_code == 0
        assert route.call_count == 1
        assert "cancelled" in result.output

    @respx.mock
    def test_cancel_without_yes_on_non_tty_exits_3(self, run_cli):
        # Arrange
        route = respx.post(
            f"{BASE}/api/v1/workspaces/{WS_UUID}/executions/e-1:cancel"
        ).mock(return_value=httpx.Response(200, json={"data": {}}))
        # Act — no --yes and stdin is not a TTY → confirmation gate holds.
        result = run_cli(["--workspace", WS_UUID, "execution", "cancel", "e-1"])
        # Assert
        assert result.exit_code == 3
        assert route.call_count == 0


# --- runtime ---------------------------------------------------------------------


class TestRuntime:
    @respx.mock
    def test_register_prints_activation_code_to_stdout(self, run_cli):
        # Arrange
        respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/runtimes").mock(
            return_value=httpx.Response(200, json={"data": {
                "id": "rt-1", "name": "ci", "status": "pending",
                "activation_code": "SECRET-CODE",
            }})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "runtime", "register", "--name", "ci"])
        # Assert — the one-time code goes to stdout, never stderr.
        assert result.exit_code == 0
        assert "SECRET-CODE" in result.output
        assert "SECRET-CODE" not in result.stderr
        assert "mesh-runtime activate" in result.stderr

    @respx.mock
    def test_register_writes_activation_file_0600(self, run_cli, tmp_path):
        # Arrange
        respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/runtimes").mock(
            return_value=httpx.Response(200, json={"data": {
                "id": "rt-1", "name": "ci", "activation_code": "FILE-CODE",
            }})
        )
        activation = tmp_path / "act.code"
        # Act
        result = run_cli(["--workspace", WS_UUID, "runtime", "register",
                          "--name", "ci", "--activation-file", str(activation)])
        # Assert — code lands in the 0600 file, not on stdout.
        assert result.exit_code == 0
        assert activation.read_text(encoding="utf-8") == "FILE-CODE\n"
        assert oct(activation.stat().st_mode & 0o777) == oct(0o600)
        assert "FILE-CODE" not in result.output

    @respx.mock
    def test_register_reads_nested_activation_code(self, run_cli):
        # Arrange — some servers nest the code under activation.code.
        respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/runtimes").mock(
            return_value=httpx.Response(200, json={"data": {
                "id": "rt-1", "name": "ci", "activation": {"code": "NESTED"},
            }})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "runtime", "register", "--name", "ci"])
        # Assert
        assert result.exit_code == 0
        assert "NESTED" in result.output

    @respx.mock
    def test_status_reads_console_view(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/runtimes/rt-1").mock(
            return_value=httpx.Response(200, json={"data": {
                "id": "rt-1", "name": "ci", "status": "online",
            }})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json",
                          "runtime", "status", "rt-1"])
        # Assert
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["status"] == "online"

    @respx.mock
    def test_register_conflict_exits_4(self, run_cli):
        # Arrange
        respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/runtimes").mock(
            return_value=httpx.Response(
                409, json={"error": {"code": "conflict", "message": "dup name"}}
            )
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "runtime", "register", "--name", "ci"])
        # Assert
        assert result.exit_code == 4


# --- completion ------------------------------------------------------------------


class TestCompletion:
    @pytest.mark.parametrize("shell,marker", [
        ("bash", "_mesh"),
        ("zsh", "mesh"),
        ("fish", "mesh"),
    ])
    def test_static_script_for_posix_shells(self, run_raw, shell, marker):
        # Act
        result = run_raw(["completion", shell])
        # Assert — a non-empty static script naming the program.
        assert result.exit_code == 0
        assert marker in result.output
        assert len(result.output) > 100

    def test_powershell_script_lists_commands(self, run_raw):
        # Act
        result = run_raw(["completion", "powershell"])
        # Assert — Register-ArgumentCompleter with the live command names.
        assert result.exit_code == 0
        assert "Register-ArgumentCompleter" in result.output
        assert "'issue'" in result.output
        assert "'auth'" in result.output

    def test_unknown_shell_is_usage_error(self, run_raw):
        # Act
        result = run_raw(["completion", "tcsh"])
        # Assert
        assert result.exit_code == 3


# --- config ----------------------------------------------------------------------


class TestConfigCommand:
    def test_set_writes_key_and_confirms(self, run_raw, mesh_env):
        # Act
        result = run_raw(["config", "set", "workspace", "acme"])
        # Assert
        assert result.exit_code == 0
        assert "workspace = acme" in result.stderr
        assert "workspace: acme" in (mesh_env / "config.yaml").read_text(encoding="utf-8")

    def test_set_unknown_key_exits_3(self, run_raw):
        # Act
        result = run_raw(["config", "set", "bogus", "x"])
        # Assert
        assert result.exit_code == 3
        assert "unknown config key" in result.stderr

    def test_set_invalid_output_value_exits_3(self, run_raw):
        # Act
        result = run_raw(["config", "set", "output", "xml"])
        # Assert
        assert result.exit_code == 3

    def test_get_table_reports_source(self, run_raw, mesh_env):
        # Arrange
        (mesh_env / "config.yaml").write_text("version: 1\nworkspace: acme\n", encoding="utf-8")
        # Act
        result = run_raw(["config", "get", "workspace"])
        # Assert
        assert result.exit_code == 0
        assert "workspace = acme" in result.stderr
        assert "source: file" in result.stderr

    def test_get_json_emits_document(self, run_raw):
        # Act
        result = run_raw(["config", "get", "output", "--output", "json"])
        # Assert
        assert result.exit_code == 0
        payload = json.loads(result.output)["data"]
        assert payload["key"] == "output"
        assert payload["value"] == "table"
        assert payload["source"] == "default"

    def test_get_unknown_key_exits_3(self, run_raw):
        # Act
        result = run_raw(["config", "get", "bogus"])
        # Assert
        assert result.exit_code == 3

    def test_unset_removes_key(self, run_raw, mesh_env):
        # Arrange
        (mesh_env / "config.yaml").write_text("version: 1\nworkspace: acme\n", encoding="utf-8")
        # Act
        result = run_raw(["config", "unset", "workspace"])
        # Assert
        assert result.exit_code == 0
        assert "workspace" not in (mesh_env / "config.yaml").read_text(encoding="utf-8")

    def test_list_table_and_json(self, run_raw):
        # Act
        table = run_raw(["config", "list"])
        as_json = run_raw(["config", "list", "--output", "json"])
        # Assert
        assert table.exit_code == 0
        assert "api_url" in table.output
        assert as_json.exit_code == 0
        keys = {row["key"] for row in json.loads(as_json.output)["data"]}
        assert keys == {"api_url", "workspace", "output"}


# --- browser ---------------------------------------------------------------------


class TestBrowser:
    @pytest.mark.parametrize("platform,expected_cmd", [
        ("darwin", "open"),
        ("win32", "cmd"),
        ("linux", "xdg-open"),
    ])
    def test_try_open_dispatches_per_platform(self, monkeypatch, platform, expected_cmd):
        # Arrange
        launched: list[list[str]] = []

        def fake_popen(cmd, **_kwargs):
            launched.append(cmd)
            return object()

        monkeypatch.setattr(browser_mod.sys, "platform", platform)
        monkeypatch.setattr(browser_mod.subprocess, "Popen", fake_popen)
        # Act
        ok = browser_mod.try_open("https://mesh.test/device")
        # Assert
        assert ok is True
        assert launched[0][0] == expected_cmd
        assert "https://mesh.test/device" in launched[0]

    def test_try_open_returns_false_when_launch_fails(self, monkeypatch):
        # Arrange — a headless box with no xdg-open raises OSError.
        def broken_popen(_cmd, **_kwargs):
            raise OSError("no xdg-open")

        monkeypatch.setattr(browser_mod.sys, "platform", "linux")
        monkeypatch.setattr(browser_mod.subprocess, "Popen", broken_popen)
        # Act / Assert
        assert browser_mod.try_open("https://mesh.test") is False

    def test_try_open_returns_false_on_subprocess_error(self, monkeypatch):
        # Arrange
        def broken_popen(_cmd, **_kwargs):
            raise subprocess.SubprocessError("boom")

        monkeypatch.setattr(browser_mod.sys, "platform", "linux")
        monkeypatch.setattr(browser_mod.subprocess, "Popen", broken_popen)
        # Act / Assert
        assert browser_mod.try_open("https://mesh.test") is False
