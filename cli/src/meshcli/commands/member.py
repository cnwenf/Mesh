"""``mesh member list`` — the workspace roster (cli.md C9)."""

from __future__ import annotations

import click

from meshcli.context import get_context
from meshcli.main import cli as root

MEMBER_COLUMNS = ["id", "member_type", "role", "status", "display", "joined_at"]


@root.group()
def member():
    """Workspace members (human + agent roster)."""


@member.command("list")
@click.option("--limit", type=int, default=None)
@click.option("--all", "fetch_all", is_flag=True, default=False)
@click.pass_context
def list_members(ctx, limit, fetch_all):
    """List the workspace roster.

    Examples:
      mesh member list
      mesh member list --output json --jq '.[] | {id, role}'
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    params = {"limit": limit} if limit else {}
    envelope = (
        app.call_all("GET", f"/api/v1/workspaces/{ws}/members", params=params)
        if fetch_all
        else app.call("GET", f"/api/v1/workspaces/{ws}/members", params=params)
    )
    app.emit(envelope, columns=MEMBER_COLUMNS)
