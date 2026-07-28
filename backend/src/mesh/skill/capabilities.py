"""Capability grant checks (skill.md §5.3: granted ⊆ required, 最小化授权).

``required_capabilities`` / ``granted_capabilities`` use the declaration-layer
mixed format (string keys OR ``{capability, permission}`` objects — skill.md
§2.4). The subset check enforces TWO things per capability key:

1. the key MUST be declared (granting an undeclared key = 422
   ``capability_not_declared``);
2. the granted ``permission`` must NOT exceed the declared ``permission`` on
   the autonomy scale (granting a *more capable* permission for a declared
   key is an ESCALATION, e.g. declaring ``read_only`` but granting ``write``
   — also 422 ``capability_not_declared``). Tightening is allowed (the
   approver may grant ``read_only`` where ``write`` was declared).

Bare string entries carry the default permission (``confirm_required``) —
the same default the §3.3 normalizer applies — so a bare grant against a
bare declaration is equal-level and passes.

The autonomy scale (LOW→HIGH): ``read_only`` < ``confirm_required`` <
``write``. ``write`` is the most autonomous (acts without a human gate);
``confirm_required`` gates the action behind approval; ``read_only`` cannot
mutate. Granting a higher-autonomy permission than declared widens the
granted surface beyond the declaration and is refused.
"""

from __future__ import annotations

from typing import Any

from mesh.errors import BusinessRuleError

DEFAULT_PERMISSION = "confirm_required"

# Autonomy ranking (LOW→HIGH). A grant's rank must be <= the declaration's.
_PERMISSION_AUTONOMY: dict[str, int] = {
    "read_only": 1,
    "confirm_required": 2,
    "write": 3,
}


def capability_keys(declared: Any) -> set[str]:
    """Extract the set of capability keys from a mixed declaration array.

    Non-list / malformed entries yield an empty set — declaration shapes are
    validated at write time by the §3.3 normalizer; this read-side helper is
    deliberately permissive.
    """
    if not isinstance(declared, list):
        return set()
    keys: set[str] = set()
    for item in declared:
        if isinstance(item, str):
            keys.add(item)
        elif isinstance(item, dict) and isinstance(item.get("capability"), str):
            keys.add(item["capability"])
    return keys


def _permission_of(entry: Any) -> str:
    """The permission carried by one declaration entry (bare str → default)."""
    if isinstance(entry, str):
        return DEFAULT_PERMISSION
    if isinstance(entry, dict):
        perm = entry.get("permission")
        if isinstance(perm, str) and perm in _PERMISSION_AUTONOMY:
            return perm
    return DEFAULT_PERMISSION


def _declared_permissions(declared: Any) -> dict[str, int]:
    """Map each declared key to its MAXIMUM declared autonomy rank."""
    ranks: dict[str, int] = {}
    if not isinstance(declared, list):
        return ranks
    for item in declared:
        if isinstance(item, str):
            key, perm = item, DEFAULT_PERMISSION
        elif isinstance(item, dict) and isinstance(item.get("capability"), str):
            key = item["capability"]
            perm = _permission_of(item)
        else:
            continue
        rank = _PERMISSION_AUTONOMY.get(perm, _PERMISSION_AUTONOMY[DEFAULT_PERMISSION])
        if key not in ranks or ranks[key] < rank:
            ranks[key] = rank
    return ranks


def assert_grants_subset_of_required(granted: Any, required: Any) -> None:
    """Raise 422 when grants exceed the declaration (keys OR permission level).

    Refuses both undeclared keys and per-key permission escalation so the
    granted surface can only ever be a *subset / tightening* of what the
    skill declared (skill.md §5.3 / §2.4).
    """
    declared_ranks = _declared_permissions(required)
    granted_items = granted if isinstance(granted, list) else []

    undeclared: list[str] = []
    escalated: list[dict[str, str]] = []
    for item in granted_items:
        if isinstance(item, str):
            key, granted_perm = item, DEFAULT_PERMISSION
        elif isinstance(item, dict) and isinstance(item.get("capability"), str):
            key = item["capability"]
            granted_perm = _permission_of(item)
        else:
            # Malformed grant entry — not a key the normalizer would accept;
            # surface it as undeclared so the approver sees a clean 422.
            continue
        if key not in declared_ranks:
            undeclared.append(key)
            continue
        granted_rank = _PERMISSION_AUTONOMY.get(
            granted_perm, _PERMISSION_AUTONOMY[DEFAULT_PERMISSION]
        )
        if granted_rank > declared_ranks[key]:
            escalated.append(
                {
                    "capability": key,
                    "granted": granted_perm,
                    "declared_max": _rank_to_perm(declared_ranks[key]),
                }
            )

    if undeclared:
        raise BusinessRuleError(
            "granted capabilities must be a subset of the declared capabilities",
            code="capability_not_declared",
            details={"undeclared": sorted(set(undeclared))},
        )
    if escalated:
        raise BusinessRuleError(
            "granted permission exceeds the declared permission (escalation)",
            code="capability_not_declared",
            details={"escalated": escalated},
        )


def _rank_to_perm(rank: int) -> str:
    for perm, value in _PERMISSION_AUTONOMY.items():
        if value == rank:
            return perm
    return DEFAULT_PERMISSION


__all__ = [
    "DEFAULT_PERMISSION",
    "assert_grants_subset_of_required",
    "capability_keys",
]
