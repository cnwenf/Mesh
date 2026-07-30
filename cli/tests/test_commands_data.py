"""``mesh export`` / ``mesh import`` — data jobs: create → poll → stream the
product; three-stage upload → validate → run, strict/lenient partial success
(cli.md C13/C14, import-export.md)."""

from __future__ import annotations

import json

import httpx
import respx

BASE = "https://mesh.test"
WS_UUID = "11111111-1111-1111-1111-111111111111"
CSV_BYTES = b"id,title\n1,hello\n"


def _export_routes(status_sequence, *, download_headers=None):
    respx.post(f"{BASE}/api/v1/data-jobs/export").mock(
        return_value=httpx.Response(200, json={"data": {"id": "job-1"}})
    )
    job = respx.get(f"{BASE}/api/v1/data-jobs/job-1")
    job.side_effect = [
        httpx.Response(200, json={"data": status}) for status in status_sequence
    ]
    return respx.get(f"{BASE}/api/v1/data-jobs/job-1/download").mock(
        return_value=httpx.Response(
            200, content=CSV_BYTES, headers=download_headers or {}
        )
    )


def _import_upload_routes(upload_data):
    """Register the upload-request + direct PUT routes; returns both."""
    request_route = respx.post(f"{BASE}/api/v1/attachments/upload-requests").mock(
        return_value=httpx.Response(200, json={"data": upload_data})
    )
    put_route = respx.put("https://upload.test/att-1").mock(
        return_value=httpx.Response(200)
    )
    return request_route, put_route


def _import_job_routes(validate_data, *, terminal_status="completed", error_message=None):
    """Register complete/create/validate/run/poll routes; returns (create, run)."""
    respx.post(f"{BASE}/api/v1/attachments/att-1/complete").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    create_route = respx.post(f"{BASE}/api/v1/data-jobs/import").mock(
        return_value=httpx.Response(200, json={"data": {"id": "job-2"}})
    )
    respx.post(f"{BASE}/api/v1/data-jobs/import/job-2/validate").mock(
        return_value=httpx.Response(200, json={"data": validate_data})
    )
    run_route = respx.post(f"{BASE}/api/v1/data-jobs/import/job-2/run").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    terminal = {"status": terminal_status}
    if error_message:
        terminal["error_message"] = error_message
    respx.get(f"{BASE}/api/v1/data-jobs/job-2").mock(
        return_value=httpx.Response(200, json={"data": terminal})
    )
    return create_route, run_route


VALIDATED = {
    "status": "validated",
    "mapping": {"title": "title"},
    "row_count": 1,
    "row_errors": [{"row": 2, "error": "bad status"}],
}


