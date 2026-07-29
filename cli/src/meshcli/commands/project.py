"""``mesh project`` — list/get/create (cli.md C8, §3.1)."""

from __future__ import annotations

import click

from meshcli.config import new_request_id
from meshcli.context import get_context
from meshcli.main import cli as root

PROJECT_COLUMNS = ["key", "name", "status", "health", "updated_at"]


@root.group()
def project():
    """Work with projects."""


@project.command("list")
@click.option("--limit", type=int, default=None)
@click.option("--all", "fetch_all", is_flag=True, default=False)
@click.pass_context
def list_projects(ctx, limit, fetch_all):
    """List projects in the workspace.

    Examples:
      mesh project list
      mesh project list --all --output json
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    params = {"limit": limit} if limit else {}
    envelope = (
        app.call_all("GET", f"/api/v1/workspaces/{ws}/projects", params=params)
        if fetch_all
        else app.call("GET", f"/api/v1/workspaces/{ws}/projects", params=params)
    )
    app.emit(envelope, columns=PROJECT_COLUMNS)


@project.command("get")
@click.argument("project_id")
@click.option("--web", is_flag=True, default=False, help="Open in the browser.")
@click.pass_context
def get_project(ctx, project_id, web):
    """Show one project.

    Example: mesh project get <id> --output json
    """
    app = get_context(ctx)
    if web:
        from meshcli.commands.issue import _web_open

        _web_open(app, f"/projects/{project_id}")
        return
    envelope = app.call("GET", f"/api/v1/projects/{project_id}")
    app.emit(envelope, columns=PROJECT_COLUMNS)


@project.command("create")
@click.option("--name", required=True)
@click.option("--key", required=True, help="Short uppercase key (e.g. WEB).")
@click.option("--description", default=None)
@click.option("--idempotency-key", default=None)
@click.pass_context
def create_project(ctx, name, key, description, idempotency_key):
    """Create a project.

    Example: mesh project create --name "Website" --key WEB
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    body = {"name": name, "key": key}
    if description:
        body["description"] = description
    envelope = app.call(
        "POST",
        f"/api/v1/workspaces/{ws}/projects",
        json=body,
        idempotency_key=idempotency_key or new_request_id(),
    )
    app.emit(envelope, columns=PROJECT_COLUMNS)
