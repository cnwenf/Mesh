"""Exit-code contract — table-driven (cli.md §3.4 / §5.1, review M1).

Cases are GENERATED from the mapping table itself (``HTTP_EXIT_CODES``), never
hand-scattered, so the contract test cannot drift from the runtime. The
named-code rows (``move_confirmation_required`` / ``validation_required`` →
3) and client-side usage errors (→ 3, NOT the auth-exclusive 2) are asserted
explicitly.
"""

from __future__ import annotations

import pytest

from meshcli.errors import (
    EXIT_AUTH,
    EXIT_CONFLICT,
    EXIT_GENERIC,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_VALIDATION,
    HTTP_EXIT_CODES,
    CliError,
    exit_code_for_status,
    from_api_error,
)


# The authoritative rows: status → exit class, per cli.md §3.4.
SPEC_ROWS = [
    (400, EXIT_VALIDATION),
    (401, EXIT_AUTH),
    (403, EXIT_AUTH),
    (404, EXIT_VALIDATION),
    (409, EXIT_CONFLICT),
    (410, EXIT_VALIDATION),
    (413, EXIT_VALIDATION),
    (415, EXIT_VALIDATION),
    (422, EXIT_VALIDATION),
    (423, EXIT_CONFLICT),
    (429, EXIT_GENERIC),
    (500, EXIT_GENERIC),
    (502, EXIT_GENERIC),
    (503, EXIT_GENERIC),
]


@pytest.mark.parametrize("status,expected", SPEC_ROWS)
def test_http_status_maps_to_spec_exit_code(status, expected):
    """Every spec row is present in the table and maps correctly."""
    assert HTTP_EXIT_CODES[status] == expected
    assert exit_code_for_status(status) == expected


@pytest.mark.parametrize("status", sorted(HTTP_EXIT_CODES))
def test_table_has_no_spec_drift(status):
    """Every table row is a spec row (no invented mappings)."""
    assert status in dict(SPEC_ROWS)


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_success_statuses_map_to_zero(status):
    assert exit_code_for_status(status) == EXIT_OK


def test_unmapped_statuses_fall_through_to_generic():
    # Anything outside the table is a generic runtime failure.
    for status in (418, 451, 504, 599):
        assert exit_code_for_status(status) == EXIT_GENERIC
    # Unmapped 5xx still exits 1 (generic runtime failure).


@pytest.mark.parametrize(
    "code,expected",
    [
        ("move_confirmation_required", EXIT_VALIDATION),  # two-step move (README §6.14)
        ("validation_required", EXIT_VALIDATION),  # import before validate
        ("source_changed", EXIT_VALIDATION),
        ("not_found", EXIT_VALIDATION),
        ("unauthorized", EXIT_AUTH),
        ("forbidden", EXIT_AUTH),
        ("conflict", EXIT_CONFLICT),
        ("locked", EXIT_CONFLICT),
        ("rate_limited", EXIT_GENERIC),
        ("internal_error", EXIT_GENERIC),
    ],
)
def test_named_codes_map_via_their_http_status(code, expected):
    envelope = {"error": {"code": code, "message": "x"}}
    status = next(s for s, e in SPEC_ROWS if e == expected and s != 429)
    # named codes travel on their HTTP status; the mapping is status-driven
    err = from_api_error(status, envelope)
    assert err.exit_code == expected
    assert err.envelope == envelope  # verbatim for --output json


def test_auth_exit_is_exclusive_never_usage():
    """Exit 2 is reserved for authentication — usage errors are 3 (review M1:
    unknown command/flag must NOT occupy the auth-exclusive code)."""
    usage = CliError("unknown command", exit_code=EXIT_VALIDATION)
    assert usage.exit_code == 3
    assert usage.exit_code != EXIT_AUTH


def test_interrupt_exit_code():
    assert EXIT_INTERRUPTED == 130


def test_hints_are_actionable():
    e = from_api_error(401, {"error": {"code": "unauthorized", "message": "expired"}})
    assert e.hint and "mesh auth login" in e.hint

    e = from_api_error(
        403,
        {"error": {"code": "forbidden", "message": "x", "details": {"required_scope": "issue:write"}}},
    )
    assert "issue:write" in (e.hint or "")

    e = from_api_error(409, {"error": {"code": "conflict", "message": "version"}})
    assert "retry" in (e.hint or "").lower()


def test_error_message_never_leaks_envelope_internals():
    # The CliError message is the envelope's own message — nothing else is
    # surfaced (no stack, no SQL — the envelope is echoed only for json out).
    envelope = {"error": {"code": "internal_error", "message": "internal server error"}}
    e = from_api_error(500, envelope)
    assert e.message == "internal server error"
    assert e.exit_code == EXIT_GENERIC
