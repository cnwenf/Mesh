"""SearchService — six-type object search with in-query visibility.

Query routing (spec §2.2, 三条查询路径写死):

* normalized ``q`` shaped ``^[a-z0-9]+-\\d+$`` → identifier exact fast path
  (``identifier = upper(trim(q))``, canonical uppercase equality) pinned
  FIRST, plus the regular path filling the rest;
* 1–2 normalized chars → normalized prefix LIKE (``*_prefix`` pattern
  indexes, ≤5 candidates per type);
* ≥3 normalized chars → trigram similarity (``*_trgm`` GIN indexes, ≤20
  candidates per type).

Query expressions match the index expressions VERBATIM through
``public.mesh_search_norm``; visibility is pushed into every query
(§3.3 — never fetch-then-filter, which would corrupt paging and leak
restricted-resource existence through counts). Ranking is the §4.6
ladder quantized to integer buckets; paging is keyset over the full
``(score_bucket, title_len, title_lex, type, id)`` total order through the
signed cursor (:mod:`mesh.search.cursor`).

PRIVACY (§5.3): the raw query never leaves the request processor — no
logging, no metrics label, no error detail carries it.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from sqlalchemy import text

from mesh.auth.rbac import role_satisfies
from mesh.db.tenant import set_tenant_context
from mesh.errors import ValidationError
from mesh.search import schemas
from mesh.search.cursor import (
    binding_fingerprint,
    canonical_sort_factors,
    encode_cursor,
    factors_as_sort_key,
)
from mesh.search.norm import is_identifier_shape, search_norm
from mesh.search.scoring import BUCKET_IDENTIFIER_EXACT, highlight_ranges, score_candidate

# Candidate caps per type per path (spec §2.2 table).
PREFIX_CAP = 5
TRIGRAM_CAP = 20

# Display-name resolution chain (README §6.1 / member.md §2.4) computed in
# SQL so the projected title and members.search_name never drift.
_MEMBER_TITLE_SQL = """COALESCE(
  NULLIF(BTRIM(m.display_override), ''),
  CASE m.member_type
    WHEN 'human' THEN COALESCE(NULLIF(BTRIM(u.display_name), ''),
                               NULLIF(split_part(u.email, '@', 1), ''))
    WHEN 'agent' THEN NULLIF(BTRIM(a.name), '')
  END,
  CASE WHEN m.member_type = 'human' THEN 'member-' || left(m.id::text, 8)
       ELSE 'agent-' || left(m.agent_id::text, 8) END
)"""

_TYPE_TO_MEMBER_TYPE = {"member": "human", "agent": "agent"}


@dataclass(frozen=True)
class Candidate:
    """A recalled row pending ranking: the rendered shape minus scoring."""

    result_type: str
    result_id: str
    title: str
    context: dict
    icon: str
    url: str
    badge: dict | None
    trigram_recalled: bool = False
    identifier_scored: str | None = None  # issue identifier, scored alongside
    pinned: bool = False  # identifier fast-path hit


def _project_visible_sql(*, role: str, alias: str = "p") -> str:
    """Project-visibility predicate text (same caliber as project service).

    owner/admin → unconditionally visible; guest → explicit
    ``member_project_access`` grant only (M12); regular roles → public ∪
    project membership ∪ access grant. Caller guards NULL project scopes.
    """
    if role_satisfies(role, "project:manage"):
        return "TRUE"
    grants = (
        f"EXISTS (SELECT 1 FROM project_members pm"
        f" WHERE pm.workspace_id = :ws AND pm.project_id = {alias}.id"
        f" AND pm.member_id = :member_id)",
        f"EXISTS (SELECT 1 FROM member_project_access mx"
        f" WHERE mx.workspace_id = :ws AND mx.project_id = {alias}.id"
        f" AND mx.member_id = :member_id)",
    )
    if role == "guest":
        return grants[1]
    return f"({alias}.visibility = 'public' OR {grants[0]} OR {grants[1]})"


def _escaped_norm_q() -> str:
    """``mesh_search_norm(:q)`` with LIKE wildcards escaped (``ESCAPE '\\'``).

    The normalizer folds case/accents but keeps ``%``/``_``, which would
    otherwise act as LIKE wildcards and over-match (q parameterized —
    binding-safe regardless; this is a precision guard, spec §5.3).
    """
    return (
        "replace(replace(replace(public.mesh_search_norm(:q), "
        "E'\\\\', E'\\\\\\\\'), '%', E'\\\\%'), '_', E'\\\\_')"
    )


_LIKE_ESCAPE = "ESCAPE E'\\\\'"


def _match_clause(norm_expr: str, *, path: str) -> str:
    """Index-verbatim match clause for the two fuzzy paths (spec §2.2).

    The trigram path ORs a normalized-substring containment arm: similarity
    threshold recall (``%``) misses exact substrings of long / mixed-script
    titles (CJK in particular — every code point yields trigrams, diluting
    the Jaccard ratio below the 0.3 default). The containment arm recalls
    them; the §4.6 ladder then classifies them as substring-or-better, so
    ranking/highlighting needs no change. The prefix path escapes LIKE
    wildcards for the same precision reason.
    """
    escaped = _escaped_norm_q()
    if path == "prefix":
        return f"{norm_expr} LIKE {escaped} || '%' {_LIKE_ESCAPE}"
    return (
        f"({norm_expr} % public.mesh_search_norm(:q) "
        f"OR {norm_expr} LIKE '%' || {escaped} || '%' {_LIKE_ESCAPE})"
    )


class SearchService:
    """Stateless orchestrator over the session factory (house pattern)."""

    def __init__(self, session_factory, settings, *, cursor_secret: bytes | None = None):
        from mesh.search.cursor import resolve_cursor_secret

        self._factory = session_factory
        self._secret = cursor_secret or resolve_cursor_secret(settings)

    @asynccontextmanager
    async def _session(self, workspace_id: uuid.UUID):
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            yield session

    # -- public API -----------------------------------------------------------
    async def search(
        self,
        *,
        actor,
        workspace,
        q: str,
        types: tuple[str, ...],
        limit: int,
        cursor: str | None,
    ) -> dict:
        """Run the search; returns ``{"data": [...], "next_cursor": str|None}``."""
        norm_q = search_norm(q)
        binding = binding_fingerprint(q, tuple(sorted(types)), workspace.id)
        after_key = self._decode_cursor(cursor, binding)
        if not norm_q.strip():
            return {"data": [], "next_cursor": None}

        path = "prefix" if len(norm_q) <= 2 else "trgm"
        cap = PREFIX_CAP if path == "prefix" else TRIGRAM_CAP
        async with self._session(workspace.id) as session:
            candidates = await self._recall(
                session,
                actor=actor,
                workspace_id=workspace.id,
                slug=workspace.slug,
                norm_q=norm_q,
                q=q,
                types=types,
                path=path,
                cap=cap,
            )
        entries = _rank(candidates, norm_q)
        return self._page(entries, q=q, types=types, binding=binding,
                          after_key=after_key, limit=limit)

    # -- cursor ---------------------------------------------------------------
    def _decode_cursor(self, cursor: str | None, binding: str) -> tuple | None:
        if cursor is None:
            return None
        from mesh.search.cursor import decode_cursor

        fp, factors = decode_cursor(self._secret, cursor)
        if fp != binding:
            # Cursor reused across a different q / types / workspace (§3.2).
            raise ValidationError("invalid cursor", code="validation_error")
        return factors_as_sort_key(factors)

    # -- recall ---------------------------------------------------------------
    async def _recall(
        self, session, *, actor, workspace_id, slug, norm_q, q, types, path, cap
    ) -> list[Candidate]:
        params = {
            "ws": workspace_id,
            "q": q,
            "member_id": actor.id,
            "user_id": actor.user_id,
            "is_manager": role_satisfies(actor.role, "project:manage"),
        }
        proj_pred = _project_visible_sql(role=actor.role)
        out: list[Candidate] = []
        if "issue" in types:
            out += await self._recall_issues(
                session, params=params, proj_pred=proj_pred, slug=slug,
                norm_q=norm_q, q=q, path=path, cap=cap,
            )
        for result_type in ("member", "agent"):
            if result_type in types:
                out += await self._recall_members(
                    session, params=params, slug=slug,
                    member_type=_TYPE_TO_MEMBER_TYPE[result_type],
                    result_type=result_type, path=path, cap=cap,
                )
        if "project" in types:
            out += await self._recall_projects(
                session, params=params, proj_pred=proj_pred, slug=slug,
                path=path, cap=cap,
            )
        if "view" in types:
            out += await self._recall_views(
                session, params=params, proj_pred=proj_pred, slug=slug,
                path=path, cap=cap,
            )
        if "chat_session" in types:
            out += await self._recall_chat_sessions(
                session, params=params, slug=slug, path=path, cap=cap,
            )
        if "agent" in types:
            out = await self._with_agent_capacity(session, params=params, candidates=out)
        return out

    async def _recall_issues(
        self, session, *, params, proj_pred, slug, norm_q, q, path, cap
    ) -> list[Candidate]:
        title_match = _match_clause("public.mesh_search_norm(i.title)", path=path)
        # Identifier recall stays prefix on BOTH paths — there is no trigram
        # index on identifier; the expression matches idx_issues_identifier_prefix.
        ident_match = _match_clause("public.mesh_search_norm(i.identifier)", path="prefix")
        pinned: list[Candidate] = []
        if is_identifier_shape(norm_q):
            pinned = await self._issue_rows(
                session,
                params=params,
                where="i.identifier = upper(BTRIM(:q))",
                proj_pred=proj_pred,
                slug=slug,
                limit=1,
                pinned=True,
            )
        regular = await self._issue_rows(
            session,
            params=params,
            where=f"({title_match} OR {ident_match})",
            proj_pred=proj_pred,
            slug=slug,
            limit=cap,
            pinned=False,
        )
        return pinned + regular

    async def _issue_rows(
        self, session, *, params, where, proj_pred, slug, limit, pinned
    ) -> list[Candidate]:
        sql = text(
            """
            SELECT i.id, i.identifier, i.title, i.project_id,
                   p.name AS project_name, s.id AS status_id,
                   s.name AS status_name, s.category AS status_category
            FROM issues i
            LEFT JOIN projects p
                   ON p.id = i.project_id AND p.workspace_id = i.workspace_id
            JOIN issue_statuses s
                   ON s.id = i.status_id AND s.workspace_id = i.workspace_id
            WHERE i.workspace_id = :ws
              AND i.deleted_at IS NULL
              AND (i.project_id IS NULL OR """ + proj_pred + """)
              AND """ + where + """
            ORDER BY i.id
            LIMIT """ + str(int(limit))
        )
        rows = (await session.execute(sql, params)).mappings().all()
        return [_issue_candidate(row, slug=slug, pinned=pinned) for row in rows]

    async def _recall_members(
        self, session, *, params, slug, member_type, result_type, path, cap
    ) -> list[Candidate]:
        sql = text(
            """
            SELECT m.id, m.member_type, m.role, """ + _MEMBER_TITLE_SQL + """ AS title,
                   a.id AS agent_id, a.name AS agent_name
            FROM members m
            LEFT JOIN users u ON u.id = m.user_id
            LEFT JOIN agents a ON a.id = m.agent_id AND a.workspace_id = m.workspace_id
            WHERE m.workspace_id = :ws
              AND m.status <> 'removed'
              AND m.member_type = :member_type
              AND (m.member_type = 'human' OR a.deleted_at IS NULL)
              AND (m.member_type = 'human'
                   OR a.visibility = 'workspace'
                   OR a.owner_user_id = :user_id
                   OR :is_manager)
              AND """ + _match_clause("m.search_name", path=path) + """
            ORDER BY m.id
            LIMIT """ + str(int(cap))
        )
        rows = (
            await session.execute(sql, params | {"member_type": member_type})
        ).mappings().all()
        return [
            _member_candidate(row, result_type=result_type, slug=slug,
                              trigram=path == "trgm")
            for row in rows
        ]

    async def _recall_projects(
        self, session, *, params, proj_pred, slug, path, cap
    ) -> list[Candidate]:
        sql = text(
            """
            SELECT p.id, p.name, p.key, p.visibility
            FROM projects p
            WHERE p.workspace_id = :ws
              AND p.deleted_at IS NULL
              AND """ + proj_pred + """
              AND """ + _match_clause("public.mesh_search_norm(p.name)", path=path) + """
            ORDER BY p.id
            LIMIT """ + str(int(cap))
        )
        rows = (await session.execute(sql, params)).mappings().all()
        return [_project_candidate(row, slug=slug, trigram=path == "trgm") for row in rows]

    async def _recall_views(
        self, session, *, params, proj_pred, slug, path, cap
    ) -> list[Candidate]:
        sql = text(
            """
            SELECT v.id, v.name, v.visibility, v.project_id, p.name AS project_name
            FROM views v
            LEFT JOIN projects p
                   ON p.id = v.project_id AND p.workspace_id = v.workspace_id
            WHERE v.workspace_id = :ws
              AND (v.visibility = 'shared' OR v.owner_member_id = :member_id)
              AND (v.project_id IS NULL OR """ + proj_pred + """)
              AND """ + _match_clause("public.mesh_search_norm(v.name)", path=path) + """
            ORDER BY v.id
            LIMIT """ + str(int(cap))
        )
        rows = (await session.execute(sql, params)).mappings().all()
        return [_view_candidate(row, slug=slug, trigram=path == "trgm") for row in rows]

    async def _recall_chat_sessions(
        self, session, *, params, slug, path, cap
    ) -> list[Candidate]:
        sql = text(
            """
            SELECT c.id, c.title, c.agent_id, a.name AS agent_name
            FROM chat_sessions c
            LEFT JOIN agents a ON a.id = c.agent_id AND a.workspace_id = c.workspace_id
            WHERE c.workspace_id = :ws
              AND c.deleted_at IS NULL
              AND c.owner_id = :member_id
              AND """ + _match_clause("public.mesh_search_norm(c.title)", path=path) + """
            ORDER BY c.id
            LIMIT """ + str(int(cap))
        )
        rows = (await session.execute(sql, params)).mappings().all()
        return [_chat_candidate(row, slug=slug, trigram=path == "trgm") for row in rows]

    async def _with_agent_capacity(
        self, session, *, params, candidates: list[Candidate]
    ) -> list[Candidate]:
        """Agent context.capacity snapshot (§4.8: server snapshot, not live).

        Returns NEW candidates (immutable pattern) with the capacity context
        key attached to agent rows.
        """
        agent_ids = sorted(
            {c.context["agent_id"] for c in candidates
             if c.result_type == "agent" and c.context.get("agent_id")}
        )
        counts: dict[uuid.UUID, dict[str, int]] = {}
        if agent_ids:
            sql = text(
                """
                SELECT agent_id, status, count(*) AS n
                FROM task_executions
                WHERE workspace_id = :ws
                  AND agent_id = ANY(CAST(:agent_ids AS uuid[]))
                  AND status IN ('running', 'queued', 'awaiting_approval')
                GROUP BY agent_id, status
                """
            )
            rows = (
                await session.execute(sql, params | {"agent_ids": agent_ids})
            ).mappings().all()
            for row in rows:
                counts.setdefault(row["agent_id"], {})[row["status"]] = int(row["n"])

        enriched: list[Candidate] = []
        for candidate in candidates:
            if candidate.result_type != "agent":
                enriched.append(candidate)
                continue
            stats = counts.get(candidate.context.get("agent_id"), {})
            context = candidate.context | {
                "capacity": {
                    "running": stats.get("running", 0),
                    "queued": stats.get("queued", 0),
                    "awaiting_approval": stats.get("awaiting_approval", 0),
                }
            }
            enriched.append(replace(candidate, context=context))
        return enriched

    # -- paging ---------------------------------------------------------------
    def _page(
        self, entries, *, q, types, binding, after_key, limit
    ) -> dict:
        if after_key is not None:
            entries = [entry for entry in entries if entry[0] > after_key]
        page = entries[:limit]
        next_cursor = None
        if len(entries) > limit and page:
            next_cursor = encode_cursor(
                self._secret, fp=binding, factors=page[-1][2]
            )
        return {"data": [item for _, item, _ in page], "next_cursor": next_cursor}


# -- candidate builders (pure, per type) --------------------------------------


def _issue_candidate(row, *, slug: str, pinned: bool) -> Candidate:
    project = None
    if row["project_id"] is not None:
        project = {"id": str(row["project_id"]), "name": row["project_name"]}
    context = {
        "identifier": row["identifier"],
        "project": project,
        "status": {
            "id": str(row["status_id"]),
            "name": row["status_name"],
            "category": row["status_category"],
        },
    }
    return Candidate(
        result_type="issue",
        result_id=str(row["id"]),
        title=row["title"],
        context=context,
        icon="issue",
        url=schemas.issue_url(slug, row["identifier"]),
        badge=schemas.status_badge(row["status_name"], row["status_category"]),
        identifier_scored=row["identifier"],
        pinned=pinned,
    )


def _member_candidate(row, *, result_type: str, slug: str, trigram: bool) -> Candidate:
    context: dict = {
        "member_type": row["member_type"],
        "role": row["role"],
    }
    if result_type == "agent":
        context["agent_id"] = row["agent_id"]  # internal: capacity join key
    return Candidate(
        result_type=result_type,
        result_id=str(row["id"]),
        title=row["title"],
        context=context,
        icon=result_type,
        url=schemas.member_url(slug, str(row["id"])),
        badge=schemas.member_type_badge(row["member_type"]),
        trigram_recalled=trigram,
    )


def _project_candidate(row, *, slug: str, trigram: bool) -> Candidate:
    badge = (
        schemas.private_visibility_badge() if row["visibility"] == "private" else None
    )
    return Candidate(
        result_type="project",
        result_id=str(row["id"]),
        title=row["name"],
        context={"visibility": row["visibility"], "key": row["key"]},
        icon="project",
        url=schemas.project_url(slug, str(row["id"])),
        badge=badge,
        trigram_recalled=trigram,
    )


def _view_candidate(row, *, slug: str, trigram: bool) -> Candidate:
    context: dict = {
        "scope": "project" if row["project_id"] is not None else "workspace",
    }
    if row["project_id"] is not None:
        context["project"] = {"id": str(row["project_id"]), "name": row["project_name"]}
    if row["visibility"] == "private":
        context["owner_only"] = True
    return Candidate(
        result_type="view",
        result_id=str(row["id"]),
        title=row["name"],
        context=context,
        icon="view",
        url=schemas.view_url(slug, str(row["id"])),
        badge=None,
        trigram_recalled=trigram,
    )


def _chat_candidate(row, *, slug: str, trigram: bool) -> Candidate:
    agent = None
    if row["agent_id"] is not None:
        agent = {"id": str(row["agent_id"]), "name": row["agent_name"] or "agent"}
    context = {
        # The human owner plus the agent counterpart (when present).
        "participants_count": 2 if agent is not None else 1,
        "agent": agent,
    }
    return Candidate(
        result_type="chat_session",
        result_id=str(row["id"]),
        title=row["title"],
        context=context,
        icon="chat_session",
        url=schemas.chat_url(slug, str(row["id"])),
        badge=None,
        trigram_recalled=trigram,
    )


# -- ranking -------------------------------------------------------------------


def _rank(candidates: list[Candidate], norm_q: str) -> list[tuple[tuple, dict, list]]:
    """Dedup, score, render and sort per the §4.6 total order.

    Returns ``(sort_key, item, cursor_factors)`` triples; identifier
    fast-path hits always take the max bucket regardless of title match.
    """
    best: dict[tuple[str, str], tuple[int, Candidate]] = {}
    for candidate in candidates:
        bucket = _candidate_bucket(candidate, norm_q)
        key = (candidate.result_type, candidate.result_id)
        if key not in best or bucket > best[key][0]:
            best[key] = (bucket, candidate)

    entries: list[tuple[tuple, dict, list]] = []
    for (result_type, result_id), (bucket, candidate) in best.items():
        item = _render_item(candidate, bucket=bucket, norm_q=norm_q)
        title_lex = search_norm(candidate.title)
        factors = canonical_sort_factors(
            score_bucket=bucket,
            title_len=len(candidate.title),
            title_lex=title_lex,
            result_type=result_type,
            result_id=result_id,
        )
        sort_key = (-bucket, len(candidate.title), title_lex, result_type, result_id)
        entries.append((sort_key, item, factors))
    entries.sort(key=lambda entry: entry[0])
    return entries


def _candidate_bucket(candidate: Candidate, norm_q: str) -> int:
    if candidate.pinned:
        return BUCKET_IDENTIFIER_EXACT
    bucket = score_candidate(
        norm_q, candidate.title, trigram_recalled=candidate.trigram_recalled
    )
    if candidate.identifier_scored is not None:
        identifier_bucket = score_candidate(
            norm_q, candidate.identifier_scored, trigram_recalled=False
        )
        bucket = max(bucket, identifier_bucket)
    return bucket


def _render_item(candidate: Candidate, *, bucket: int, norm_q: str) -> dict:
    item: dict = {
        "type": candidate.result_type,
        "id": candidate.result_id,
        "title": candidate.title,
        "context": _public_context(candidate),
        "icon": candidate.icon,
        "url": candidate.url,
    }
    if candidate.badge is not None:
        item["badge"] = candidate.badge
    ranges = highlight_ranges(bucket, norm_q, candidate.title)
    if ranges:
        item["highlight"] = {"title": {"unit": "codepoint", "ranges": ranges}}
    return item


def _public_context(candidate: Candidate) -> dict:
    """Context without internal-only keys (the agent capacity join key)."""
    return {
        key: value
        for key, value in candidate.context.items()
        if key != "agent_id" or candidate.result_type != "agent"
    }
