"""Workspace service — CRUD, slug redirects, settings, prefix registry.

Each public method owns its transaction (``session_factory() + begin()``) so
it can be exercised directly from tests without route plumbing. Tenant-bound
transactions set the ``mesh.workspace_id`` GUC up front so every read/write is
correct under RLS (restricted app role) and without it (owner role).

Locale single source (R3/R4, T32): the ONLY locale truth is
``settings.default_locale`` (default ``en``); there is no ``default_language``
column, response field or dual write.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.auth.audit import write_audit
from mesh.db.constraints import violates as _violates
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import (
    IdentifierPrefixRegistry,
    Workspace,
    WorkspaceSlugHistory,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.validation import validate_https_url, validate_iana_timezone, validate_locale

SLUG_PATTERN = re.compile(r"^[a-z0-9-]{2,32}$")
PREFIX_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,11}$")
DEFAULT_INBOX_PREFIX = "WS"
DEFAULT_SETTINGS: dict = {"default_locale": "en"}
WORKSPACE_CHANNEL = "workspace:{workspace_id}"
_NOT_FOUND_MESSAGE = "workspace not found"


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _validate_slug(slug: str) -> None:
    if not SLUG_PATTERN.match(slug):
        raise ValidationError(
            "slug must match ^[a-z0-9-]{2,32}$",
            details={"slug": slug[:64]},
        )


def _validate_prefix_key(key: str) -> None:
    if not PREFIX_KEY_PATTERN.match(key):
        raise ValidationError(
            "prefix must match ^[A-Z][A-Z0-9_]{1,11}$",
            details={"prefix": key[:32]},
        )


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        raise ValidationError("name must be 1-80 characters")


# settings.json known-key type validation (workspace.md §2.2): unknown keys
# pass through for forward compatibility; known keys are type-checked and the
# canonical ones carry their named 422 codes.
def _validate_settings_keys(patch: dict) -> None:
    def _type_error(key: str, expected: str) -> ValidationError:
        return ValidationError(
            f"settings.{key} must be {expected}", details={"key": key}
        )

    for key, value in patch.items():
        if key == "default_locale":
            validate_locale(value)  # 422 unsupported_locale
        elif key == "default_theme":
            from mesh.validation import validate_theme

            validate_theme(value)
        elif key in ("default_status_set", "default_project_visibility", "new_member_default_role"):
            if not isinstance(value, str):
                raise _type_error(key, "a string")
        elif key == "default_priorities":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise _type_error(key, "an array of strings")
        elif key == "inbox_issue_prefix":
            if not isinstance(value, str):
                raise _type_error(key, "a string")
        elif key == "status_strict_mode":
            # issue.md §3.4/§4.4:严格模式状态流转总开关(bool,默认 false)
            if not isinstance(value, bool):
                raise _type_error(key, "a boolean")
        elif key in ("invitation_max_uses_cap", "invitation_max_lifetime_hours_cap"):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise _type_error(key, "a positive integer")
        elif key == "seat_limit":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise _type_error(key, "an integer or null")
        elif key == "feature_flags":
            if not isinstance(value, dict):
                raise _type_error(key, "an object")
        # Unknown keys: pass through (§2.2 forward compatibility).


@dataclass(frozen=True)
class WorkspacePatch:
    """A PATCH /workspaces/{id} request — every field optional (unset = keep)."""

    name: str | None = None
    slug: str | None = None
    logo_url: str | None = None
    timezone: str | None = None
    settings: dict | None = None


def workspace_to_dict(workspace: Workspace, *, my_role: str, list_view: bool = False) -> dict:
    """Render a workspace for the §3.2 response envelopes (no default_language)."""
    if list_view:
        return {
            "id": workspace.id,
            "name": workspace.name,
            "slug": workspace.slug,
            "logo_url": workspace.logo_url,
            "my_role": my_role,
            "created_at": workspace.created_at,
        }
    return {
        "id": workspace.id,
        "name": workspace.name,
        "slug": workspace.slug,
        "logo_url": workspace.logo_url,
        "timezone": workspace.timezone,
        "settings": workspace.settings or {},
        "my_role": my_role,
        "created_at": workspace.created_at,
        "updated_at": workspace.updated_at,
    }


async def change_inbox_prefix(session: AsyncSession, *, workspace_id: uuid.UUID, new_key: str) -> None:
    """Rotate the inbox prefix (§2.6 semantics ②): retire old, register new.

    The old prefix is retired PERMANENTLY — historic identifiers are never
    renumbered (README §6.3). Any conflict with a registered key (including
    retired history) → 422 ``prefix_reserved``.
    """
    conflict = await session.scalar(
        select(IdentifierPrefixRegistry.id).where(
            IdentifierPrefixRegistry.workspace_id == workspace_id,
            IdentifierPrefixRegistry.key == new_key,
        )
    )
    if conflict is not None:
        raise BusinessRuleError(
            "prefix is reserved or already in use",
            code="prefix_reserved",
            details={"prefix": new_key},
        )
    current = await session.scalar(
        select(IdentifierPrefixRegistry).where(
            IdentifierPrefixRegistry.workspace_id == workspace_id,
            IdentifierPrefixRegistry.kind == "inbox",
        )
    )
    if current is not None:
        current.kind = "retired"
    session.add(
        IdentifierPrefixRegistry(workspace_id=workspace_id, key=new_key, kind="inbox")
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        if _violates(exc, "uq_prefix_registry_ws_key"):
            raise BusinessRuleError(
                "prefix is reserved or already in use",
                code="prefix_reserved",
                details={"prefix": new_key},
            ) from exc
        raise


async def occupy_project_prefix(
    session: AsyncSession, *, workspace_id: uuid.UUID, key: str, project_id: uuid.UUID
) -> None:
    """Register a project key in the same transaction as project creation.

    Hook for the project module (workspace.md §2.6 semantics ①, project.md
    §3.3): conflict with ANY registered key (project/inbox/retired) →
    409 ``project_key_taken``.
    """
    session.add(
        IdentifierPrefixRegistry(
            workspace_id=workspace_id, key=key, kind="project", project_id=project_id
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        if _violates(exc, "uq_prefix_registry_ws_key"):
            raise ConflictError(
                "project key is already taken in this workspace",
                code="project_key_taken",
                details={"key": key},
            ) from exc
        raise


async def next_inbox_issue_number(session: AsyncSession, *, workspace_id: uuid.UUID) -> int:
    """Row-locked increment of ``workspaces.inbox_issue_seq`` (README §6.3/T15).

    The same mechanism as ``projects.issue_seq``: concurrent inbox-issue
    creation never produces duplicate numbers under
    ``UNIQUE(workspace_id, identifier)``.
    """
    return (
        await session.execute(
            text(
                "UPDATE workspaces SET inbox_issue_seq = inbox_issue_seq + 1 "
                "WHERE id = :ws RETURNING inbox_issue_seq"
            ),
            {"ws": workspace_id},
        )
    ).scalar_one()


class WorkspaceService:
    """Stateless orchestrator over the workspace tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, clock=None) -> None:
        self._factory = session_factory
        self._clock = clock

    # -- W1 create --------------------------------------------------------------

    async def create_workspace(
        self,
        *,
        user: User,
        name: str,
        slug: str,
        timezone: str = "UTC",
        logo_url: str | None = None,
        settings: dict | None = None,
    ) -> dict:
        _validate_name(name)
        _validate_slug(slug)
        validate_iana_timezone(timezone)
        if logo_url is not None:
            validate_https_url(logo_url, field="logo_url")
        merged_settings = dict(DEFAULT_SETTINGS)
        if settings:
            _validate_settings_keys(settings)
            merged_settings.update(settings)
        prefix_key = merged_settings.get("inbox_issue_prefix", DEFAULT_INBOX_PREFIX)
        _validate_prefix_key(prefix_key)

        async with self._factory() as session, session.begin():
            workspace = Workspace(
                name=name,
                slug=slug,
                timezone=timezone,
                logo_url=logo_url,
                settings=merged_settings,
            )
            session.add(workspace)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_workspaces_slug"):
                    raise ConflictError(
                        "slug is already taken", code="slug_taken", details={"slug": slug}
                    ) from exc
                raise

            # Tenant tables below require the GUC (RLS on the app-role path).
            await set_tenant_context(session, workspace.id)
            member = Member(
                workspace_id=workspace.id,
                member_type="human",
                user_id=user.id,
                role="owner",
                joined_at=_now(self._clock),
            )
            session.add(member)
            session.add(
                IdentifierPrefixRegistry(
                    workspace_id=workspace.id, key=prefix_key, kind="inbox"
                )
            )
            # Seed the canonical issue status set in the SAME transaction
            # (issue.md §1.2.3 / README §6.3: at-least-one default per scope,
            # guaranteed transactionally). Lazy import keeps the dependency
            # direction workspace → issue one-way at module load.
            from mesh.issue.statuses import seed_default_statuses

            await seed_default_statuses(session, workspace_id=workspace.id)
            await session.flush()
            await emit_realtime(
                session,
                workspace_id=workspace.id,
                channel=WORKSPACE_CHANNEL.format(workspace_id=workspace.id),
                event="member.added",
                data={
                    "member_id": str(member.id),
                    "member_type": "human",
                    "role": "owner",
                },
            )
            # Tenant creation is a sensitive write (auth.md §5.3).
            await write_audit(
                session,
                workspace_id=workspace.id,
                actor_member_id=member.id,
                actor_kind="member",
                action="workspace.created",
                resource_type="workspace",
                resource_id=workspace.id,
                metadata={"slug": workspace.slug},
            )
            result = workspace_to_dict(workspace, my_role="owner")
        return result

    # -- W2 list ----------------------------------------------------------------

    async def list_workspaces(
        self, *, user: User, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[dict], str | None]:
        """List the user's workspaces (active membership, non-deleted).

        Membership is read through the ``mesh_my_workspaces`` SECURITY DEFINER
        function — the caller's workspaces are not known up front, so no tenant
        GUC can be set first (RLS stays fail-closed everywhere else).
        """
        limit = max(1, min(limit, 100))
        query = (
            "SELECT w.id, w.name, w.slug, w.logo_url, w.created_at, m.role "
            "FROM mesh_my_workspaces(:uid) m "
            "JOIN workspaces w ON w.id = m.workspace_id "
            "WHERE m.status = 'active' AND w.deleted_at IS NULL "
        )
        params: dict = {"uid": user.id, "lim": limit + 1}
        if cursor is not None:
            position = decode_cursor(cursor)
            query += "AND (w.created_at, w.id) < (:sv, :cid) "
            params["sv"] = position.sort_value
            params["cid"] = position.id
        # id DESC to match the (created_at, id) < cursor predicate on ties.
        query += "ORDER BY w.created_at DESC, w.id DESC LIMIT :lim"

        async with self._factory() as session:
            rows = (await session.execute(text(query), params)).all()
        items = [
            {
                "id": row.id,
                "name": row.name,
                "slug": row.slug,
                "logo_url": row.logo_url,
                "my_role": row.role,
                "created_at": row.created_at,
            }
            for row in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return items, next_cursor

    # -- W4 update ----------------------------------------------------------------

    async def update_workspace(
        self, *, actor: Member, workspace: Workspace, patch: WorkspacePatch
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace.id)
            ws = await session.scalar(
                select(Workspace).where(
                    Workspace.id == workspace.id, Workspace.deleted_at.is_(None)
                )
            )
            if ws is None:
                raise NotFoundError(_NOT_FOUND_MESSAGE)

            changes: dict = {}
            if patch.name is not None and patch.name != ws.name:
                _validate_name(patch.name)
                changes["name"] = patch.name
            if patch.slug is not None and patch.slug != ws.slug:
                _validate_slug(patch.slug)
                taken = await session.scalar(
                    select(Workspace.id).where(
                        Workspace.slug == patch.slug,
                        Workspace.deleted_at.is_(None),
                        Workspace.id != ws.id,
                    )
                )
                if taken is not None:
                    raise ConflictError(
                        "slug is already taken",
                        code="slug_taken",
                        details={"slug": patch.slug},
                    )
                changes["slug"] = patch.slug
            if patch.logo_url is not None and patch.logo_url != ws.logo_url:
                validate_https_url(patch.logo_url, field="logo_url")
                changes["logo_url"] = patch.logo_url
            if patch.timezone is not None and patch.timezone != ws.timezone:
                validate_iana_timezone(patch.timezone)
                changes["timezone"] = patch.timezone

            merged_settings: dict | None = None
            if patch.settings is not None:
                _validate_settings_keys(patch.settings)
                merged_settings = {**(ws.settings or {}), **patch.settings}
                if merged_settings != (ws.settings or {}):
                    changes["settings"] = merged_settings

            if not changes:
                # §6.9: saving with no field changes is a no-op (no event).
                return workspace_to_dict(ws, my_role=actor.role)

            # Prefix rotation happens only when the effective prefix changes.
            if merged_settings is not None:
                old_prefix = (ws.settings or {}).get(
                    "inbox_issue_prefix", DEFAULT_INBOX_PREFIX
                )
                new_prefix = merged_settings.get("inbox_issue_prefix", DEFAULT_INBOX_PREFIX)
                _validate_prefix_key(new_prefix)
                if new_prefix != old_prefix:
                    await change_inbox_prefix(
                        session, workspace_id=ws.id, new_key=new_prefix
                    )

            old_slug = ws.slug
            for field in ("name", "slug", "logo_url", "timezone"):
                if field in changes:
                    setattr(ws, field, changes[field])
            if merged_settings is not None:
                ws.settings = merged_settings
            ws.updated_at = _now(self._clock)

            if "slug" in changes:
                # An old slug maps to the workspace that released it; when the
                # same slug was released before (A releases, B takes, B
                # releases), the mapping follows the most recent releaser (W6).
                await session.execute(
                    pg_insert(WorkspaceSlugHistory)
                    .values(workspace_id=ws.id, old_slug=old_slug)
                    .on_conflict_do_update(
                        index_elements=["old_slug"],
                        set_={"workspace_id": ws.id},
                    )
                )
            try:
                # Explicit flush: a concurrent slug grab loses the pre-check
                # race but must still surface as 409, not a 500 at commit.
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_workspaces_slug"):
                    raise ConflictError(
                        "slug is already taken",
                        code="slug_taken",
                        details={"slug": changes.get("slug", ws.slug)},
                    ) from exc
                raise
            await emit_realtime(
                session,
                workspace_id=ws.id,
                channel=WORKSPACE_CHANNEL.format(workspace_id=ws.id),
                event="workspace.updated",
                data={"workspace_id": str(ws.id), "changes": changes},
            )
            await write_audit(
                session,
                workspace_id=ws.id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="workspace.updated",
                resource_type="workspace",
                resource_id=ws.id,
                metadata={"changes": changes},
            )
            result = workspace_to_dict(ws, my_role=actor.role)
        return result

    # -- W10 delete / restore -----------------------------------------------------

    async def delete_workspace(
        self, *, actor: Member, workspace: Workspace, confirm_slug: str
    ) -> None:
        """Soft-delete (owner only, slug typed as second confirmation)."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace.id)
            ws = await session.scalar(
                select(Workspace).where(
                    Workspace.id == workspace.id, Workspace.deleted_at.is_(None)
                )
            )
            if ws is None:
                raise NotFoundError(_NOT_FOUND_MESSAGE)
            if actor.role != "owner":
                raise ForbiddenError("only the workspace owner can delete it")
            if confirm_slug != ws.slug:
                raise ValidationError(
                    "confirmation slug does not match the workspace slug",
                    details={"expected_field": "confirm_slug"},
                )
            ws.deleted_at = _now(self._clock)
            ws.updated_at = ws.deleted_at
            await emit_realtime(
                session,
                workspace_id=ws.id,
                channel=WORKSPACE_CHANNEL.format(workspace_id=ws.id),
                event="workspace.deleted",
                data={"workspace_id": str(ws.id)},
            )
            await write_audit(
                session,
                workspace_id=ws.id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="workspace.deleted",
                resource_type="workspace",
                resource_id=ws.id,
            )

    async def restore_workspace(self, *, actor: Member, workspace_id: uuid.UUID) -> dict:
        """Restore a soft-deleted workspace (owner only, within retention)."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            ws = await session.scalar(select(Workspace).where(Workspace.id == workspace_id))
            if ws is None:
                raise NotFoundError(_NOT_FOUND_MESSAGE)
            member = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id,
                    Member.id == actor.id,
                    Member.status == "active",
                )
            )
            if member is None:
                raise NotFoundError(_NOT_FOUND_MESSAGE)
            if member.role != "owner":
                raise ForbiddenError("only the workspace owner can restore it")
            if ws.deleted_at is None:
                return workspace_to_dict(ws, my_role=member.role)  # already active
            # Capture before the flush: a failed flush expires ORM state, and
            # touching ws afterwards would refresh over the dead transaction.
            current_slug = ws.slug
            ws.deleted_at = None
            ws.updated_at = _now(self._clock)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_workspaces_slug"):
                    raise ConflictError(
                        "the workspace slug was taken while deleted",
                        code="slug_taken",
                        details={"slug": current_slug},
                    ) from exc
                raise
            await emit_realtime(
                session,
                workspace_id=ws.id,
                channel=WORKSPACE_CHANNEL.format(workspace_id=ws.id),
                event="workspace.updated",
                data={"workspace_id": str(ws.id), "changes": {"restored": True}},
            )
            await write_audit(
                session,
                workspace_id=ws.id,
                actor_member_id=member.id,
                actor_kind="member",
                action="workspace.restored",
                resource_type="workspace",
                resource_id=ws.id,
            )
            result = workspace_to_dict(ws, my_role=member.role)
        return result
