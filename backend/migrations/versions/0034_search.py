"""search: global search indexes, members.search_name projection + sync
triggers, mesh_search_norm normalizer (search-command-palette.md §2.2).

Single-head chain 0001 → 0034, chained after integrations 0033.

Objects created:

- extensions ``pg_trgm`` + ``unaccent`` (guarded — only when absent; a
  ledger table records what THIS migration created so downgrade drops
  exactly and only those);
- ``public.mesh_search_norm(TEXT)`` — the SINGLE normalization entry point
  (NFKD + unaccent + lower; plpgsql IMMUTABLE PARALLEL SAFE so expression
  indexes stay matched across planner versions, spec §2.2 R5-H3);
- ``members.search_name`` — search-only projection of the README §6.1
  display-name resolution chain, backfilled in ≤10k-row batches;
- 11 search indexes (9 mesh_search_norm expression indexes + 2 projection
  column indexes) plus 2 tenant/status support indexes — names verbatim
  per spec §2.2 (R4-M2 count);
- sync triggers: members (insert / display_override / status), users
  (display_name / email rename → ALL member rows of that user), agents
  (name rename → that agent's member rows). Display rendering never reads
  the projection — it is search-only (spec §2.2 同步契约).

Transactional by design: plain CREATE INDEX (not CONCURRENTLY) is fine for
this batch — test databases are fresh; production first-deploy notes go in
the runbook. ``normalize(t, NFKD)`` + the explicit ``'public.unaccent'``
regdictionary are PG16 verbatim per spec.

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-30
"""
from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

# The display-name resolution chain (README §6.1 / member.md §2.4) computed
# in SQL over a members row + its optional users/agents JOINs — shared by the
# backfill and the sync trigger helper so projection and trigger never drift.
SEARCH_NAME_CHAIN_SQL = """COALESCE(
  NULLIF(BTRIM(m.display_override), ''),
  CASE m.member_type
    WHEN 'human' THEN COALESCE(NULLIF(BTRIM(u.display_name), ''),
                               NULLIF(split_part(u.email, '@', 1), ''))
    WHEN 'agent' THEN NULLIF(BTRIM(a.name), '')
  END,
  CASE WHEN m.member_type = 'human' THEN 'member-' || left(m.id::text, 8)
       ELSE 'agent-' || left(m.agent_id::text, 8) END,
  '')"""

SEARCH_INDEXES: tuple[str, ...] = (
    # 1a/1b — member projection column indexes (column is pre-normalized).
    "idx_members_search_name_trgm",
    "idx_members_search_name_prefix",
    # 2b/2c — issue expression indexes (title trigram, title + identifier prefix).
    "idx_issues_title_trgm",
    "idx_issues_title_prefix",
    "idx_issues_identifier_prefix",
    # 3 — project expression indexes.
    "idx_projects_name_trgm",
    "idx_projects_name_prefix",
    # 4 — view expression indexes.
    "idx_views_name_trgm",
    "idx_views_name_prefix",
    # 5 — chat_session expression indexes.
    "idx_chat_sessions_title_trgm",
    "idx_chat_sessions_title_prefix",
)

SUPPORT_INDEXES: tuple[str, ...] = (
    "idx_members_ws_type_active",
    "idx_issues_ws_not_deleted",
)


def upgrade() -> None:
    _create_extensions_guarded()
    _create_norm_function()
    _add_projection_column()
    _backfill_projection()
    _create_indexes()
    _create_sync_triggers()


def downgrade() -> None:
    # Triggers first (they call the helper), then helper + normalizer.
    op.execute("DROP TRIGGER IF EXISTS trg_agents_search_name ON agents")
    op.execute("DROP TRIGGER IF EXISTS trg_users_search_name ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_members_search_name ON members")
    op.execute("DROP FUNCTION IF EXISTS public.mesh_search_sync_agent()")
    op.execute("DROP FUNCTION IF EXISTS public.mesh_search_sync_user()")
    op.execute("DROP FUNCTION IF EXISTS public.mesh_search_sync_member()")
    op.execute("DROP FUNCTION IF EXISTS public.mesh_member_search_name(UUID)")
    for name in SUPPORT_INDEXES + SEARCH_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    # The normalizer has expression-index dependents — they must be gone
    # first (above); DROP refusal is the integrity backstop.
    op.execute("DROP FUNCTION IF EXISTS public.mesh_search_norm(TEXT)")
    op.execute("ALTER TABLE members DROP COLUMN IF EXISTS search_name")
    _drop_guarded_extensions()


