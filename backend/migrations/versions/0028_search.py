"""search: global search normalization, projection and indexes.

Platform-capability increment (search-command-palette.md §2.2, README §6.1 /
§6.2). Single-head chain 0001 → 0028.

- ``public.mesh_search_norm(TEXT)`` — the SINGLE normalization entry point
  (NFKD + unaccent + lower). plpgsql IMMUTABLE PARALLEL SAFE so expression
  indexes stay matchable across planner versions (§2.2 R5-H3). Every search
  normalization — projection writes, index expressions, query expressions,
  backfills — goes through this function only.
- ``members.search_name`` — controlled search projection of the README §6.1
  display-name resolution chain (display_override → users.display_name →
  users.email / agents.name). Search-only; never rendered. Synced by the
  service-layer single function ``sync_member_search_name`` plus a daily
  reconcile (search-command-palette.md §2.2 sync contract).
- 11 search indexes (9 ``mesh_search_norm`` expression indexes + 2 member
  projection column indexes) + 2 tenant/status support indexes. Three query
  paths map 1:1 onto them: 1–2 char prefix (``*_prefix`` B-tree
  text_pattern_ops), canonical identifier equality (existing
  ``uq_issues_identifier``), ≥3 char trigram (``*_trgm`` GIN gin_trgm_ops).
- One-shot batched backfill (≤10k rows per UPDATE statement).

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

SEARCH_INDEXES = (
    "idx_members_search_name_trgm",
    "idx_members_search_name_prefix",
    "idx_issues_title_trgm",
    "idx_issues_title_prefix",
    "idx_issues_identifier_prefix",
    "idx_projects_name_trgm",
    "idx_projects_name_prefix",
    "idx_views_name_trgm",
    "idx_views_name_prefix",
    "idx_chat_sessions_title_trgm",
    "idx_chat_sessions_title_prefix",
)


def upgrade() -> None:
    # -- extensions (idempotent) ------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # -- single normalization entry point (search-command-palette.md §2.2) ------
    # unaccent(text) is STABLE (reads the dictionary); the two-argument form
    # pins an explicit regdictionary so the IMMUTABLE declaration is honest.
    # plpgsql (never inlined) keeps expression-index indexprs matching the
    # query expression across planner versions (R5-H3).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_search_norm(t TEXT) RETURNS TEXT
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS
        $$ BEGIN
          RETURN lower(public.unaccent('public.unaccent'::regdictionary, normalize(t, NFKD)));
        END $$
        """
    )
    # The API connects as the restricted mesh_app role (README §6.2 rule 5);
    # query expressions call the function, so it needs EXECUTE.
    op.execute(f"GRANT EXECUTE ON FUNCTION public.mesh_search_norm(TEXT) TO {APP_ROLE}")

    # -- members.search_name projection (README §6.1 registered snapshot) --------
    op.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS search_name TEXT NOT NULL DEFAULT ''")
    op.execute(
        "COMMENT ON COLUMN members.search_name IS "
        "'检索专用投影 = public.mesh_search_norm(README §6.1 显示名解析链结果);"
        "仅用于检索,不用于显示渲染。同步契约见 search-command-palette.md §2.2'"
    )

    # -- backfill before building indexes (fresh databases: no-op) ---------------
    # Batched ≤10k rows per UPDATE (§2.2 回填迁移); each statement is bounded so
    # no single UPDATE holds a huge row set.
    op.execute(
        """
        DO $$
        DECLARE
          v_batch INT := 10000;
          v_rows  INT;
        BEGIN
          LOOP
            WITH batch AS (
              SELECT m.id
              FROM members m
              WHERE m.search_name = ''
              ORDER BY m.id
              LIMIT v_batch
              FOR UPDATE OF m SKIP LOCKED
            )
            UPDATE members m
            SET search_name = public.mesh_search_norm(src.display_name)
            FROM (
              SELECT b.id,
                     COALESCE(
                       NULLIF(m2.display_override, ''),
                       CASE m2.member_type
                         WHEN 'human' THEN COALESCE(NULLIF(u.display_name, ''), u.email)
                         WHEN 'agent' THEN a.name
                       END,
                       ''
                     ) AS display_name
              FROM batch b
              JOIN members m2 ON m2.id = b.id
              LEFT JOIN users u ON u.id = m2.user_id
              LEFT JOIN agents a ON a.id = m2.agent_id
            ) src
            WHERE m.id = src.id;
            GET DIAGNOSTICS v_rows = ROW_COUNT;
            EXIT WHEN v_rows = 0;
          END LOOP;
        END $$
        """
    )

    # -- member/agent: projection column indexes (§2.2 items 1a–1c) ---------------
    # ≥3 char fuzzy: trigram GIN on the already-normalized column.
    op.execute(
        "CREATE INDEX idx_members_search_name_trgm ON members USING gin (search_name gin_trgm_ops)"
    )
    # 1–2 char prefix: B-tree pattern index, workspace-scoped. The partial
    # predicate mirrors the roster visibility predicate carried by queries.
    op.execute(
        "CREATE INDEX idx_members_search_name_prefix "
        "ON members (workspace_id, search_name text_pattern_ops) WHERE status <> 'removed'"
    )
    # Tenant/type/status support index (not one of the 11 search indexes).
    op.execute(
        "CREATE INDEX idx_members_ws_type_active "
        "ON members (workspace_id, member_type) WHERE status <> 'removed'"
    )

    # -- issues (§2.2 item 2) ------------------------------------------------------
    op.execute(
        "CREATE INDEX idx_issues_title_trgm "
        "ON issues USING gin ((public.mesh_search_norm(title)) gin_trgm_ops) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_title_prefix "
        "ON issues (workspace_id, (public.mesh_search_norm(title)) text_pattern_ops) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_identifier_prefix "
        "ON issues (workspace_id, (public.mesh_search_norm(identifier)) text_pattern_ops) "
        "WHERE deleted_at IS NULL"
    )
    # Tenant/soft-delete support index (BitmapAnd partner; not a search index).
    op.execute(
        "CREATE INDEX idx_issues_ws_not_deleted "
        "ON issues (workspace_id, project_id) WHERE deleted_at IS NULL"
    )

    # -- projects (§2.2 item 3) ----------------------------------------------------
    op.execute(
        "CREATE INDEX idx_projects_name_trgm "
        "ON projects USING gin ((public.mesh_search_norm(name)) gin_trgm_ops) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_projects_name_prefix "
        "ON projects (workspace_id, (public.mesh_search_norm(name)) text_pattern_ops) "
        "WHERE deleted_at IS NULL"
    )

    # -- views (§2.2 item 4) -------------------------------------------------------
    op.execute(
        "CREATE INDEX idx_views_name_trgm "
        "ON views USING gin ((public.mesh_search_norm(name)) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_views_name_prefix "
        "ON views (workspace_id, (public.mesh_search_norm(name)) text_pattern_ops)"
    )

    # -- chat_sessions (§2.2 item 5) ------------------------------------------------
    op.execute(
        "CREATE INDEX idx_chat_sessions_title_trgm "
        "ON chat_sessions USING gin ((public.mesh_search_norm(title)) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_chat_sessions_title_prefix "
        "ON chat_sessions (workspace_id, (public.mesh_search_norm(title)) text_pattern_ops)"
    )

    op.execute("ANALYZE members")
    op.execute("ANALYZE issues")
    op.execute("ANALYZE projects")
    op.execute("ANALYZE views")
    op.execute("ANALYZE chat_sessions")


def downgrade() -> None:
    for name in SEARCH_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("DROP INDEX IF EXISTS idx_issues_ws_not_deleted")
    op.execute("DROP INDEX IF EXISTS idx_members_ws_type_active")
    op.execute("ALTER TABLE members DROP COLUMN IF EXISTS search_name")
    op.execute("DROP FUNCTION IF EXISTS public.mesh_search_norm(TEXT)")
    # Extensions are intentionally kept: other databases may rely on them and
    # dropping shared extensions is not reversible safely from one module.
