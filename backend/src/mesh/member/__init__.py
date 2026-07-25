"""Member module — the unified roster feature layer (member.md owns the spec).

Builds roster CRUD, display-name resolution, role/status management and guest
project access on top of the ``members`` / ``member_project_access`` tables
(created by the workspace increment, ``0004_workspace.py``). ``members.id`` is
the system-wide reference key (README §6.1); every reference site (assignee,
author, mention, recipient) points here.
"""
