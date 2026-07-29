"""``mesh execution`` — get/logs/cancel (cli.md C11/C12, runtime.md §3.1/§3.3)."""

from __future__ import annotations

import sys

import click

from meshcli.context import get_context
from meshcli.errors import EXIT_INTERRUPTED, CliError
from meshcli.main import cli as root
from meshcli.sse import fetch_history, follow_logs

EXECUTION_COLUMNS = ["id", "status", "trigger_kind", "created_at", "finished_at"]


@root.group()
def execution():
    """Agent executions (runs) and their logs."""


@execution.command("get")
@click.argument("execution_id")
@click.option("--web", is_flag=True, default=False, help="Open in the browser.")
@click.pass_context
def get_execution(ctx, execution_id, web):
    """Show one execution.

    Example: mesh execution get <id> --output json
    """
    app = get_context(ctx)
    if web:
        from meshcli.commands.issue import _web_open

        _web_open(app, f"/executions/{execution_id}")
        return
    ws = app.require_workspace()
    envelope = app.call("GET", f"/api/v1/workspaces/{ws}/executions/{execution_id}")
    app.emit(envelope, columns=EXECUTION_COLUMNS)


@execution.command("logs")
@click.argument("execution_id")
@click.option("--follow", is_flag=True, default=False,
              help="Follow the live log via the SSE channel until `end`.")
@click.option("--offset", type=int, default=0, show_default=True,
              help="Start offset (resume point).")
@click.option("--stream", type=click.Choice(["stdout", "stderr"]), default=None,
              help="History fetch: show only one stream.")
@click.option("--timestamps/--no-timestamps", "timestamps", default=True, show_default=True,
              help="Prefix each line with its RFC3339 server timestamp.")
@click.pass_context
def execution_logs(ctx, execution_id, follow, offset, stream, timestamps):
    """Print execution logs; --follow streams until the run ends.

    \b
    Follow semantics (cli.md C12): offset-based resume with de-duplication
    (no loss, no duplicates), Ctrl-C exits 130 with no dangling connection.
    Examples:
      mesh execution logs <id>
      mesh execution logs <id> --follow --no-timestamps | grep ERROR
      mesh execution logs <id> --stream stderr
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    if follow:
        try:
            follow_logs(
                app.client,
                workspace_id=ws,
                execution_id=execution_id,
                start_offset=offset,
                timestamps=timestamps,
            )
        except KeyboardInterrupt:
            sys.exit(EXIT_INTERRUPTED)
        return
    for line in fetch_history(
        app.client,
        workspace_id=ws,
        execution_id=execution_id,
        offset=offset,
        stream=stream,
        timestamps=timestamps,
    ):
        print(line, flush=True)


@execution.command("cancel")
@click.argument("execution_id")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation.")
@click.pass_context
def cancel_execution(ctx, execution_id, yes):
    """Cancel a running execution (destructive — asks unless --yes).

    Example: mesh execution cancel <id> --yes
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    if yes:
        app.yes = True
    app.confirm(f"Cancel execution {execution_id}?")
    envelope = app.call("POST", f"/api/v1/workspaces/{ws}/executions/{execution_id}:cancel")
    app.emit(envelope, columns=EXECUTION_COLUMNS)
