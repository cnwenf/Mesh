"""``mesh issue`` — list/get/create/update/status/comment/children/dependencies
(cli.md C7/C17/C20/C24/C25, §3.1 mapping table)."""

from __future__ import annotations

import webbrowser

import click

from meshcli.browser import try_open
from meshcli.config import new_request_id
from meshcli.context import get_context
from meshcli.errors import EXIT_VALIDATION, CliError
from meshcli.main import cli as root
from meshcli.output import stderr

ISSUE_COLUMNS = ["identifier", "title", "status", "priority", "assignee", "updated_at"]


@root.group()
def issue():
    """Work with issues."""


def _web_open(app, path: str) -> None:
    """--web: open the canonical deep link; no data request, no output (C25)."""
    url = f"{app.api_url.rstrip('/')}{path}"
    if not try_open(url) and not webbrowser.open(url):
        stderr(f"Could not open a browser — open this URL manually:\n  {url}")


@issue.command("list")
@click.option("--limit", type=int, default=None, help="Page size (default: server default).")
@click.option("--all", "fetch_all", is_flag=True, default=False,
              help="Follow next_cursor and fetch every page.")
@click.option("--status", "status_filter", default=None, help="Filter by status.")
@click.option("--priority", default=None, help="Filter by priority.")
@click.option("--assignee", "assignee_id", default=None, help="Filter by assignee member id.")
@click.pass_context
def list_issues(ctx, limit, fetch_all, status_filter, priority, assignee_id):
    """List issues in the workspace.

    \b
    Examples:
      mesh issue list --all --output json | jq '.[].identifier'
      mesh issue list --status in_progress --limit 20
      mesh issue list --output json --jq '.[] | .identifier'
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    params = {}
    if limit is not None:
        params["limit"] = limit
    if status_filter:
        params["status"] = status_filter
    if priority:
        params["priority"] = priority
    if assignee_id:
        params["assignee_id"] = assignee_id
    if fetch_all:
        envelope = app.call_all("GET", f"/api/v1/workspaces/{ws}/issues", params=params)
    else:
        envelope = app.call("GET", f"/api/v1/workspaces/{ws}/issues", params=params)
    app.emit(envelope, columns=ISSUE_COLUMNS)


@issue.command("get")
@click.argument("issue_id")
@click.option("--web", is_flag=True, default=False,
              help="Open the issue in the browser instead of printing it.")
@click.pass_context
def get_issue(ctx, issue_id, web):
    """Show one issue (by UUID or by identifier like MES-42).

    Examples:
      mesh issue get MES-42
      mesh issue get MES-42 --output json --jq '.data.title'
      mesh issue get MES-42 --web
    """
    app = get_context(ctx)
    if web:
        ws = app.require_workspace()
        _web_open(app, f"/w/{ws}/issues/by-identifier/{issue_id}")
        return
    envelope = app.call("GET", f"/api/v1/issues/{_resolve_issue_id(app, issue_id)}")
    app.emit(envelope, columns=ISSUE_COLUMNS)


def _resolve_issue_id(app, issue_id: str) -> str:
    """Accept a UUID directly; resolve identifiers via the workspace route."""
    import uuid

    try:
        uuid.UUID(issue_id)
        return issue_id
    except ValueError:
        pass
    ws = app.require_workspace()
    envelope = app.call(
        "GET", f"/api/v1/workspaces/{ws}/issues/by-identifier/{issue_id}"
    )
    resolved = envelope.get("data", {}).get("id")
    if not resolved:
        raise CliError(f"issue {issue_id!r} not found", exit_code=EXIT_VALIDATION)
    return resolved


@issue.command("create")
@click.option("--title", required=True, help="Issue title.")
@click.option("--description-file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Read the description from a file (long-text contract, §11.1).")
@click.option("--priority", type=click.Choice(["none", "low", "medium", "high", "urgent"]),
              default=None)
@click.option("--assignee", "assignee_id", default=None, help="Assignee member id.")
@click.option("--project", "project_id", default=None, help="Project id.")
@click.option("--idempotency-key", default=None,
              help="Idempotency key (auto-generated when omitted).")
@click.pass_context
def create_issue(ctx, title, description_file, priority, assignee_id, project_id, idempotency_key):
    """Create an issue.

    \b
    Examples:
      mesh issue create --title "Fix login" --priority high
      mesh issue create --title "Spec" --description-file ./body.md --output json
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    body: dict = {"title": title}
    if description_file:
        with open(description_file, encoding="utf-8") as handle:
            body["description"] = handle.read()
    if priority:
        body["priority"] = priority
    if assignee_id:
        body["assignee_id"] = assignee_id
    if project_id:
        body["project_id"] = project_id
    envelope = app.call(
        "POST",
        f"/api/v1/workspaces/{ws}/issues",
        json=body,
        idempotency_key=idempotency_key or new_request_id(),
    )
    app.emit(envelope, columns=ISSUE_COLUMNS)