class TestExport:
    @respx.mock
    def test_export_polls_then_streams_to_file(self, run_cli, tmp_path):
        # Arrange
        _export_routes([{"status": "running"}, {"status": "completed"}])
        out = tmp_path / "issues.csv"
        # Act
        result = run_cli(["--workspace", WS_UUID, "export", "issues", "-o", str(out)])
        # Assert — product streamed verbatim; progress on stderr only.
        assert result.exit_code == 0
        assert out.read_bytes() == CSV_BYTES
        assert "export job created: job-1" in result.stderr
        assert "job job-1: running" in result.stderr
        assert f"Wrote {len(CSV_BYTES)} bytes to {out}" in result.stderr

    @respx.mock
    def test_export_request_body_carries_scope_and_format(self, run_cli, tmp_path):
        # Arrange
        create = respx.post(f"{BASE}/api/v1/data-jobs/export").mock(
            return_value=httpx.Response(200, json={"data": {"id": "job-1"}})
        )
        respx.get(f"{BASE}/api/v1/data-jobs/job-1").mock(
            return_value=httpx.Response(200, json={"data": {"status": "completed"}})
        )
        respx.get(f"{BASE}/api/v1/data-jobs/job-1/download").mock(
            return_value=httpx.Response(200, content=CSV_BYTES)
        )
        out = tmp_path / "issues.json"
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "export", "issues",
            "--project", "p-1", "--format", "json", "-o", str(out),
        ])
        # Assert
        assert result.exit_code == 0
        body = json.loads(create.calls[0].request.content)
        assert body == {
            "workspace_id": WS_UUID,
            "entity_type": "issues",
            "format": "json",
            "scope": "project",
            "project_id": "p-1",
        }

    @respx.mock
    def test_export_download_deprecation_header_warns(self, run_cli, tmp_path):
        # Arrange — Deprecation + Sunset on the STREAMING response must warn.
        _export_routes(
            [{"status": "completed"}],
            download_headers={"Deprecation": "true", "Sunset": "Sat, 31 Dec 2026 00:00:00 GMT"},
        )
        out = tmp_path / "issues.csv"
        # Act
        result = run_cli(["--workspace", WS_UUID, "export", "issues", "-o", str(out)])
        # Assert
        assert result.exit_code == 0
        assert "deprecated" in result.stderr
        assert "sunset" in result.stderr

    @respx.mock
    def test_export_failed_job_exits_1_with_server_message(self, run_cli, tmp_path):
        # Arrange
        _export_routes([{"status": "failed", "error_message": "quota exceeded"}])
        out = tmp_path / "issues.csv"
        # Act
        result = run_cli(["--workspace", WS_UUID, "export", "issues", "-o", str(out)])
        # Assert
        assert result.exit_code == 1
        assert "quota exceeded" in result.stderr
        assert not out.exists()

    @respx.mock
    def test_export_auth_failure_exits_2(self, run_cli, tmp_path):
        # Arrange
        respx.post(f"{BASE}/api/v1/data-jobs/export").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )
        # Act
        result = run_cli(
            ["--workspace", WS_UUID, "export", "issues", "-o", str(tmp_path / "x.csv")],
            credential={"kind": "pat", "token": "mesh_pat_dead"},
        )
        # Assert
        assert result.exit_code == 2

    def test_export_missing_output_file_is_usage_error(self, run_cli):
        # Act
        result = run_cli(["--workspace", WS_UUID, "export", "issues"])
        # Assert
        assert result.exit_code == 3

    def test_export_unknown_entity_is_usage_error(self, run_cli, tmp_path):
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "export", "projects", "-o", str(tmp_path / "x.csv")
        ])
        # Assert
        assert result.exit_code == 3


