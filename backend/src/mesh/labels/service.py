"""Label & custom-field definition service (label-property.md §2–§3, definition layer).

Stateless orchestrator over ``labels`` / ``custom_field_defs`` /
``custom_field_options``. Each public method owns its transaction, sets the
tenant GUC (RLS floor), enforces scope-level authorization (workspace admin
OR project lead for project-scoped definitions, §3.4), validates per-type
config/default shapes (named 422 codes), emits the §6.7 registered events
through the outbox's unique write path, and writes the audit trail.

The issue-association surface (issue_labels / per-issue values, merge,
``issue.labels_changed`` …) is deliberately NOT here — it lands with the
issue-module increment (MES-32 remainder, gated on MES-31).
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.constraints import violates
from mesh.db.models.label import (
    CUSTOM_FIELD_TYPE_VALUES,
    SELECT_FIELD_TYPES,
    CustomFieldDef,
    CustomFieldOption,
    Label,
)
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime

LABEL_CHANNEL = "workspace:{workspace_id}:labels"
CUSTOM_FIELDS_CHANNEL = "workspace:{workspace_id}:custom_fields"
PROJECT_CHANNEL = "project:{project_id}"

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200  # §3.4: collections are small; clients may one-shot ≤200

LABEL_NAME_MAX = 50
FIELD_NAME_MAX = 100
OPTION_NAME_MAX = 100
TEXT_DEFAULT_MAX = 2000
URL_DEFAULT_MAX = 2048

COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
REQUIRED_ON_PATTERN = re.compile(r"^(save|status:[a-z_]+)$")

_LABEL_NOT_FOUND = "label not found"
_FIELD_DEF_NOT_FOUND = "custom field not found"
_OPTION_NOT_FOUND = "option not found"
_PROJECT_NOT_FOUND = "project not found"


class _Unset:
    """Sentinel distinguishing "omitted" from an explicit null in PATCHes."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return "<UNSET>"


UNSET = _Unset()


@dataclass(frozen=True)
class LabelPatch:
    name: str | _Unset = UNSET
    color: str | _Unset = UNSET
    description: str | None | _Unset = UNSET


@dataclass(frozen=True)
class FieldDefPatch:
    name: str | _Unset = UNSET
    is_required: bool | _Unset = UNSET
    required_on: list[Any] | _Unset = UNSET
    default_value: Any | _Unset = UNSET
    config: dict[str, Any] | _Unset = UNSET
    position: float | _Unset = UNSET
    is_active: bool | _Unset = UNSET


