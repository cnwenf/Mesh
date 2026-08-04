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

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    MeshError,
    NotFoundError,
    ValidationError,
)
from mesh.issue.filters import (
    LIST_STATEMENT_TIMEOUT_MS,
    MAX_FILTER_CONDITIONS,
    MAX_FILTER_DEPTH,
    FilterTooComplexError,
    coerce_date,
)
from mesh.issue.statuses import resolve_default_status
from mesh.search.cursor import (
    decode_cursor as decode_signed_cursor,
)
from mesh.search.cursor import (
    encode_cursor as encode_signed_cursor,
)
from mesh.views.config import PRIORITY_KEYS, STATE_CATEGORY_KEYS, validate_group_axes

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
            raise FilterTooComplexError(_TOO_COMPLEX, details={"depth": depth, "max_depth": MAX_FILTER_DEPTH})
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
_PROCESS_VIEW_CURSOR_SECRET = secrets.token_bytes(32)
SWIMLANE_CURSOR_TTL = timedelta(minutes=15)

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


def _now(clock: Any | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


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
        cursor_secret: str | bytes | None = None,
    ) -> None:
        self._factory = session_factory
        self._issues = issue_service
        self._views = view_service
        self._clock = clock
        if isinstance(cursor_secret, str):
            self._cursor_secret = cursor_secret.encode("utf-8")
        else:
            self._cursor_secret = cursor_secret or _PROCESS_VIEW_CURSOR_SECRET

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
            await session.execute(text(f"SET LOCAL statement_timeout = {LIST_STATEMENT_TIMEOUT_MS}"))
            view = await session.scalar(
                select(View).where(View.id == view_id, View.workspace_id == workspace_id)
            )
            if view is None:
                raise NotFoundError(_VIEW_NOT_FOUND)
            await self._views.assert_can_read(session, viewer=viewer, view=view)
            if view.layout in ("timeline", "table"):
                raise NotImplementedLayout("layout is not renderable", details={"layout": view.layout})
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            validate_group_axes(view.group_by, sub_group_by)
            if group_by not in _SUPPORTED_GROUP_BY:
                raise ValidationError(
                    "group_by=label/custom-field awaits the label-property association increment",
                    code=PROJECTION_FIELD_PENDING,
                    details={"group_by": group_by},
                )
            if sub_group_by is not None and sub_group_by not in _SUPPORTED_GROUP_BY:
                raise ValidationError(
                    "sub_group_by=label/custom-field awaits the label-property association increment",
                    code=PROJECTION_FIELD_PENDING,
                    details={"sub_group_by": sub_group_by},
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
        if view.layout == "board" and view.sub_group_by is not None:
            return await self._run_swimlanes(
                session,
                workspace_id=workspace_id,
                viewer=viewer,
                view=view,
                group_by=group_by,
                sub_group_by=view.sub_group_by,
                page_limit=page_limit,
                cursor=cursor,
            )
        conditions = self._base_conditions(viewer, session, view)
        group_expr = _GROUP_EXPR[group_by]

        # Full per-group totals over the whole filtered set (README §6.14:
        # count is the group total, data the current page slice).
        counts: dict[str, int] = {}
        count_rows = (
            await session.execute(select(group_expr, func.count()).where(*conditions).group_by(group_expr))
        ).all()
        for value, count in count_rows:
            counts[_group_key(group_by, value)] = int(count)

        # Ordered page slice: manual per-view position wins, canonical
        # issues.position is the fallback (kanban §2.7). One overall cursor.
        vip = aliased(ViewIssuePosition)
        sort_col = func.coalesce(vip.position, Issue.position)
        current_group_key = func.coalesce(cast(group_expr, String), _EMPTY_MEMBER_KEY)
        stmt = (
            select(Issue, vip.position)
            .outerjoin(
                vip,
                and_(
                    vip.view_id == view.id,
                    vip.issue_id == Issue.id,
                    vip.group_key == current_group_key,
                    vip.sub_group_key == "",
                ),
            )
            .where(*conditions)
            .order_by(sort_col.asc(), Issue.id.asc())
        )
        if cursor is not None:
            position = decode_cursor(cursor)
            stmt = stmt.where(func.row(sort_col, Issue.id) > func.row(position.sort_value, position.id))
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
            session,
            group_by=group_by,
            workspace_id=workspace_id,
            counts=counts,
            membership=membership,
            configured_order=(view.board_settings or {}).get("columns"),
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

        result: dict[str, Any] = {
            "layout": view.layout,
            "group_by": group_by,
            "groups": groups,
            "next_cursor": next_cursor,
        }
        # Workspace-wide boards can contain issues from projects with
        # different private status domains, so one flat category→status map
        # would be ambiguous even in the one-dimensional response.
        if group_by == "state_category" and view.project_id is not None:
            result["column_target_status"] = await self._column_target_status(
                session,
                group_by=group_by,
                workspace_id=workspace_id,
                view=view,
                keys=ordered_keys,
            )
        return result

    @staticmethod
    def _sort_value(issue: Issue, *, field: str, position: float) -> Any:
        if field == "position":
            return position
        value = getattr(issue, field)
        if field == "priority":
            return PRIORITY_KEYS.index(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    def _sort_cell_rows(
        self,
        rows: list[tuple[Issue, str, str, float]],
        *,
        sort_rules: list[dict],
    ) -> list[tuple[Issue, str, str, float]]:
        ordered = sorted(rows, key=lambda row: str(row[0].id))
        rules = sort_rules or [{"field": "position", "order": "asc"}]
        for rule in reversed(rules):
            if rule.get("field_kind") == "custom_field":
                raise ValidationError(
                    "custom-field sorting awaits the association increment",
                    code=PROJECTION_FIELD_PENDING,
                )
            field = rule["field"]
            present = [
                row for row in ordered if self._sort_value(row[0], field=field, position=row[3]) is not None
            ]
            missing = [row for row in ordered if row not in present]
            present.sort(
                key=lambda row: self._sort_value(row[0], field=field, position=row[3]),
                reverse=rule["order"] == "desc",
            )
            ordered = present + missing
        return ordered

    @staticmethod
    def _swimlane_fingerprint(
        *,
        view: View,
        viewer: Member,
        page_limit: int,
        group_keys: list[str],
        lane_keys: list[str],
        rows: list[tuple[Issue, str, str, float]],
    ) -> str:
        snapshot = {
            "v": 1,
            "view_id": str(view.id),
            "viewer_id": str(viewer.id),
            "limit": page_limit,
            "updated_at": view.updated_at.isoformat(),
            "filters": view.filters,
            "group_by": view.group_by or "state_category",
            "sub_group_by": view.sub_group_by,
            "sort": view.sort,
            "columns": group_keys,
            "lanes": lane_keys,
            "rows": [
                [
                    str(issue.id),
                    issue.updated_at.isoformat(),
                    lane_key,
                    group_key,
                    position,
                ]
                for issue, lane_key, group_key, position in sorted(
                    rows, key=lambda row: (row[1], row[2], str(row[0].id))
                )
            ],
        }
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _run_swimlanes(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        viewer: Member,
        view: View,
        group_by: str,
        sub_group_by: str,
        page_limit: int,
        cursor: str | None,
    ) -> dict:
        """Build the two-dimensional board envelope.

        The query intentionally loads the filtered projection snapshot once,
        then applies the deterministic lane → column → cell-position ordering
        in memory. This keeps the response skeleton, distinct aggregate
        counts and page slicing anchored to the exact same visible row set;
        it also makes a stale cursor fail closed instead of drifting into a
        different cell.
        """
        conditions = self._base_conditions(viewer, session, view)
        vip = aliased(ViewIssuePosition)
        raw_rows = (
            await session.execute(
                select(
                    Issue,
                    vip.position,
                    vip.group_key,
                    vip.sub_group_key,
                )
                .outerjoin(vip, and_(vip.view_id == view.id, vip.issue_id == Issue.id))
                .where(*conditions)
            )
        ).all()

        group_counts: dict[str, int] = {}
        lane_counts: dict[str, int] = {}
        cell_counts: dict[tuple[str, str], int] = {}
        group_ids: dict[str, set[uuid.UUID]] = {}
        lane_ids: dict[str, set[uuid.UUID]] = {}
        rows: list[tuple[Issue, str, str, float]] = []
        for issue, manual_position, manual_group, manual_sub_group in raw_rows:
            group_key = group_key_for(group_by, issue)
            lane_key = group_key_for(sub_group_by, issue)
            group_ids.setdefault(group_key, set()).add(issue.id)
            lane_ids.setdefault(lane_key, set()).add(issue.id)
            cell = (lane_key, group_key)
            cell_counts[cell] = cell_counts.get(cell, 0) + 1
            position = (
                float(manual_position)
                if manual_position is not None and manual_group == group_key and manual_sub_group == lane_key
                else float(issue.position)
            )
            rows.append((issue, lane_key, group_key, position))
        group_counts.update({key: len(ids) for key, ids in group_ids.items()})
        lane_counts.update({key: len(ids) for key, ids in lane_ids.items()})

        group_keys = await self._ordered_group_keys(
            session,
            group_by=group_by,
            workspace_id=workspace_id,
            counts=group_counts,
            membership={},
            configured_order=(view.board_settings or {}).get("columns"),
        )
        lane_keys = await self._ordered_group_keys(
            session,
            group_by=sub_group_by,
            workspace_id=workspace_id,
            counts=lane_counts,
            membership={},
        )
        rows_by_cell: dict[tuple[str, str], list[tuple[Issue, str, str, float]]] = {}
        for row in rows:
            rows_by_cell.setdefault((row[1], row[2]), []).append(row)
        rows = [
            row
            for lane_key in lane_keys
            for group_key in group_keys
            for row in self._sort_cell_rows(
                rows_by_cell.get((lane_key, group_key), []),
                sort_rules=view.sort or [],
            )
        ]
        fingerprint = self._swimlane_fingerprint(
            view=view,
            viewer=viewer,
            page_limit=page_limit,
            group_keys=group_keys,
            lane_keys=lane_keys,
            rows=rows,
        )

        start = 0
        if cursor is not None:
            cursor_fingerprint, factors = decode_signed_cursor(self._cursor_secret, cursor)
            if cursor_fingerprint != fingerprint or len(factors) != 5:
                raise ConflictError("cursor no longer matches this view", code="cursor_invalidated")
            expires_at, cursor_lane, cursor_group, cursor_position, cursor_issue_id = factors
            if type(expires_at) is not int or int(_now(self._clock).timestamp()) > expires_at:
                raise ConflictError("cursor no longer matches this view", code="cursor_invalidated")
            for index, (issue, lane_key, group_key, position) in enumerate(rows):
                if [lane_key, group_key, position, str(issue.id)] == [
                    cursor_lane,
                    cursor_group,
                    cursor_position,
                    cursor_issue_id,
                ]:
                    start = index + 1
                    break
            else:
                raise ConflictError("cursor no longer matches this view", code="cursor_invalidated")

        page_rows = rows[start : start + page_limit + 1]
        has_more = len(page_rows) > page_limit
        page_rows = page_rows[:page_limit]
        next_cursor = None
        if has_more and page_rows:
            last_issue, last_lane, last_group, last_position = page_rows[-1]
            next_cursor = encode_signed_cursor(
                self._cursor_secret,
                fp=fingerprint,
                factors=[
                    int((_now(self._clock) + SWIMLANE_CURSOR_TTL).timestamp()),
                    last_lane,
                    last_group,
                    last_position,
                    str(last_issue.id),
                ],
            )

        page_membership: dict[tuple[str, str], list[dict]] = {}
        for issue, lane_key, group_key, _position in page_rows:
            page_membership.setdefault((lane_key, group_key), []).append(
                await self._issues.render_issue(session, issue)
            )

        group_labels = await self._group_labels(
            session,
            group_by=group_by,
            workspace_id=workspace_id,
            keys=group_keys,
        )
        lane_labels = await self._group_labels(
            session,
            group_by=sub_group_by,
            workspace_id=workspace_id,
            keys=lane_keys,
        )
        wip_map = (view.board_settings or {}).get("wip", {})
        columns = [
            {
                "key": key,
                "label": group_labels.get(key, key),
                "count": group_counts.get(key, 0),
                "wip": wip_map.get(key),
            }
            for key in group_keys
        ]
        lanes = [
            {
                "key": lane_key,
                "label": lane_labels.get(lane_key, lane_key),
                "count": lane_counts.get(lane_key, 0),
                "groups": [
                    {
                        "key": group_key,
                        "count": cell_counts.get((lane_key, group_key), 0),
                        "data": page_membership.get((lane_key, group_key), []),
                    }
                    for group_key in group_keys
                ],
            }
            for lane_key in lane_keys
        ]
        result: dict[str, Any] = {
            "layout": view.layout,
            "group_by": group_by,
            "sub_group_by": sub_group_by,
            "columns": columns,
            "lanes": lanes,
            "next_cursor": next_cursor,
        }
        # A flat target-status map is safe only when every cell shares a fixed
        # target project and the swimlane is not another status/project axis.
        if (
            group_by == "state_category"
            and view.project_id is not None
            and sub_group_by not in {"status", "project"}
        ):
            result["column_target_status"] = await self._column_target_status(
                session,
                group_by=group_by,
                workspace_id=workspace_id,
                view=view,
                keys=group_keys,
            )
        return result

    async def _ordered_group_keys(
        self,
        session: AsyncSession,
        *,
        group_by: str,
        workspace_id: uuid.UUID,
        counts: dict[str, int],
        membership: dict[str, list[dict]],
        configured_order: list[str] | None = None,
    ) -> list[str]:
        present = set(counts) | set(membership)
        if group_by == "state_category":
            ordered = list(STATE_CATEGORY_KEYS)
        elif group_by == "priority":
            ordered = list(PRIORITY_KEYS)
        elif group_by == "status":
            status_ids = [uuid.UUID(key) for key in present]
            status_rows = (
                await session.execute(
                    select(
                        IssueStatus.id,
                        IssueStatus.category,
                        IssueStatus.position,
                    ).where(
                        IssueStatus.workspace_id == workspace_id,
                        IssueStatus.id.in_(status_ids) if status_ids else IssueStatus.id.is_(None),
                    )
                )
            ).all()
            status_order = {
                str(status_id): (
                    STATE_CATEGORY_KEYS.index(category),
                    float(position),
                    str(status_id),
                )
                for status_id, category, position in status_rows
            }
            ordered = sorted(
                present,
                key=lambda key: status_order.get(key, (len(STATE_CATEGORY_KEYS), float("inf"), key)),
            )
        else:
            # Dynamic groups (status/assignee/project): deterministic label,
            # then key ordering. ``casefold`` approximates lower(... COLLATE C)
            # without relying on the database's locale.
            labels = await self._group_labels(
                session, group_by=group_by, workspace_id=workspace_id, keys=sorted(present)
            )
            ordered = sorted(
                present,
                key=lambda key: (
                    key == _EMPTY_MEMBER_KEY,
                    labels.get(key, key).casefold(),
                    key,
                ),
            )
        if not configured_order:
            return ordered
        configured = list(dict.fromkeys(key for key in configured_order if key in ordered))
        return configured + [key for key in ordered if key not in configured]

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
                select(Project).where(Project.id == uuid.UUID(key), Project.workspace_id == workspace_id)
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

        This helper is called only for ``state_category`` in a project-scoped
        view, where the whole response has one unambiguous target status
        domain (kanban §2.4).
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
        return {}


__all__ = [
    "NotImplementedLayout",
    "PROJECTION_FIELD_PENDING",
    "ProjectionService",
    "compile_view_filters",
    "group_key_for",
]
