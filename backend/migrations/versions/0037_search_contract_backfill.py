"""Backfill the complete search database contract on existing installs.

Some databases reached revision 0036 from an earlier 0035 artifact that did
not yet contain ``mesh_search_text_score`` or ``mesh_resync_search_name`` and
left the extension ownership ledger writable through default app-role table
privileges. Fresh installs are protected in 0035; this forward migration is
the idempotent repair path for already-versioned databases.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    # Revoke first: the ledger determines which extensions a later downgrade
    # may drop. The table owner retains its inherent access.
    op.execute("REVOKE ALL PRIVILEGES ON TABLE mesh_search_ext_ledger FROM PUBLIC")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE mesh_search_ext_ledger FROM {APP_ROLE}"
    )
    _create_score_function()
    _create_resync_function()


def downgrade() -> None:
    # Deliberately non-destructive. Both functions may already have existed on
    # a 0036 database, and restoring broad ledger privileges would recreate the
    # vulnerability. Downgrading below 0035 still removes the functions and
    # ledger through 0035's owning downgrade.
    pass


def _create_score_function() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.mesh_search_text_score(t TEXT, q TEXT)
        RETURNS INT
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS
        $$
        DECLARE
          nt TEXT := public.mesh_search_norm(t);
          nq TEXT := public.mesh_search_norm(q);
          title_tokens TEXT[];
          query_tokens TEXT[];
          boundary_tokens TEXT[];
          acronym TEXT := '';
          token TEXT;
          query_token TEXT;
          all_match BOOLEAN;
          needle TEXT;
          needle_pos INT;
          index_pos INT;
        BEGIN
          IF nq = '' OR nt = '' THEN RETURN 0; END IF;
          IF nt = nq THEN RETURN 90; END IF;
          IF nt LIKE nq || '%' THEN RETURN 80; END IF;

          title_tokens := regexp_split_to_array(nt, '[[:space:]_./-]+');
          query_tokens := regexp_split_to_array(nq, '[[:space:]_./-]+');

          IF cardinality(query_tokens) > 1 THEN
            all_match := TRUE;
            FOREACH query_token IN ARRAY query_tokens LOOP
              IF query_token = '' OR NOT EXISTS (
                SELECT 1 FROM unnest(title_tokens) AS tt
                WHERE tt <> '' AND tt LIKE query_token || '%'
              ) THEN
                all_match := FALSE;
                EXIT;
              END IF;
            END LOOP;
            IF all_match THEN RETURN 70; END IF;

            all_match := TRUE;
            FOREACH query_token IN ARRAY query_tokens LOOP
              IF query_token = '' OR NOT EXISTS (
                SELECT 1 FROM unnest(title_tokens) AS tt
                WHERE tt <> '' AND position(query_token IN tt) > 0
              ) THEN
                all_match := FALSE;
                EXIT;
              END IF;
            END LOOP;
            IF all_match THEN RETURN 40; END IF;
            needle := array_to_string(query_tokens, ' ');
          ELSE
            FOREACH token IN ARRAY title_tokens LOOP
              IF token <> '' AND token LIKE nq || '%' THEN RETURN 70; END IF;
              IF token <> '' THEN acronym := acronym || left(token, 1); END IF;
            END LOOP;
            IF length(nq) >= 2 AND acronym = nq THEN RETURN 60; END IF;

            boundary_tokens := regexp_split_to_array(
              public.mesh_search_norm(
                regexp_replace(t, '([[:lower:][:digit:]])([[:upper:]])', E'\\1 \\2', 'g')
              ),
              '[[:space:]_./-]+'
            );
            FOREACH token IN ARRAY boundary_tokens LOOP
              IF token <> '' AND token LIKE nq || '%' THEN RETURN 50; END IF;
            END LOOP;
            IF position(nq IN nt) > 0 THEN RETURN 40; END IF;
            needle := nq;
          END IF;

          needle_pos := 1;
          FOR index_pos IN 1..length(nt) LOOP
            IF substring(nt FROM index_pos FOR 1) =
               substring(needle FROM needle_pos FOR 1) THEN
              needle_pos := needle_pos + 1;
              IF needle_pos > length(needle) THEN RETURN 20; END IF;
            END IF;
          END LOOP;

          RETURN 10;
        END $$;
        """
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.mesh_search_text_score(TEXT, TEXT) TO {APP_ROLE}"
    )


def _create_resync_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.mesh_resync_search_name(
          p_kind TEXT,
          p_id UUID DEFAULT NULL
        ) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
        $$
        DECLARE
          changed BIGINT;
        BEGIN
          IF p_kind NOT IN ('member', 'user', 'agent', 'all') THEN
            RAISE EXCEPTION 'mesh_resync_search_name: unknown kind %', p_kind;
          END IF;
          IF p_kind <> 'all' AND p_id IS NULL THEN
            RAISE EXCEPTION 'mesh_resync_search_name: id is required for kind %', p_kind;
          END IF;

          UPDATE members m
          SET search_name = public.mesh_member_search_name(m.id)
          WHERE (
              p_kind = 'all'
              OR (p_kind = 'member' AND m.id = p_id)
              OR (p_kind = 'user' AND m.user_id = p_id)
              OR (p_kind = 'agent' AND m.agent_id = p_id)
            )
            AND m.search_name IS DISTINCT FROM public.mesh_member_search_name(m.id);
          GET DIAGNOSTICS changed = ROW_COUNT;
          RETURN changed;
        END $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.mesh_resync_search_name(TEXT, UUID) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.mesh_resync_search_name(TEXT, UUID) TO {APP_ROLE}"
    )
