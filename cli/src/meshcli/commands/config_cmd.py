"""``mesh config`` — local configuration (cli.md C6/C27)."""

from __future__ import annotations

import click

from meshcli import config as config_mod
from meshcli.errors import EXIT_VALIDATION, CliError
from meshcli.main import cli as root
from meshcli.output import emit_json, emit_table, stderr


@root.group("config")
def config_group():
    """Manage local CLI configuration (non-secret)."""


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """Set a config key (api_url | workspace | output).

    \b
    Examples:
      mesh config set workspace acme
      mesh config set output json
      mesh config set api_url https://mesh.corp.com
    """
    if key not in config_mod.KNOWN_KEYS:
        raise CliError(
            f"unknown config key {key!r}",
            exit_code=EXIT_VALIDATION,
            hint=f"Known keys: {', '.join(sorted(config_mod.KNOWN_KEYS))}.",
        )
    config_mod.config_set(key, value)
    stderr(f"✓ {key} = {value}")


@config_group.command("get")
@click.argument("key")
@click.option("--output", "output", type=click.Choice(["table", "json"]), default=None)
def config_get(key, output):
    """Show the effective value of a key and where it comes from.

    Example: mesh config get workspace
    """
    resolved = config_mod.config_get(key)
    if output == "json":
        emit_json({"data": {"key": key, **resolved.__dict__}})
    else:
        stderr(f"{key} = {resolved.value}  (source: {resolved.source})")


@config_group.command("unset")
@click.argument("key")
def config_unset(key):
    """Remove a key from the config file (restores env/default).

    Example: mesh config unset workspace
    """
    config_mod.config_unset(key)
    stderr(f"✓ unset {key}")


@config_group.command("list")
@click.option("--all", "show_all", is_flag=True, default=True, help="Show every key + source.")
@click.option("--output", "output", type=click.Choice(["table", "json"]), default=None)
def config_list(show_all, output):
    """List effective configuration with sources (default|env|file|flag).

    Example: mesh config list --all
    """
    rows = config_mod.config_list_all()
    if output == "json":
        emit_json({"data": rows})
    else:
        emit_table(rows, ["key", "value", "source"])
