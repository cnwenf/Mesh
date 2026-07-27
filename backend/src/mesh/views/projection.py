"""View projection layer — grouped projection query + filter compiler.

Implements the issue-coupled half of kanban.md: ``GET /views/{id}/issues``
executes a saved view's config (filters / group_by / sort) against ``issues``
at query time and returns the README §6.14 **overall-cursor** grouped envelope
(``{"groups":[{key,label,count,wip?,data}], "next_cursor"}`` — one cursor for
the whole response, never per-group cursors).

The saved-view filter shape (``{"operator","conditions":[...]}`` with ops
eq/neq/in/not_in/lt/lte/gt/gte/is_null/is_not_null/contains) differs from the
issue module's flat tree, so it is compiled here against the ``issues`` columns.
Every value is bound as a parameter (never spliced into SQL — kanban §2.9
injection guard). Filter limits (depth ≤3, conditions ≤20 → ``filter_too_complex``)
and ``statement_timeout`` (→ ``query_cost_exceeded``) follow README §6.14.

Label / custom-field filtering & grouping require the ``issue_labels`` /
``issue_custom_field_values`` association tables owned by the label-property
increment (MES-32); until those exist they are gated with a named code, exactly
as the issue module gates ``group_by=label`` (no mock stand-ins).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, MeshError, NotFoundError, ValidationError
from mesh.issue.filters import (
    LIST_STATEMENT_TIMEOUT_MS,
    MAX_FILTER_CONDITIONS,
    MAX_FILTER_DEPTH,
    FilterTooComplexError,
    coerce_date,
)
from mesh.issue.statuses import resolve_default_status
from mesh.views.config import PRIORITY_KEYS, STATE_CATEGORY_KEYS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from mesh.issue.service import IssueService
    from mesh.views.service import ViewService

# Named code for label / custom-field projection gated on MES-32 associations.
PROJECTION_FIELD_PENDING = "projection_field_pending"

# Built-in filterable columns (kanban §2.3 minus ``label``/``q`` which are
# handled specially; custom fields are gated on MES-32).
_FILTER_COLUMNS: dict[str, Any] = {
    "state_category": Issue.state_category,
    "status_id": Issue.status_id,
    "priority": Issue.priority,
    "assignee_id": Issue.assignee_id,
    "reporter_id": Issue.reporter_id,
    "project_id": Issue.project_id,
    "cycle_id": Issue.cycle_id,
    "milestone_id": Issue.milestone_id,
    "due_date": Issue.due_date,
    "start_date": Issue.start_date,
    "created_at": Issue.created_at,
    "updated_at": Issue.updated_at,
    "parent_id": Issue.parent_id,
}

_UUID_FIELDS = frozenset(
    {
        "status_id",
        "assignee_id",
        "reporter_id",
        "project_id",
        "cycle_id",
        "milestone_id",
        "parent_id",
    }
)
_DATE_FIELDS = frozenset({"due_date", "start_date"})
_DATETIME_FIELDS = frozenset({"created_at", "updated_at"})
_NULL_OPS = frozenset({"is_null", "is_not_null"})
_LIST_OPS = frozenset({"in", "not_in"})

_TOO_COMPLEX = "filter is too complex"


def _invalid_filters(message: str, **details: Any) -> ValidationError:
    return ValidationError(message, code="invalid_filters", details=details)


def _coerce_scalar(field: str, value: Any) -> Any:
    """Bind-ready value for a leaf condition (parameterized, never spliced)."""
    if field in _UUID_FIELDS:
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError) as exc:
            raise _invalid_filters("invalid UUID filter value", field=field) from exc
    if field in _DATE_FIELDS:
        try:
            return coerce_date(value)
        except (ValueError, TypeError) as exc:
            raise _invalid_filters("invalid date filter value", field=field) from exc
    if field in _DATETIME_FIELDS:
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError) as exc:
            raise _invalid_filters("invalid datetime filter value", field=field) from exc
    return value


def _enforce_limits(filters: dict) -> None:
    """Defense-in-depth re-check of README §6.14 limits on stored config."""

    def walk(node: Any, depth: int, counter: list[int]) -> None:
        if not isinstance(node, dict) or "operator" not in node:
            counter[0] += 1
            if counter[0] > MAX_FILTER_CONDITIONS:
                raise FilterTooComplexError(
                    _TOO_COMPLEX,
                    details={"conditions": counter[0], "max": MAX_FILTER_CONDITIONS},
                )
            return
        if depth > MAX_FILTER_DEPTH:
            raise FilterTooComplexError(
                _TOO_COMPLEX, details={"depth": depth, "max_depth": MAX_FILTER_DEPTH}
            )
        conditions = node.get("conditions")
        if not isinstance(conditions, list):
            raise _invalid_filters("conditions must be an array")
        for child in conditions:
            walk(child, depth + 1, counter)

    walk(filters, 1, [0])


def _compile_leaf(condition: dict) -> Any:
    # Custom-field conditions gate on the MES-32 association tables.
    if condition.get("field_kind") == "custom_field" or "field_def_id" in condition:
        raise ValidationError(
            "custom-field filters await the label-property association increment",
            code=PROJECTION_FIELD_PENDING,
            details={"field_def_id": str(condition.get("field_def_id"))[:64]},
        )

    field = condition.get("field")
    op = condition.get("op")

    if field == "label":
        raise ValidationError(
            "label filters await the label-property association increment",
            code=PROJECTION_FIELD_PENDING,
            details={"field": "label"},
        )

    if field == "q":
        # title/identifier search (contains only, kanban §2.3).
        pattern = f"%{condition.get('value', '')}%"
        return or_(Issue.title.ilike(pattern), Issue.identifier.ilike(pattern))

    if field not in _FILTER_COLUMNS:
        raise _invalid_filters("unknown filter field", field=str(field)[:32])

    column = _FILTER_COLUMNS[field]

    if op in _NULL_OPS:
        return column.is_(None) if op == "is_null" else column.is_not(None)

    if op in _LIST_OPS:
        raw = condition.get("value")
        if not isinstance(raw, list):
            raise _invalid_filters("list op requires an array value", field=field, op=op)
        values = [_coerce_scalar(field, item) for item in raw]
        return column.in_(values) if op == "in" else column.not_in(values)

    if op == "contains":
        # Only meaningful on text columns; reject type/op mismatch (§3.3).
        if field in _UUID_FIELDS or field in _DATE_FIELDS or field in _DATETIME_FIELDS:
            raise _invalid_filters("contains is not valid for this field", field=field)
        return column.ilike(f"%{condition.get('value', '')}%")

    value = _coerce_scalar(field, condition.get("value"))
    if op == "eq":
        return column == value
    if op == "neq":
        return column != value
    if op == "lt":
        return column < value
    if op == "lte":
        return column <= value
    if op == "gt":
        return column > value
    if op == "gte":
        return column >= value
    raise _invalid_filters("unknown filter op", field=str(field)[:32], op=str(op)[:16])


def _compile_node(node: Any) -> Any:
    if not isinstance(node, dict):
        raise _invalid_filters("filter node must be an object")
    if "operator" in node:
        operator = node.get("operator")
        conditions = node.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise _invalid_filters("conditions must be a non-empty array")
        clauses = [_compile_node(child) for child in conditions]
        return and_(*clauses) if operator == "AND" else or_(*clauses)
    return _compile_leaf(node)


def compile_view_filters(filters: dict | None) -> Any:
    """Compile a validated saved-view ``filters`` JSONB into a SQL clause.

    ``{}``/``None`` → ``None`` (no filter). Raises ``filter_too_complex`` past
    the §6.14 limits, ``projection_field_pending`` for label/custom-field
    conditions, and ``invalid_filters`` for type/op mismatches.
    """
    if not filters:
        return None
    _enforce_limits(filters)
    return _compile_node(filters)


# ---------------------------------------------------------------------------
# grouped projection query (README §6.14 overall-cursor contract)
# ---------------------------------------------------------------------------

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

_VIEW_NOT_FOUND = "view not found"
_EMPTY_MEMBER_KEY = "__none__"
_EMPTY_PROJECT_KEY = "__none__"

_SUPPORTED_GROUP_BY = frozenset({"state_category", "status", "assignee", "priority", "project"})

_CATEGORY_LABELS: dict[str, str] = {
    "backlog": "Backlog",
    "todo": "Todo",
    "in_progress": "In Progress",
    "in_review": "In Review",
    "blocked": "Blocked",
    "done": "Done",
    "cancelled": "Cancelled",
}
_PRIORITY_LABELS: dict[str, str] = {
    "urgent": "Urgent",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "none": "None",
}

_GROUP_EXPR: dict[str, Any] = {
    "state_category": Issue.state_category,
    "status": Issue.status_id,
    "assignee": Issue.assignee_id,
    "priority": Issue.priority,
    "project": Issue.project_id,
}


class NotImplementedLayout(MeshError):
    """501 ``not_implemented`` for timeline/table rendering (kanban §3.3)."""

    status_code = 501
    code = "not_implemented"


def _limit_page(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("invalid limit", code="invalid_limit", details={"limit": limit})
    return min(limit, MAX_PAGE_LIMIT)


def _raw_group_value(group_by: str, issue: Issue) -> Any:
    return getattr(issue, _GROUP_FIELD[group_by])


_GROUP_FIELD: dict[str, str] = {
    "state_category": "state_category",
    "status": "status_id",
    "assignee": "assignee_id",
    "priority": "priority",
    "project": "project_id",
}


def _group_key(group_by: str, value: Any) -> str:
    if value is None:
        return _EMPTY_MEMBER_KEY  # assignee/project NULL → "__none__"
    return str(value)


def group_key_for(group_by: str, issue: Issue) -> str:
    """The group key an issue falls under for a given group_by (kanban §2.4)."""
    return _group_key(group_by, _raw_group_value(group_by, issue))


class ProjectionService:
    """Executes a saved view's config against issues (kanban §3.2)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        issue_service: IssueService,
        view_service: ViewService,
        *,
        clock: Any | None = None,
    ) -> None:
        self._factory = session_factory
        self._issues = issue_service
        self._views = view_service
        self._clock = clock

    async def execute_view(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await session.execute(
                text(f"SET LOCAL statement_timeout = {LIST_STATEMENT_TIMEOUT_MS}")
            )
            view = await session.scalar(
                select(View).where(View.id == view_id, View.workspace_id == workspace_id)
            )
            if view is None:
                raise NotFoundError(_VIEW_NOT_FOUND)
            await self._views.assert_can_read(session, viewer=viewer, view=view)
            if view.layout in ("timeline", "table"):
                raise NotImplementedLayout(
                    "layout is not renderable", details={"layout": view.layout}
                )
            group_by = view.group_by or "state_category"
            if group_by not in _SUPPORTED_GROUP_BY:
                raise ValidationError(
                    "group_by=label/custom-field awaits the label-property association increment",
                    code=PROJECTION_FIELD_PENDING,
                    details={"group_by": group_by},
                )
            try:
                return await self._run(
                    session,
                    viewer=viewer,
                    workspace_id=workspace_id,
                    view=view,
                    group_by=group_by,
                    page_limit=page_limit,
                    cursor=cursor,
                )
            except DBAPIError as exc:
                orig = getattr(exc, "orig", None)
                if "canceling statement due to statement timeout" in str(orig):
                    raise BusinessRuleError(
                        "query exceeded the cost budget; narrow the conditions",
                        code="query_cost_exceeded",
                    ) from exc
                raise

    def _base_conditions(self, viewer: Member, session: AsyncSession, view: View) -> list:
        conditions = [Issue.workspace_id == view.workspace_id, Issue.deleted_at.is_(None)]
        visibility = self._issues._base_visibility_clause(viewer, view.workspace_id)
        if visibility is not None:
            conditions.append(visibility)
        # A project-scoped view projects that project's issues.
        if view.project_id is not None:
            conditions.append(Issue.project_id == view.project_id)
        filters_clause = compile_view_filters(view.filters)
        if filters_clause is not None:
            conditions.append(filters_clause)
        return conditions

    async def _run(
        self,
        session: AsyncSession,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        view: View,
        group_by: str,
        page_limit: int,
        cursor: str | None,
    ) -> dict:
        conditions = self._base_conditions(viewer, session, view)
        group_expr = _GROUP_EXPR[group_by]

        # Full per-group totals over the whole filtered set (README §6.14:
        # count is the group total, data the current page slice).
        counts: dict[str, int] = {}
        count_rows = (
            await session.execute(
                select(group_expr, func.count()).where(*conditions).group_by(group_expr)
            )
        ).all()
        for value, count in count_rows:
            counts[_group_key(group_by, value)] = int(count)

        # Ordered page slice: manual per-view position wins, canonical
        # issues.position is the fallback (kanban §2.7). One overall cursor.
        vip = aliased(ViewIssuePosition)
        sort_col = func.coalesce(vip.position, Issue.position)
        stmt = (
            select(Issue, vip.position)
            .outerjoin(vip, and_(vip.view_id == view.id, vip.issue_id == Issue.id))
            .where(*conditions)
            .order_by(sort_col.asc(), Issue.id.asc())
        )
        if cursor is not None:
            position = decode_cursor(cursor)
            stmt = stmt.where(
                func.row(sort_col, Issue.id) > func.row(position.sort_value, position.id)
            )
        raw_rows = (await session.execute(stmt.limit(page_limit + 1))).all()

        next_cursor = None
        if len(raw_rows) > page_limit:
            raw_rows = raw_rows[:page_limit]
            last_issue, last_pos = raw_rows[-1]
            last_sort = last_pos if last_pos is not None else last_issue.position
            next_cursor = encode_cursor(last_sort, last_issue.id)

        # Bucket the page slice into groups, rendering each card.
        membership: dict[str, list[dict]] = {}
        for issue, _pos in raw_rows:
            rendered = await self._issues.render_issue(session, issue)
            key = group_key_for(group_by, issue)
            membership.setdefault(key, []).append(rendered)

        wip_map = (view.board_settings or {}).get("wip", {})
        ordered_keys = await self._ordered_group_keys(
            session, group_by=group_by, workspace_id=workspace_id, counts=counts, membership=membership
        )
        labels = await self._group_labels(
            session, group_by=group_by, workspace_id=workspace_id, keys=ordered_keys
        )
        groups = []
        for key in ordered_keys:
            groups.append(
                {
                    "key": key,
                    "label": labels.get(key, key),
                    "count": counts.get(key, 0),
                    "wip": wip_map.get(key),
                    "data": membership.get(key, []),
                }
            )

        return {
            "layout": view.layout,
            "group_by": group_by,
            "column_target_status": await self._column_target_status(
                session, group_by=group_by, workspace_id=workspace_id, view=view, keys=ordered_keys
            ),
            "groups": groups,
            "next_cursor": next_cursor,
        }

    async def _ordered_group_keys(
        self,
        session: AsyncSession,
        *,
        group_by: str,
        workspace_id: uuid.UUID,
        counts: dict[str, int],
        membership: dict[str, list[dict]],
    ) -> list[str]:
        present = set(counts) | set(membership)
        if group_by == "state_category":
            return list(STATE_CATEGORY_KEYS)
        if group_by == "priority":
            return list(PRIORITY_KEYS)
        # Dynamic groups (status/assignee/project): order by label.
        labels = await self._group_labels(
            session, group_by=group_by, workspace_id=workspace_id, keys=sorted(present)
        )
        return sorted(present, key=lambda key: (labels.get(key, key), key))

    async def _group_labels(
        self,
        session: AsyncSession,
        *,
        group_by: str,
        workspace_id: uuid.UUID,
        keys: list[str],
    ) -> dict[str, str]:
        labels: dict[str, str] = {}
        if group_by == "state_category":
            return {key: _CATEGORY_LABELS.get(key, key) for key in keys}
        if group_by == "priority":
            return {key: _PRIORITY_LABELS.get(key, key) for key in keys}
        if group_by == "status":
            status_ids = [uuid.UUID(key) for key in keys if key != _EMPTY_MEMBER_KEY]
            rows = (
                await session.execute(
                    select(IssueStatus.id, IssueStatus.name).where(
                        IssueStatus.id.in_(status_ids) if status_ids else IssueStatus.id.is_(None)
                    )
                )
            ).all()
            name_by_id = {str(sid): name for sid, name in rows}
            return {key: name_by_id.get(key, key) for key in keys}
        if group_by == "assignee":
            for key in keys:
                if key == _EMPTY_MEMBER_KEY:
                    labels[key] = "No assignee"
                    continue
                summary = await self._issues._member_summary(
                    session, workspace_id=workspace_id, member_id=uuid.UUID(key)
                )
                labels[key] = summary["name"] if summary is not None else key
            return labels
        # project
        for key in keys:
            if key == _EMPTY_PROJECT_KEY:
                labels[key] = "No project"
                continue
            project = await session.scalar(
                select(Project).where(
                    Project.id == uuid.UUID(key), Project.workspace_id == workspace_id
                )
            )
            labels[key] = project.name if project is not None else key
        return labels

    async def _column_target_status(
        self,
        session: AsyncSession,
        *,
        group_by: str,
        workspace_id: uuid.UUID,
        view: View,
        keys: list[str],
    ) -> dict[str, str]:
        """Map a drop target (group key) to the status_id a drag should set.

        state_category → that category's default status in the view's scope
        (kanban §2.4); status → identity. Empty for assignee/priority/project
        (the move command handles those group keys directly).
        """
        if group_by == "state_category":
            mapping: dict[str, str] = {}
            for category in STATE_CATEGORY_KEYS:
                try:
                    status = await resolve_default_status(
                        session,
                        workspace_id=workspace_id,
                        project_id=view.project_id,
                        category=category,
                    )
                except Exception:  # no status in category for this scope
                    continue
                mapping[category] = str(status.id)
            return mapping
        if group_by == "status":
            return {key: key for key in keys if key != _EMPTY_MEMBER_KEY}
        return {}


__all__ = [
    "NotImplementedLayout",
    "PROJECTION_FIELD_PENDING",
    "ProjectionService",
    "compile_view_filters",
    "group_key_for",
]
