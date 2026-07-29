"""Entry appearance negotiation truth table (theme.md §2.2/§2.3 ①).

Real PostgreSQL. Covers the server-side negotiation chain behind the
personalized HTML entry: session-cookie resolution (hash-only lookup,
revoked/expired rejection), the three-value user-preference semantics
(light/dark terminate; system terminates and follows OS → no injection;
absent/null/invalid → workspace level), workspace-default resolution from
the /w/{slug}/ route segment, and the unauthenticated /invite entry via the
invitation-preview same-source resolver.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from mesh.auth.security import hash_token
from mesh.web.appearance import (
    AppearanceResolution,
    is_invite_entry,
    resolve_entry_appearance,
    workspace_slug_from_path,
)

pytestmark = pytest.mark.unit


# --- pure helpers --------------------------------------------------------------


def test_workspace_slug_from_path_table():
    assert workspace_slug_from_path("/w/acme/board") == "acme"
    assert workspace_slug_from_path("/w/acme") == "acme"
    assert workspace_slug_from_path("/w/acme/issues/by-identifier/ACM-1") == "acme"
    assert workspace_slug_from_path("/w/acm?x=1") == "acm"
    assert workspace_slug_from_path("/settings") is None
    assert workspace_slug_from_path("/invite/invtk_abc") is None
    assert workspace_slug_from_path("/") is None
    assert workspace_slug_from_path("/w/") is None


def test_is_invite_entry_table():
    assert is_invite_entry("/invite/invtk_abc") is True
    assert is_invite_entry("/invite") is True
    assert is_invite_entry("/invited") is False
    assert is_invite_entry("/w/invite/board") is False


# --- fixtures / helpers --------------------------------------------------------


async def _seed_user(session_factory, *, email: str, theme: object = "__absent__"):
    """Insert a user; return its id. ``theme`` sentinel "__absent__" omits the key."""
    settings: dict = {} if theme == "__absent__" else {"theme": theme}
    async with session_factory() as session, session.begin():
        return (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name, settings) "
                    "VALUES (:e, 'U', CAST(:s AS jsonb)) RETURNING id"
                ),
                {"e": email, "s": json.dumps(settings)},
            )
        ).scalar_one()


async def _seed_session(
    session_factory,
    user_id,
    token: str,
    *,
    expires_in: timedelta = timedelta(days=1),
    revoked: bool = False,
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO sessions (user_id, token_hash, type, expires_at, revoked_at) "
                "VALUES (:u, :h, 'web', :exp, :rev)"
            ),
            {
                "u": user_id,
                "h": hash_token(token),
                "exp": datetime.now(UTC) + expires_in,
                "rev": datetime.now(UTC) if revoked else None,
            },
        )


async def _seed_workspace(session_factory, *, slug: str, default_theme: str | None):
    """Insert a workspace (with owner member rows skipped — not needed here)."""
    settings: dict = {} if default_theme is None else {"default_theme": default_theme}
    async with session_factory() as session, session.begin():
        return (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug, settings) "
                    "VALUES (:n, :s, CAST(:st AS jsonb)) RETURNING id"
                ),
                {"n": f"WS {slug}", "s": slug, "st": json.dumps(settings)},
            )
        ).scalar_one()


# --- chain truth table ---------------------------------------------------------


async def test_no_cookie_is_static_shell(session_factory):
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value=None, path="/w/acme/board"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)


async def test_unknown_cookie_is_static_shell(session_factory):
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_ghost", path="/w/acme/board"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)


async def test_revoked_session_is_static_shell(session_factory):
    uid = await _seed_user(session_factory, email="rev@corp.com", theme="dark")
    await _seed_session(session_factory, uid, "mesh_rft_rev", revoked=True)
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_rev", path="/settings"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)


async def test_expired_session_is_static_shell(session_factory):
    uid = await _seed_user(session_factory, email="exp@corp.com", theme="dark")
    await _seed_session(
        session_factory, uid, "mesh_rft_exp", expires_in=timedelta(minutes=-5)
    )
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_exp", path="/settings"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)


async def test_user_dark_terminates(session_factory):
    uid = await _seed_user(session_factory, email="dark@corp.com", theme="dark")
    await _seed_session(session_factory, uid, "mesh_rft_dark")
    await _seed_workspace(session_factory, slug="acme", default_theme="light")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_dark", path="/w/acme/board"
    )
    assert resolution == AppearanceResolution(mode="dark", personalized=True)


async def test_user_light_terminates_over_workspace(session_factory):
    uid = await _seed_user(session_factory, email="light@corp.com", theme="light")
    await _seed_session(session_factory, uid, "mesh_rft_light")
    await _seed_workspace(session_factory, slug="acme2", default_theme="dark")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_light", path="/w/acme2/board"
    )
    assert resolution == AppearanceResolution(mode="light", personalized=True)


async def test_user_system_ignores_workspace_and_injects_nothing(session_factory):
    # theme.md §2.1: explicit system terminates at level 1 and follows the OS;
    # the server cannot know the OS preference → no injection, but the response
    # is still personalized (private, no-store).
    uid = await _seed_user(session_factory, email="sys@corp.com", theme="system")
    await _seed_session(session_factory, uid, "mesh_rft_sys")
    await _seed_workspace(session_factory, slug="acme3", default_theme="dark")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_sys", path="/w/acme3/board"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=True)


async def test_absent_theme_falls_to_workspace_default(session_factory):
    uid = await _seed_user(session_factory, email="inherit@corp.com")  # absent
    await _seed_session(session_factory, uid, "mesh_rft_inherit")
    await _seed_workspace(session_factory, slug="acme4", default_theme="dark")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_inherit", path="/w/acme4/board"
    )
    assert resolution == AppearanceResolution(mode="dark", personalized=True)


async def test_null_theme_falls_to_workspace_default(session_factory):
    uid = await _seed_user(session_factory, email="null@corp.com", theme=None)
    await _seed_session(session_factory, uid, "mesh_rft_null")
    await _seed_workspace(session_factory, slug="acme5", default_theme="dark")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_null", path="/w/acme5/"
    )
    assert resolution == AppearanceResolution(mode="dark", personalized=True)


async def test_invalid_persisted_theme_converges_to_workspace_level(session_factory):
    # Binary convergence (theme.md §5.3): an invalid persisted value is never
    # injected as-is; it behaves as absent and falls through to level 2.
    uid = await _seed_user(session_factory, email="evil@corp.com", theme="evil")
    await _seed_session(session_factory, uid, "mesh_rft_evil")
    await _seed_workspace(session_factory, slug="acme6", default_theme="dark")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_evil", path="/w/acme6/board"
    )
    assert resolution == AppearanceResolution(mode="dark", personalized=True)


async def test_absent_theme_without_slug_segment_injects_nothing(session_factory):
    uid = await _seed_user(session_factory, email="noslug@corp.com")
    await _seed_session(session_factory, uid, "mesh_rft_noslug")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_noslug", path="/settings"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=True)


async def test_workspace_default_system_injects_nothing(session_factory):
    uid = await _seed_user(session_factory, email="wssystem@corp.com")
    await _seed_session(session_factory, uid, "mesh_rft_wssystem")
    await _seed_workspace(session_factory, slug="acme7", default_theme="system")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_wssystem", path="/w/acme7/board"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=True)


async def test_unknown_slug_injects_nothing(session_factory):
    uid = await _seed_user(session_factory, email="ghostws@corp.com")
    await _seed_session(session_factory, uid, "mesh_rft_ghostws")
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_ghostws", path="/w/ghost/board"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=True)


async def test_soft_deleted_workspace_injects_nothing(session_factory):
    uid = await _seed_user(session_factory, email="deletedws@corp.com")
    await _seed_session(session_factory, uid, "mesh_rft_deletedws")
    ws_id = await _seed_workspace(session_factory, slug="acme8", default_theme="dark")
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE workspaces SET deleted_at = now() WHERE id = :id"),
            {"id": ws_id},
        )
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value="mesh_rft_deletedws", path="/w/acme8/board"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=True)


async def test_invite_entry_uses_preview_same_source_unauthenticated(session_factory):
    # theme.md §2.2: the unauthenticated invite-accept entry resolves level 2
    # from the invitation-preview same-source data; personalized stays False
    # (no session → the static shell may be cached).
    calls: list[str] = []

    async def preview_resolver(token: str) -> str | None:
        calls.append(token)
        return "dark"

    resolution = await resolve_entry_appearance(
        session_factory,
        cookie_value=None,
        path="/invite/invtk_real",
        invite_default_theme=preview_resolver,
        invite_token="invtk_real",
    )
    assert resolution == AppearanceResolution(mode="dark", personalized=False)
    assert calls == ["invtk_real"]


async def test_invite_entry_invalid_preview_injects_nothing(session_factory):
    async def preview_resolver(token: str) -> str | None:
        return None

    resolution = await resolve_entry_appearance(
        session_factory,
        cookie_value=None,
        path="/invite/invtk_bad",
        invite_default_theme=preview_resolver,
        invite_token="invtk_bad",
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)


async def test_invite_entry_without_token_injects_nothing(session_factory):
    resolution = await resolve_entry_appearance(
        session_factory, cookie_value=None, path="/invite"
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)


async def test_invitation_preview_same_source_integration(session_factory):
    """End-to-end with the real InvitationService preview as the resolver."""
    from mesh.workspace.invitations import InvitationService
    from mesh.workspace.service import WorkspaceService

    ws_service = WorkspaceService(session_factory)
    user_id = await _seed_user(session_factory, email="owner-int@corp.com")
    from mesh.db.models.user import User

    user = User(id=user_id, email="owner-int@corp.com", display_name="U")
    created = await ws_service.create_workspace(user=user, name="Theme WS", slug="theme-ws")
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspaces SET settings = coalesce(settings, '{}'::jsonb) "
                "|| '{\"default_theme\": \"dark\"}'::jsonb WHERE id = :id"
            ),
            {"id": created["id"]},
        )
    async with session_factory() as session:
        member_rows = await session.execute(
            text("SELECT id FROM members WHERE workspace_id = :w"), {"w": created["id"]}
        )
        member_id = member_rows.scalar_one()
    from mesh.db.models.member import Member

    admin = Member(id=member_id, workspace_id=created["id"], user_id=user_id, role="owner")
    inv_service = InvitationService(session_factory)
    invitation = (await inv_service.create_invitations(actor=admin, workspace_id=created["id"]))[0]
    token = invitation["invite_link"].rsplit("/", 1)[-1]

    async def preview_resolver(t: str) -> str | None:
        preview = await inv_service.preview_invitation(token=t)
        if not preview.get("valid"):
            return None
        return preview.get("appearance", {}).get("default_theme")

    resolution = await resolve_entry_appearance(
        session_factory,
        cookie_value=None,
        path=f"/invite/{token}",
        invite_default_theme=preview_resolver,
        invite_token=token,
    )
    assert resolution == AppearanceResolution(mode="dark", personalized=False)


async def test_resolver_failure_degrades_to_static_shell(session_factory):
    # The entry must never break the HTML response: a raising resolver
    # degrades to no injection, not to a 500.
    async def broken_resolver(token: str) -> str | None:
        raise RuntimeError("boom")

    resolution = await resolve_entry_appearance(
        session_factory,
        cookie_value=None,
        path="/invite/invtk_x",
        invite_default_theme=broken_resolver,
        invite_token="invtk_x",
    )
    assert resolution == AppearanceResolution(mode=None, personalized=False)
