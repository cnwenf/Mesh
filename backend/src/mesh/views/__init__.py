"""Kanban views module — the saved-projection definition layer (kanban.md).

Owns the ``views`` table and its REST surface: CRUD, config PATCH (shallow
JSONB merge), WIP limit config, sidebar reorder and the ``view.updated``
realtime event through the outbox single write path. The issue-coupled
projection (executing a view's filters against issues, per-view card
positions, atomic moves) lives in the later increment.
"""
