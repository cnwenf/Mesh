"""Global search service (search-command-palette.md §3).

Three query paths routed by input shape (§2.2):

- **canonical identifier** (normalized ``^[a-z0-9]+-\\d+$``): uppercase
  equality fast path on ``UNIQUE(workspace_id, identifier)`` — hit pinned
  top (score bucket 9), regular path fills the rest;
- **1–2 chars**: normalized prefix match (``*_prefix`` pattern indexes);
- **≥3 chars**: trigram similarity (``*_trgm`` GIN expression indexes).

Visibility is pushed INTO every query (§3.3): private projects / private
agents / others' chat sessions never enter results, counts or defaults.
Ordering is the §4.6 total order — every factor DB-computable — mirrored
factor-for-factor by the signed cursor tuple (§3.2 / R2-H4).

Structure: async methods are thin SQL glue; all row construction, capacity
merging and pagination logic lives in pure synchronous builders (directly
unit-tested without async plumbing).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.member import Member
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ValidationError
from mesh.search.cursor import (
    SearchCursor,
    binding_fingerprint,
    decode_search_cursor,
    encode_search_cursor,
)
from mesh.search.scoring import normalize_search_text
from mesh.search.shapes import render_result

SEARCH_TYPES: tuple[str, ...] = (
    "issue",
    "member",
    "agent",
    "project",
    "view",
    "chat_session",
)
# member rows split into member/agent; the SQL query type for both:
_MEMBER_QUERY_TYPES = frozenset({"member", "agent"})

MAX_QUERY_LENGTH = 120
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
PREFIX_TYPE_CAP = 5  # §2.2 candidate caps
FUZZY_TYPE_CAP = 20
STATEMENT_TIMEOUT_MS = 3000

IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+-\d+$")

# §4.6 quantized ladder — single source of truth is the DB function
# ``public.mesh_search_text_score`` (migration 0029): exact 8 > prefix 7 >
# token-prefix 6 (every query token prefixes some title token; separators
# - _ / . normalized to spaces) > acronym 5 (query chars = initials of
# successive title tokens) > substring 3 > fuzzy 1. ``scoring.py`` mirrors
# the identical algorithm for unit tests — no SQL/Python divergence (M6).
_SQL_SCORE = "public.mesh_search_text_score({norm}, :nq)"

# Issue score: the title ladder, lifted to ≥6 when the identifier itself
# prefix-matches (M1 — identifier retrieval on the 1–2 char path).
_ISSUE_SCORE = (
    "GREATEST(public.mesh_search_text_score({norm}, :nq), "
    "CASE WHEN public.mesh_search_norm(i.identifier) LIKE :prefix_pat ESCAPE '\\' "
    "THEN 6 ELSE 0 END)"
)


@dataclass(frozen=True)
class SearchParams:
    """Validated request parameters."""

    q: str
    normalized: str
    types: frozenset[str]
    limit: int
    cursor: SearchCursor | None


@dataclass(frozen=True)
class Row:
    """One candidate in the merged ordering."""

    sort_key: tuple
    type: str
    id: uuid.UUID
    title: str
    score_bucket: int
    title_len: int
    title_lex: str
    payload: dict


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def validate_search_params(
    *,
    q: str | None,
    types: str | None,
    limit: int | None,
    cursor_raw: str | None,
    workspace_id: uuid.UUID,
    secret: str,
) -> SearchParams | None:
    """Validate + decode; returns None when q is empty (§3.2 empty rule)."""
    query = (q or "").strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise ValidationError(
            "q exceeds maximum length",
            details={"max_length": MAX_QUERY_LENGTH},
        )

    if types is None or types.strip() == "":
        selected = frozenset(SEARCH_TYPES)
    else:
        parts = [t.strip() for t in types.split(",") if t.strip()]
        invalid = [t for t in parts if t not in SEARCH_TYPES]
        if invalid or not parts:
            raise ValidationError(
                "invalid types filter",
                details={"invalid": invalid, "allowed": list(SEARCH_TYPES)},
            )
        selected = frozenset(parts)

    page_limit = DEFAULT_LIMIT if limit is None else limit
    if not 1 <= page_limit <= MAX_LIMIT:
        raise ValidationError(
            "invalid limit",
            details={"min": 1, "max": MAX_LIMIT},
        )

    if not query:
        # Empty q → empty object results; the palette empty state is
        # assembled client-side (favorites endpoint + local recents, §4.2.1).
        return None

    fingerprint = binding_fingerprint(query, selected, workspace_id)
    cursor = None
    if cursor_raw is not None:
        cursor = decode_search_cursor(
            cursor_raw, expected_fingerprint=fingerprint, secret=secret
        )

    return SearchParams(
        q=query,
        normalized=normalize_search_text(query),
        types=selected,
        limit=page_limit,
        cursor=cursor,
    )


# ---------------------------------------------------------------------------
# SQL fragment builders (pure — unit tested)
# ---------------------------------------------------------------------------


def is_admin_role(viewer: Member) -> bool:
    return viewer.role in ("admin", "owner")


def visible_projects_subquery(viewer: Member, member_id_param: str = ":mid") -> str:
    """`SELECT id FROM projects ...` fragment — project visibility (§3.3)."""
    if is_admin_role(viewer):
        return "SELECT id FROM projects WHERE workspace_id = :ws AND deleted_at IS NULL"
    if viewer.role == "guest":
        return (
            "SELECT project_id FROM member_project_access "
            f"WHERE workspace_id = :ws AND member_id = {member_id_param}"
        )
    return (
        "SELECT id FROM projects WHERE workspace_id = :ws AND deleted_at IS NULL "
        "AND (visibility = 'public' "
        "OR id IN (SELECT project_id FROM project_members "
        f"WHERE workspace_id = :ws AND member_id = {member_id_param}) "
        f"OR lead_member_id = {member_id_param})"
    )


def issue_visibility_clause(viewer: Member) -> str:
    if is_admin_role(viewer):
        return "TRUE"
    if viewer.role == "guest":
        return (
            "(i.project_id IN (SELECT project_id FROM member_project_access "
            "WHERE workspace_id = :ws AND member_id = :mid) "
            "OR i.assignee_id = :mid OR i.reporter_id = :mid)"
        )
    return (
        "(i.project_id IS NULL OR i.project_id IN "
        f"({visible_projects_subquery(viewer)}))"
    )


def keyset_clause(entity_type: str, cursor: SearchCursor | None) -> str:
    """Keyset filter for one entity list, given the merged-order cursor.

    ``title_lex`` comparisons are forced to ``COLLATE "C"`` (code-point
    order) so the DB ordering is identical to the Python merge/cursor
    boundary comparison regardless of the database's default collation
    (H2 — compose's default ``en_US.utf8`` would otherwise diverge from
    Python's code-point comparison on CJK ties, dropping or duplicating
    rows across page boundaries; zh-CN is a first-release language).
    """
    if cursor is None:
        return "TRUE"
    # CAST(:k_tlx AS TEXT) — the ``:param::type`` shorthand is not parsed
    # after a bindparam in text(); explicit CAST compiles cleanly.
    base = (
        "(score_bucket < :k_sb OR (score_bucket = :k_sb AND title_len > :k_tl) "
        "OR (score_bucket = :k_sb AND title_len = :k_tl "
        "AND (title_lex COLLATE \"C\") > (CAST(:k_tlx AS TEXT) COLLATE \"C\"))"
    )
    if entity_type < cursor.result_type:
        return base + ")"
    if entity_type == cursor.result_type:
        return (
            base
            + " OR (score_bucket = :k_sb AND title_len = :k_tl "
            "AND (title_lex COLLATE \"C\") = (CAST(:k_tlx AS TEXT) COLLATE \"C\") "
            "AND id > :k_id))"
        )
    # entity_type > cursor.result_type: equal-prefix rows sort AFTER cursor.
    return (
        "(score_bucket < :k_sb OR (score_bucket = :k_sb AND title_len > :k_tl) "
        "OR (score_bucket = :k_sb AND title_len = :k_tl "
        "AND (title_lex COLLATE \"C\") >= (CAST(:k_tlx AS TEXT) COLLATE \"C\")))"
    )


def match_clause(mode: str, norm_expr: str) -> str:
    if mode == "prefix":
        return f"{norm_expr} LIKE :prefix_pat ESCAPE '\\'"
    # ≥3 chars: trigram similarity (fuzzy) OR contiguous substring (§4.6
    # 「连续子串」tier — also the recall path for CJK titles, where raw
    # similarity of a short query inside a long title stays below the
    # threshold). Both forms use the same GIN trigram index.
    return (
        f"({norm_expr} LIKE :substr_pat ESCAPE '\\' "
        f"OR {norm_expr} % public.mesh_search_norm(:nq))"
    )


def order_limit(fetch: int) -> str:
    # COLLATE "C" keeps the SQL order byte-identical to the Python code-point
    # merge (H2 — see keyset_clause).
    return (
        "ORDER BY score_bucket DESC, title_len ASC, "
        "(title_lex COLLATE \"C\") ASC, id ASC "
        f"LIMIT {int(fetch)}"
    )


# ---------------------------------------------------------------------------
# Row builders (pure — unit tested)
# ---------------------------------------------------------------------------


def _sort_key(score_bucket: int, title_len: int, title_lex: str, rtype: str, row_id) -> tuple:
    return (-score_bucket, title_len, title_lex, rtype, str(row_id))


def issue_payload(row: Mapping) -> dict[str, Any]:
    return {
        "identifier": row["identifier"],
        "status_id": row["status_id"],
        "status_name": row["status_name"],
        "state_category": row["state_category"],
        "project_id": row["project_id"],
        "project_name": row["project_name"],
    }


def build_issue_rows(rows: Sequence[Mapping]) -> list[Row]:
    return [
        Row(
            sort_key=_sort_key(
                r["score_bucket"], r["title_len"], r["title_lex"], "issue", r["id"]
            ),
            type="issue",
            id=r["id"],
            title=r["title"],
            score_bucket=r["score_bucket"],
            title_len=r["title_len"],
            title_lex=r["title_lex"],
            payload=issue_payload(r),
        )
        for r in rows
    ]


def build_pin_row(row: Mapping | None) -> Row | None:
    """Identifier exact hit → bucket 9 pin (top of the total order)."""
    if row is None:
        return None
    title = row["title"]
    normalized = normalize_search_text(title)
    return Row(
        sort_key=(-9, len(title), normalized, "issue", str(row["id"])),
        type="issue",
        id=row["id"],
        title=title,
        score_bucket=9,
        title_len=len(title),
        title_lex=normalized,
        payload=issue_payload(row),
    )


def build_member_rows(rows: Sequence[Mapping], selected_types: frozenset[str]) -> list[Row]:
    out: list[Row] = []
    for r in rows:
        rtype = "agent" if r["member_type"] == "agent" else "member"
        if rtype not in selected_types:
            continue
        out.append(
            Row(
                sort_key=_sort_key(
                    r["score_bucket"], r["title_len"], r["title_lex"], rtype, r["id"]
                ),
                type=rtype,
                id=r["id"],
                title=r["title"],
                score_bucket=r["score_bucket"],
                title_len=r["title_len"],
                title_lex=r["title_lex"],
                payload={
                    "row_id": r["id"],
                    "role": r["role"],
                    "lifecycle_status": r["lifecycle_status"],
                    "agent_id": r["id"] if rtype == "agent" else None,
                },
            )
        )
    return out


def build_project_rows(rows: Sequence[Mapping]) -> list[Row]:
    return [
        Row(
            sort_key=_sort_key(
                r["score_bucket"], r["title_len"], r["title_lex"], "project", r["id"]
            ),
            type="project",
            id=r["id"],
            title=r["title"],
            score_bucket=r["score_bucket"],
            title_len=r["title_len"],
            title_lex=r["title_lex"],
            payload={
                "row_id": r["id"],
                "visibility": r["visibility"],
                "key": r["key"],
            },
        )
        for r in rows
    ]


def build_view_rows(rows: Sequence[Mapping]) -> list[Row]:
    return [
        Row(
            sort_key=_sort_key(
                r["score_bucket"], r["title_len"], r["title_lex"], "view", r["id"]
            ),
            type="view",
            id=r["id"],
            title=r["title"],
            score_bucket=r["score_bucket"],
            title_len=r["title_len"],
            title_lex=r["title_lex"],
            payload={
                "row_id": r["id"],
                "visibility": r["visibility"],
                "project_id": r["project_id"],
                "project_name": r["project_name"],
            },
        )
        for r in rows
    ]


def build_chat_rows(rows: Sequence[Mapping]) -> list[Row]:
    return [
        Row(
            sort_key=_sort_key(
                r["score_bucket"], r["title_len"], r["title_lex"], "chat_session", r["id"]
            ),
            type="chat_session",
            id=r["id"],
            title=r["title"],
            score_bucket=r["score_bucket"],
            title_len=r["title_len"],
            title_lex=r["title_lex"],
            payload={
                "row_id": r["id"],
                "agent_id": r["agent_id"],
                "agent_name": r["agent_name"],
            },
        )
        for r in rows
    ]


def merge_capacities(
    member_agent_ids: Mapping[uuid.UUID, uuid.UUID | None],
    execution_counts: Sequence[Mapping],
    approval_counts: Sequence[Mapping],
) -> dict[uuid.UUID, dict[str, int]]:
    """member_id → capacity snapshot (§6.12 running N / queued M / awaiting K)."""
    running_queued: dict[uuid.UUID, tuple[int, int]] = {
        row["agent_id"]: (row["running"], row["queued"]) for row in execution_counts
    }
    awaiting: dict[uuid.UUID, int] = {
        row["agent_id"]: row["pending"] for row in approval_counts
    }
    capacities: dict[uuid.UUID, dict[str, int]] = {}
    for member_id, agent_id in member_agent_ids.items():
        stats = running_queued.get(agent_id) if agent_id is not None else None
        capacities[member_id] = {
            "running": stats[0] if stats else 0,
            "queued": stats[1] if stats else 0,
            "awaiting_approval": awaiting.get(agent_id, 0) if agent_id else 0,
        }
    return capacities


def apply_capacities(agent_rows: Sequence[Row], capacities: Mapping) -> None:
    for row in agent_rows:
        row.payload["capacity"] = capacities.get(
            row.id, {"running": 0, "queued": 0, "awaiting_approval": 0}
        )


def paginate_rows(
    rows: list[Row], limit: int, fingerprint: str, secret: str
) -> tuple[list[Row], str | None]:
    """Global-order slice + next cursor (tuple mirrors §4.6 factor-for-factor)."""
    ordered = sorted(rows, key=lambda r: r.sort_key)
    if len(ordered) > limit:
        page = ordered[:limit]
        last = page[-1]
        cursor = encode_search_cursor(
            score_bucket=last.score_bucket,
            title_len=last.title_len,
            title_lex=last.title_lex,
            result_type=last.type,
            row_id=last.id,
            fingerprint=fingerprint,
            secret=secret,
        )
        return page, cursor
    return ordered, None


def row_to_dict(r: Row) -> dict[str, Any]:
    return {"type": r.type, "id": r.id, "title": r.title, "payload": r.payload}


# ---------------------------------------------------------------------------
# Service (thin async SQL glue over the pure builders above)
# ---------------------------------------------------------------------------


class SearchService:
    """Stateless search orchestrator; one transaction per request."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, secret: str):
        self._sf = session_factory
        self._secret = secret

    async def search(
        self,
        *,
        viewer: Member,
        workspace: Workspace,
        q: str | None,
        types: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        params = validate_search_params(
            q=q,
            types=types,
            limit=limit,
            cursor_raw=cursor,
            workspace_id=workspace.id,
            secret=self._secret,
        )
        if params is None:
            return {"data": [], "next_cursor": None}

        fingerprint = binding_fingerprint(params.q, params.types, workspace.id)
        try:
            async with self._sf() as session, session.begin():
                await set_tenant_context(session, workspace.id)
                await session.execute(
                    text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                )
                rows = await self._collect(session, viewer=viewer, workspace=workspace, p=params)
        except (DBAPIError, asyncpg.exceptions.QueryCanceledError) as exc:
            # statement_timeout backstop (§2.2 / §3.5 query_cost_exceeded).
            # asyncpg's QueryCanceledError can surface raw or DBAPI-wrapped.
            orig = getattr(exc, "orig", exc)
            if isinstance(orig, asyncpg.exceptions.QueryCanceledError):
                raise BusinessRuleError(
                    "search query cost exceeded; narrow types or lengthen q",
                    code="query_cost_exceeded",
                ) from exc
            raise

        page, next_cursor = paginate_rows(rows, params.limit, fingerprint, self._secret)
        rendered = [
            render_result(row_to_dict(r), workspace_slug=workspace.slug, query=params.q)
            for r in page
        ]
        return {"data": rendered, "next_cursor": next_cursor}

    async def _collect(
        self,
        session: AsyncSession,
        *,
        viewer: Member,
        workspace: Workspace,
        p: SearchParams,
    ) -> list[Row]:
        nq = p.normalized
        mode = "prefix" if len(nq) <= 2 else "trigram"
        cap = PREFIX_TYPE_CAP if mode == "prefix" else FUZZY_TYPE_CAP
        # Per-type fetch size must track the page size (H1): with a fixed cap
        # a page of `limit` rows could consume the whole per-type budget and
        # emit next_cursor=null while rows remain — silent loss. Fetch
        # max(cap, limit+1) per type so any full page always has one row in
        # hand to prove there is more (and the cursor boundary is exact).
        fetch = max(cap, p.limit + 1)
        base_params: dict[str, Any] = {
            "ws": workspace.id,
            "mid": viewer.id,
            "viewer_user_id": viewer.user_id,
            "viewer_is_admin": is_admin_role(viewer),
            "nq": nq,
            "prefix_pat": f"{_like_escape(nq)}%",
            "token_pat": f"% {_like_escape(nq)}%",
            "substr_pat": f"%{_like_escape(nq)}%",
            # Not an SQL bind — the per-entity queries read this to build
            # their keyset clause (SQLAlchemy ignores unbound dict keys).
            "_cursor": p.cursor,
        }
        if p.cursor is not None:
            base_params.update(
                {
                    "k_sb": p.cursor.score_bucket,
                    "k_tl": p.cursor.title_len,
                    "k_tlx": p.cursor.title_lex,
                    "k_id": p.cursor.row_id,
                }
            )

        rows: list[Row] = []
        pinned_id: uuid.UUID | None = None

        # Identifier canonical fast path — page one only (pin is bucket 9).
        if p.cursor is None and IDENTIFIER_PATTERN.fullmatch(nq) and "issue" in p.types:
            pinned = build_pin_row(await self._fetch_identifier_pin(session, base_params, viewer, p.q))
            if pinned is not None:
                pinned_id = pinned.id
                rows.append(pinned)

        if "issue" in p.types:
            sql_rows = await self._fetch_issues(session, base_params, viewer, mode, fetch, pinned_id)
            rows.extend(build_issue_rows(sql_rows))
        if p.types & _MEMBER_QUERY_TYPES:
            sql_rows = await self._fetch_members(session, base_params, viewer, mode, fetch)
            rows.extend(build_member_rows(sql_rows, p.types))
        if "project" in p.types:
            sql_rows = await self._fetch_projects(session, base_params, viewer, mode, fetch)
            rows.extend(build_project_rows(sql_rows))
        if "view" in p.types:
            sql_rows = await self._fetch_views(session, base_params, viewer, mode, fetch)
            rows.extend(build_view_rows(sql_rows))
        if "chat_session" in p.types:
            sql_rows = await self._fetch_chat_sessions(session, base_params, mode, fetch)
            rows.extend(build_chat_rows(sql_rows))

        await self._enrich_agent_capacity(session, base_params, rows)
        return rows

    async def _fetch_identifier_pin(
        self, session: AsyncSession, params: dict[str, Any], viewer: Member, raw_q: str
    ) -> Mapping | None:
        sql = text(
            """
            SELECT i.id, i.identifier, i.title, i.state_category,
                   i.status_id, s.name AS status_name,
                   p.id AS project_id, p.name AS project_name
            FROM issues i
            LEFT JOIN issue_statuses s
              ON s.workspace_id = i.workspace_id AND s.id = i.status_id
            LEFT JOIN projects p
              ON p.workspace_id = i.workspace_id AND p.id = i.project_id
            WHERE i.workspace_id = :ws AND i.deleted_at IS NULL
              AND i.identifier = upper(trim(:raw_q))
              AND {vis}
            LIMIT 1
            """.replace(
                "{vis}", issue_visibility_clause(viewer)
            )
        )
        result = (await session.execute(sql, {**params, "raw_q": raw_q})).mappings().first()
        return result

    async def _fetch_issues(
        self,
        session: AsyncSession,
        params: dict[str, Any],
        viewer: Member,
        mode: str,
        cap: int,
        pinned_id: uuid.UUID | None,
    ) -> Sequence[Mapping]:
        norm = "public.mesh_search_norm(i.title)"
        score_case = _ISSUE_SCORE.format(norm=norm)
        pin_filter = "AND i.id <> :pinned_id" if pinned_id is not None else ""
        if mode == "prefix":
            # 1–2 char path matches title OR identifier prefix (M1 — §1.2 S2
            # promises identifier retrieval; idx_issues_identifier_prefix is
            # built exactly for this, §2.2 DDL 2c).
            issue_match = (
                f"({norm} LIKE :prefix_pat ESCAPE '\\' "
                "OR public.mesh_search_norm(i.identifier) LIKE :prefix_pat ESCAPE '\\')"
            )
        else:
            issue_match = match_clause(mode, norm)
        sql = text(
            f"""
            SELECT * FROM (
              SELECT i.id AS id, i.identifier AS identifier, i.title AS title,
                     i.state_category AS state_category, i.status_id AS status_id,
                     s.name AS status_name,
                     p.id AS project_id, p.name AS project_name,
                     {score_case} AS score_bucket,
                     char_length(i.title) AS title_len,
                     {norm} AS title_lex
              FROM issues i
              LEFT JOIN issue_statuses s
                ON s.workspace_id = i.workspace_id AND s.id = i.status_id
              LEFT JOIN projects p
                ON p.workspace_id = i.workspace_id AND p.id = i.project_id
              WHERE i.workspace_id = :ws AND i.deleted_at IS NULL
                AND {issue_visibility_clause(viewer)}
                AND {issue_match}
                {pin_filter}
            ) t
            WHERE {keyset_clause("issue", params.get("_cursor"))}
            {order_limit(cap)}
            """
        )
        bound = dict(params)
        if pinned_id is not None:
            bound["pinned_id"] = pinned_id
        return (await session.execute(sql, bound)).mappings().all()

    async def _fetch_members(
        self,
        session: AsyncSession,
        params: dict[str, Any],
        viewer: Member,
        mode: str,
        cap: int,
    ) -> Sequence[Mapping]:
        norm = "m.search_name"
        score_case = _SQL_SCORE.format(norm=norm)
        # The title expression MUST mirror the projection's display chain
        # exactly (M4 — §2.2 「杜绝漂移」): NULLIF(display_name,'') so an
        # empty display_name falls through to email exactly as search_name
        # was computed. title_lex IS the projection column itself, so the
        # ordering key cannot diverge from the rendered title by construction.
        title_expr = (
            "COALESCE(NULLIF(m.display_override, ''), "
            "CASE m.member_type WHEN 'human' THEN COALESCE(NULLIF(u.display_name, ''), u.email) "
            "WHEN 'agent' THEN a.name END, '')"
        )
        # Private agents are visible to their owner and admins ONLY (§3.3):
        # non-privileged viewers never see them — not in results, not in
        # counts, not in defaults (existence is never exposed).
        sql = text(
            f"""
            SELECT * FROM (
              SELECT m.id AS id, m.member_type AS member_type, m.role AS role,
                     {title_expr} AS title,
                     a.visibility AS agent_visibility,
                     a.lifecycle_status AS lifecycle_status,
                     {score_case} AS score_bucket,
                     char_length({title_expr}) AS title_len,
                     m.search_name AS title_lex
              FROM members m
              LEFT JOIN users u ON u.id = m.user_id
              LEFT JOIN agents a ON a.workspace_id = m.workspace_id AND a.id = m.agent_id
              WHERE m.workspace_id = :ws AND m.status <> 'removed'
                AND (m.member_type = 'human'
                     OR (a.deleted_at IS NULL
                         AND (a.visibility = 'workspace'
                              OR :viewer_is_admin = TRUE
                              OR a.owner_user_id = :viewer_user_id)))
                AND {match_clause(mode, norm)}
            ) t
            WHERE (member_type = 'human' AND {keyset_clause("member", params.get("_cursor"))})
               OR (member_type = 'agent' AND {keyset_clause("agent", params.get("_cursor"))})
            {order_limit(cap * 2)}
            """
        )
        return (await session.execute(sql, params)).mappings().all()

    async def _fetch_projects(
        self,
        session: AsyncSession,
        params: dict[str, Any],
        viewer: Member,
        mode: str,
        cap: int,
    ) -> Sequence[Mapping]:
        norm = "public.mesh_search_norm(p.name)"
        score_case = _SQL_SCORE.format(norm=norm)
        if is_admin_role(viewer):
            vis = "TRUE"
        else:
            vis = f"p.id IN ({visible_projects_subquery(viewer)})"
        sql = text(
            f"""
            SELECT * FROM (
              SELECT p.id AS id, p.name AS title, p.key AS key,
                     p.visibility AS visibility,
                     {score_case} AS score_bucket,
                     char_length(p.name) AS title_len,
                     {norm} AS title_lex
              FROM projects p
              WHERE p.workspace_id = :ws AND p.deleted_at IS NULL
                AND {vis}
                AND {match_clause(mode, norm)}
            ) t
            WHERE {keyset_clause("project", params.get("_cursor"))}
            {order_limit(cap)}
            """
        )
        return (await session.execute(sql, params)).mappings().all()

    async def _fetch_views(
        self,
        session: AsyncSession,
        params: dict[str, Any],
        viewer: Member,
        mode: str,
        cap: int,
    ) -> Sequence[Mapping]:
        norm = "public.mesh_search_norm(v.name)"
        score_case = _SQL_SCORE.format(norm=norm)
        # Private views: OWNER ONLY — §3.3 is explicit (「私有视图仅 owner」),
        # so even admins do not see other members' private views here (no
        # admin bypass). Project-owned views AND with project visibility
        # (§3.3 — invisible project ⇒ its views invisible even when shared).
        own_clause = "(v.visibility = 'shared' OR v.owner_member_id = :mid)"
        project_clause = (
            f"(v.project_id IS NULL OR v.project_id IN ({visible_projects_subquery(viewer)}))"
        )
        sql = text(
            f"""
            SELECT * FROM (
              SELECT v.id AS id, v.name AS title, v.visibility AS visibility,
                     v.project_id AS project_id, p.name AS project_name,
                     {score_case} AS score_bucket,
                     char_length(v.name) AS title_len,
                     {norm} AS title_lex
              FROM views v
              LEFT JOIN projects p ON p.workspace_id = v.workspace_id AND p.id = v.project_id
              WHERE v.workspace_id = :ws
                AND {own_clause}
                AND {project_clause}
                AND {match_clause(mode, norm)}
            ) t
            WHERE {keyset_clause("view", params.get("_cursor"))}
            {order_limit(cap)}
            """
        )
        return (await session.execute(sql, params)).mappings().all()

    async def _fetch_chat_sessions(
        self,
        session: AsyncSession,
        params: dict[str, Any],
        mode: str,
        cap: int,
    ) -> Sequence[Mapping]:
        norm = "public.mesh_search_norm(c.title)"
        score_case = _SQL_SCORE.format(norm=norm)
        # Participant model: sessions are 1:1 owner + agent — only the owner
        # member row sees the session (§3.3).
        sql = text(
            f"""
            SELECT * FROM (
              SELECT c.id AS id, c.title AS title,
                     c.agent_id AS agent_id, a.name AS agent_name,
                     {score_case} AS score_bucket,
                     char_length(c.title) AS title_len,
                     {norm} AS title_lex
              FROM chat_sessions c
              LEFT JOIN agents a ON a.workspace_id = c.workspace_id AND a.id = c.agent_id
              WHERE c.workspace_id = :ws
                AND c.status <> 'deleted' AND c.deleted_at IS NULL
                AND c.owner_id = :mid
                AND {match_clause(mode, norm)}
            ) t
            WHERE {keyset_clause("chat_session", params.get("_cursor"))}
            {order_limit(cap)}
            """
        )
        return (await session.execute(sql, params)).mappings().all()

    async def _enrich_agent_capacity(
        self, session: AsyncSession, params: dict[str, Any], rows: list[Row]
    ) -> None:
        agent_rows = [r for r in rows if r.type == "agent"]
        if not agent_rows:
            return
        member_ids = [r.id for r in agent_rows]
        member_agent_ids = {
            row["id"]: row["agent_id"]
            for row in (
                await session.execute(
                    text(
                        "SELECT id, agent_id FROM members "
                        "WHERE workspace_id = :ws AND id = ANY(:ids)"
                    ),
                    {**params, "ids": member_ids},
                )
            ).mappings()
        }
        real_agent_ids = [a for a in member_agent_ids.values() if a is not None]
        execution_counts: Sequence[Mapping] = []
        approval_counts: Sequence[Mapping] = []
        if real_agent_ids:
            execution_counts = (
                await session.execute(
                    text(
                        """
                        SELECT agent_id,
                               COUNT(*) FILTER (
                                 WHERE status IN ('claimed', 'running', 'cancelling')
                               ) AS running,
                               COUNT(*) FILTER (WHERE status = 'queued') AS queued
                        FROM task_executions
                        WHERE workspace_id = :ws AND agent_id = ANY(:agent_ids)
                        GROUP BY agent_id
                        """
                    ),
                    {**params, "agent_ids": real_agent_ids},
                )
            ).mappings().all()
            approval_counts = (
                await session.execute(
                    text(
                        """
                        SELECT te.agent_id AS agent_id, COUNT(*) AS pending
                        FROM approvals ap
                        JOIN task_executions te
                          ON te.workspace_id = ap.workspace_id
                         AND te.id = ap.subject_execution_id
                        WHERE ap.workspace_id = :ws AND ap.status = 'pending'
                          AND te.agent_id = ANY(:agent_ids)
                        GROUP BY te.agent_id
                        """
                    ),
                    {**params, "agent_ids": real_agent_ids},
                )
            ).mappings().all()
        apply_capacities(
            agent_rows, merge_capacities(member_agent_ids, execution_counts, approval_counts)
        )
