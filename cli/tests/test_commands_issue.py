"""``mesh issue`` — list/get/create/update/status/comment/children/
dependencies (cli.md C7/C17/C20/C24/C25, §3.1 mapping table)."""

from __future__ import annotations

import json

import httpx
import respx

BASE = "https://mesh.test"
WS_UUID = "11111111-1111-1111-1111-111111111111"
ISSUE_UUID = "22222222-2222-2222-2222-222222222222"
DEP_UUID = "33333333-3333-3333-3333-333333333333"


def _issue(identifier: str = "MES-1", **overrides) -> dict:
    row = {
        "id": ISSUE_UUID,
        "identifier": identifier,
        "title": "First issue",
        "status": "todo",
        "priority": "high",
        "assignee": None,
        "updated_at": "2026-07-29T00:00:00Z",
    }
    row.update(overrides)
    return row


class TestIssueList:
    @respx.mock
    def test_issue_list_table(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(200, json={"data": [_issue()]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "list"])
        # Assert
        assert result.exit_code == 0
        assert "IDENTIFIER" in result.output
        assert "MES-1" in result.output

    @respx.mock
    def test_issue_list_forwards_filter_params(self, run_cli):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "list",
            "--limit", "20", "--status", "in_progress",
            "--priority", "high", "--assignee", "m-1",
        ])
        # Assert
        assert result.exit_code == 0
        params = route.calls[0].request.url.params
        assert params["limit"] == "20"
        assert params["status"] == "in_progress"
        assert params["priority"] == "high"
        assert params["assignee_id"] == "m-1"

    @respx.mock
    def test_issue_list_all_merges_pages(self, run_cli):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues")
        route.side_effect = [
            httpx.Response(200, json={"data": [_issue("MES-1")], "next_cursor": "c1"}),
            httpx.Response(200, json={"data": [_issue("MES-2")]}),
        ]
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "list", "--all"])
        # Assert
        assert result.exit_code == 0
        assert "MES-1" in result.output and "MES-2" in result.output
        assert route.calls[1].request.url.params["cursor"] == "c1"

    @respx.mock
    def test_issue_list_json_is_exactly_one_document(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(200, json={"data": [_issue()]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "--output", "json", "issue", "list"])
        # Assert
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["data"][0]["identifier"] == "MES-1"

    @respx.mock
    def test_issue_list_jq_emits_json_lines(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(
                200, json={"data": [_issue("MES-1"), _issue("MES-2")]}
            )
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "--output", "json", "--jq", ".[].identifier",
            "issue", "list",
        ])
        # Assert
        assert result.exit_code == 0
        assert result.output.splitlines() == ['"MES-1"', '"MES-2"']

    @respx.mock
    def test_issue_list_jq_with_table_output_exits_3(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        # Act — --jq without --output json is a usage error.
        result = run_cli(["--workspace", WS_UUID, "--jq", ".[].identifier", "issue", "list"])
        # Assert
        assert result.exit_code == 3
        assert "--output json" in result.stderr

    @respx.mock
    def test_issue_list_401_exits_2(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "expired"}}
            )
        )
        # Act
        result = run_cli(
            ["--workspace", WS_UUID, "issue", "list"],
            credential={"kind": "pat", "token": "mesh_pat_dead"},
        )
        # Assert
        assert result.exit_code == 2

    @respx.mock
    def test_issue_list_403_without_scope_detail_hints_roles(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(
                403, json={"error": {"code": "forbidden", "message": "nope"}}
            )
        )
        # Act
        result = run_cli(
            ["--workspace", WS_UUID, "issue", "list"],
            credential={"kind": "pat", "token": "mesh_pat_low"},
        )
        # Assert
        assert result.exit_code == 2
        assert "role or token scopes" in result.stderr

    def test_issue_list_without_workspace_exits_3(self, run_cli):
        # Act — no flag, no config, no credential-bound workspace.
        result = run_cli(["issue", "list"])
        # Assert
        assert result.exit_code == 3
        assert "no workspace resolved" in result.stderr


