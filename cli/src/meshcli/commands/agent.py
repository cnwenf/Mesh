"""``mesh agent`` — roster + execution history (cli.md C10)."""

from __future__ import annotations

import click

from meshcli.context import get_context
from meshcli.main import cli as root

AGENT_COLUMNS = ["id", "name", "role_tag", "lifecycle_status", "updated_at"]
EXECUTION_COLUMNS = ["id", "status", "trigger", "queued_at", "finished_at"]


@root.group()
def agent():
    """AI agents (roster + run history)."""


@agent.command("list")
@click.option("--limit", type=int, default=None)
@click.option("--all", "fetch_all", is_flag=True, default=False)
@click.pass_context
def list_agents(ctx, limit, fetch_all):
    """List agents in the workspace.

    Example: mesh agent list --output json
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    params = {"limit": limit} if limit else {}
    envelope = (
        app.call_all("GET", f"/api/v1/workspaces/{ws}/agents", params=params)
        if fetch_all
        else app.call("GET", f"/api/v1/workspaces/{ws}/agents", params=params)
    )
    app.emit(envelope, columns=AGENT_COLUMNS)


@agent.command("executions")
@click.argument("agent_id")
@click.option("--limit", type=int, default=None)
@click.option("--all", "fetch_all", is_flag=True, default=False)
@click.pass_context
def agent_executions(ctx, agent_id, limit, fetch_all):
    """List an agent's execution history (runtime.md §3.1, filtered).

    Example: mesh agent executions <agent-id> --all
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    params = {"agent_id": agent_id}
    if limit:
        params["limit"] = limit
    envelope = (
        app.call_all("GET", f"/api/v1/workspaces/{ws}/executions", params=params)
        if fetch_all
        else app.call("GET", f"/api/v1/workspaces/{ws}/executions", params=params)
    )
    app.emit(envelope, columns=EXECUTION_COLUMNS)
