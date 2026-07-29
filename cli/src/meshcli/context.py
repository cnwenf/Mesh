"""Shared command context (cli.md C16/C18/C20/C21, §3.1, §3.5).

Built once per invocation from the merged flag/env/file config; every command
module talks to the API through this object so the output discipline (stdout
= results only), workspace resolution (UUID or slug), pagination (--all),
confirmation (TTY gate) and jq integration stay uniform.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from meshcli import config as config_mod
from meshcli.config import CredentialEntry, resolve_key
from meshcli.errors import EXIT_VALIDATION, CliError
from meshcli.http import ClientOptions, MeshClient
from meshcli.output import apply_jq, emit_json, emit_json_lines, emit_table, stderr


@dataclass
class AppContext:
    client: MeshClient
    api_url: str
    workspace: str | None  # slug or UUID as given by the user
    output: str  # "table" | "json"
    verbose: bool = False
    quiet: bool = False
    yes: bool = False
    jq: str | None = None
    no_header: bool = False
    config: dict = field(default_factory=dict)

    # -- workspace resolution (C16 / cli.md §3.1 path convention) ----------------

    def require_workspace(self) -> str:
        """The workspace path segment (UUID or slug resolved to UUID).

        No resolvable workspace → exit 3 with an actionable hint listing the
        workspaces the credential belongs to."""
        if self.workspace:
            return self._resolve_workspace_segment(self.workspace)
        # Stored credential may carry an approved-bound workspace (device login).
        entry = self.client._load_credential()
        if entry is not None and entry.workspace:
            return self._resolve_workspace_segment(entry.workspace)
        hint = "Set one with --workspace <slug> or `mesh config set workspace <slug>`."
        workspaces = self._list_workspace_names()
        if workspaces:
            hint = f"Your workspaces: {', '.join(workspaces)}. " + hint
        raise CliError("no workspace resolved for this command", exit_code=EXIT_VALIDATION, hint=hint)

    def _resolve_workspace_segment(self, workspace: str) -> str:
        try:
            uuid.UUID(workspace)
            return workspace  # already a UUID — the routes accept it directly
        except ValueError:
            pass
        response = self.client.request("GET", f"/api/v1/workspaces/by-slug/{workspace}")
        data = response.json().get("data", {})
        workspace_id = data.get("id")
        if not workspace_id:
            raise CliError(
                f"workspace {workspace!r} not found", exit_code=EXIT_VALIDATION
            )
        return workspace_id

    def _list_workspace_names(self) -> list[str]:
        try:
            response = self.client.request("GET", "/api/v1/workspaces")
            rows = response.json().get("data", [])
            return [str(r.get("slug", r.get("id", ""))) for r in rows if isinstance(r, dict)]
        except CliError:
            return []

    # -- request + result emission -------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        if_match: str | None = None,
    ) -> dict:
        response = self.client.request(
            method,
            path,
            json=json,
            params=params,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
        body = response.json()
        return body if isinstance(body, dict) else {"data": body}

    def emit(
        self,
        envelope: dict,
        *,
        columns: list[str] | None = None,
        row_of: Callable[[dict], dict] | None = None,
    ) -> None:
        """Print one successful result under the output discipline (§3.5)."""
        if self.jq is not None:
            if self.output != "json":
                raise CliError(
                    "--jq is only compatible with --output json",
                    exit_code=EXIT_VALIDATION,
                    hint="Re-run with --output json --jq '<expr>'.",
                )
            emit_json_lines(apply_jq(envelope, self.jq))
            return
        if self.output == "json":
            emit_json(envelope)
            return
        # table mode
        data = envelope.get("data")
        rows: list[dict]
        if isinstance(data, list):
            rows = [row_of(r) if row_of else r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [row_of(data) if row_of else data]
        else:
            emit_json(envelope)  # scalar payloads fall back to json
            return
        if columns is None:
            columns = list(rows[0].keys()) if rows else []
        emit_table(rows, columns, no_header=self.no_header)

    # -- pagination (C20) ------------------------------------------------------------

    def call_all(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict:
        """Follow ``next_cursor`` to exhaustion; merges ``data`` arrays."""
        params = dict(params or {})
        collected: list[Any] = []
        last: dict = {}
        while True:
            envelope = self.call(method, path, params=params)
            data = envelope.get("data")
            if isinstance(data, list):
                collected.extend(data)
            last = envelope
            cursor = envelope.get("next_cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        last["data"] = collected
        last.pop("next_cursor", None)
        return last

    # -- destructive confirmation (§4.1) ----------------------------------------------

    def confirm(self, message: str) -> None:
        """Gate a destructive action: [y/N] on a TTY; --yes skips; a non-TTY
        without --yes is a hard exit-3 error (never silent, never hang)."""
        if self.yes:
            return
        if not sys.stdin.isatty():
            raise CliError(
                f"refusing to run a destructive operation without confirmation: {message}",
                exit_code=EXIT_VALIDATION,
                hint="Re-run with --yes to confirm.",
            )
        answer = input(f"{message} [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise CliError("aborted by user", exit_code=EXIT_VALIDATION)

    def progress(self, message: str) -> None:
        """Progress goes to stderr (never stdout), silenced by --quiet."""
        if not self.quiet:
            stderr(message)


def get_context(ctx) -> AppContext:
    """Build (once per invocation) the AppContext from the stored global flags."""
    if "app" not in ctx.obj:
        flags = ctx.obj["flags"]
        ctx.obj["app"] = build_context(
            api_url=flags["api_url"],
            workspace=flags["workspace"],
            output=flags["output"],
            verbose=flags["verbose"],
            quiet=flags["quiet"],
            yes=flags["yes"],
            insecure=flags["insecure"],
            ca_cert=flags["ca_cert"],
            jq=flags["jq"],
            no_header=flags["no_header"],
        )
    return ctx.obj["app"]


def build_context(
    *,
    api_url: str | None,
    workspace: str | None,
    output: str | None,
    verbose: bool,
    quiet: bool,
    yes: bool,
    insecure: bool,
    ca_cert: str | None,
    jq: str | None,
    no_header: bool,
    config: dict | None = None,
) -> AppContext:
    """Merge flag > env > file > default for every knob, then build the client."""
    config = config if config is not None else config_mod.load_config_raw()
    resolved_url = resolve_key("api_url", flag_value=api_url, config=config).value
    resolved_workspace = resolve_key("workspace", flag_value=workspace, config=config).value or None
    resolved_output = resolve_key("output", flag_value=output, config=config).value

    # CA precedence: --ca-cert > per-host config tls.ca_cert > SSL_CERT_FILE env.
    effective_ca = ca_cert
    if effective_ca is None:
        host_tls = (config.get("hosts", {}) or {}).get(resolved_url, {}).get("tls", {}) or {}
        effective_ca = host_tls.get("ca_cert")

    host = resolved_url

    def loader() -> CredentialEntry | None:
        return config_mod.load_credential(host)

    def saver(entry: CredentialEntry) -> None:
        config_mod.save_credential(host, entry)

    def clearer() -> None:
        config_mod.clear_credential(host)

    client = MeshClient(
        ClientOptions(
            base_url=resolved_url,
            verbose=verbose,
            insecure=insecure,
            ca_cert=effective_ca,
        ),
        credential_loader=loader,
        credential_saver=saver,
        credential_clearer=clearer,
    )
    return AppContext(
        client=client,
        api_url=resolved_url,
        workspace=resolved_workspace,
        output=resolved_output,
        verbose=verbose,
        quiet=quiet,
        yes=yes,
        jq=jq,
        no_header=no_header,
        config=config,
    )