class TestImport:
    @respx.mock
    def test_import_dry_run_validates_and_stops(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1",
                               "upload_method": "PUT"})
        _, run_route = _import_job_routes(VALIDATED)
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues",
            "--file", str(source), "--dry-run",
        ])
        # Assert — validate happened, run never did.
        assert result.exit_code == 0
        assert run_route.call_count == 0
        assert "dry run" in result.stderr
        assert "row error" in result.stderr

    @respx.mock
    def test_import_dry_run_json_preview_is_one_document(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        _import_job_routes(VALIDATED)
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "--output", "json", "import", "issues",
            "--file", str(source), "--dry-run",
        ])
        # Assert
        assert result.exit_code == 0
        preview = json.loads(result.output)["data"]
        assert preview["job_id"] == "job-2"
        assert preview["mapping"] == {"title": "title"}
        assert preview["errors"] == [{"row": 2, "error": "bad status"}]

    @respx.mock
    def test_import_run_completes(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        create, upload = _import_upload_routes(
            {"id": "att-1", "upload_url": "https://upload.test/att-1"}
        )
        _, run_route = _import_job_routes(VALIDATED)
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues",
            "--file", str(source), "--yes",
        ])
        # Assert — the source file was PUT to the direct-upload URL, then run.
        assert result.exit_code == 0
        assert upload.call_count == 1
        assert run_route.call_count == 1
        assert "import complete (job job-2)" in result.stderr
        import_body = json.loads(create.calls[0].request.content)
        assert import_body["file_name"] == "rows.csv"
        assert import_body["content_type"] == "text/csv"

    @respx.mock
    def test_import_run_without_yes_on_non_tty_exits_3(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        _, run_route = _import_job_routes(VALIDATED)
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues", "--file", str(source)
        ])
        # Assert — confirmation gate holds; nothing ran.
        assert result.exit_code == 3
        assert run_route.call_count == 0

    @respx.mock
    def test_import_json_suffix_sends_json_format(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.json"
        source.write_text('[{"title": "one"}]', encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        job_create, _ = _import_job_routes(VALIDATED)
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues",
            "--file", str(source), "--yes", "--project", "p-9",
        ])
        # Assert
        assert result.exit_code == 0
        body = json.loads(job_create.calls[0].request.content)
        assert body["format"] == "json"
        assert body["target_project_id"] == "p-9"
        assert body["source_attachment_id"] == "att-1"

    @respx.mock
    def test_import_completed_with_errors_is_lenient_by_default(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        respx.post(f"{BASE}/api/v1/attachments/att-1/complete").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        respx.post(f"{BASE}/api/v1/data-jobs/import").mock(
            return_value=httpx.Response(200, json={"data": {"id": "job-2"}})
        )
        respx.post(f"{BASE}/api/v1/data-jobs/import/job-2/validate").mock(
            return_value=httpx.Response(200, json={"data": VALIDATED})
        )
        respx.post(f"{BASE}/api/v1/data-jobs/import/job-2/run").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        respx.get(f"{BASE}/api/v1/data-jobs/job-2").mock(
            return_value=httpx.Response(200, json={"data": {
                "status": "completed_with_errors",
                "download_url": "https://mesh.test/report.csv",
            }})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues", "--file", str(source), "--yes"
        ])
        # Assert — partial success exits 0 with a visible warning + report URL.
        assert result.exit_code == 0
        assert "completed with errors" in result.stderr
        assert "https://mesh.test/report.csv" in result.stderr

    @respx.mock
    def test_import_completed_with_errors_strict_exits_3(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        _import_job_routes(VALIDATED, terminal_status="completed_with_errors")
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues",
            "--file", str(source), "--yes", "--strict",
        ])
        # Assert
        assert result.exit_code == 3
        assert "--strict" in result.stderr

    @respx.mock
    def test_import_failed_job_exits_1(self, run_cli, tmp_path):
        # Arrange
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        _import_job_routes(VALIDATED, terminal_status="failed", error_message="db down")
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues", "--file", str(source), "--yes"
        ])
        # Assert
        assert result.exit_code == 1
        assert "db down" in result.stderr

    @respx.mock
    def test_import_run_validation_required_exits_3_with_hint(self, run_cli, tmp_path):
        # Arrange — the run endpoint rejects an unvalidated job.
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        _import_upload_routes({"id": "att-1", "upload_url": "https://upload.test/att-1"})
        respx.post(f"{BASE}/api/v1/attachments/att-1/complete").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        respx.post(f"{BASE}/api/v1/data-jobs/import").mock(
            return_value=httpx.Response(200, json={"data": {"id": "job-2"}})
        )
        respx.post(f"{BASE}/api/v1/data-jobs/import/job-2/validate").mock(
            return_value=httpx.Response(200, json={"data": VALIDATED})
        )
        respx.post(f"{BASE}/api/v1/data-jobs/import/job-2/run").mock(
            return_value=httpx.Response(422, json={
                "error": {"code": "validation_required", "message": "validate first"}
            })
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues", "--file", str(source), "--yes"
        ])
        # Assert
        assert result.exit_code == 3
        assert "validate" in result.stderr

    @respx.mock
    def test_import_upload_without_direct_url_exits_1(self, run_cli, tmp_path):
        # Arrange — server forgot the direct-upload URL.
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        respx.post(f"{BASE}/api/v1/attachments/upload-requests").mock(
            return_value=httpx.Response(200, json={"data": {"id": "att-1"}})
        )
        # Act
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues", "--file", str(source), "--dry-run"
        ])
        # Assert
        assert result.exit_code == 1
        assert "direct-upload URL" in result.stderr

    @respx.mock
    def test_import_direct_upload_failure_exits_1(self, run_cli, tmp_path):
        # Arrange — the presigned PUT itself fails.
        source = tmp_path / "rows.csv"
        source.write_text("title\none\n", encoding="utf-8")
        respx.post(f"{BASE}/api/v1/attachments/upload-requests").mock(
            return_value=httpx.Response(200, json={"data": {
                "id": "att-1", "presigned_url": "https://upload.test/att-1",
            }})
        )
        respx.put("https://upload.test/att-1").mock(
            return_value=httpx.Response(500, content=b"storage down")
        )
        # Act — presigned_url fallback key + default PUT method get us to the PUT.
        result = run_cli([
            "--workspace", WS_UUID, "import", "issues", "--file", str(source), "--dry-run"
        ])
        # Assert
        assert result.exit_code == 1
        assert "direct upload failed with HTTP 500" in result.stderr

    def test_import_missing_file_is_usage_error(self, run_cli):
        # Act
        result = run_cli(["--workspace", WS_UUID, "import", "issues"])
        # Assert
        assert result.exit_code == 3