class TestIssueGet:
    @respx.mock
    def test_issue_get_by_uuid(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(200, json={"data": _issue()})
        )
        # Act — a UUID needs no workspace resolution.
        result = run_cli(["--workspace", WS_UUID, "issue", "get", ISSUE_UUID])
        # Assert
        assert result.exit_code == 0
        assert "MES-1" in result.output

    @respx.mock
    def test_issue_get_404_exits_3(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(
                404, json={"error": {"code": "not_found", "message": "missing"}}
            )
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "get", ISSUE_UUID])
        # Assert
        assert result.exit_code == 3

    @respx.mock
    def test_issue_get_by_identifier_resolves_via_workspace(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues/by-identifier/MES-42").mock(
            return_value=httpx.Response(200, json={"data": {"id": ISSUE_UUID}})
        )
        get_route = respx.get(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(200, json={"data": _issue("MES-42")})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "get", "MES-42"])
        # Assert
        assert result.exit_code == 0
        assert "MES-42" in result.output
        assert get_route.call_count == 1

    @respx.mock
    def test_issue_get_unknown_identifier_exits_3(self, run_cli):
        # Arrange — the by-identifier route answers but carries no id.
        respx.get(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues/by-identifier/MES-0").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "get", "MES-0"])
        # Assert
        assert result.exit_code == 3
        assert "not found" in result.stderr

    @respx.mock
    def test_issue_get_web_opens_browser_without_data_request(self, run_cli, monkeypatch):
        # Arrange
        data_route = respx.get(f"{BASE}/api/v1/issues/{ISSUE_UUID}")
        import meshcli.commands.issue as issue_mod

        opened: list[str] = []
        monkeypatch.setattr(issue_mod, "try_open", lambda url: opened.append(url) or True)
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "get", "MES-42", "--web"])
        # Assert — no data fetched, deep link opened, stdout stays empty.
        assert result.exit_code == 0
        assert result.output == ""
        assert data_route.call_count == 0
        assert f"/w/{WS_UUID}/issues/by-identifier/MES-42" in opened[0]

    @respx.mock
    def test_issue_get_web_fallback_prints_manual_url(self, run_cli, monkeypatch):
        # Arrange — headless box: neither launcher works.
        import meshcli.commands.issue as issue_mod

        monkeypatch.setattr(issue_mod, "try_open", lambda url: False)
        monkeypatch.setattr(issue_mod.webbrowser, "open", lambda url: False)
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "get", "MES-42", "--web"])
        # Assert
        assert result.exit_code == 0
        assert "Could not open a browser" in result.stderr
        assert f"{BASE}/w/{WS_UUID}/issues/by-identifier/MES-42" in result.stderr


class TestIssueCreate:
    @respx.mock
    def test_issue_create_posts_fields_and_idempotency_key(self, run_cli):
        # Arrange
        route = respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(201, json={"data": _issue()})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "create",
            "--title", "Fix login", "--priority", "high",
            "--assignee", "m-1", "--project", "p-1",
        ])
        # Assert
        assert result.exit_code == 0
        request = route.calls[0].request
        body = json.loads(request.content)
        assert body == {
            "title": "Fix login",
            "priority": "high",
            "assignee_id": "m-1",
            "project_id": "p-1",
        }
        assert request.headers["Idempotency-Key"]  # auto-generated

    @respx.mock
    def test_issue_create_custom_idempotency_key(self, run_cli):
        # Arrange
        route = respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(201, json={"data": _issue()})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "create",
            "--title", "T", "--idempotency-key", "k-1",
        ])
        # Assert
        assert result.exit_code == 0
        assert route.calls[0].request.headers["Idempotency-Key"] == "k-1"

    @respx.mock
    def test_issue_create_reads_description_file(self, run_cli, tmp_path):
        # Arrange
        body_file = tmp_path / "body.md"
        body_file.write_text("long body text", encoding="utf-8")
        route = respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(201, json={"data": _issue()})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "create",
            "--title", "Spec", "--description-file", str(body_file),
        ])
        # Assert
        assert result.exit_code == 0
        assert json.loads(route.calls[0].request.content)["description"] == "long body text"

    @respx.mock
    def test_issue_create_json_output_is_the_envelope(self, run_cli):
        # Arrange
        respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(201, json={"data": _issue()})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "--output", "json", "issue", "create", "--title", "T"
        ])
        # Assert
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["identifier"] == "MES-1"

    def test_issue_create_missing_title_is_usage_error(self, run_cli):
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "create"])
        # Assert
        assert result.exit_code == 3

    @respx.mock
    def test_issue_create_422_exits_3(self, run_cli):
        # Arrange
        respx.post(f"{BASE}/api/v1/workspaces/{WS_UUID}/issues").mock(
            return_value=httpx.Response(422, json={
                "error": {"code": "invalid_request", "message": "title too long"}
            })
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "create", "--title", "T"])
        # Assert
        assert result.exit_code == 3


