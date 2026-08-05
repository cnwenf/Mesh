"""View service — saved projection configs (kanban.md §3, README §6).

Each public method owns its transaction (``session_factory() + begin()``) and
sets the ``mesh.workspace_id`` GUC up front, so every read/write is correct
under RLS (restricted app role) and without it (owner role). Workspace-less
paths resolve the tenant through the narrow SECURITY DEFINER lookup
``mesh_view_workspace_id`` (migration 0011) BEFORE the membership gate runs.

Contract anchors (kanban.md + README §6):
- config JSONB is whitelist-validated before storage (kanban §2.9); failures
  raise 400-class named codes (kanban §3.3) via ``mesh.views.config``;
- scope uniqueness (README §6.3): name conflicts → 409 ``view_name_taken``;
  duplicate default views → 409 ``default_view_conflict`` (partial expression
  unique index is the backstop for the in-transaction default handoff);
- realtime events ride the outbox single write path (§6.6/§6.7): the registry
  has no ``view.deleted`` name, so deletion emits ``view.updated`` with
  ``deleted: true``;
- no-change PATCH is a no-op (§6.9: empty diff emits nothing);
- PATCH board_settings is a SHALLOW merge — top-level keys replace, siblings
  survive (kanban issue slice: JSONB 配置浅合并).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import paginate
from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.constraints import violates as _violates
from mesh.db.models.label import CustomFieldDef, CustomFieldOption
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.view import View
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime
from mesh.views.config import (
    validate_board_settings,
    validate_display_fields,
    validate_filters,
    validate_group_axes,
    validate_group_by,
    validate_layout,
    validate_name,
    validate_sort,
    validate_visibility,
)
from mesh.views.schemas import CreateViewRequest, WipRequest

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100

_VIEW_NOT_FOUND = "view not found"
_DUPLICATE_SUFFIX_LIMIT = 50

_CUSTOM_EQUALITY_OPS = frozenset({"eq", "neq", "in", "not_in", "is_null", "is_not_null"})
_CUSTOM_RANGE_OPS = frozenset({"lt", "lte", "gt", "gte"})
_CUSTOM_TEXT_OPS = _CUSTOM_EQUALITY_OPS | {"contains"}

# PATCH fields that REPLACE the stored JSONB wholesale; board_settings is the
# shallow-merged exception (handled separately).
_REPLACE_JSON_FIELDS = ("filters", "sort", "display_fields")


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _view_channel(view_id: uuid.UUID) -> str:
    return f"view:{view_id}"


def _workspace_views_channel(workspace_id: uuid.UUID) -> str:
    return f"workspace:{workspace_id}:views"


class ViewService:
    """Stateless orchestrator over the views table (kanban.md §3)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def render_view(self, view: View, *, can_write: bool | None = None) -> dict:
        payload = {
            "id": str(view.id),
            "workspace_id": str(view.workspace_id),
            "project_id": str(view.project_id) if view.project_id is not None else None,
            "owner_member_id": str(view.owner_member_id),
            "name": view.name,
            "layout": view.layout,
            "visibility": view.visibility,
            "filters": view.filters,
            "group_by": view.group_by,
            "sub_group_by": view.sub_group_by,
            "sort": view.sort,
            "display_fields": view.display_fields,
            "board_settings": view.board_settings,
            "position": view.position,
            "is_default": view.is_default,
            "created_at": _isoformat(view.created_at),
            "updated_at": _isoformat(view.updated_at),
        }
        if can_write is not None:
            payload["can_write"] = can_write
        return payload

    # ------------------------------------------------------------------
    # workspace resolution for the workspace-less /views/{id} paths
    # ------------------------------------------------------------------

    async def resolve_view_workspace(self, view_id: uuid.UUID) -> uuid.UUID | None:
        """Narrow SECURITY DEFINER lookup (migration 0011) — no tenant GUC yet."""
        async with self._factory() as session:
            return await session.scalar(text("SELECT mesh_view_workspace_id(:id)"), {"id": view_id})

    # ------------------------------------------------------------------
    # loading + authorization (kanban §3.4)
    # ------------------------------------------------------------------

    async def _load_view(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        for_update: bool = False,
    ) -> View:
        stmt = select(View).where(View.id == view_id, View.workspace_id == workspace_id)
        if for_update:
            # Row lock for write paths: serialises the If-Match version check
            # against the UPDATE so two concurrent PATCHes carrying the same
            # valid If-Match cannot both pass (CWE-362).
            stmt = stmt.with_for_update()
        view = await session.scalar(stmt)
        if view is None:
            raise NotFoundError(_VIEW_NOT_FOUND)
        return view

    @staticmethod
    def _is_workspace_manager(viewer: Member) -> bool:
        return role_satisfies(viewer.role, "project:manage")

    async def _project(
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
            raise NotFoundError("project not found")
        return project

    async def _project_role(
        self, session: AsyncSession, *, project_id: uuid.UUID, member_id: uuid.UUID
    ) -> str | None:
        return await session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.member_id == member_id,
            )
        )

    async def _guest_permission(
        self, session: AsyncSession, *, project_id: uuid.UUID, member_id: uuid.UUID
    ) -> str | None:
        return await session.scalar(
            select(MemberProjectAccess.permission).where(
                MemberProjectAccess.project_id == project_id,
                MemberProjectAccess.member_id == member_id,
            )
        )

    async def _project_readable(self, session: AsyncSession, *, viewer: Member, project: Project) -> bool:
        """Project visibility gate (mirrors project.md §3.4 read matrix)."""
        if self._is_workspace_manager(viewer):
            return True
        if viewer.role == "guest":
            return (
                await self._guest_permission(session, project_id=project.id, member_id=viewer.id) is not None
            )
        if project.visibility == "public":
            return True
        return await self._project_role(session, project_id=project.id, member_id=viewer.id) is not None

    async def _is_project_lead(self, session: AsyncSession, *, viewer: Member, project: Project) -> bool:
        if self._is_workspace_manager(viewer):
            return True
        if project.lead_member_id == viewer.id:
            return True
        return (await self._project_role(session, project_id=project.id, member_id=viewer.id)) == "lead"

    async def assert_can_read(self, session: AsyncSession, *, viewer: Member, view: View) -> None:
        """Read gate (kanban §3.4): private = owner only (invisible → 404);
        shared = workspace members, project-scoped views also require project
        visibility so private projects don't leak through shared configs."""
        if view.visibility == "private":
            if view.owner_member_id == viewer.id:
                return
            raise NotFoundError(_VIEW_NOT_FOUND)
        if view.project_id is None:
            return
        project = await session.scalar(
            select(Project).where(
                Project.id == view.project_id,
                Project.workspace_id == view.workspace_id,
                Project.deleted_at.is_(None),
            )
        )
        if project is None or not await self._project_readable(session, viewer=viewer, project=project):
            raise NotFoundError(_VIEW_NOT_FOUND)

    async def assert_can_write(self, session: AsyncSession, *, viewer: Member, view: View) -> None:
        """Write gate (kanban §3.4): owner, workspace admin, or project lead
        for project-scoped views. Foreign private views are 403 on writes."""
        if view.owner_member_id == viewer.id:
            return
        if self._is_workspace_manager(viewer):
            return
        if view.project_id is not None:
            project = await session.scalar(
                select(Project).where(
                    Project.id == view.project_id,
                    Project.workspace_id == view.workspace_id,
                    Project.deleted_at.is_(None),
                )
            )
            if project is not None and await self._is_project_lead(session, viewer=viewer, project=project):
                return
        raise ForbiddenError("not allowed to modify this view")

    # ------------------------------------------------------------------
    # realtime + audit helpers
    # ------------------------------------------------------------------

    async def _emit_view_event(
        self,
        session: AsyncSession,
        *,
        view: View,
        data: dict,
        include_detail_channel: bool = True,
    ) -> None:
        """view.updated through the outbox unique write path (§6.6/§6.7).

        Detail channel ``view:{id}`` plus the workspace list channel so sidebars
        refresh. Deletion skips the detail channel (the resource is gone).
        """
        if include_detail_channel:
            await emit_realtime(
                session,
                workspace_id=view.workspace_id,
                channel=_view_channel(view.id),
                event="view.updated",
                data=data,
            )
        await emit_realtime(
            session,
            workspace_id=view.workspace_id,
            channel=_workspace_views_channel(view.workspace_id),
            event="view.updated",
            data=data,
        )

    async def _audit(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        actor: Member,
        action: str,
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
            resource_type="view",
            resource_id=resource_id,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------
    # shared transaction steps
    # ------------------------------------------------------------------

    async def _validate_project_ref(
        self,
        session: AsyncSession,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> Project:
        project = await self._project(session, workspace_id=workspace_id, project_id=project_id)
        if not await self._project_readable(session, viewer=viewer, project=project):
            raise ForbiddenError("project is not visible to you")
        return project

    @staticmethod
    def _custom_definition_scope(project_id: uuid.UUID | None) -> Any:
        if project_id is None:
            return CustomFieldDef.project_id.is_(None)
        return or_(
            CustomFieldDef.project_id.is_(None),
            CustomFieldDef.project_id == project_id,
        )

    @staticmethod
    def _custom_config_error(
        *, code: str, message: str, path: str, field_def_id: str
    ) -> ValidationError:
        return ValidationError(
            message,
            code=code,
            details={"path": path, "field_def_id": field_def_id},
        )

    async def validate_config_references(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
        group_by: str | None,
        sub_group_by: str | None,
        filters: dict,
        sort: list,
    ) -> None:
        """Resolve every custom config reference against the current value domain.

        Shape validation is intentionally separate and pure. This transaction-
        aware gate closes the remaining fail-open surface: a config can only
        reference active definitions in the view's scope, supported operators,
        active enum options, and active workspace members. Execution paths call
        the same method so legacy/directly-written JSONB also fails closed.
        """

        definitions: dict[tuple[str, str], CustomFieldDef] = {}
        option_ids: dict[uuid.UUID, set[str]] = {}

        async def resolve(raw: Any, *, code: str, path: str) -> CustomFieldDef:
            try:
                canonical = str(uuid.UUID(str(raw)))
            except (ValueError, AttributeError, TypeError) as exc:
                raise self._custom_config_error(
                    code=code,
                    message="field_def_id must be a UUID",
                    path=path,
                    field_def_id=str(raw)[:64],
                ) from exc
            cache_key = (code, canonical)
            if cache_key in definitions:
                return definitions[cache_key]
            definition = await session.scalar(
                select(CustomFieldDef).where(
                    CustomFieldDef.workspace_id == workspace_id,
                    CustomFieldDef.id == uuid.UUID(canonical),
                    CustomFieldDef.is_active.is_(True),
                    self._custom_definition_scope(project_id),
                )
            )
            if definition is None:
                raise self._custom_config_error(
                    code=code,
                    message="custom field is not available to this view",
                    path=path,
                    field_def_id=canonical,
                )
            definitions[cache_key] = definition
            return definition

        async def active_options(definition: CustomFieldDef) -> set[str]:
            if definition.id not in option_ids:
                rows = (
                    await session.execute(
                        select(CustomFieldOption.id).where(
                            CustomFieldOption.workspace_id == workspace_id,
                            CustomFieldOption.field_def_id == definition.id,
                            CustomFieldOption.is_active.is_(True),
                        )
                    )
                ).all()
                option_ids[definition.id] = {str(option_id) for (option_id,) in rows}
            return option_ids[definition.id]

        async def validate_filter_value(
            definition: CustomFieldDef,
            value: Any,
            *,
            path: str,
        ) -> Any:
            field_type = definition.type
            if field_type in {"text", "textarea", "url"}:
                if not isinstance(value, str):
                    raise self._custom_config_error(
                        code="invalid_filters",
                        message="custom text filter value must be a string",
                        path=path,
                        field_def_id=str(definition.id),
                    )
                return value
            if field_type == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise self._custom_config_error(
                        code="invalid_filters",
                        message="custom number filter value must be finite",
                        path=path,
                        field_def_id=str(definition.id),
                    )
                return value
            if field_type in {"date", "datetime"}:
                if not isinstance(value, str):
                    parsed = None
                else:
                    try:
                        parsed = (
                            date.fromisoformat(value)
                            if field_type == "date"
                            else datetime.fromisoformat(value.replace("Z", "+00:00"))
                        )
                    except ValueError:
                        parsed = None
                if parsed is None:
                    raise self._custom_config_error(
                        code="invalid_filters",
                        message=f"custom {field_type} filter value must be ISO-8601",
                        path=path,
                        field_def_id=str(definition.id),
                    )
                return value
            if field_type == "boolean":
                if not isinstance(value, bool):
                    raise self._custom_config_error(
                        code="invalid_filters",
                        message="custom boolean filter value must be true or false",
                        path=path,
                        field_def_id=str(definition.id),
                    )
                return value
            if field_type == "member":
                try:
                    member_id = uuid.UUID(value) if isinstance(value, str) else None
                except ValueError:
                    member_id = None
                member = (
                    await session.scalar(
                        select(Member.id).where(
                            Member.workspace_id == workspace_id,
                            Member.id == member_id,
                            Member.status == "active",
                        )
                    )
                    if member_id is not None
                    else None
                )
                if member is None:
                    raise self._custom_config_error(
                        code="invalid_filters",
                        message="custom member filter value is not active in this workspace",
                        path=path,
                        field_def_id=str(definition.id),
                    )
                return str(member_id)
            if field_type in {"single_select", "multi_select"}:
                try:
                    canonical = str(uuid.UUID(value)) if isinstance(value, str) else ""
                except ValueError:
                    canonical = ""
                if canonical not in await active_options(definition):
                    raise self._custom_config_error(
                        code="invalid_filters",
                        message="custom select filter value is not an active option",
                        path=path,
                        field_def_id=str(definition.id),
                    )
                return canonical
            raise self._custom_config_error(
                code="invalid_filters",
                message="unsupported custom field type",
                path=path,
                field_def_id=str(definition.id),
            )

        async def walk(node: Any, *, path: str) -> None:
            if not isinstance(node, dict):
                return
            if "conditions" in node:
                for index, child in enumerate(node.get("conditions", [])):
                    await walk(child, path=f"{path}.conditions[{index}]")
                return
            if node.get("field_kind") != "custom_field" and "field_def_id" not in node:
                return
            definition = await resolve(
                node.get("field_def_id"),
                code="invalid_filters",
                path=f"{path}.field_def_id",
            )
            op = node.get("op")
            allowed = (
                _CUSTOM_TEXT_OPS
                if definition.type in {"text", "textarea", "url"}
                else _CUSTOM_EQUALITY_OPS
            )
            if definition.type in {"number", "date", "datetime"}:
                allowed |= _CUSTOM_RANGE_OPS
            if op not in allowed:
                raise self._custom_config_error(
                    code="invalid_filters",
                    message=f"operator {op!r} is not valid for custom field type {definition.type!r}",
                    path=f"{path}.op",
                    field_def_id=str(definition.id),
                )
            if op in {"is_null", "is_not_null"}:
                return
            raw = node.get("value")
            if op in {"in", "not_in"}:
                node["value"] = [
                    await validate_filter_value(
                        definition,
                        item,
                        path=f"{path}.value[{index}]",
                    )
                    for index, item in enumerate(raw)
                ]
            else:
                node["value"] = await validate_filter_value(
                    definition,
                    raw,
                    path=f"{path}.value",
                )

        built_in_axes = {
            "state_category",
            "status",
            "assignee",
            "priority",
            "project",
            "label",
        }
        for field_name, axis in (("group_by", group_by), ("sub_group_by", sub_group_by)):
            if axis is not None and axis not in built_in_axes:
                await resolve(axis, code="invalid_group_by", path=f"$.{field_name}")
        await walk(filters, path="$")
        for index, rule in enumerate(sort):
            if rule.get("field_kind") == "custom_field" or "field_def_id" in rule:
                await resolve(
                    rule.get("field_def_id"),
                    code="invalid_sort",
                    path=f"$.[{index}].field_def_id",
                )

    async def _next_position(self, session: AsyncSession, *, workspace_id: uuid.UUID) -> float:
        maximum = await session.scalar(
            select(func.max(View.position)).where(View.workspace_id == workspace_id)
        )
        return 1.0 if maximum is None else float(maximum) + 1.0

    async def _clear_scope_defaults(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
        except_view_id: uuid.UUID | None = None,
    ) -> None:
        """Unset every default view in the target scope (same transaction as
        setting the new default — kanban §2.2 / README §6.3 'at least one')."""
        scope = View.project_id == project_id if project_id is not None else View.project_id.is_(None)
        stmt = (
            update(View)
            .where(View.workspace_id == workspace_id, View.is_default.is_(True), scope)
            .values(is_default=False)
        )
        if except_view_id is not None:
            stmt = stmt.where(View.id != except_view_id)
        await session.execute(stmt)

    async def _insert_view(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        owner_member_id: uuid.UUID,
        name: str,
        layout: str,
        visibility: str,
        project_id: uuid.UUID | None,
        filters: dict,
        group_by: str | None,
        sub_group_by: str | None,
        sort: list,
        display_fields: list,
        board_settings: dict,
        is_default: bool,
        position: float,
    ) -> View:
        view = View(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            owner_member_id=owner_member_id,
            name=name,
            layout=layout,
            visibility=visibility,
            filters=filters,
            group_by=group_by,
            sub_group_by=sub_group_by,
            sort=sort,
            display_fields=display_fields,
            board_settings=board_settings,
            is_default=is_default,
            position=position,
        )
        session.add(view)
        try:
            await session.flush()
        except IntegrityError as exc:
            if _violates(exc, "uq_views_name"):
                raise ConflictError(
                    "a view with this name already exists in this scope",
                    code="view_name_taken",
                    details={"name": name},
                ) from exc
            if _violates(exc, "uq_views_default"):
                raise ConflictError(
                    "a default view already exists in this scope",
                    code="default_view_conflict",
                ) from exc
            raise
        return view

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_view(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateViewRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        if actor.role == "guest":
            raise ForbiddenError("guests cannot create views")
        name = validate_name(body.name)
        layout = validate_layout(body.layout)
        visibility = validate_visibility(body.visibility)
        group_by = validate_group_by(body.group_by)
        sub_group_by = validate_group_by(body.sub_group_by)
        validate_group_axes(group_by, sub_group_by)
        filters = validate_filters(body.filters)
        sort = validate_sort(body.sort)
        display_fields = validate_display_fields(body.display_fields)
        board_settings = validate_board_settings(body.board_settings)

        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project_id: uuid.UUID | None = None
            if body.project_id is not None:
                try:
                    project_id = uuid.UUID(body.project_id)
                except ValueError as exc:
                    raise NotFoundError("project not found") from exc
                await self._validate_project_ref(
                    session, viewer=actor, workspace_id=workspace_id, project_id=project_id
                )
            await self.validate_config_references(
                session,
                workspace_id=workspace_id,
                project_id=project_id,
                group_by=group_by,
                sub_group_by=sub_group_by,
                filters=filters,
                sort=sort,
            )
            if body.is_default:
                await self._clear_scope_defaults(session, workspace_id=workspace_id, project_id=project_id)
            view = await self._insert_view(
                session,
                workspace_id=workspace_id,
                owner_member_id=actor.id,
                name=name,
                layout=layout,
                visibility=visibility,
                project_id=project_id,
                filters=filters,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sort=sort,
                display_fields=display_fields,
                board_settings=board_settings,
                is_default=body.is_default,
                position=await self._next_position(session, workspace_id=workspace_id),
            )
            rendered = self.render_view(view, can_write=True)
            await self._emit_view_event(session, view=view, data=rendered)
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="view.created",
                resource_id=view.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def list_views(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(View).where(View.workspace_id == workspace_id)
            if project_id is not None:
                stmt = stmt.where(View.project_id == project_id)
            # Visible = own views (any visibility) + shared views. Private
            # views of others are NEVER listed (kanban §3.4: private = owner
            # only — managers included; they can write shared views instead).
            stmt = stmt.where(
                or_(
                    View.owner_member_id == viewer.id,
                    View.visibility == "shared",
                )
            )
            page = await paginate(
                session,
                stmt,
                sort_column=View.position,
                id_column=View.id,
                sort_value_of=lambda row: float(row.position),
                id_of=lambda row: row.id,
                cursor=cursor,
                limit=limit if limit is not None else DEFAULT_PAGE_LIMIT,
            )
            views = list(page.items)
            # Project-scoped shared views also require project visibility so
            # private projects don't leak through shared configs (managers
            # pass this check for every project).
            visible: list[View] = []
            for view in views:
                if view.owner_member_id != viewer.id and view.project_id is not None:
                    project = await session.scalar(
                        select(Project).where(
                            Project.id == view.project_id,
                            Project.workspace_id == workspace_id,
                            Project.deleted_at.is_(None),
                        )
                    )
                    if project is None or not await self._project_readable(
                        session, viewer=viewer, project=project
                    ):
                        continue
                visible.append(view)
            views = visible
            items = [
                self.render_view(
                    view,
                    can_write=await self._can_write_quick(session, viewer=viewer, view=view),
                )
                for view in views
            ]
            return items, page.next_cursor

    async def _can_write_quick(self, session: AsyncSession, *, viewer: Member, view: View) -> bool:
        if view.owner_member_id == viewer.id:
            return True
        if self._is_workspace_manager(viewer):
            return True
        if view.project_id is not None:
            project = await session.scalar(
                select(Project).where(
                    Project.id == view.project_id,
                    Project.workspace_id == view.workspace_id,
                    Project.deleted_at.is_(None),
                )
            )
            return project is not None and await self._is_project_lead(
                session, viewer=viewer, project=project
            )
        return False

    async def get_view(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            await self.assert_can_read(session, viewer=viewer, view=view)
            return self.render_view(
                view, can_write=await self._can_write_quick(session, viewer=viewer, view=view)
            )

    async def update_view(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        fields: dict[str, Any],
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id, for_update=True)
            await self.assert_can_write(session, viewer=actor, view=view)
            if if_match is not None and not self._matches_version(view, if_match):
                raise ConflictError(
                    "view was modified concurrently",
                    code="conflict",
                    details={"id": str(view.id)},
                )

            changes: dict[str, Any] = {}
            if "name" in fields:
                name = validate_name(fields["name"])
                if name != view.name:
                    changes["name"] = name
            if "layout" in fields:
                layout = validate_layout(fields["layout"])
                if layout != view.layout:
                    changes["layout"] = layout
            if "visibility" in fields:
                visibility = validate_visibility(fields["visibility"])
                if visibility != view.visibility:
                    changes["visibility"] = visibility
            if "group_by" in fields:
                group_by = validate_group_by(fields["group_by"])
                if group_by != view.group_by:
                    changes["group_by"] = group_by
            if "sub_group_by" in fields:
                sub_group_by = validate_group_by(fields["sub_group_by"])
                if sub_group_by != view.sub_group_by:
                    changes["sub_group_by"] = sub_group_by
            validate_group_axes(
                changes.get("group_by", view.group_by),
                changes.get("sub_group_by", view.sub_group_by),
            )
            if "filters" in fields:
                filters = validate_filters(fields["filters"])
                if filters != view.filters:
                    changes["filters"] = filters
            if "sort" in fields:
                sort = validate_sort(fields["sort"])
                if sort != view.sort:
                    changes["sort"] = sort
            if "display_fields" in fields:
                display_fields = validate_display_fields(fields["display_fields"])
                if display_fields != view.display_fields:
                    changes["display_fields"] = display_fields
            if "board_settings" in fields:
                # Shallow merge: top-level keys replace, siblings survive.
                incoming = validate_board_settings(fields["board_settings"])
                merged = {**(view.board_settings or {}), **incoming}
                if merged != view.board_settings:
                    changes["board_settings"] = merged
            if "project_id" in fields:
                raw = fields["project_id"]
                new_project_id: uuid.UUID | None = None
                if raw is not None:
                    try:
                        new_project_id = uuid.UUID(raw)
                    except ValueError as exc:
                        raise NotFoundError("project not found") from exc
                    await self._validate_project_ref(
                        session, viewer=actor, workspace_id=workspace_id, project_id=new_project_id
                    )
                if new_project_id != view.project_id:
                    changes["project_id"] = new_project_id
            await self.validate_config_references(
                session,
                workspace_id=workspace_id,
                project_id=changes.get("project_id", view.project_id),
                group_by=changes.get("group_by", view.group_by),
                sub_group_by=changes.get("sub_group_by", view.sub_group_by),
                filters=changes.get("filters", view.filters or {}),
                sort=changes.get("sort", view.sort or []),
            )
            if "is_default" in fields and not fields["is_default"] and view.is_default:
                changes["is_default"] = False
            if "is_default" in fields and fields["is_default"] and not view.is_default:
                target_project = changes.get("project_id", view.project_id)
                await self._clear_scope_defaults(
                    session,
                    workspace_id=workspace_id,
                    project_id=target_project,
                    except_view_id=view.id,
                )
                changes["is_default"] = True

            if not changes:
                # §6.9: empty diff is a no-op (no event, no audit).
                return self.render_view(
                    view, can_write=await self._can_write_quick(session, viewer=actor, view=view)
                )

            for field, value in changes.items():
                setattr(view, field, value)
            view.updated_at = _now(self._clock)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_views_name"):
                    # NOTE: view.name would lazy-refresh inside the aborted
                    # transaction — report the known pending value instead.
                    raise ConflictError(
                        "a view with this name already exists in this scope",
                        code="view_name_taken",
                        details={"name": changes.get("name")},
                    ) from exc
                if _violates(exc, "uq_views_default"):
                    raise ConflictError(
                        "a default view already exists in this scope",
                        code="default_view_conflict",
                    ) from exc
                raise

            rendered = self.render_view(
                view, can_write=await self._can_write_quick(session, viewer=actor, view=view)
            )
            await self._emit_view_event(
                session,
                view=view,
                data={**rendered, "changes": sorted(changes.keys())},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="view.updated",
                resource_id=view.id,
                metadata={"changes": sorted(changes.keys())},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def delete_view(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id, for_update=True)
            await self.assert_can_write(session, viewer=actor, view=view)
            payload = {
                "id": str(view.id),
                "workspace_id": str(view.workspace_id),
                "project_id": str(view.project_id) if view.project_id is not None else None,
                "deleted": True,
                "updated_at": _isoformat(_now(self._clock)),
            }
            await session.delete(view)
            await session.flush()
            # No registered view.deleted name (§6.7 registry): the list channel
            # carries the removal as a view.updated frame with deleted=true.
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_workspace_views_channel(workspace_id),
                event="view.updated",
                data=payload,
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="view.deleted",
                resource_id=view_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def duplicate_view(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            source = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            await self.assert_can_read(session, viewer=actor, view=source)
            if actor.role == "guest":
                raise ForbiddenError("guests cannot create views")
            await self.validate_config_references(
                session,
                workspace_id=workspace_id,
                project_id=source.project_id,
                group_by=source.group_by,
                sub_group_by=source.sub_group_by,
                filters=source.filters or {},
                sort=source.sort or [],
            )
            name = await self._duplicate_name(session, source=source)
            view = await self._insert_view(
                session,
                workspace_id=workspace_id,
                owner_member_id=actor.id,
                name=name,
                layout=source.layout,
                visibility=source.visibility,
                project_id=source.project_id,
                filters=dict(source.filters or {}),
                group_by=source.group_by,
                sub_group_by=source.sub_group_by,
                sort=list(source.sort or []),
                display_fields=list(source.display_fields or []),
                board_settings=dict(source.board_settings or {}),
                is_default=False,
                position=await self._next_position(session, workspace_id=workspace_id),
            )
            rendered = self.render_view(view, can_write=True)
            await self._emit_view_event(session, view=view, data=rendered)
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="view.duplicated",
                resource_id=view.id,
                metadata={"source_view_id": str(source.id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def _duplicate_name(self, session: AsyncSession, *, source: View) -> str:
        """'Name (copy)', then 'Name (copy N)' until the scope key is free."""
        scope = (
            View.project_id == source.project_id
            if source.project_id is not None
            else View.project_id.is_(None)
        )
        candidate = f"{source.name} (copy)"
        for suffix in range(2, _DUPLICATE_SUFFIX_LIMIT + 1):
            taken = await session.scalar(
                select(View.id).where(View.workspace_id == source.workspace_id, scope, View.name == candidate)
            )
            if taken is None:
                return candidate
            candidate = f"{source.name} (copy {suffix})"
        raise ConflictError(
            "too many copies of this view",
            code="view_name_taken",
            details={"name": candidate},
        )

    async def patch_wip(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        body: WipRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        if body.enforcement not in ("warn", "block"):
            raise ValidationError(
                "enforcement must be warn or block",
                code="invalid_board_settings",
                details={"enforcement": body.enforcement},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id, for_update=True)
            await self.assert_can_write(session, viewer=actor, view=view)
            board_settings = dict(view.board_settings or {})
            wip = dict(board_settings.get("wip") or {})
            if body.limit is None:
                if body.group_key not in wip:
                    # No-op: removing an absent rule (§6.9).
                    return self.render_view(
                        view, can_write=await self._can_write_quick(session, viewer=actor, view=view)
                    )
                del wip[body.group_key]
            else:
                rule = {"limit": body.limit, "enforcement": body.enforcement}
                if wip.get(body.group_key) == rule:
                    return self.render_view(
                        view, can_write=await self._can_write_quick(session, viewer=actor, view=view)
                    )
                wip[body.group_key] = rule
            board_settings["wip"] = wip
            view.board_settings = board_settings
            view.updated_at = _now(self._clock)
            await session.flush()
            rendered = self.render_view(
                view, can_write=await self._can_write_quick(session, viewer=actor, view=view)
            )
            await self._emit_view_event(session, view=view, data={**rendered, "changes": ["board_settings"]})
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="view.wip_updated",
                resource_id=view.id,
                metadata={"group_key": body.group_key},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def reorder_views(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_ids: list[uuid.UUID],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> list[dict]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            views: dict[uuid.UUID, View] = {}
            for view_id in view_ids:
                view = await self._load_view(
                    session, workspace_id=workspace_id, view_id=view_id, for_update=True
                )
                await self.assert_can_write(session, viewer=actor, view=view)
                views[view.id] = view
            for index, view_id in enumerate(view_ids):
                views[view_id].position = float(index + 1)
            await session.flush()
            ordered = [views[view_id] for view_id in view_ids]
            return [
                self.render_view(
                    view, can_write=await self._can_write_quick(session, viewer=actor, view=view)
                )
                for view in ordered
            ]

    # ------------------------------------------------------------------
    # optimistic concurrency
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_version(view: View, if_match: str) -> bool:
        """Optimistic concurrency (README §6.14): If-Match carries updated_at."""
        candidate = if_match.strip().strip('"')
        if candidate == _isoformat(view.updated_at):
            return True
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed == view.updated_at
