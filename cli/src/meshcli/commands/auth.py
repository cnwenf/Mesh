"""``mesh auth`` — login (device code / PAT), status, logout (cli.md C1–C5)."""

from __future__ import annotations

import sys
import time
from typing import Any

import click

from meshcli import API_VERSION, __version__
from meshcli import config as config_mod
from meshcli.browser import try_open
from meshcli.config import CredentialEntry
from meshcli.context import get_context
from meshcli.errors import EXIT_AUTH, EXIT_VALIDATION, CliError
from meshcli.main import cli as root
from meshcli.output import emit_json, stderr

DEFAULT_DEVICE_SCOPES = "issue:read issue:write comment:write project:manage"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
CLIENT_ID = "mesh-cli"
POLL_SLOW_DOWN_EXTRA_SECONDS = 5


@root.group()
def auth():
    """Authenticate and inspect credentials."""


# --- login ------------------------------------------------------------------------


def _read_token_stdin() -> str:
    """C1: the PAT enters via stdin or a 0600 file — NEVER a CLI argument."""
    if sys.stdin.isatty():
        line = click.prompt("Paste your personal access token", hide_input=True)
    else:
        line = sys.stdin.readline()
    token = line.strip()
    if not token:
        raise CliError("no token provided on stdin", exit_code=EXIT_VALIDATION)
    return token


def _pat_login(ctx_obj, token: str) -> None:
    """Validate the PAT with one `GET /me` probe, then persist it (C1)."""
    app = ctx_obj
    # Temporarily probe with this token (the store may hold something else).
    original_loader = app.client._load_credential
    app.client._load_credential = lambda: CredentialEntry(kind="pat", token=token)
    try:
        envelope = app.call("GET", "/api/v1/me")
    finally:
        app.client._load_credential = original_loader
    user = envelope.get("data", {})
    config_mod.save_credential(
        app.api_url,
        CredentialEntry(kind="pat", token=token, prefix=token[:12]),
    )
    stderr(f"✓ Logged in as {user.get('email', 'unknown')} (PAT)")
    stderr(f"  Token stored in {config_mod.credentials_path()} (mode 0600)")


def _device_login(ctx_obj, scopes: str, no_browser: bool) -> None:
    """RFC 8628 device-code flow (cli.md §3.2 / §4.2 golden path)."""
    app = ctx_obj
    # 1) Request the grant. The device_code NEVER leaves this process as text.
    code_response = app.client.request(
        "POST",
        "/api/v1/auth/device/code",
        json={"client_id": CLIENT_ID, "scope": scopes},
    )
    issued = code_response.json().get("data", {})
    device_code = issued["device_code"]
    user_code = issued["user_code"]
    verification_uri = issued.get("verification_uri", "/device")
    interval = max(1, int(issued.get("interval", 5)))
    expires_in = int(issued.get("expires_in", 900))

    base = app.api_url.rstrip("/")
    full_uri = f"{base}{verification_uri}"
    stderr("! First, open this URL in your browser and enter the code:")
    stderr(f"    {full_uri}")
    stderr(f"    Code: {user_code}")
    if not no_browser:
        stderr("  (attempting to open your browser automatically…)")
        try_open(f"{full_uri}?user_code={user_code}")

    # 2) Poll at the granted interval; slow_down widens it (+5s, cli.md §3.2).
    deadline = time.monotonic() + expires_in
    stderr("✓ Waiting for authorization… (polling)")
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            response = app.client.request(
                "POST",
                "/api/v1/auth/device/token",
                json={
                    "grant_type": DEVICE_GRANT_TYPE,
                    "device_code": device_code,
                    "client_id": CLIENT_ID,
                },
            )
        except CliError as exc:
            # RFC 8628 polling branches (cli.md §3.2 / §5.1 四分支):
            # pending → keep polling; slow_down → widen the interval; the two
            # terminals end the flow with the auth-exclusive exit code.
            code = str((exc.envelope or {}).get("error", {}).get("code"))
            if code == "authorization_pending":
                continue
            if code == "slow_down":
                interval += POLL_SLOW_DOWN_EXTRA_SECONDS
                continue
            if code == "access_denied":
                raise CliError(
                    "the device authorization was denied",
                    exit_code=EXIT_AUTH,
                    hint="Run `mesh auth login` to start a fresh device flow.",
                ) from exc
            if code == "expired_token":
                raise CliError(
                    "the device code expired before approval",
                    exit_code=EXIT_AUTH,
                    hint="Run `mesh auth login` again for a fresh code.",
                ) from exc
            raise
        data = response.json().get("data", {})
        workspace = data.get("workspace", {})
        # 3) Success: persist the device session; the workspace chosen on the
        # approval page becomes the CLI default (cli.md §4.2 R2-H1 — no
        # second selection after success).
        entry = CredentialEntry(
            kind="device_session",
            token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            scopes=data.get("scope", "").split(),
            workspace=workspace.get("slug"),
        )
        config_mod.save_credential(app.api_url, entry)
        stderr("✓ Logged in via device authorization")
        if workspace.get("slug"):
            _adopt_default_workspace(workspace["slug"])
            stderr(
                f"  Default workspace: {workspace['slug']} (bound at approval;"
                " override with --workspace or `mesh config set workspace`)"
            )
        stderr(f"  Token stored in {config_mod.credentials_path()} (mode 0600)")
        return
    raise CliError(
        "the device code expired before approval",
        exit_code=EXIT_AUTH,
        hint="Run `mesh auth login` again to get a fresh code.",
    )


