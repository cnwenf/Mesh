"""IntegrityError classification helpers (shared across services).

PostgreSQL reports unique-INDEX violations without a constraint name (only
true constraints carry one), so mapping a violation to a named conflict code
checks the driver attribute first and falls back to scanning the error text,
where the violated index name always appears.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def constraint_name(exc: IntegrityError) -> str | None:
    """The violated constraint/index name, when the driver exposes it."""
    return getattr(exc.orig, "constraint_name", None)


def violates(exc: IntegrityError, name: str) -> bool:
    """True when the integrity error comes from the named constraint/index."""
    if constraint_name(exc) == name:
        return True
    orig = getattr(exc, "orig", None)
    return orig is not None and name in str(orig)
