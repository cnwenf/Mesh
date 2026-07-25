"""Workspace module — the multi-tenancy isolation root (workspace.md).

Backend core of stage 2: workspace CRUD with slug redirects, the soft
multi-tenancy gates, the invitation lifecycle with redemption separation, and
the RBAC role matrix with audit. Realtime events flow exclusively through the
transactional outbox (README §6.6).
"""
