"""search: global search normalization, projection and indexes.

Platform-capability increment (search-command-palette.md §2.2, README §6.1 /
§6.2). Single-head chain 0001 → 0035.

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

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
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

    # -- §4.6 scoring ladder — single source of truth (M6) -----------------------
    # The service SELECTs use this function as score_bucket; scoring.py
    # mirrors the identical algorithm for unit tests, so SQL and Python can
    # never diverge (M6: the previous parallel SQL CASE / Python ladder
    # disagreed on separator handling and lacked acronym/word-boundary
    # tiers). Inputs are expected ALREADY normalized (mesh_search_norm).
    #   8 exact > 7 prefix > 6 token-prefix (every query token prefixes
    #   some title token; separators - _ / . count as token boundaries)
    #   > 5 acronym (query chars = initials of successive title tokens)
    #   > 3 contiguous substring > 1 trigram fuzzy fallback.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_search_text_score(t TEXT, q TEXT)
        RETURNS INT
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS
        $$
        DECLARE
          toks_t TEXT[];
          toks_q TEXT[];
          tok TEXT;
          flat_q TEXT;
          p INT;
          all_prefixed BOOLEAN;
          non_empty INT := 0;
        BEGIN
          IF q = '' THEN RETURN 1; END IF;
          IF t = q THEN RETURN 8; END IF;
          IF t LIKE q || '%' THEN RETURN 7; END IF;
          -- Separator normalization: - _ / . (and space) are token boundaries.
          toks_t := string_to_array(regexp_replace(t, '[-_/. ]+', ' ', 'g'), ' ');
          toks_q := string_to_array(regexp_replace(q, '[-_/. ]+', ' ', 'g'), ' ');
          -- Token-prefix: every non-empty query token prefixes some title token.
          all_prefixed := TRUE;
          FOREACH tok IN ARRAY toks_q LOOP
            IF tok = '' THEN CONTINUE; END IF;
            non_empty := non_empty + 1;
            IF NOT EXISTS (
              SELECT 1 FROM unnest(toks_t) AS tt
              WHERE tt <> '' AND tt LIKE tok || '%'
            ) THEN
              all_prefixed := FALSE;
              EXIT;
            END IF;
          END LOOP;
          IF non_empty > 0 AND all_prefixed THEN RETURN 6; END IF;
          -- Acronym: the query's characters (sans separators) match the
          -- first characters of successive title tokens in order.
          flat_q := regexp_replace(q, '[-_/. ]+', '', 'g');
          IF flat_q <> '' AND array_length(toks_t, 1) >= length(flat_q) THEN
            p := 1;
            FOREACH tok IN ARRAY toks_t LOOP
              IF p > length(flat_q) THEN EXIT; END IF;
              IF tok <> '' AND left(tok, 1) = substring(flat_q FROM p FOR 1) THEN
                p := p + 1;
              END IF;
            END LOOP;
            IF p > length(flat_q) THEN RETURN 5; END IF;
          END IF;
          IF position(q in t) > 0 THEN RETURN 3; END IF;
          RETURN 1;
        END $$
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION public.mesh_search_text_score(TEXT, TEXT) TO {APP_ROLE}")

    # -- single search_name resync entry point (§2.2 sync contract) --------------
    # SECURITY DEFINER so the app role can resync across workspaces (a user
    # rename touches that user's member rows in EVERY workspace — the tenant
    # GUC / RLS would hide the other workspaces' rows otherwise; same pattern
    # as the mesh_<entity>_workspace_id resolvers). All normalization goes
    # through public.mesh_search_norm; the IS DISTINCT FROM guard keeps it a
    # no-op when already consistent.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_resync_search_name(
          p_kind TEXT,
          p_id UUID DEFAULT NULL
        ) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
        $$
        DECLARE
          v_count BIGINT;
        BEGIN
          IF p_kind NOT IN ('member', 'user', 'agent', 'all') THEN
            RAISE EXCEPTION 'mesh_resync_search_name: unknown kind %', p_kind;
          END IF;
          UPDATE members m
          SET search_name = src.norm
          FROM (
            SELECT m2.id AS mid,
                   public.mesh_search_norm(COALESCE(
                     NULLIF(m2.display_override, ''),
                     CASE m2.member_type
                       WHEN 'human' THEN COALESCE(NULLIF(u.display_name, ''), u.email)
                       WHEN 'agent' THEN a.name
                     END,
                     ''
                   )) AS norm
            FROM members m2
            LEFT JOIN users u ON u.id = m2.user_id
            LEFT JOIN agents a ON a.id = m2.agent_id
            WHERE (p_kind = 'all')
               OR (p_kind = 'member' AND m2.id = p_id)
               OR (p_kind = 'user' AND m2.user_id = p_id)
               OR (p_kind = 'agent' AND m2.agent_id = p_id)
          ) src
          WHERE m.id = src.mid
            AND m.search_name IS DISTINCT FROM src.norm;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count;
        END $$
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION public.mesh_resync_search_name(TEXT, UUID) TO {APP_ROLE}")

    # -- members.search_name projection (README §6.1 registered snapshot) --------
    op.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS search_name TEXT NOT NULL DEFAULT ''")
    op.execute(
        "COMMENT ON COLUMN members.search_name IS "
        "'检索专用投影 = public.mesh_search_norm(README §6.1 显示名解析链结果);"
        "仅用于检索,不用于显示渲染。同步契约见 search-command-palette.md §2.2'"
    )

    # -- backfill before building indexes (fresh databases: no-op) ---------------
    # Batched walk (§2.2 「每批 ≤1 万行,不持长事务」 — M3: the previous
    # single-UPDATE form violated the batch contract): keyset walk over
    # members.id, ≤10 000 rows per UPDATE statement, same normalization
    # chain the service layer and the daily reconcile use.
    op.execute(
        """
        DO $$
        DECLARE
          v_batch CONSTANT INT := 10000;
          v_last UUID := '00000000-0000-0000-0000-000000000000';
          v_ids UUID[];
        BEGIN
          LOOP
            SELECT array_agg(id ORDER BY id) INTO v_ids
            FROM (SELECT id FROM members WHERE id > v_last ORDER BY id LIMIT v_batch) b;
            EXIT WHEN v_ids IS NULL;
            v_last := v_ids[array_length(v_ids, 1)];
            UPDATE members m
            SET search_name = src.norm
            FROM (
              SELECT m2.id,
                     public.mesh_search_norm(COALESCE(
                       NULLIF(m2.display_override, ''),
                       CASE m2.member_type
                         WHEN 'human' THEN COALESCE(NULLIF(u.display_name, ''), u.email)
                         WHEN 'agent' THEN a.name
                       END,
                       ''
                     )) AS norm
              FROM members m2
              LEFT JOIN users u ON u.id = m2.user_id
              LEFT JOIN agents a ON a.id = m2.agent_id
              WHERE m2.id = ANY (v_ids)
            ) src
            WHERE m.id = src.id
              AND m.search_name IS DISTINCT FROM src.norm;
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
    op.execute("DROP FUNCTION IF EXISTS public.mesh_resync_search_name(TEXT, UUID)")
    op.execute("DROP FUNCTION IF EXISTS public.mesh_search_norm(TEXT)")
    # Extensions are intentionally kept: other databases may rely on them and
    # dropping shared extensions is not reversible safely from one module.
