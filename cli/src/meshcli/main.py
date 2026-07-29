"""``mesh`` command entry point (cli.md §4.4 help / §3.4 exit codes / C27 aliases).

Responsibilities:
- the root click group with the global flags;
- alias expansion (single-level config sugar, C27) BEFORE click parses argv,
  with did-you-mean on unknown commands (exit 3);
- the exit-code discipline: CliError → its code; click usage errors → 3
  (NEVER 2 — 2 is auth-exclusive); SIGINT → 130; unexpected exceptions → 1
  with a neutral message (no stack traces on stdout).
"""

from __future__ import annotations

import signal
import sys

import click

from meshcli import __version__
from meshcli.config import did_you_mean, expand_alias, load_aliases
from meshcli.errors import EXIT_GENERIC, EXIT_INTERRUPTED, EXIT_VALIDATION, CliError
from meshcli.output import stderr


class MeshGroup(click.Group):
    """Root group: usage errors exit 3 and suggest the nearest command."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as exc:
            name = args[0] if args else ""
            suggestion = did_you_mean(name, list(self.list_commands(ctx)))
            if suggestion:
                raise click.UsageError(
                    f"{exc.format_message()} Did you mean {suggestion}?"
                ) from None
            raise

    def command_names(self, ctx) -> list[str]:
        return list(self.list_commands(ctx))


@click.group(
    cls=MeshGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "mesh — the Mesh developer platform CLI.\n\n"
        "A thin REST client over the same /api/v1 the Web uses: issue/project/"
        "member/agent/execution/runtime commands, device-code or PAT login, "
        "streaming execution logs, export/import.\n\n"
        "Run `mesh <group> --help` for a command group, `mesh <group> <cmd> "
        "--help` for full flag documentation with examples."
    ),
)
@click.option("--workspace", "workspace", default=None, help="Workspace slug or UUID (overrides config).")
@click.option("--output", "output", type=click.Choice(["table", "json"]), default=None,
              help="Output format (default: table; json is the scripting contract).")
@click.option("--api-url", "api_url", default=None, help="API base URL (overrides config/env).")
@click.option("--verbose", is_flag=True, default=False,
              help="Print method/path/status/elapsed to stderr (never bodies, never tokens).")
@click.option("--quiet", is_flag=True, default=False, help="Suppress progress output on stderr.")
@click.option("--yes", is_flag=True, default=False, help="Skip interactive confirmation prompts.")
@click.option("--insecure", is_flag=True, default=False,
              help="Disable TLS verification for THIS invocation only (never persisted).")
@click.option("--ca-cert", "ca_cert", default=None, type=click.Path(),
              help="Custom CA bundle PEM for this invocation.")
@click.option("--jq", "jq_expr", default=None,
              help="Filter successful `.data` with a jq expression (requires --output json).")
@click.option("--no-header", is_flag=True, default=False, help="Omit the table header row.")
@click.version_option(__version__, prog_name="mesh", message="%(prog)s %(version)s (API v1)")
@click.pass_context
def cli(ctx, workspace, output, api_url, verbose, quiet, yes, insecure, ca_cert, jq_expr, no_header):
    """Global options are stored; the client is built lazily per command."""
    ctx.ensure_object(dict)
    ctx.obj["flags"] = {
        "workspace": workspace,
        "output": output,
        "api_url": api_url,
        "verbose": verbose,
        "quiet": quiet,
        "yes": yes,
        "insecure": insecure,
        "ca_cert": ca_cert,
        "jq": jq_expr,
        "no_header": no_header,
    }


def _register_commands() -> None:
    from meshcli.commands.agent import agent
    from meshcli.commands.auth import auth
    from meshcli.commands.completion import completion
    from meshcli.commands.config_cmd import config_group
    from meshcli.commands.data import export, import_
    from meshcli.commands.execution import execution
    from meshcli.commands.issue import issue
    from meshcli.commands.member import member
    from meshcli.commands.project import project
    from meshcli.commands.runtime import runtime

    for command in (
        auth, config_group, issue, project, member, agent,
        execution, runtime, export, import_, completion,
    ):
        cli.add_command(command)


_commands_registered = False


def _ensure_commands() -> None:
    global _commands_registered
    if not _commands_registered:
        _register_commands()
        _commands_registered = True


def main(argv: list[str] | None = None) -> int:
    """Entry point with the exit-code discipline (cli.md §3.4)."""
    _ensure_commands()
    argv = list(sys.argv[1:] if argv is None else argv)
    # C27: single-level alias expansion before click sees the arguments.
    try:
        aliases = load_aliases()
    except Exception:  # noqa: BLE001 — a broken config must not mask usage
        aliases = {}
    argv = expand_alias(argv, aliases)

    # SIGINT → 130, cleanly (no traceback, no hanging connections).
    def _sigint(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, _sigint)
    try:
        cli.main(args=argv, prog_name="mesh", standalone_mode=False)
        return 0
    except KeyboardInterrupt:
        stderr("interrupted")
        return EXIT_INTERRUPTED
    except CliError as exc:
        stderr(f"Error: {exc.message}")
        if exc.hint:
            stderr(f"Hint: {exc.hint}")
        return exc.exit_code
    except click.UsageError as exc:
        # Unknown command/flag/argument → exit 3, never the auth-exclusive 2.
        stderr(f"Error: {exc.format_message()}")
        return EXIT_VALIDATION
    except click.Abort:
        stderr("aborted")
        return EXIT_VALIDATION
    except Exception as exc:  # noqa: BLE001 — neutral failure surface
        stderr(f"Error: unexpected failure ({exc.__class__.__name__})")
        return EXIT_GENERIC
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    sys.exit(main())