def _adopt_default_workspace(slug: str) -> None:
    data = config_mod.load_config_raw()
    data["workspace"] = slug
    config_mod.save_config_raw(data)


@auth.command("login")
@click.option("--with-token", is_flag=True, default=False,
              help="Read a PAT from stdin (CI/headless) instead of the device flow.")
@click.option("--token-file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Read the PAT from a file (should be 0600) instead of stdin.")
@click.option("--scopes", default=DEFAULT_DEVICE_SCOPES, show_default=True,
              help="Space-joined scopes to request in the device flow.")
@click.option("--no-browser", is_flag=True, default=False,
              help="Do not attempt to open a browser automatically.")
@click.pass_context
def login(ctx, with_token, token_file, scopes, no_browser):
    """Sign in — device-code flow by default, PAT via --with-token/--token-file.

    \b
    Examples:
      mesh auth login                      # device-code flow (opens a browser)
      echo "$MESH_PAT" | mesh auth login --with-token
      mesh auth login --token-file ~/.mesh-pat   # file must be 0600
    """
    app = get_context(ctx)
    if with_token or token_file:
        if token_file:
            with open(token_file, encoding="utf-8") as handle:
                token = handle.read().strip()
            if not token:
                raise CliError(f"no token in {token_file}", exit_code=EXIT_VALIDATION)
        else:
            token = _read_token_stdin()
        _pat_login(app, token)
    else:
        _device_login(app, scopes, no_browser)


# --- status ------------------------------------------------------------------------


@auth.command("status")
@click.pass_context
def status(ctx):
    """Show the current credential (masked) and identity. Exit 2 if not logged in.

    \b
    Example:
      mesh auth status
      mesh auth status --output json
    """
    app = get_context(ctx)
    entry = config_mod.load_credential(app.api_url)
    if entry is None or not entry.token:
        stderr("Error: not authenticated. Run `mesh auth login` to sign in.")
        sys.exit(EXIT_AUTH)

    # Server-side introspection (never echoes a plaintext fragment).
    intro = {}
    me = {}
    try:
        intro = app.call("GET", "/api/v1/auth/token").get("data", {})
        me = app.call("GET", "/api/v1/me").get("data", {})
    except CliError as exc:
        if exc.exit_code == EXIT_AUTH:
            stderr("Error: stored credential is invalid or expired. Run `mesh auth login`.")
            sys.exit(EXIT_AUTH)
        raise

    envelope = {
        "data": {
            "authenticated": True,
            "kind": intro.get("kind", entry.kind),
            "prefix": intro.get("prefix") or entry.prefix,
            "name": intro.get("name"),
            "scopes": intro.get("scopes", entry.scopes),
            "workspace": entry.workspace,
            "expires_at": intro.get("expires_at"),
            "last_used_at": intro.get("last_used_at"),
            "api_url": app.api_url,
            "api_version": API_VERSION,
            "user": {"email": me.get("email"), "display_name": me.get("display_name")} if me else None,
        }
    }
    if app.output == "json":
        emit_json(envelope)
        return
    data = envelope["data"]
    stderr(f"Credential: {data['kind']} ({data['prefix']}…)")
    if data["user"] and data["user"].get("email"):
        stderr(f"User:       {data['user']['email']}")
    if data["scopes"]:
        stderr(f"Scopes:     {' '.join(data['scopes'])}")
    if data["workspace"]:
        stderr(f"Workspace:  {data['workspace']}")
    if data["expires_at"]:
        stderr(f"Expires:    {data['expires_at']}")
    if data["last_used_at"]:
        stderr(f"Last used:  {data['last_used_at']}")
    stderr(f"API:        {data['api_url']} ({data['api_version']})")


