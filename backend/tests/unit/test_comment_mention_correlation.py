"""Comment mention → logical execution migration and tenant integrity."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.comment import CommentMention

pytestmark = pytest.mark.unit

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _sync_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def _seed_workspace_graph(connection, *, workspace_id: uuid.UUID, suffix: str) -> dict:
    user_id = uuid.uuid4()
    member_id = uuid.uuid4()
    status_id = uuid.uuid4()
    issue_id = uuid.uuid4()
    connection.execute(
        text("INSERT INTO workspaces (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": workspace_id, "name": f"Mention {suffix}", "slug": f"mention-{suffix}"},
    )
    connection.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, :display_name)"),
        {
            "id": user_id,
            "email": f"mention-{suffix}@example.test",
            "display_name": f"Member {suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO members (id, workspace_id, member_type, user_id) "
            "VALUES (:id, :workspace_id, 'human', :user_id)"
        ),
        {"id": member_id, "workspace_id": workspace_id, "user_id": user_id},
    )
    connection.execute(
        text(
            "INSERT INTO issue_statuses "
            "(id, workspace_id, name, category, is_default) "
            "VALUES (:id, :workspace_id, 'Todo', 'todo', true)"
        ),
        {"id": status_id, "workspace_id": workspace_id},
    )
    connection.execute(
        text(
            "INSERT INTO issues "
            "(id, workspace_id, identifier_namespace_key, number, identifier, "
            " title, status_id, state_category, reporter_id) "
            "VALUES (:id, :workspace_id, 'mention', 1, :identifier, "
            " 'Mention migration', :status_id, 'todo', :reporter_id)"
        ),
        {
            "id": issue_id,
            "workspace_id": workspace_id,
            "identifier": f"M-{suffix}",
            "status_id": status_id,
            "reporter_id": member_id,
        },
    )
    return {"member_id": member_id, "issue_id": issue_id}


def _insert_comment(connection, *, workspace_id, issue_id, member_id) -> uuid.UUID:
    comment_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO comments "
            "(id, workspace_id, issue_id, author_kind, author_id, body_markdown) "
            "VALUES (:id, :workspace_id, :issue_id, 'member', :author_id, 'mention')"
        ),
        {
            "id": comment_id,
            "workspace_id": workspace_id,
            "issue_id": issue_id,
            "author_id": member_id,
        },
    )
    return comment_id


def test_comment_mention_model_declares_canonical_fk_and_pending_index():
    table = CommentMention.__table__
    foreign_key = next(
        constraint
        for constraint in table.foreign_key_constraints
        if constraint.name
        == "comment_mentions_triggered_execution_id_task_executions"
    )
    assert tuple(column.name for column in foreign_key.columns) == (
        "workspace_id",
        "triggered_execution_id",
    )
    assert foreign_key.referred_table.name == "task_executions"
    assert foreign_key.ondelete == "SET NULL (triggered_execution_id)"
    assert "idx_mentions_pending_trigger" in {index.name for index in table.indexes}


def test_0041_backfills_canonical_execution_and_enforces_tenant_fk(db_url):
    database_name = f"mesh_comment_0041_{uuid.uuid4().hex}"
    database_url = f"{db_url.rsplit('/', 1)[0]}/{database_name}"
    maintenance_url = f"{_sync_url(db_url).rsplit('/', 1)[0]}/postgres"
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    database_engine = None

    try:
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        config = _alembic_config(database_url)
        command.upgrade(config, "0040")
        database_engine = create_engine(_sync_url(database_url))

        workspace_id = uuid.uuid4()
        foreign_workspace_id = uuid.uuid4()
        ids: dict[str, uuid.UUID] = {}
        with database_engine.begin() as connection:
            local = _seed_workspace_graph(connection, workspace_id=workspace_id, suffix=workspace_id.hex[:8])
            _seed_workspace_graph(
                connection,
                workspace_id=foreign_workspace_id,
                suffix=foreign_workspace_id.hex[:8],
            )
            ids.update(local)
            ids["legacy_comment"] = _insert_comment(connection, workspace_id=workspace_id, **local)
            ids["canonical_comment"] = _insert_comment(connection, workspace_id=workspace_id, **local)
            ids["pending_comment"] = _insert_comment(connection, workspace_id=workspace_id, **local)
            ids["cross_tenant_comment"] = _insert_comment(connection, workspace_id=workspace_id, **local)

            legacy_key = f"legacy-{uuid.uuid4().hex}"
            pending_key = f"pending-{uuid.uuid4().hex}"
            ids["published_outbox"] = uuid.uuid4()
            ids["pending_outbox"] = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, workspace_id, event_type, payload, idempotency_key, "
                    " status, published_at) VALUES "
                    "(:published_id, :workspace_id, 'execution.enqueue', "
                    " CAST(:published_payload AS jsonb), :published_scoped, "
                    " 'published', now()), "
                    "(:pending_id, :workspace_id, 'execution.enqueue', "
                    " CAST(:pending_payload AS jsonb), :pending_scoped, 'pending', NULL)"
                ),
                {
                    "published_id": ids["published_outbox"],
                    "pending_id": ids["pending_outbox"],
                    "workspace_id": workspace_id,
                    "published_payload": json.dumps({"idempotency_key": legacy_key}),
                    "pending_payload": json.dumps({"idempotency_key": pending_key}),
                    "published_scoped": f"ws:{workspace_id}:{legacy_key}",
                    "pending_scoped": f"ws:{workspace_id}:{pending_key}",
                },
            )
            ids["legacy_execution"] = uuid.uuid4()
            ids["canonical_execution"] = uuid.uuid4()
            ids["foreign_execution"] = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO task_executions "
                    "(id, workspace_id, trigger, idempotency_key) VALUES "
                    "(:legacy_id, :workspace_id, 'mention', :legacy_key), "
                    "(:canonical_id, :workspace_id, 'mention', :canonical_key), "
                    "(:foreign_id, :foreign_workspace_id, 'mention', :foreign_key)"
                ),
                {
                    "legacy_id": ids["legacy_execution"],
                    "canonical_id": ids["canonical_execution"],
                    "foreign_id": ids["foreign_execution"],
                    "workspace_id": workspace_id,
                    "foreign_workspace_id": foreign_workspace_id,
                    "legacy_key": legacy_key,
                    "canonical_key": f"canonical-{uuid.uuid4().hex}",
                    "foreign_key": f"foreign-{uuid.uuid4().hex}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO comment_mentions "
                    "(workspace_id, comment_id, mentioned_id, triggered_execution_id) "
                    "VALUES "
                    "(:workspace_id, :legacy_comment, :member_id, :published_outbox), "
                    "(:workspace_id, :canonical_comment, :member_id, :canonical_execution), "
                    "(:workspace_id, :pending_comment, :member_id, :pending_outbox)"
                ),
                {
                    "workspace_id": workspace_id,
                    "member_id": local["member_id"],
                    **ids,
                },
            )

        database_engine.dispose()
        database_engine = None
        command.upgrade(config, "0041")
        database_engine = create_engine(_sync_url(database_url))

        with database_engine.connect() as connection:
            rows = {
                row.comment_id: row
                for row in connection.execute(
                    text(
                        "SELECT comment_id, triggered_execution_id, pending_trigger_event_id "
                        "FROM comment_mentions WHERE workspace_id = :workspace_id"
                    ),
                    {"workspace_id": workspace_id},
                )
            }
            assert rows[ids["legacy_comment"]].triggered_execution_id == ids["legacy_execution"]
            assert rows[ids["legacy_comment"]].pending_trigger_event_id is None
            assert rows[ids["canonical_comment"]].triggered_execution_id == ids["canonical_execution"]
            assert rows[ids["canonical_comment"]].pending_trigger_event_id is None
            assert rows[ids["pending_comment"]].triggered_execution_id is None
            assert rows[ids["pending_comment"]].pending_trigger_event_id == ids["pending_outbox"]

            constraint = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = "
                    "'comment_mentions_triggered_execution_id_task_executions'"
                )
            ).scalar_one()
            assert "FOREIGN KEY (workspace_id, triggered_execution_id)" in constraint
            assert "task_executions(workspace_id, id)" in constraint
            assert connection.execute(
                text(
                    "SELECT to_regclass('public.idx_mentions_pending_trigger') "
                    "IS NOT NULL"
                )
            ).scalar_one()

            with pytest.raises(IntegrityError) as cross_tenant:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO comment_mentions "
                            "(workspace_id, comment_id, mentioned_id, "
                            " triggered_execution_id) VALUES "
                            "(:workspace_id, :comment_id, :member_id, :execution_id)"
                        ),
                        {
                            "workspace_id": workspace_id,
                            "comment_id": ids["cross_tenant_comment"],
                            "member_id": local["member_id"],
                            "execution_id": ids["foreign_execution"],
                        },
                    )
            assert getattr(cross_tenant.value.orig, "sqlstate", None) == "23503"

            with pytest.raises(IntegrityError) as dangling:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO comment_mentions "
                            "(workspace_id, comment_id, mentioned_id, "
                            " triggered_execution_id) VALUES "
                            "(:workspace_id, :comment_id, :member_id, :execution_id)"
                        ),
                        {
                            "workspace_id": workspace_id,
                            "comment_id": ids["cross_tenant_comment"],
                            "member_id": local["member_id"],
                            "execution_id": uuid.uuid4(),
                        },
                    )
            assert getattr(dangling.value.orig, "sqlstate", None) == "23503"

        database_engine.dispose()
        database_engine = None
        command.downgrade(config, "0040")
        database_engine = create_engine(_sync_url(database_url))
        with database_engine.connect() as connection:
            downgraded = {
                row.comment_id: row.triggered_execution_id
                for row in connection.execute(
                    text(
                        "SELECT comment_id, triggered_execution_id "
                        "FROM comment_mentions WHERE workspace_id = :workspace_id"
                    ),
                    {"workspace_id": workspace_id},
                )
            }
            assert downgraded[ids["legacy_comment"]] == ids["published_outbox"]
            assert downgraded[ids["pending_comment"]] == ids["pending_outbox"]
            assert downgraded[ids["canonical_comment"]] == ids["canonical_execution"]
            assert connection.execute(
                text(
                    "SELECT count(*) = 0 FROM information_schema.columns "
                    "WHERE table_name = 'comment_mentions' "
                    "AND column_name = 'pending_trigger_event_id'"
                )
            ).scalar_one()
    finally:
        if database_engine is not None:
            database_engine.dispose()
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
        maintenance_engine.dispose()
