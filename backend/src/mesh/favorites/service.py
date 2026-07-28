"""Favorites service (README §6.19).

Stateless orchestrator over the session factory, following the house
pattern: each method owns its transaction, sets the tenant GUC, and returns
plain dicts. Target existence is validated per type against the owning
table; list responses prune dead (soft-deleted / removed) targets.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mesh.db.models.chat import FAVORITE_TARGET_TYPE_VALUES, ChatSession, Favorite
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import NotFoundError, ValidationError

_TARGET_NOT_FOUND = "favorite target not found"

# Owning table per target type (all carry ``deleted_at`` soft-delete).
_TARGET_TABLES: dict[str, str] = {
    "issue": "issues",
    "project": "projects",
    "view": "views",
    "chat_session": "chat_sessions",
}

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"t": _iso(created_at), "i": str(row_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["i"])
    except (ValueError, KeyError, binascii.Error, TypeError) as exc:
        raise ValidationError("invalid cursor", code="invalid_cursor") from exc


def _render(row: Favorite) -> dict:
    return {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "member_id": str(row.member_id),
        "target_type": row.target_type,
        "target_id": str(row.target_id),
        "created_at": _iso(row.created_at),
    }


class FavoritesService:
    """CRUD + listing over the unified favorites model."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def _live_target_ids(
        self, session, workspace_id: uuid.UUID, target_type: str, target_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Subset of ``target_ids`` that still exist and are not soft-deleted."""
        if not target_ids:
            return set()
        table = _TARGET_TABLES[target_type]
        rows = (
            await session.execute(
                text(
                    f"SELECT id FROM {table} "
                    f"WHERE workspace_id = :ws AND id = ANY(:ids) AND deleted_at IS NULL"
                ),
                {"ws": workspace_id, "ids": target_ids},
            )
        ).scalars().all()
        return set(rows)

    async def assert_target_live(
        self, session, workspace_id: uuid.UUID, target_type: str, target_id: uuid.UUID
    ) -> None:
        """404 unless the target exists in-workspace and is not soft-deleted."""
        live = await self._live_target_ids(session, workspace_id, target_type, [target_id])
        if target_id not in live:
            raise NotFoundError(_TARGET_NOT_FOUND)

    async def _assert_chat_session_pinnable(
        self, session, *, workspace_id: uuid.UUID, target_id: uuid.UUID, actor: Member
    ) -> None:
        """M2: a chat_session favorite is owner-only — uniform 404 otherwise.

        Without this, ``PUT /favorites/chat_session/{id}`` is an existence
        oracle for another member's private session (§3.5 forbids leaking
        existence). Missing, soft-deleted, or foreign-owner all map to 404.
        """
        row = await session.scalar(
            select(ChatSession).where(
                ChatSession.workspace_id == workspace_id, ChatSession.id == target_id
            )
        )
        if row is None or row.deleted_at is not None or row.owner_id != actor.id:
            raise NotFoundError(_TARGET_NOT_FOUND)

    async def put(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
    ) -> dict:
        if target_type not in FAVORITE_TARGET_TYPE_VALUES:
            raise ValidationError(
                "invalid target_type", details={"target_type": str(target_type)[:32]}
            )
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            if target_type == "chat_session":
                await self._assert_chat_session_pinnable(
                    session, workspace_id=workspace_id, target_id=target_id, actor=actor
                )
            else:
                await self.assert_target_live(session, workspace_id, target_type, target_id)
            # Idempotent PUT: the partial-less unique (member, type, target)
            # absorbs the race; an existing row is returned unchanged.
            stmt = (
                pg_insert(Favorite)
                .values(
                    workspace_id=workspace_id,
                    member_id=actor.id,
                    target_type=target_type,
                    target_id=target_id,
                    created_at=_utcnow(),
                )
                .on_conflict_do_nothing(
                    index_elements=["member_id", "target_type", "target_id"]
                )
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(Favorite).where(
                    Favorite.member_id == actor.id,
                    Favorite.target_type == target_type,
                    Favorite.target_id == target_id,
                )
            )
        assert row is not None  # inserted or pre-existing
        return _render(row)

    async def remove(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
    ) -> None:
        """Idempotent DELETE — removing an absent favorite is a no-op."""
        if target_type not in FAVORITE_TARGET_TYPE_VALUES:
            raise ValidationError(
                "invalid target_type", details={"target_type": str(target_type)[:32]}
            )
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await session.execute(
                delete(Favorite).where(
                    Favorite.member_id == actor.id,
                    Favorite.target_type == target_type,
                    Favorite.target_id == target_id,
                )
            )

    async def list(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        target_type: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        limit = max(1, min(int(limit), MAX_LIMIT))
        if target_type is not None and target_type not in FAVORITE_TARGET_TYPE_VALUES:
            raise ValidationError(
                "invalid target_type", details={"target_type": str(target_type)[:32]}
            )
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(Favorite)
                .where(Favorite.workspace_id == workspace_id, Favorite.member_id == actor.id)
                .order_by(Favorite.created_at.desc(), Favorite.id.desc())
            )
            if target_type is not None:
                stmt = stmt.where(Favorite.target_type == target_type)
            if cursor is not None:
                cursor_created_at, cursor_id = _decode_cursor(cursor)
                # asyncpg rejects anonymous composite parameters — expanded
                # OR form of the keyset comparison.
                stmt = stmt.where(
                    or_(
                        Favorite.created_at < cursor_created_at,
                        (Favorite.created_at == cursor_created_at)
                        & (Favorite.id < cursor_id),
                    )
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars().all())
            # Prune dead targets per type (§6.19 — dead targets never list).
            kept: list[Favorite] = []
            for group_type in {row.target_type for row in rows[:limit]}:
                ids = [row.target_id for row in rows[:limit] if row.target_type == group_type]
                live = await self._live_target_ids(session, workspace_id, group_type, ids)
                kept.extend(row for row in rows[:limit] if row.target_id in live)
            kept.sort(key=lambda row: (row.created_at, str(row.id)), reverse=True)
        next_cursor = None
        if len(rows) > limit and kept:
            last = kept[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return {"items": [_render(row) for row in kept], "next_cursor": next_cursor}

    async def cleanup_for_target(
        self, session, *, workspace_id: uuid.UUID, target_type: str, target_id: uuid.UUID
    ) -> None:
        """Drop every member's favorite rows for a removed target.

        Called inside the owning module's business transaction (polymorphic
        logical FK consistency — §6.2 rule 4: service layer, no physical FK).
        """
        await session.execute(
            delete(Favorite).where(
                Favorite.workspace_id == workspace_id,
                Favorite.target_type == target_type,
                Favorite.target_id == target_id,
            )
        )
