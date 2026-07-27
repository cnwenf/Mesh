"""Capability declaration normalization (agent.md §3.3, README §6.4 / §6.11).

The enqueue path must derive TWO strictly-typed fields from the skill-layer
declaration forms (skill.md allows mixed "string key" and
``{capability, permission}`` object entries):

* ``required_capabilities`` — a PURE STRING ARRAY of capability keys for
  scheduling (runtime claim matches ``e.required_capabilities <@
  runtimes.capabilities``; an object slipping in here would make the JSONB
  ``<@`` match fail forever and the task could never be claimed);
* ``capability_grants`` — a STRICT ``[{capability, permission}]`` object
  array for the authorization snapshot (``permission`` is REQUIRED after
  normalization: string entries default to ``confirm_required``; the same
  capability declared twice keeps the STRICTER permission).

This function is the backend's executable implementation of the §3.3
algorithm; it is line-for-line equivalent to the PL/pgSQL reference
``normalize_capability_declarations`` in
``docs/specs/validation/schema_r2_validation.sql`` (same entry rules, same
ranking ``confirm_required > write > read_only``, same ordering, same error
conditions). Integration test T28 drives THIS implementation with mixed
declarations and asserts every semantic.
"""

from __future__ import annotations

from typing import Any

PERMISSION_VALUES = ("read_only", "write", "confirm_required")

# Strictness ranking — higher wins when the same capability is declared twice.
_PERMISSION_RANK = {"read_only": 1, "write": 2, "confirm_required": 3}
_RANK_TO_PERMISSION = {rank: name for name, rank in _PERMISSION_RANK.items()}

# Default for entries that do not annotate a permission (declaration-layer
# shorthand): unlabeled capabilities are treated as high-risk gates.
_DEFAULT_PERMISSION = "confirm_required"


class CapabilityInvalidError(ValueError):
    """A declaration cannot be normalized (API layer maps this to 422).

    ``code`` is the stable error code (``capability_invalid``); the message
    mirrors the PL/pgSQL reference's RAISE text so both implementations
    reject identical inputs with identical reasons.
    """

    code = "capability_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(f"capability_invalid: {message}")


def normalize_capability_declarations(declared: Any) -> dict[str, list]:
    """Normalize mixed capability declarations into strict scheduling fields.

    Input: a list of entries, each either a string capability key or an
    object ``{"capability": <key>, "permission": <perm>?}``.

    Output: ``{"required": [...], "grants": [...]}`` where ``required`` is a
    deduplicated, lexicographically sorted string array and ``grants`` is
    sorted by capability with the strictest declared permission each.

    Raises :class:`CapabilityInvalidError` for a non-list input, an entry
    that is neither a string nor a ``{capability}`` object, or a permission
    that is not one of ``read_only | write | confirm_required``.
    """
    if not isinstance(declared, list):
        raise CapabilityInvalidError("declarations must be a JSON array")

    ranks: dict[str, int] = {}
    required: list[str] = []
    for item in declared:
        if isinstance(item, str):
            # String entry → default high-risk gate (mirrors the reference).
            key, permission = item, _DEFAULT_PERMISSION
        elif isinstance(item, dict) and isinstance(item.get("capability"), str):
            key = item["capability"]
            raw_permission = item.get("permission")
            if raw_permission is None:
                # Object entry without an annotated permission → strictest.
                permission = _DEFAULT_PERMISSION
            elif isinstance(raw_permission, str) and raw_permission in _PERMISSION_RANK:
                permission = raw_permission
            else:
                raise CapabilityInvalidError(
                    "permission must be read_only|write|confirm_required "
                    f"({key})"
                )
        else:
            raise CapabilityInvalidError(
                "entry must be a string key or a {capability, permission?} object"
            )
        required.append(key)
        rank = _PERMISSION_RANK[permission]
        if key not in ranks or ranks[key] < rank:
            # Same capability declared twice keeps the STRICTER permission.
            ranks[key] = rank

    return {
        "required": sorted(set(required)),
        "grants": [
            {"capability": key, "permission": _RANK_TO_PERMISSION[ranks[key]]}
            for key in sorted(ranks)
        ],
    }


__all__ = [
    "PERMISSION_VALUES",
    "CapabilityInvalidError",
    "normalize_capability_declarations",
]