# ---------------------------------------------------------------------------
# Extensions — guarded so downgrade drops only what this migration created.
# ---------------------------------------------------------------------------


def _create_extensions_guarded() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mesh_search_ext_ledger (
          name TEXT PRIMARY KEY
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
            CREATE EXTENSION pg_trgm;
            INSERT INTO mesh_search_ext_ledger(name) VALUES ('pg_trgm')
            ON CONFLICT (name) DO NOTHING;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'unaccent') THEN
            CREATE EXTENSION unaccent;
            INSERT INTO mesh_search_ext_ledger(name) VALUES ('unaccent')
            ON CONFLICT (name) DO NOTHING;
          END IF;
        END $$;
        """
    )


def _drop_guarded_extensions() -> None:
    op.execute(
        """
        DO $$
        DECLARE ext_name TEXT;
        BEGIN
          FOR ext_name IN SELECT name FROM mesh_search_ext_ledger LOOP
            EXECUTE format('DROP EXTENSION IF EXISTS %I', ext_name);
          END LOOP;
        END $$;
        """
    )
    op.execute("DROP TABLE IF EXISTS mesh_search_ext_ledger")


# ---------------------------------------------------------------------------
# Normalizer + projection.
# ---------------------------------------------------------------------------


def _create_norm_function() -> None:
    # Verbatim per spec §2.2: plpgsql (never inlined — indexprs stay matched
    # across planner versions, R5-H3), explicit regdictionary pins the
    # dictionary so the IMMUTABLE claim holds (R3-M1).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_search_norm(t TEXT) RETURNS TEXT
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS
        $$ BEGIN
          RETURN lower(public.unaccent('public.unaccent'::regdictionary,
                                       normalize(t, NFKD)));
        END $$;
        """
    )


