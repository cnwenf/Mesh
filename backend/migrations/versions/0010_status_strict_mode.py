"""issue: 严格模式状态流转载体(issue.md §1.2.3/§3.4/§4.4/§5.2,README §6.14)

Spec §4.4「严格模式可在状态定义上配置『允许的下一步』」的存储载体:

- ``issue_statuses.allowed_transitions JSONB NOT NULL DEFAULT '[]'``:该状态
  「允许的下一步」目标状态 id 列表(JSON 字符串数组,元素为 UUID 字符串)。
  空数组 = 未配置任何允许的下一步。
- 严格模式总开关位于工作区设置 ``workspaces.settings.status_strict_mode``
  (bool,默认 false):关闭 = 任意状态可切任意状态(默认自由流转);开启 =
  仅当前状态 ``allowed_transitions`` 中列出的目标可达,违规 409
  ``invalid_status_transition``(issue.md §3.4 错误码表 / §5.2 验收项)。

Revision ID: 0009
Revises: 0009
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE issue_statuses "
        "ADD COLUMN allowed_transitions JSONB NOT NULL DEFAULT '[]'"
    )
    op.execute(
        "ALTER TABLE issue_statuses ADD CONSTRAINT ck_issue_statuses_allowed_transitions "
        "CHECK (jsonb_typeof(allowed_transitions) = 'array')"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE issue_statuses DROP CONSTRAINT IF EXISTS ck_issue_statuses_allowed_transitions"
    )
    op.execute("ALTER TABLE issue_statuses DROP COLUMN IF EXISTS allowed_transitions")