@dataclass(frozen=True)
class OptionPatch:
    name: str | _Unset = UNSET
    color: str | None | _Unset = UNSET
    position: float | _Unset = UNSET
    is_active: bool | _Unset = UNSET


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _isoformat(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _limit_page(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", code="invalid_limit")
    return min(limit, MAX_PAGE_LIMIT)


# ---------------------------------------------------------------------------
# request validation (400 validation_error / 422 invalid_field_config)
# ---------------------------------------------------------------------------


def _validate_label_name(name: str) -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= LABEL_NAME_MAX:
        raise ValidationError(f"label name must be 1-{LABEL_NAME_MAX} characters")


def _validate_color(value: str | None, *, field: str = "color") -> None:
    if value is None:
        return
    if not isinstance(value, str) or not COLOR_PATTERN.match(value):
        raise ValidationError(
            f"{field} must be a #RRGGBB hex color",
            details={field: value[:32] if isinstance(value, str) else None},
        )


def _validate_field_name(name: str) -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= FIELD_NAME_MAX:
        raise ValidationError(f"name must be 1-{FIELD_NAME_MAX} characters")


def _validate_field_key(field_key: str) -> None:
    if not isinstance(field_key, str) or not FIELD_KEY_PATTERN.match(field_key):
        raise ValidationError(
            "field_key must match ^[a-z][a-z0-9_]{0,49}$",
            details={"field_key": field_key[:64] if isinstance(field_key, str) else None},
        )


def _validate_field_type(field_type: str) -> None:
    if field_type not in CUSTOM_FIELD_TYPE_VALUES:
        raise ValidationError(
            "unsupported field type",
            details={"type": field_type, "supported": list(CUSTOM_FIELD_TYPE_VALUES)},
        )


def _validate_option_name(name: str) -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= OPTION_NAME_MAX:
        raise ValidationError(f"option name must be 1-{OPTION_NAME_MAX} characters")


def _validate_position(position: float) -> None:
    if isinstance(position, bool) or not isinstance(position, (int, float)):
        raise ValidationError("position must be a number")
    if not math.isfinite(position):
        raise ValidationError("position must be finite")


def _validate_required_on(required_on: list[Any]) -> None:
    if not isinstance(required_on, list) or not all(
        isinstance(item, str) and REQUIRED_ON_PATTERN.match(item) for item in required_on
    ):
        raise ValidationError(
            "required_on entries must be 'save' or 'status:<category>'",
            details={"required_on": [repr(item)[:32] for item in required_on][:8]},
        )


def _config_error(message: str, details: dict[str, Any] | None = None) -> BusinessRuleError:
    return BusinessRuleError(message, code="invalid_field_config", details=details)


# Allowed per-type config keys (§2.4: numeric precision/unit, date format,
# url validation). Types not listed accept no config at all.
_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "number": frozenset({"precision", "unit", "min", "max"}),
    "date": frozenset({"format"}),
    "datetime": frozenset({"format"}),
    "url": frozenset({"require_https"}),
}


def _validate_config(field_type: str, config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise _config_error("config must be an object")
    allowed = _CONFIG_KEYS.get(field_type, frozenset())
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise _config_error(
            f"config keys not allowed for type {field_type}",
            details={"unknown_keys": unknown, "allowed": sorted(allowed)},
        )
    if field_type == "number":
        precision = config.get("precision")
        if precision is not None and (
            isinstance(precision, bool) or not isinstance(precision, int) or precision < 0
        ):
            raise _config_error("config.precision must be a non-negative integer")
        unit = config.get("unit")
        if unit is not None and (not isinstance(unit, str) or len(unit) > 20):
            raise _config_error("config.unit must be a string of at most 20 characters")
        for bound in ("min", "max"):
            value = config.get(bound)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise _config_error(f"config.{bound} must be a number")
        if (
            config.get("min") is not None
            and config.get("max") is not None
            and config["min"] > config["max"]
        ):
            raise _config_error("config.min must not exceed config.max")
    if field_type in ("date", "datetime"):
        fmt = config.get("format")
        if fmt is not None and (not isinstance(fmt, str) or len(fmt) > 40):
            raise _config_error("config.format must be a string of at most 40 characters")
    if field_type == "url":
        flag = config.get("require_https")
        if flag is not None and not isinstance(flag, bool):
            raise _config_error("config.require_https must be a boolean")


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _validate_default_value(
    field_type: str,
    default_value: Any,
    *,
    config: dict[str, Any],
) -> None:
    """Type-shape check for ``default_value`` (422 invalid_field_config).

    Enum membership (option ids) is checked separately once the option rows
    are in scope — see :meth:`LabelService._validate_enum_default`.
    """
    if default_value is None:
        return
    if field_type in ("text", "textarea"):
        if not isinstance(default_value, str) or not 1 <= len(default_value) <= TEXT_DEFAULT_MAX:
            raise _config_error(
                f"default_value for {field_type} must be a string of at most "
                f"{TEXT_DEFAULT_MAX} characters"
            )
        return
    if field_type == "url":
        if (
            not isinstance(default_value, str)
            or not 1 <= len(default_value) <= URL_DEFAULT_MAX
            or not re.match(r"^https?://[^\s]+$", default_value)
        ):
            raise _config_error("default_value for url must be a valid http(s) URL")
        return
    if field_type == "number":
        if not _is_finite_number(default_value):
            raise _config_error("default_value for number must be a finite number")
        minimum = config.get("min")
        maximum = config.get("max")
        if minimum is not None and default_value < minimum:
            raise _config_error("default_value is below config.min")
        if maximum is not None and default_value > maximum:
            raise _config_error("default_value is above config.max")
        precision = config.get("precision")
        if precision is not None and round(abs(default_value), 10) != round(
            float(default_value), precision
        ):
            raise _config_error(
                f"default_value must have at most {precision} decimal places"
            )
        return
    if field_type in ("date", "datetime"):
        if not isinstance(default_value, str):
            raise _config_error(f"default_value for {field_type} must be an ISO-8601 string")
        try:
            if field_type == "date":
                date.fromisoformat(default_value)
            else:
                datetime.fromisoformat(default_value.replace("Z", "+00:00"))
        except ValueError:
            raise _config_error(
                f"default_value is not a valid {field_type}",
                details={"default_value": default_value[:64]},
            ) from None
        return
    if field_type == "boolean":
        if not isinstance(default_value, bool):
            raise _config_error("default_value for boolean must be true or false")
        return
    if field_type == "member":
        raise _config_error("default_value is not supported for member fields")
    if field_type in SELECT_FIELD_TYPES:
        # Shape only; option membership is validated against the DB rows.
        if field_type == "single_select":
            if not isinstance(default_value, str):
                raise _config_error(
                    "default_value for single_select must be an option id string"
                )
        elif not isinstance(default_value, list) or not all(
            isinstance(item, str) for item in default_value
        ):
            raise _config_error(
                "default_value for multi_select must be an array of option id strings"
            )


class LabelService:
    """Stateless orchestrator over the label-property definition tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_label(label: Label) -> dict:
        return {
            "id": str(label.id),
            "workspace_id": str(label.workspace_id),
            "project_id": str(label.project_id) if label.project_id is not None else None,
            "name": label.name,
            "color": label.color,
            "description": label.description,
            "scope": "project" if label.project_id is not None else "workspace",
            "created_at": _isoformat(label.created_at),
            "updated_at": _isoformat(label.updated_at),
        }

    @staticmethod
    def render_option(option: CustomFieldOption) -> dict:
        return {
            "id": str(option.id),
            "field_def_id": str(option.field_def_id),
            "name": option.name,
            "color": option.color,
            "position": option.position,
            "is_active": option.is_active,
            "created_at": _isoformat(option.created_at),
            "updated_at": _isoformat(option.updated_at),
        }

    @classmethod
    def render_field_def(
        cls, field_def: CustomFieldDef, options: list[CustomFieldOption] | None = None
    ) -> dict:
        return {
            "id": str(field_def.id),
            "workspace_id": str(field_def.workspace_id),
            "project_id": str(field_def.project_id)
            if field_def.project_id is not None
            else None,
            "name": field_def.name,
            "field_key": field_def.field_key,
            "type": field_def.type,
            "is_required": field_def.is_required,
            "required_on": list(field_def.required_on),
            "default_value": field_def.default_value,
            "config": dict(field_def.config),
            "position": field_def.position,
            "is_active": field_def.is_active,
            "options": [cls.render_option(option) for option in (options or [])],
            "created_at": _isoformat(field_def.created_at),
            "updated_at": _isoformat(field_def.updated_at),
        }

    # ------------------------------------------------------------------
    # authorization (label-property.md §3.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_workspace_manager(viewer: Member) -> bool:
        return role_satisfies(viewer.role, "project:manage")

    async def _project_role(
        self, session: AsyncSession, *, project_id: uuid.UUID, member_id: uuid.UUID
    ) -> str | None:
        return await session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.member_id == member_id,
            )
        )

    async def _is_project_lead(
        self, session: AsyncSession, *, viewer: Member, project: Project
    ) -> bool:
        if project.lead_member_id == viewer.id:
            return True
        return (
            await self._project_role(session, project_id=project.id, member_id=viewer.id)
        ) == "lead"

    async def _load_scope_project(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> Project:
        project = await session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
                Project.deleted_at.is_(None),
            )
        )
        if project is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        return project

    async def _assert_can_manage_scope(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
    ) -> Project | None:
        """Write gate: workspace admin, or lead of the scoped project (§3.4)."""
        if self._is_workspace_manager(actor):
            if project_id is not None:
                return await self._load_scope_project(
                    session, workspace_id=workspace_id, project_id=project_id
                )
            return None
        if project_id is None:
            raise ForbiddenError("workspace admin required for workspace-level definitions")
        project = await self._load_scope_project(
            session, workspace_id=workspace_id, project_id=project_id
        )
        if not await self._is_project_lead(session, viewer=actor, project=project):
            raise ForbiddenError("project lead or workspace admin required")
        return project

    async def _assert_can_view_project(
        self, session: AsyncSession, *, viewer: Member, project: Project
    ) -> None:
        """Read gate for explicitly-scoped queries (mirrors project.md §3.3)."""
        if self._is_workspace_manager(viewer):
            return
        if viewer.role == "guest":
            granted = await session.scalar(
                select(MemberProjectAccess.id).where(
                    MemberProjectAccess.project_id == project.id,
                    MemberProjectAccess.member_id == viewer.id,
                )
            )
            if granted is None:
                raise NotFoundError(_PROJECT_NOT_FOUND)
            return
        if project.visibility == "public":
            return
        role = await self._project_role(
            session, project_id=project.id, member_id=viewer.id
        )
        if role is None and project.lead_member_id != viewer.id:
            raise ForbiddenError("project is private")

    async def _visible_project_clause(self, session: AsyncSession, *, viewer: Member):
        """SQL clause restricting to projects visible to ``viewer`` (None = all)."""
        if self._is_workspace_manager(viewer):
            return None
        public = Project.visibility == "public"
        lead = Project.lead_member_id == viewer.id
        member_of = Project.id.in_(
            select(ProjectMember.project_id).where(ProjectMember.member_id == viewer.id)
        )
        if viewer.role == "guest":
            granted = Project.id.in_(
                select(MemberProjectAccess.project_id).where(
                    MemberProjectAccess.member_id == viewer.id
                )
            )
            return or_(public, granted)
        return or_(public, lead, member_of)

    # ------------------------------------------------------------------
    # realtime + audit helpers (§6.7 registered names, §6.6 unique path)
    # ------------------------------------------------------------------

    async def _emit_label_event(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project: Project | None,
        event: str,
        data: dict,
    ) -> None:
        """Workspace-scoped labels → workspace channel; project-scoped labels
        follow the project module pattern: always the detail channel, plus the
        workspace channel when the project is public (§3.5 / §6.7)."""
        if project is None:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=LABEL_CHANNEL.format(workspace_id=workspace_id),
                event=event,
                data=data,
            )
            return
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=PROJECT_CHANNEL.format(project_id=project.id),
            event=event,
            data=data,
        )
        if project.visibility == "public":
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=LABEL_CHANNEL.format(workspace_id=workspace_id),
                event=event,
                data=data,
            )

    async def _emit_field_event(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project: Project | None,
        data: dict,
    ) -> None:
        if project is None:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=CUSTOM_FIELDS_CHANNEL.format(workspace_id=workspace_id),
                event="custom_field.updated",
                data=data,
            )
            return
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=PROJECT_CHANNEL.format(project_id=project.id),
            event="custom_field.updated",
            data=data,
        )
        if project.visibility == "public":
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=CUSTOM_FIELDS_CHANNEL.format(workspace_id=workspace_id),
                event="custom_field.updated",
                data=data,
            )

    async def _emit_option_event(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project: Project | None,
        data: dict,
    ) -> None:
        # Option changes ride the same channels as their field definition,
        # under the registered custom_field_option.updated name (§6.7).
        if project is None:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=CUSTOM_FIELDS_CHANNEL.format(workspace_id=workspace_id),
                event="custom_field_option.updated",
                data=data,
            )
            return
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=PROJECT_CHANNEL.format(project_id=project.id),
            event="custom_field_option.updated",
            data=data,
        )
        if project.visibility == "public":
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=CUSTOM_FIELDS_CHANNEL.format(workspace_id=workspace_id),
                event="custom_field_option.updated",
                data=data,
            )

    async def _audit(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        actor: Member,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        metadata: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_member_id=actor.id,
            actor_kind="member",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------
    # workspace resolution for workspace-less paths (§3.1)
    # ------------------------------------------------------------------

    async def _resolve_workspace(self, function: str, entity_id: uuid.UUID) -> uuid.UUID | None:
        """Narrow SECURITY DEFINER lookup (migration 0008) — no tenant GUC yet."""
        async with self._factory() as session:
            return await session.scalar(text(f"SELECT {function}(:id)"), {"id": entity_id})

    async def resolve_label_workspace(self, label_id: uuid.UUID) -> uuid.UUID | None:
        return await self._resolve_workspace("mesh_label_workspace_id", label_id)

    async def resolve_field_def_workspace(self, field_def_id: uuid.UUID) -> uuid.UUID | None:
        return await self._resolve_workspace(
            "mesh_custom_field_def_workspace_id", field_def_id
        )

    # ------------------------------------------------------------------
    # labels CRUD
    # ------------------------------------------------------------------

    async def list_labels(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(Label).where(Label.workspace_id == workspace_id)
            if project_id is not None:
                project = await self._load_scope_project(
                    session, workspace_id=workspace_id, project_id=project_id
                )
                await self._assert_can_view_project(session, viewer=viewer, project=project)
                stmt = stmt.where(
                    or_(Label.project_id == project_id, Label.project_id.is_(None))
                )
            else:
                clause = await self._visible_project_clause(session, viewer=viewer)
                if clause is not None:
                    stmt = stmt.where(
                        or_(
                            Label.project_id.is_(None),
                            Label.project_id.in_(
                                select(Project.id).where(
                                    Project.workspace_id == workspace_id,
                                    Project.deleted_at.is_(None),
                                    clause,
                                )
                            ),
                        )
                    )
            items, next_cursor = await self._paginate_labels(
                session, stmt, limit=page_limit, cursor=cursor
            )
            return [self.render_label(label) for label in items], next_cursor

    async def _paginate_labels(
        self,
        session: AsyncSession,
        stmt: Select,
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Label], str | None]:
        if cursor is not None:
            position = decode_cursor(cursor)
            stmt = stmt.where((Label.created_at, Label.id) > (position.sort_value, position.id))
        ordered = stmt.order_by(Label.created_at.asc(), Label.id.asc()).limit(limit + 1)
        rows = list((await session.execute(ordered)).scalars().all())
        if len(rows) <= limit:
            return rows, None
        kept = rows[:limit]
        last = kept[-1]
        return kept, encode_cursor(last.created_at, last.id)

    async def create_label(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        name: str,
        color: str,
        description: str | None = None,
        project_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_label_name(name)
        _validate_color(color)
        if description is not None and (
            not isinstance(description, str) or len(description) > 500
        ):
            raise ValidationError("description must be at most 500 characters")
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._assert_can_manage_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=project_id
            )
            label = Label(
                workspace_id=workspace_id,
                project_id=project_id,
                name=name,
                color=color,
                description=description,
            )
            session.add(label)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_labels_name"):
                    raise ConflictError(
                        "a label with this name already exists in this scope",
                        code="label_name_taken",
                        details={"name": name},
                    ) from exc
                raise
            await self._emit_label_event(
                session,
                workspace_id=workspace_id,
                project=project,
                event="label.created",
                data=self.render_label(label),
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="label.created",
                resource_type="label",
                resource_id=label.id,
                metadata={"name": name, "scope": self.render_label(label)["scope"]},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return self.render_label(label)

    async def _load_label(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        label_id: uuid.UUID,
        for_update: bool = False,
    ) -> Label:
        stmt = select(Label).where(
            Label.id == label_id, Label.workspace_id == workspace_id
        )
        if for_update:
            # Row lock serializes the If-Match check against the UPDATE
            # (README §6.14 optimistic concurrency, CWE-362).
            stmt = stmt.with_for_update()
        label = await session.scalar(stmt)
        if label is None:
            raise NotFoundError(_LABEL_NOT_FOUND)
        return label

    @staticmethod
    def _matches_version(updated_at: datetime, if_match: str) -> bool:
        candidate = if_match.strip().strip('"')
        if candidate == _isoformat(updated_at):
            return True
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed == updated_at

    async def update_label(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        label_id: uuid.UUID,
        patch: LabelPatch,
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            label = await self._load_label(
                session,
                workspace_id=workspace_id,
                label_id=label_id,
                for_update=if_match is not None,
            )
            project = None
            if label.project_id is not None:
                project = await self._load_scope_project(
                    session, workspace_id=workspace_id, project_id=label.project_id
                )
            await self._assert_can_manage_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=label.project_id
            )
            if if_match is not None and not self._matches_version(label.updated_at, if_match):
                raise ConflictError(
                    "label was modified concurrently",
                    code="conflict",
                    details={"id": str(label.id)},
                )
            changes: dict[str, Any] = {}
            if not isinstance(patch.name, _Unset) and patch.name != label.name:
                _validate_label_name(patch.name)
                changes["name"] = (label.name, patch.name)
                label.name = patch.name
            if not isinstance(patch.color, _Unset) and patch.color != label.color:
                _validate_color(patch.color)
                changes["color"] = (label.color, patch.color)
                label.color = patch.color
            if not isinstance(patch.description, _Unset) and patch.description != label.description:
                if patch.description is not None and (
                    not isinstance(patch.description, str) or len(patch.description) > 500
                ):
                    raise ValidationError("description must be at most 500 characters")
                changes["description"] = (label.description, patch.description)
                label.description = patch.description
            if not changes:
                return self.render_label(label)
            label.updated_at = _now(self._clock)
            new_name = label.name  # plain value: ORM attr access after a failed
            # flush would hit the dead transaction (lazy refresh).
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_labels_name"):
                    raise ConflictError(
                        "a label with this name already exists in this scope",
                        code="label_name_taken",
                        details={"name": new_name},
                    ) from exc
                raise
            await self._emit_label_event(
                session,
                workspace_id=workspace_id,
                project=project,
                event="label.updated",
                data=self.render_label(label),
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="label.updated",
                resource_type="label",
                resource_id=label.id,
                metadata={"changes": sorted(changes.keys())},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return self.render_label(label)

    async def delete_label(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        label_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            label = await self._load_label(session, workspace_id=workspace_id, label_id=label_id)
            project = None
            if label.project_id is not None:
                project = await session.scalar(
                    select(Project).where(
                        Project.id == label.project_id,
                        Project.workspace_id == workspace_id,
                    )
                )
            await self._assert_can_manage_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=label.project_id
            )
            await session.delete(label)
            await session.flush()
            # Deleting the label cascades issue_labels rows (ON DELETE CASCADE);
            # the issue-side ``issue.labels_changed`` broadcast lands with the
            # issue increment (MES-32 remainder).
            await self._emit_label_event(
                session,
                workspace_id=workspace_id,
                project=project,
                event="label.deleted",
                data={
                    "id": str(label.id),
                    "project_id": str(label.project_id)
                    if label.project_id is not None
                    else None,
                    "name": label.name,
                },
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="label.deleted",
                resource_type="label",
                resource_id=label.id,
                metadata={"name": label.name},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(label_id), "deleted": True}

    # ------------------------------------------------------------------
    # custom field definitions CRUD
    # ------------------------------------------------------------------

    async def _load_options(
        self, session: AsyncSession, *, field_def_id: uuid.UUID
    ) -> list[CustomFieldOption]:
        rows = (
            await session.execute(
                select(CustomFieldOption)
                .where(CustomFieldOption.field_def_id == field_def_id)
                .order_by(
                    CustomFieldOption.position.asc(), CustomFieldOption.created_at.asc()
                )
            )
        ).scalars().all()
        return list(rows)

    async def _render_field_def(
        self, session: AsyncSession, field_def: CustomFieldDef
    ) -> dict:
        options = (
            await self._load_options(session, field_def_id=field_def.id)
            if field_def.type in SELECT_FIELD_TYPES
            else None
        )
        return self.render_field_def(field_def, options)

    async def list_field_defs(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(CustomFieldDef).where(CustomFieldDef.workspace_id == workspace_id)
            if is_active is not None:
                stmt = stmt.where(CustomFieldDef.is_active.is_(is_active))
            if project_id is not None:
                project = await self._load_scope_project(
                    session, workspace_id=workspace_id, project_id=project_id
                )
                await self._assert_can_view_project(session, viewer=viewer, project=project)
                stmt = stmt.where(
                    or_(
                        CustomFieldDef.project_id == project_id,
                        CustomFieldDef.project_id.is_(None),
                    )
                )
            else:
                clause = await self._visible_project_clause(session, viewer=viewer)
                if clause is not None:
                    stmt = stmt.where(
                        or_(
                            CustomFieldDef.project_id.is_(None),
                            CustomFieldDef.project_id.in_(
                                select(Project.id).where(
                                    Project.workspace_id == workspace_id,
                                    Project.deleted_at.is_(None),
                                    clause,
                                )
                            ),
                        )
                    )
            items, next_cursor = await self._paginate_field_defs(
                session, stmt, limit=page_limit, cursor=cursor
            )
            rendered = [await self._render_field_def(session, item) for item in items]
            return rendered, next_cursor

    async def _paginate_field_defs(
        self,
        session: AsyncSession,
        stmt: Select,
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[CustomFieldDef], str | None]:
        if cursor is not None:
            position = decode_cursor(cursor)
            stmt = stmt.where(
                (CustomFieldDef.created_at, CustomFieldDef.id)
                > (position.sort_value, position.id)
            )
        ordered = stmt.order_by(
            CustomFieldDef.created_at.asc(), CustomFieldDef.id.asc()
        ).limit(limit + 1)
        rows = list((await session.execute(ordered)).scalars().all())
        if len(rows) <= limit:
            return rows, None
        kept = rows[:limit]
        last = kept[-1]
        return kept, encode_cursor(last.created_at, last.id)

    async def _validate_enum_default(
        self,
        session: AsyncSession,
        *,
        field_def: CustomFieldDef,
        default_value: Any,
    ) -> None:
        if default_value is None:
            return
        option_rows = await self._load_options(session, field_def_id=field_def.id)
        active_ids = {str(option.id) for option in option_rows if option.is_active}
        wanted = [default_value] if field_def.type == "single_select" else default_value
        unknown = [value for value in wanted if value not in active_ids]
        if unknown:
            raise _config_error(
                "default_value must reference active options of this field",
                details={"unknown_option_ids": unknown},
            )

    async def create_field_def(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        name: str,
        field_key: str,
        field_type: str,
        project_id: uuid.UUID | None = None,
        is_required: bool = False,
        required_on: list[Any] | None = None,
        default_value: Any | None = None,
        config: dict[str, Any] | None = None,
        position: float = 0,
        options: list[dict[str, Any]] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_field_name(name)
        _validate_field_key(field_key)
        _validate_field_type(field_type)
        _validate_position(position)
        required_on = required_on or []
        _validate_required_on(required_on)
        config = config or {}
        _validate_config(field_type, config)
        options = options or []
        if options and field_type not in SELECT_FIELD_TYPES:
            raise _config_error(
                f"options are only valid for {SELECT_FIELD_TYPES} fields",
                details={"type": field_type},
            )
        seen_names: set[str] = set()
        for option in options:
            _validate_option_name(option["name"])
            _validate_color(option.get("color"), field="option color")
            _validate_position(option.get("position", 0))
            if option["name"] in seen_names:
                raise ValidationError(
                    "duplicate option name in request", details={"name": option["name"]}
                )
            seen_names.add(option["name"])
        if field_type in SELECT_FIELD_TYPES and default_value is not None:
            raise _config_error(
                "enum default_value must be set via PATCH after the options exist"
            )
        _validate_default_value(field_type, default_value, config=config)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._assert_can_manage_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=project_id
            )
            field_def = CustomFieldDef(
                workspace_id=workspace_id,
                project_id=project_id,
                name=name,
                field_key=field_key,
                type=field_type,
                is_required=is_required,
                required_on=list(required_on),
                default_value=default_value,
                config=dict(config),
                position=position,
            )
            session.add(field_def)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_cfdefs_key"):
                    raise ConflictError(
                        "a field with this field_key already exists in this scope",
                        code="field_key_taken",
                        details={"field_key": field_key},
                    ) from exc
                raise
            for index, option in enumerate(options):
                session.add(
                    CustomFieldOption(
                        workspace_id=workspace_id,
                        field_def_id=field_def.id,
                        name=option["name"],
                        color=option.get("color"),
                        position=option.get("position", float(index)),
                    )
                )
            await session.flush()
            rendered = await self._render_field_def(session, field_def)
            await self._emit_field_event(
                session,
                workspace_id=workspace_id,
                project=project,
                data={**rendered, "change": "created"},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="custom_field.created",
                resource_type="custom_field",
                resource_id=field_def.id,
                metadata={"field_key": field_key, "type": field_type},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def _load_field_def(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        for_update: bool = False,
    ) -> CustomFieldDef:
        stmt = select(CustomFieldDef).where(
            CustomFieldDef.id == field_def_id,
            CustomFieldDef.workspace_id == workspace_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        field_def = await session.scalar(stmt)
        if field_def is None:
            raise NotFoundError(_FIELD_DEF_NOT_FOUND)
        return field_def

    async def _assert_field_visible(
        self, session: AsyncSession, *, viewer: Member, field_def: CustomFieldDef
    ) -> Project | None:
        """Resolve (and visibility-check) the field's scope project."""
        if field_def.project_id is None:
            return None
        project = await session.scalar(
            select(Project).where(
                Project.id == field_def.project_id,
                Project.workspace_id == field_def.workspace_id,
            )
        )
        # A dangling scope is impossible (composite FK) unless the project row
        # is hard-deleted mid-flight; treat as not found.
        if project is None or project.deleted_at is not None:
            raise NotFoundError(_FIELD_DEF_NOT_FOUND)
        await self._assert_can_view_project(session, viewer=viewer, project=project)
        return project

    async def update_field_def(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        patch: FieldDefPatch,
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            field_def = await self._load_field_def(
                session,
                workspace_id=workspace_id,
                field_def_id=field_def_id,
                for_update=if_match is not None,
            )
            project = await self._assert_field_visible(
                session, viewer=actor, field_def=field_def
            )
            await self._assert_can_manage_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=field_def.project_id,
            )
            if if_match is not None and not self._matches_version(
                field_def.updated_at, if_match
            ):
                raise ConflictError(
                    "custom field was modified concurrently",
                    code="conflict",
                    details={"id": str(field_def.id)},
                )
            changes: dict[str, Any] = {}
            if not isinstance(patch.name, _Unset) and patch.name != field_def.name:
                _validate_field_name(patch.name)
                changes["name"] = (field_def.name, patch.name)
                field_def.name = patch.name
            if (
                not isinstance(patch.is_required, _Unset)
                and patch.is_required != field_def.is_required
            ):
                changes["is_required"] = (field_def.is_required, patch.is_required)
                field_def.is_required = patch.is_required
            if not isinstance(patch.required_on, _Unset) and list(
                patch.required_on
            ) != list(field_def.required_on):
                _validate_required_on(patch.required_on)
                changes["required_on"] = (field_def.required_on, patch.required_on)
                field_def.required_on = list(patch.required_on)
            if not isinstance(patch.position, _Unset) and patch.position != field_def.position:
                _validate_position(patch.position)
                changes["position"] = (field_def.position, patch.position)
                field_def.position = patch.position
            if not isinstance(patch.is_active, _Unset) and patch.is_active != field_def.is_active:
                changes["is_active"] = (field_def.is_active, patch.is_active)
                field_def.is_active = patch.is_active
            next_config = (
                dict(patch.config)
                if not isinstance(patch.config, _Unset)
                else dict(field_def.config)
            )
            if not isinstance(patch.config, _Unset) and patch.config != field_def.config:
                _validate_config(field_def.type, next_config)
                changes["config"] = (field_def.config, patch.config)
                field_def.config = dict(patch.config)
            if not isinstance(patch.default_value, _Unset) and (
                patch.default_value != field_def.default_value
            ):
                _validate_default_value(
                    field_def.type, patch.default_value, config=next_config
                )
                if field_def.type in SELECT_FIELD_TYPES:
                    await self._validate_enum_default(
                        session, field_def=field_def, default_value=patch.default_value
                    )
                changes["default_value"] = (field_def.default_value, patch.default_value)
                field_def.default_value = patch.default_value
            if not changes:
                return await self._render_field_def(session, field_def)
            field_def.updated_at = _now(self._clock)
            await session.flush()
            rendered = await self._render_field_def(session, field_def)
            await self._emit_field_event(
                session,
                workspace_id=workspace_id,
                project=project,
                data={**rendered, "change": "updated"},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="custom_field.updated",
                resource_type="custom_field",
                resource_id=field_def.id,
                metadata={"changes": sorted(changes.keys())},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def delete_field_def(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            field_def = await self._load_field_def(
                session, workspace_id=workspace_id, field_def_id=field_def_id
            )
            project = await self._assert_field_visible(
                session, viewer=actor, field_def=field_def
            )
            await self._assert_can_manage_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=field_def.project_id,
            )
            field_key = field_def.field_key
            await session.delete(field_def)
            await session.flush()
            # Options (and, once it exists, issue_custom_field_values) cascade
            # via ON DELETE CASCADE — §4.5 删除(级联清值与选项).
            await self._emit_field_event(
                session,
                workspace_id=workspace_id,
                project=project,
                data={
                    "id": str(field_def.id),
                    "field_key": field_key,
                    "project_id": str(field_def.project_id)
                    if field_def.project_id is not None
                    else None,
                    "change": "deleted",
                },
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="custom_field.deleted",
                resource_type="custom_field",
                resource_id=field_def.id,
                metadata={"field_key": field_key},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(field_def_id), "deleted": True}

    # ------------------------------------------------------------------
    # enum options CRUD (§3.1)
    # ------------------------------------------------------------------

    async def list_options(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            field_def = await self._load_field_def(
                session, workspace_id=workspace_id, field_def_id=field_def_id
            )
            await self._assert_field_visible(session, viewer=viewer, field_def=field_def)
            stmt = select(CustomFieldOption).where(
                CustomFieldOption.field_def_id == field_def_id
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    (CustomFieldOption.created_at, CustomFieldOption.id)
                    > (position.sort_value, position.id)
                )
            ordered = stmt.order_by(
                CustomFieldOption.created_at.asc(), CustomFieldOption.id.asc()
            ).limit(page_limit + 1)
            rows = list((await session.execute(ordered)).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return [self.render_option(option) for option in rows], next_cursor

    async def create_option(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        name: str,
        color: str | None = None,
        position: float = 0,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_option_name(name)
        _validate_color(color, field="color")
        _validate_position(position)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            field_def = await self._load_field_def(
                session, workspace_id=workspace_id, field_def_id=field_def_id
            )
            project = await self._assert_field_visible(
                session, viewer=actor, field_def=field_def
            )
            await self._assert_can_manage_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=field_def.project_id,
            )
            if field_def.type not in SELECT_FIELD_TYPES:
                raise _config_error(
                    f"options are only valid for {SELECT_FIELD_TYPES} fields",
                    details={"type": field_def.type},
                )
            if not field_def.is_active:
                raise BusinessRuleError(
                    "cannot add options to an inactive field", code="field_inactive"
                )
            option = CustomFieldOption(
                workspace_id=workspace_id,
                field_def_id=field_def_id,
                name=name,
                color=color,
                position=position,
            )
            session.add(option)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "custom_field_options_field_def_id_name_key") or violates(
                    exc, "uq_cfopts_def_name"
                ):
                    raise ConflictError(
                        "an option with this name already exists for this field",
                        code="conflict",
                        details={"name": name},
                    ) from exc
                raise
            rendered = self.render_option(option)
            await self._emit_option_event(
                session,
                workspace_id=workspace_id,
                project=project,
                data={"field_def_id": str(field_def_id), "option": rendered, "change": "created"},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="custom_field_option.created",
                resource_type="custom_field_option",
                resource_id=option.id,
                metadata={"field_def_id": str(field_def_id), "name": name},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def _load_option(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        option_id: uuid.UUID,
        for_update: bool = False,
    ) -> CustomFieldOption:
        stmt = select(CustomFieldOption).where(
            CustomFieldOption.id == option_id,
            CustomFieldOption.field_def_id == field_def_id,
            CustomFieldOption.workspace_id == workspace_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        option = await session.scalar(stmt)
        if option is None:
            raise NotFoundError(_OPTION_NOT_FOUND)
        return option

    async def update_option(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        option_id: uuid.UUID,
        patch: OptionPatch,
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            field_def = await self._load_field_def(
                session, workspace_id=workspace_id, field_def_id=field_def_id
            )
            project = await self._assert_field_visible(
                session, viewer=actor, field_def=field_def
            )
            await self._assert_can_manage_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=field_def.project_id,
            )
            option = await self._load_option(
                session,
                workspace_id=workspace_id,
                field_def_id=field_def_id,
                option_id=option_id,
                for_update=if_match is not None,
            )
            if if_match is not None and not self._matches_version(option.updated_at, if_match):
                raise ConflictError(
                    "option was modified concurrently",
                    code="conflict",
                    details={"id": str(option.id)},
                )
            changes: dict[str, Any] = {}
            if not isinstance(patch.name, _Unset) and patch.name != option.name:
                _validate_option_name(patch.name)
                changes["name"] = (option.name, patch.name)
                option.name = patch.name
            if not isinstance(patch.color, _Unset) and patch.color != option.color:
                _validate_color(patch.color, field="color")
                changes["color"] = (option.color, patch.color)
                option.color = patch.color
            if not isinstance(patch.position, _Unset) and patch.position != option.position:
                _validate_position(patch.position)
                changes["position"] = (option.position, patch.position)
                option.position = patch.position
            if not isinstance(patch.is_active, _Unset) and patch.is_active != option.is_active:
                changes["is_active"] = (option.is_active, patch.is_active)
                option.is_active = patch.is_active
            if not changes:
                return self.render_option(option)
            option.updated_at = _now(self._clock)
            new_name = option.name  # plain value: safe after a failed flush
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "custom_field_options_field_def_id_name_key") or violates(
                    exc, "uq_cfopts_def_name"
                ):
                    raise ConflictError(
                        "an option with this name already exists for this field",
                        code="conflict",
                        details={"name": new_name},
                    ) from exc
                raise
            rendered = self.render_option(option)
            await self._emit_option_event(
                session,
                workspace_id=workspace_id,
                project=project,
                data={"field_def_id": str(field_def_id), "option": rendered, "change": "updated"},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="custom_field_option.updated",
                resource_type="custom_field_option",
                resource_id=option.id,
                metadata={"field_def_id": str(field_def_id), "changes": sorted(changes.keys())},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def delete_option(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        field_def_id: uuid.UUID,
        option_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            field_def = await self._load_field_def(
                session, workspace_id=workspace_id, field_def_id=field_def_id
            )
            project = await self._assert_field_visible(
                session, viewer=actor, field_def=field_def
            )
            await self._assert_can_manage_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=field_def.project_id,
            )
            option = await self._load_option(
                session,
                workspace_id=workspace_id,
                field_def_id=field_def_id,
                option_id=option_id,
            )
            await session.delete(option)
            await session.flush()
            # Once issue values exist (MES-32 remainder), deleting an option is
            # resolved per §4.5 (multi: remove entry; single: clear) by the
            # value-writing service — nothing to cascade in this increment.
            await self._emit_option_event(
                session,
                workspace_id=workspace_id,
                project=project,
                data={
                    "field_def_id": str(field_def_id),
                    "id": str(option_id),
                    "change": "deleted",
                },
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="custom_field_option.deleted",
                resource_type="custom_field_option",
                resource_id=option.id,
                metadata={"field_def_id": str(field_def_id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(option_id), "deleted": True}