def _add_projection_column() -> None:
    op.execute(
        "ALTER TABLE members ADD COLUMN IF NOT EXISTS search_name TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        """
        COMMENT ON COLUMN members.search_name IS
          '检索专用投影 = public.mesh_search_norm(README §6.1 显示名解析链结果);仅用于检索,不用于显示渲染。同步契约见 search-command-palette.md §2.2'
        """
    )


def _backfill_projection() -> None:
    # Batches of ≤10k rows (spec §2.2 回填迁移): the migration transaction
    # holds until commit, so this is chunked work inside it rather than
    # separate commits — acceptable for first-deploy/fresh test DBs.
    # The chain is resolved inside the derived table (LEFT JOINs cannot see
    # the UPDATE target as a sibling FROM entry — PostgreSQL scope rule);
    # the members alias there is ``mm``.
    chain_alias_sql = SEARCH_NAME_CHAIN_SQL.replace("m.", "mm.")
    op.execute(
        """
        DO $$
        DECLARE batch_rows INT;
        BEGIN
          LOOP
            UPDATE members m
            SET search_name = public.mesh_search_norm(sub.display_chain)
            FROM (
              SELECT mm.id AS mid, """ + chain_alias_sql + """ AS display_chain
              FROM (SELECT id FROM members WHERE search_name = ''
                    ORDER BY id LIMIT 10000) bb
              JOIN members mm ON mm.id = bb.id
              LEFT JOIN users u ON u.id = mm.user_id
              LEFT JOIN agents a ON a.id = mm.agent_id AND a.workspace_id = mm.workspace_id
            ) sub
            WHERE m.id = sub.mid;
            GET DIAGNOSTICS batch_rows = ROW_COUNT;
            EXIT WHEN batch_rows = 0;
          END LOOP;
        END $$;
        """
    )


# ---------------------------------------------------------------------------
# Indexes — query expressions must match these verbatim (spec §2.2).
# ---------------------------------------------------------------------------


def _create_indexes() -> None:
    op.execute(
        "CREATE INDEX idx_members_search_name_trgm"
        " ON members USING gin (search_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_members_search_name_prefix"
        " ON members (workspace_id, search_name text_pattern_ops)"
        " WHERE status <> 'removed'"
    )
    op.execute(
        "CREATE INDEX idx_members_ws_type_active"
        " ON members (workspace_id, member_type)"
        " WHERE status <> 'removed'"
    )
    op.execute(
        "CREATE INDEX idx_issues_title_trgm"
        " ON issues USING gin ((public.mesh_search_norm(title)) gin_trgm_ops)"
        " WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_title_prefix"
        " ON issues (workspace_id, (public.mesh_search_norm(title)) text_pattern_ops)"
        " WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_identifier_prefix"
        " ON issues (workspace_id, (public.mesh_search_norm(identifier)) text_pattern_ops)"
        " WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_ws_not_deleted"
        " ON issues (workspace_id, project_id)"
        " WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_projects_name_trgm"
        " ON projects USING gin ((public.mesh_search_norm(name)) gin_trgm_ops)"
        " WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_projects_name_prefix"
        " ON projects (workspace_id, (public.mesh_search_norm(name)) text_pattern_ops)"
        " WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_views_name_trgm"
        " ON views USING gin ((public.mesh_search_norm(name)) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_views_name_prefix"
        " ON views (workspace_id, (public.mesh_search_norm(name)) text_pattern_ops)"
    )
    op.execute(
        "CREATE INDEX idx_chat_sessions_title_trgm"
        " ON chat_sessions USING gin ((public.mesh_search_norm(title)) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_chat_sessions_title_prefix"
        " ON chat_sessions (workspace_id, (public.mesh_search_norm(title)) text_pattern_ops)"
    )


# ---------------------------------------------------------------------------
# Sync triggers — the controlled-sync contract (spec §2.2 写死).
# ---------------------------------------------------------------------------


def _create_sync_triggers() -> None:
    # Single helper: recompute ONE member row's projection through the same
    # chain the backfill used (index/query/backfill share mesh_search_norm).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_member_search_name(p_member_id UUID)
        RETURNS TEXT
        LANGUAGE sql STABLE AS
        $$
          SELECT public.mesh_search_norm(""" + SEARCH_NAME_CHAIN_SQL + """)
          FROM members m
          LEFT JOIN users u ON u.id = m.user_id
          LEFT JOIN agents a ON a.id = m.agent_id AND a.workspace_id = m.workspace_id
          WHERE m.id = p_member_id
        $$;
        """
    )

    # members: enrollment + override/status changes. The sync UPDATE below
    # touches ONLY search_name, so it cannot re-fire this column-list
    # trigger (no recursion).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_search_sync_member()
        RETURNS trigger
        LANGUAGE plpgsql AS
        $$
        BEGIN
          UPDATE members
          SET search_name = public.mesh_member_search_name(NEW.id)
          WHERE id = NEW.id;
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_members_search_name
        AFTER INSERT OR UPDATE OF display_override, status ON members
        FOR EACH ROW EXECUTE FUNCTION public.mesh_search_sync_member()
        """
    )

    # users: a rename recomputes ALL member rows of that user (cross-workspace).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_search_sync_user()
        RETURNS trigger
        LANGUAGE plpgsql AS
        $$
        BEGIN
          UPDATE members
          SET search_name = public.mesh_member_search_name(id)
          WHERE user_id = NEW.id;
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_users_search_name
        AFTER UPDATE OF display_name, email ON users
        FOR EACH ROW EXECUTE FUNCTION public.mesh_search_sync_user()
        """
    )

    # agents: a rename recomputes that agent's member row(s).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_search_sync_agent()
        RETURNS trigger
        LANGUAGE plpgsql AS
        $$
        BEGIN
          UPDATE members
          SET search_name = public.mesh_member_search_name(id)
          WHERE agent_id = NEW.id AND workspace_id = NEW.workspace_id;
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agents_search_name
        AFTER UPDATE OF name ON agents
        FOR EACH ROW EXECUTE FUNCTION public.mesh_search_sync_agent()
        """
    )