@issue.command("update")
@click.argument("issue_id")
@click.option("--title", default=None)
@click.option("--description-file", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--priority", type=click.Choice(["none", "low", "medium", "high", "urgent"]),
              default=None)
@click.option("--assignee", "assignee_id", default=None)
@click.option("--version", "expected_version", type=int, default=None,
              help="Optimistic-concurrency version (If-Match); 409 → exit 4.")
@click.pass_context
def update_issue(ctx, issue_id, title, description_file, priority, assignee_id, expected_version):
    """Update issue fields (PATCH with optional If-Match).

    Examples:
      mesh issue update MES-42 --priority high
      mesh issue update MES-42 --title "New" --version 7   # 409 → exit 4
    """
    app = get_context(ctx)
    resolved = _resolve_issue_id(app, issue_id)
    body: dict = {}
    if title is not None:
        body["title"] = title
    if description_file:
        with open(description_file, encoding="utf-8") as handle:
            body["description"] = handle.read()
    if priority is not None:
        body["priority"] = priority
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    if not body:
        raise CliError("nothing to update — pass at least one field", exit_code=EXIT_VALIDATION)
    envelope = app.call(
        "PATCH",
        f"/api/v1/issues/{resolved}",
        json=body,
        if_match=str(expected_version) if expected_version is not None else None,
    )
    app.emit(envelope, columns=ISSUE_COLUMNS)


@issue.command("status")
@click.argument("issue_id")
@click.argument("new_status")
@click.pass_context
def set_status(ctx, issue_id, new_status):
    """Move an issue to a new status (StatusPatch; 422 → exit 3).

    Example: mesh issue status MES-42 in_progress
    """
    app = get_context(ctx)
    resolved = _resolve_issue_id(app, issue_id)
    envelope = app.call(
        "PATCH", f"/api/v1/issues/{resolved}", json={"status": new_status}
    )
    app.emit(envelope, columns=ISSUE_COLUMNS)


@issue.command("comment")
@click.argument("issue_id")
@click.option("--content-file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Read the comment body from a file (long-text contract).")
@click.option("--content", default=None, help="Inline comment body (short text only).")
@click.pass_context
def add_comment(ctx, issue_id, content_file, content):
    """Add a comment to an issue.

    Examples:
      mesh issue comment MES-42 --content "shipped in v0.14"
      mesh issue comment MES-42 --content-file ./review.md
    """
    app = get_context(ctx)
    resolved = _resolve_issue_id(app, issue_id)
    if content_file:
        with open(content_file, encoding="utf-8") as handle:
            body_text = handle.read()
    elif content is not None:
        body_text = content
    else:
        raise CliError(
            "provide --content or --content-file", exit_code=EXIT_VALIDATION
        )
    envelope = app.call(
        "POST",
        f"/api/v1/issues/{resolved}/comments",
        json={"body_markdown": body_text},
        idempotency_key=new_request_id(),
    )
    app.emit(envelope, columns=["id", "body_markdown", "created_at"])


@issue.command("children")
@click.argument("issue_id")
@click.pass_context
def children(ctx, issue_id):
    """List an issue's sub-issues.

    Example: mesh issue children MES-10
    """
    app = get_context(ctx)
    resolved = _resolve_issue_id(app, issue_id)
    envelope = app.call("GET", f"/api/v1/issues/{resolved}/children")
    app.emit(envelope, columns=ISSUE_COLUMNS)


@issue.command("dependencies")
@click.argument("issue_id")
@click.option("--add", "add_id", default=None, help="Add a dependency (issue id).")
@click.pass_context
def dependencies(ctx, issue_id, add_id):
    """List an issue's dependencies, or add one with --add.

    \b
    Examples:
      mesh issue dependencies MES-10
      mesh issue dependencies MES-10 --add <issue-uuid>
    """
    app = get_context(ctx)
    resolved = _resolve_issue_id(app, issue_id)
    if add_id:
        envelope = app.call(
            "POST",
            f"/api/v1/issues/{resolved}/dependencies",
            json={"depends_on_issue_id": add_id},
            idempotency_key=new_request_id(),
        )
    else:
        envelope = app.call("GET", f"/api/v1/issues/{resolved}/dependencies")
    app.emit(envelope, columns=ISSUE_COLUMNS)
