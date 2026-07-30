"""``mesh export`` / ``mesh import`` — data-jobs (cli.md C13/C14, import-export.md).

Export: create job → poll to terminal → STREAM the product to disk (memory
does not grow with row count; progress on stderr).
Import: three-stage attachment upload → create job → validate (--dry-run
stops here, printing the mapping preview + row errors) → run;
``completed_with_errors`` exits 0 by default (partial success) and 3 under
``--strict``; running before validation → ``validation_required`` → exit 3.
"""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path

import click
import httpx

from meshcli.context import AppContext, get_context
from meshcli.errors import EXIT_GENERIC, EXIT_VALIDATION, CliError
from meshcli.main import cli as root
from meshcli.output import emit_json, stderr

POLL_INTERVAL_SECONDS = 1.0
TERMINAL_STATUSES = frozenset({"completed", "completed_with_errors", "failed"})


def _wait_terminal(app: AppContext, job_id: str) -> dict:
    """Poll GET /data-jobs/{id} until terminal; progress on stderr."""
    while True:
        envelope = app.call("GET", f"/api/v1/data-jobs/{job_id}")
        data = envelope.get("data", {})
        status = data.get("status")
        app.progress(f"  job {job_id}: {status}")
        if status in TERMINAL_STATUSES:
            return data
        time.sleep(POLL_INTERVAL_SECONDS)


def _stream_download(app: AppContext, job_id: str, out_path: str) -> int:
    """Stream the product to ``out_path``; returns bytes written."""
    response = app.client.stream_request("GET", f"/api/v1/data-jobs/{job_id}/download")
    written = 0
    try:
        with open(out_path, "wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
                written += len(chunk)
                app.progress(f"  downloaded {written} bytes")
    finally:
        response.close()
    return written


def _upload_source_file(app: AppContext, workspace_id: str, file_path: Path) -> str:
    """Three-stage attachment upload (attachment.md §3) → attachment id."""
    size = file_path.stat().st_size
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    requested = app.call(
        "POST",
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": file_path.name,
            "file_size": size,
            "content_type": content_type,
        },
    ).get("data", {})
    attachment_id = requested["id"]
    upload_url = requested.get("upload_url") or requested.get("presigned_url")
    method = (requested.get("upload_method") or "PUT").upper()
    if not upload_url:
        raise CliError(
            "server did not return a direct-upload URL", exit_code=EXIT_GENERIC
        )
    with open(file_path, "rb") as handle:
        body = handle.read()
    response = httpx.request(method, upload_url, content=body, timeout=120)
    if response.status_code >= 400:
        raise CliError(
            f"direct upload failed with HTTP {response.status_code}",
            exit_code=EXIT_GENERIC,
        )
    app.call("POST", f"/api/v1/attachments/{attachment_id}/complete", json={})
    return attachment_id


@root.command("export")
@click.argument("entity", type=click.Choice(["issues"]))
@click.option("--project", "project_id", default=None, help="Scope to a project.")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv",
              show_default=True)
@click.option("-o", "--output-file", required=True, type=click.Path(dir_okay=False),
              help="Destination file (streamed).")
@click.pass_context
def export(ctx, entity, project_id, fmt, output_file):
    """Export entities to a file (streamed; memory-flat for large sets).

    \b
    Examples:
      mesh export issues -o issues.csv
      mesh export issues --project <id> --format json -o issues.json
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    body = {
        "workspace_id": ws,
        "entity_type": entity,
        "format": fmt,
        "scope": "project" if project_id else "workspace",
    }
    if project_id:
        body["project_id"] = project_id
    job = app.call("POST", "/api/v1/data-jobs/export", json=body).get("data", {})
    job_id = job["id"]
    app.progress(f"export job created: {job_id}")
    final = _wait_terminal(app, job_id)
    if final.get("status") == "failed":
        raise CliError(
            final.get("error_message", "export job failed"), exit_code=EXIT_GENERIC
        )
    written = _stream_download(app, job_id, output_file)
    stderr(f"✓ Wrote {written} bytes to {output_file}")


@root.command("import")
@click.argument("entity", type=click.Choice(["issues"]))
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, default=False,
              help="Validate only: mapping preview + row errors; no writes.")
@click.option("--strict", is_flag=True, default=False,
              help="Exit 3 on completed_with_errors (zero tolerance for partial success).")
@click.option("--project", "target_project_id", default=None)
@click.option("--yes", is_flag=True, default=False, help="Skip the run confirmation.")
@click.pass_context
def import_(ctx, entity, file_path, dry_run, strict, target_project_id, yes):
    """Import entities from a file (validate → run).

    \b
    Examples:
      mesh import issues --file rows.csv --dry-run     # validate only (exit 0)
      mesh import issues --file rows.csv               # validate + run
      mesh import issues --file rows.csv --strict      # partial success → exit 3
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    path = Path(file_path)
    fmt = "json" if path.suffix.lower() == ".json" else "csv"
    app.progress(f"uploading {path.name} …")
    attachment_id = _upload_source_file(app, ws, path)

    body = {
        "workspace_id": ws,
        "entity_type": entity,
        "format": fmt,
        "source_attachment_id": attachment_id,
        "auto_infer": True,
    }
    if target_project_id:
        body["target_project_id"] = target_project_id
    job = app.call("POST", "/api/v1/data-jobs/import", json=body).get("data", {})
    job_id = job["id"]
    app.progress(f"import job created: {job_id} — validating …")

    validated = app.call("POST", f"/api/v1/data-jobs/import/{job_id}/validate").get("data", {})
    errors = validated.get("errors") or validated.get("row_errors") or []
    preview = {
        "job_id": job_id,
        "status": validated.get("status"),
        "mapping": validated.get("mapping"),
        "row_count": validated.get("row_count"),
        "errors": errors,
    }
    if app.output == "json":
        emit_json({"data": preview})
    else:
        stderr(f"mapping: {preview['mapping']}")
        stderr(f"rows: {preview['row_count']}")
        for row_error in errors:
            stderr(f"  row error: {row_error}")

    if dry_run:
        stderr("dry run — no data written.")
        return

    if yes:
        app.yes = True
    app.confirm(f"Run import job {job_id}?")
    app.call("POST", f"/api/v1/data-jobs/import/{job_id}/run")
    final = _wait_terminal(app, job_id)
    status = final.get("status")
    if status == "failed":
        raise CliError(
            final.get("error_message", "import job failed"), exit_code=EXIT_GENERIC
        )
    if status == "completed_with_errors":
        report_url = final.get("download_url")
        stderr(
            f"⚠ import completed with errors (job {job_id});"
            + (f" error report: {report_url}" if report_url else "")
        )
        if strict:
            raise CliError(
                "import completed with errors and --strict is set",
                exit_code=EXIT_VALIDATION,
            )
        return
    stderr(f"✓ import complete (job {job_id})")
