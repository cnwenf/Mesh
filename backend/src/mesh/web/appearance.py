"""Per-request appearance resolution for the personalized HTML entry.

theme.md §2.3 ① (precise injection): the entry middleware resolves the
requester's theme negotiation chain so the first frame can be painted without
a wrong-theme flash. Mirrors the §2.2 chain, server-side:

1. user preference  ``users.settings.theme``
   - ``light``/``dark``  → that value, terminates;
   - ``system``          → terminates at this level and follows the OS; the
                           server cannot know the OS preference, so it injects
                           nothing (the client resolves via ``matchMedia``);
   - absent/``null``     → skip to level 2 (invalid persisted values are
                           treated as absent — binary convergence, never
                           injected as-is);
2. workspace default ``workspaces.settings.default_theme``
   - resolved from the ``/w/{slug}/`` route path segment (URL-derived identity,
     never from query/header), or — for the unauthenticated ``/invite`` entry —
     from the invitation-preview same-source data;
   - ``light``/``dark`` → that value; ``system``/absent → inject nothing;
3. system fallback — dynamic media-query result, not server-resolvable.

Security boundary (theme.md §5.3): the injected value is converged to the
binary ``light|dark``; the response carrying a resolved session is always
``Cache-Control: private, no-store`` so shared caches never store a
personalized shell. This module is read-only: it never mutates sessions,
users, or workspaces, and never issues credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.security import hash_token
from mesh.db.models.user import Session, User
from mesh.db.models.workspace import Workspace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

ResolvedMode = Literal["light", "dark"]

_SLUG_SEGMENT = re.compile(r"^/w/([^/?#]+)")
_INVITE_ENTRY = re.compile(r"^/invite(?:[/=?#]|$)")


@dataclass(frozen=True)
class AppearanceResolution:
    """Outcome of the entry negotiation for one HTML request.

    ``mode`` is the converged binary theme to inline-inject, or ``None`` when
    the server cannot resolve precisely (static shell served; client-side
    locator/skeleton tiers take over, theme.md §2.3 ②/③).

    ``personalized`` is ``True`` when the request carried a live session —
    even with ``mode is None`` the response must be ``private, no-store``
    (auth.md §3 cache boundary): a session-bearing request is never
    share-cacheable, regardless of whether injection occurred.
    """

    mode: ResolvedMode | None
    personalized: bool


def _binary(value: object) -> ResolvedMode | None:
    """Converge any persisted/derived value to the binary theme or None."""
    if value == "light" or value == "dark":
        return value
    return None


def workspace_slug_from_path(path: str) -> str | None:
    """Extract the ``/w/{slug}/`` identity segment from a route path.

    URL-derived (synchronous) — never from query params or headers.
    """
    match = _SLUG_SEGMENT.match(path)
    if match is None:
        return None
    return match.group(1)


def is_invite_entry(path: str) -> bool:
    """Whether the route path is the public invitation-accept entry."""
    return _INVITE_ENTRY.match(path) is not None


async def _session_user_settings(
    session_factory: async_sessionmaker[AsyncSession], cookie_value: str
) -> dict | None:
    """Resolve a live session cookie to its owner's ``users.settings``.

    Returns ``None`` for absent/unknown/revoked/expired sessions. Read-only.
    """
    token_hash = hash_token(cookie_value)
    async with session_factory() as db:
        row = (
            await db.execute(
                select(User.settings)
                .join(Session, Session.user_id == User.id)
                .where(
                    Session.token_hash == token_hash,
                    Session.revoked_at.is_(None),
                    Session.expires_at > func.now(),
                )
            )
        ).first()
    return row[0] if row is not None else None


async def _workspace_default_theme(
    session_factory: async_sessionmaker[AsyncSession], slug: str
) -> str | None:
    """Read ``workspaces.settings.default_theme`` by slug (live workspaces)."""
    async with session_factory() as db:
        settings = await db.scalar(
            select(Workspace.settings).where(
                Workspace.slug == slug, Workspace.deleted_at.is_(None)
            )
        )
    if settings is None:
        return None
    return (settings or {}).get("default_theme")


async def resolve_entry_appearance(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    cookie_value: str | None,
    path: str,
    invite_default_theme: Callable[[str], Awaitable[str | None]] | None = None,
    invite_token: str | None = None,
) -> AppearanceResolution:
    """Resolve the first-frame appearance for one HTML document request.

    ``invite_default_theme`` is the invitation-preview same-source resolver
    (theme.md §2.2): given an invitation token it returns the workspace's
    ``default_theme`` for a valid preview, else ``None``. Injected by the
    entry layer so this module stays free of service wiring.

    Never raises: any lookup failure degrades to no injection.
    """
    try:
        user_settings: dict | None = None
        if cookie_value:
            user_settings = await _session_user_settings(session_factory, cookie_value)
        personalized = user_settings is not None

        theme = (user_settings or {}).get("theme")
        if theme == "system":
            # Explicit system terminates at level 1 and follows the OS —
            # never falls back to the workspace default (theme.md §2.1).
            return AppearanceResolution(mode=None, personalized=personalized)
        user_mode = _binary(theme)
        if user_mode is not None:
            return AppearanceResolution(mode=user_mode, personalized=personalized)

        # Level 2: workspace default (absent/null/invalid user preference).
        slug = workspace_slug_from_path(path)
        if slug is not None:
            default = await _workspace_default_theme(session_factory, slug)
            return AppearanceResolution(
                mode=_binary(default), personalized=personalized
            )
        if is_invite_entry(path) and invite_token and invite_default_theme is not None:
            default = await invite_default_theme(invite_token)
            return AppearanceResolution(
                mode=_binary(default), personalized=personalized
            )
        return AppearanceResolution(mode=None, personalized=personalized)
    except Exception:
        # Entry injection must never break the HTML response; degrade to the
        # static shell (personalized only if a session was already resolved).
        return AppearanceResolution(mode=None, personalized=False)