# --- logout ------------------------------------------------------------------------


@auth.command("logout")
@click.option("--revoke", is_flag=True, default=False,
              help="Also revoke server-side (PAT: default only clears locally).")
@click.pass_context
def logout(ctx, revoke):
    """Log out — revoke the session, or clear the local PAT (--revoke to revoke it).

    \b
    Session login: revokes the refresh token server-side and clears locally.
    PAT login:     clears the local token; --revoke also revokes it on the
                   server via DELETE /api/v1/auth/token (destructive — asks
                   for confirmation unless --yes).
    """
    app = get_context(ctx)
    entry = config_mod.load_credential(app.api_url)
    if entry is None:
        stderr("Not logged in (nothing to do).")
        return

    if entry.kind == "pat" and not revoke:
        config_mod.clear_credential(app.api_url)
        stderr("✓ Cleared the local token (it remains valid server-side; use --revoke to revoke).")
        return

    if revoke:
        app.confirm("Revoke the current credential on the server?")
    try:
        if entry.kind == "pat" or revoke:
            # Self-revocation endpoint: needs the credential as Bearer.
            app.call("DELETE", "/api/v1/auth/token")
        else:
            # Device session: revoke by refresh on the server logout endpoint,
            # then the self-revoke covers the access-bound session.
            app.call("DELETE", "/api/v1/auth/token")
    except CliError as exc:
        if exc.exit_code != EXIT_AUTH:
            raise
        stderr("Credential already invalid on the server; clearing locally.")
    config_mod.clear_credential(app.api_url)
    stderr("✓ Logged out.")


# --- version ------------------------------------------------------------------------


@root.command("version")
@click.option(
    "--verbose",
    "verbose",
    is_flag=True,
    help="Also report runtime/platform, the configured API base URL and the live server API version.",
)
@click.pass_context
def version_cmd(ctx, verbose: bool):
    """Print the CLI version and target API version.

    Example: mesh version --output json
    Example: mesh version --verbose
    """
    app = get_context(ctx)
    data: dict[str, Any] = {"cli_version": __version__, "api_version": API_VERSION}
    if verbose:
        import platform

        data["python"] = platform.python_version()
        data["platform"] = f"{platform.system().lower()}-{platform.machine()}"
        data["api_url"] = app.api_url
        server: dict[str, Any] = {"reachable": False}
        try:
            # The public contract document carries the server's API version
            # (cli.md §5.4 版本协商); any Deprecation/Sunset header on this
            # response triggers the shared stderr upgrade warning.
            response = app.client.request("GET", "/openapi.json")
            info = response.json().get("info", {})
            server = {"reachable": True, "api_version": info.get("version")}
        except (CliError, ValueError):
            pass  # informational probe — an unreachable server is not an error
        data["server"] = server
    if app.output == "json":
        emit_json({"data": data})
    else:
        stderr(f"mesh {__version__} (API {API_VERSION})")
        if verbose:
            stderr(f"python {data['python']} on {data['platform']}")
            stderr(f"api-url {data['api_url']}")
            server = data["server"]
            if server.get("reachable"):
                stderr(f"server reachable — API version {server.get('api_version')}")
            else:
                stderr("server unreachable")
