"""``mesh runtime`` — console-side register + read-only status (cli.md C15/C15b).

Boundary (cli.md §1.3): the CLI NEVER speaks the daemon protocol — no
heartbeat, no ``/api/v1/daemon/*``. ``register`` creates the console shadow
record and prints install guidance; ``status`` is a human troubleshooting
read of the daemon-reported state via the console API.
"""

from __future__ import annotations

import click

from meshcli.config import new_request_id
from meshcli.context import get_context
from meshcli.main import cli as root
from meshcli.output import stderr

RUNTIME_COLUMNS = ["id", "name", "status", "last_heartbeat_at", "updated_at"]


@root.group()
def runtime():
    """Runtimes (console side only; the daemon is the mesh-runtime binary)."""


@runtime.command("register")
@click.option("--name", required=True, help="Human-readable runtime name.")
@click.option("--activation-file", type=click.Path(dir_okay=False), default=None,
              help="Write the one-time activation code to this 0600 file.")
@click.pass_context
def register_runtime(ctx, name, activation_file):
    """Create a runtime shadow record and print the install guidance.

    The one-time activation code goes to --activation-file (created 0600) or
    stdout — NEVER into a CLI argument or diagnostic output (cli.md §5.3).
    Feed it to `mesh-runtime activate` on the host that runs the daemon.

    Example: mesh runtime register --name ci-runner --activation-file ./act.code
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    envelope = app.call(
        "POST",
        f"/api/v1/workspaces/{ws}/runtimes",
        json={"name": name},
        idempotency_key=new_request_id(),
    )
    data = envelope.get("data", {})
    activation_code = data.pop("activation_code", None) or data.pop("activation", {}).get(
        "code"
    )
    app.emit(envelope, columns=RUNTIME_COLUMNS)
    if activation_code:
        if activation_file:
            import os

            fd = os.open(activation_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(activation_code + "\n")
            stderr(f"✓ Activation code written to {activation_file} (mode 0600)")
        else:
            # stdout is acceptable (it is not a diagnostic channel); the code
            # never appears on stderr/verbose traces.
            print(activation_code)
        stderr("Next: run `mesh-runtime activate` on the daemon host with this code.")


@runtime.command("status")
@click.argument("runtime_id")
@click.pass_context
def runtime_status(ctx, runtime_id):
    """Read-only troubleshooting view of a runtime (console API only).

    Reads what the daemon last reported — never contacts the daemon
    namespace, never fakes liveness (cli.md §1.3, review H8).
    Example: mesh runtime status <id>
    """
    app = get_context(ctx)
    ws = app.require_workspace()
    envelope = app.call("GET", f"/api/v1/workspaces/{ws}/runtimes/{runtime_id}")
    app.emit(envelope, columns=RUNTIME_COLUMNS)
