"""Exit-code contract + error model (cli.md §3.4, table-driven).

Exit classes (stable across the CLI major version):

    0   success
    1   generic runtime error (5xx, network, 429 retries exhausted)
    2   authentication failure — EXCLUSIVE (401/403: not logged in, expired,
        revoked, insufficient scope); never a usage error
    3   validation failure (400/404/410/413/415/422 — including
        ``move_confirmation_required`` and ``validation_required`` — and
        client-side usage errors: unknown command/flag/argument)
    4   conflict (409/423)
    130 user interrupt (SIGINT)

The HTTP↔exit mapping is DATA (``HTTP_EXIT_CODES``) so the contract test
generates its cases from the same table the runtime uses — no hand-written
examples to drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_AUTH = 2
EXIT_VALIDATION = 3
EXIT_CONFLICT = 4
EXIT_INTERRUPTED = 130

# HTTP status → exit code. Everything unmapped falls through to EXIT_GENERIC.
HTTP_EXIT_CODES: dict[int, int] = {
    400: EXIT_VALIDATION,
    401: EXIT_AUTH,
    403: EXIT_AUTH,
    404: EXIT_VALIDATION,
    409: EXIT_CONFLICT,
    410: EXIT_VALIDATION,
    413: EXIT_VALIDATION,
    415: EXIT_VALIDATION,
    422: EXIT_VALIDATION,
    423: EXIT_CONFLICT,
    429: EXIT_GENERIC,  # only after retries are exhausted (see http.py)
    500: EXIT_GENERIC,
    502: EXIT_GENERIC,
    503: EXIT_GENERIC,
}


@dataclass(eq=False)
class CliError(Exception):
    """A user-facing CLI failure carrying its exit code + actionable hint.

    ``message`` states what happened; ``hint`` states the next step (cli.md
    §4.3: every error = what happened + what to do next). Not frozen: the
    interpreter/click must be able to attach ``__traceback__``.
    """

    message: str
    exit_code: int = EXIT_GENERIC
    hint: str | None = None
    # The raw API error envelope, echoed verbatim for --output json.
    envelope: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def exit_code_for_status(status_code: int) -> int:
    """Map an HTTP status to its exit code (generic for anything unknown)."""
    if 200 <= status_code < 300:
        return EXIT_OK
    return HTTP_EXIT_CODES.get(status_code, EXIT_GENERIC)


def from_api_error(status_code: int, envelope: dict[str, Any]) -> CliError:
    """Build a CliError from an API error envelope with an actionable hint.

    Hints are derived from the envelope ``code`` (cli.md §4.3 table), never
    from internals — no token/stack/SQL leakage.
    """
    error = envelope.get("error", {}) if isinstance(envelope, dict) else {}
    code = str(error.get("code", ""))
    message = str(error.get("message", "request failed"))
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    exit_code = exit_code_for_status(status_code)

    hint: str | None = None
    if status_code == 401:
        hint = "Run `mesh auth login` to sign in."
    elif status_code == 403:
        required = details.get("required_scope")
        if required:
            hint = (
                f"This token lacks scope `{required}`. Re-create it with the "
                "needed scope, then retry."
            )
        elif details.get("reason") == "interactive_session_required":
            hint = (
                "This session lacks recent active authentication. Complete "
                "reauth on the Web, then re-run `mesh auth login`."
            )
        else:
            hint = "Your role or token scopes do not permit this action."
    elif code == "conflict":
        hint = "Re-fetch the resource (e.g. `mesh issue get <id>`) and retry."
    elif code == "move_confirmation_required":
        hint = "Re-run with the confirmation token/flag from this response."
    elif code == "validation_required":
        hint = "Run the validate step first (e.g. `mesh import issues --dry-run`)."
    elif status_code == 429:
        hint = "Rate limited — retry later."

    return CliError(message=message, exit_code=exit_code, hint=hint, envelope=envelope)