class TestIssueUpdate:
    @respx.mock
    def test_issue_update_patches_fields(self, run_cli):
        # Arrange
        route = respx.patch(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(200, json={"data": _issue(priority="urgent")})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "update", ISSUE_UUID,
            "--title", "New", "--priority", "urgent", "--assignee", "m-2",
        ])
        # Assert
        assert result.exit_code == 0
        assert json.loads(route.calls[0].request.content) == {
            "title": "New", "priority": "urgent", "assignee_id": "m-2",
        }

    @respx.mock
    def test_issue_update_sends_if_match_with_version(self, run_cli):
        # Arrange
        route = respx.patch(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(200, json={"data": _issue()})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "update", ISSUE_UUID,
            "--title", "New", "--version", "7",
        ])
        # Assert
        assert result.exit_code == 0
        assert route.calls[0].request.headers["If-Match"] == "7"

    @respx.mock
    def test_issue_update_conflict_exits_4(self, run_cli):
        # Arrange — stale If-Match version.
        respx.patch(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(409, json={
                "error": {"code": "conflict", "message": "version mismatch"}
            })
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "update", ISSUE_UUID,
            "--title", "New", "--version", "6",
        ])
        # Assert
        assert result.exit_code == 4
        assert "Re-fetch" in result.stderr

    def test_issue_update_no_fields_exits_3(self, run_cli):
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "update", ISSUE_UUID])
        # Assert
        assert result.exit_code == 3
        assert "nothing to update" in result.stderr

    @respx.mock
    def test_issue_update_reads_description_file(self, run_cli, tmp_path):
        # Arrange
        body_file = tmp_path / "desc.md"
        body_file.write_text("new description", encoding="utf-8")
        route = respx.patch(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(200, json={"data": _issue()})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "update", ISSUE_UUID,
            "--description-file", str(body_file),
        ])
        # Assert
        assert result.exit_code == 0
        assert json.loads(route.calls[0].request.content)["description"] == "new description"


class TestIssueStatus:
    @respx.mock
    def test_issue_status_patches_status(self, run_cli):
        # Arrange
        route = respx.patch(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(
                200, json={"data": _issue(status="in_progress")}
            )
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "status", ISSUE_UUID, "in_progress"])
        # Assert
        assert result.exit_code == 0
        assert json.loads(route.calls[0].request.content) == {"status": "in_progress"}

    @respx.mock
    def test_issue_status_422_invalid_transition_exits_3(self, run_cli):
        # Arrange
        respx.patch(f"{BASE}/api/v1/issues/{ISSUE_UUID}").mock(
            return_value=httpx.Response(422, json={
                "error": {"code": "invalid_transition", "message": "cannot reopen"}
            })
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "status", ISSUE_UUID, "todo"])
        # Assert
        assert result.exit_code == 3


class TestIssueComment:
    @respx.mock
    def test_issue_comment_inline_content(self, run_cli):
        # Arrange
        route = respx.post(f"{BASE}/api/v1/issues/{ISSUE_UUID}/comments").mock(
            return_value=httpx.Response(201, json={"data": {
                "id": "c-1", "body_markdown": "shipped", "created_at": "T",
            }})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "comment", ISSUE_UUID, "--content", "shipped"
        ])
        # Assert
        assert result.exit_code == 0
        body = json.loads(route.calls[0].request.content)
        assert body == {"body_markdown": "shipped"}
        assert route.calls[0].request.headers["Idempotency-Key"]

    @respx.mock
    def test_issue_comment_content_file(self, run_cli, tmp_path):
        # Arrange
        body_file = tmp_path / "review.md"
        body_file.write_text("LGTM with nits", encoding="utf-8")
        route = respx.post(f"{BASE}/api/v1/issues/{ISSUE_UUID}/comments").mock(
            return_value=httpx.Response(201, json={"data": {"id": "c-2"}})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "comment", ISSUE_UUID,
            "--content-file", str(body_file),
        ])
        # Assert
        assert result.exit_code == 0
        assert json.loads(route.calls[0].request.content)["body_markdown"] == "LGTM with nits"

    def test_issue_comment_without_body_exits_3(self, run_cli):
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "comment", ISSUE_UUID])
        # Assert
        assert result.exit_code == 3
        assert "--content" in result.stderr


class TestIssueChildrenAndDependencies:
    @respx.mock
    def test_issue_children_lists_sub_issues(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/issues/{ISSUE_UUID}/children").mock(
            return_value=httpx.Response(200, json={"data": [_issue("MES-11")]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "children", ISSUE_UUID])
        # Assert
        assert result.exit_code == 0
        assert "MES-11" in result.output

    @respx.mock
    def test_issue_dependencies_list(self, run_cli):
        # Arrange
        respx.get(f"{BASE}/api/v1/issues/{ISSUE_UUID}/dependencies").mock(
            return_value=httpx.Response(200, json={"data": [_issue("MES-9")]})
        )
        # Act
        result = run_cli(["--workspace", WS_UUID, "issue", "dependencies", ISSUE_UUID])
        # Assert
        assert result.exit_code == 0
        assert "MES-9" in result.output

    @respx.mock
    def test_issue_dependencies_add_posts_link(self, run_cli):
        # Arrange
        route = respx.post(f"{BASE}/api/v1/issues/{ISSUE_UUID}/dependencies").mock(
            return_value=httpx.Response(201, json={"data": _issue("MES-9")})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "issue", "dependencies", ISSUE_UUID, "--add", DEP_UUID
        ])
        # Assert
        assert result.exit_code == 0
        assert json.loads(route.calls[0].request.content) == {"depends_on_issue_id": DEP_UUID}
