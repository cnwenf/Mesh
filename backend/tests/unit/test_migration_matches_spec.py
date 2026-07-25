"""Baseline migration must stay in lockstep with the spec validation DDL.

Both files define the same canonical tables; this test fails if the migration
drifts from docs/specs/validation/schema_r2_validation.sql on any contract
clause (unique keys, composite FKs, RLS policies, partial indexes).
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
MIGRATION = BACKEND_DIR / "migrations" / "versions" / "0001_baseline.py"
VALIDATION_SQL = REPO_ROOT / "docs" / "specs" / "validation" / "schema_r2_validation.sql"


def _normalized(text: str) -> str:
    """Collapse whitespace so DDL can be compared across formatting."""
    return re.sub(r"\s+", " ", text)


def _assert_fragment(fragment: str, *, where: str) -> None:
    haystack = _normalized(where)
    assert _normalized(fragment) in haystack, f"missing DDL fragment: {fragment!r}"


def test_baseline_migration_contains_canonical_contract_clauses():
    migration = MIGRATION.read_text(encoding="utf-8")
    clauses = [
        # outbox (§6.6)
        "idempotency_key TEXT NULL UNIQUE",
        "CHECK (status IN ('pending','published','failed'))",
        "CREATE INDEX idx_outbox_pending ON outbox_events (created_at) WHERE status = 'pending'",
        # realtime channels (§6.7)
        "UNIQUE (workspace_id, channel)",
        # realtime events (§6.7 unique write path)
        "UNIQUE (channel, seq)",
        "UNIQUE (outbox_event_id)",
        "FOREIGN KEY (workspace_id, channel) "
        "REFERENCES realtime_channels(workspace_id, channel) ON DELETE CASCADE",
        "CREATE INDEX idx_realtime_events_replay ON realtime_events (channel, seq)",
        "CREATE INDEX idx_realtime_events_ws_created ON realtime_events (workspace_id, created_at)",
        # RLS defense-in-depth (§6.2 rule 5/8)
        "ALTER TABLE realtime_channels ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE realtime_events  ENABLE ROW LEVEL SECURITY",
        "CREATE POLICY mesh_rt_channels_tenant ON realtime_channels",
        "CREATE POLICY mesh_rt_events_tenant ON realtime_events",
        "current_setting('mesh.workspace_id')::uuid",
        # workspaces (workspace.md baseline)
        "settings           JSONB NOT NULL DEFAULT '{\"default_locale\": \"en\"}'",
        "CREATE UNIQUE INDEX uq_workspaces_slug ON workspaces(slug) WHERE deleted_at IS NULL",
    ]
    for clause in clauses:
        _assert_fragment(clause, where=migration)


def test_validation_sql_still_contains_the_same_clauses():
    """Guard the other direction: the spec validation DDL must keep them too."""
    validation = VALIDATION_SQL.read_text(encoding="utf-8")
    for clause in [
        "UNIQUE (outbox_event_id)",
        "UNIQUE (workspace_id, channel)",
        "current_setting('mesh.workspace_id')::uuid",
    ]:
        _assert_fragment(clause, where=validation)
