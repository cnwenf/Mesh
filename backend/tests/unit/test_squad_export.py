"""Squad markdown export renderer tests (squad.md §4.5 parity L486).

Pure-function coverage: section structure, kind labels, related-task tags,
empty sections, NUL stripping, chronological assembly.
"""

from __future__ import annotations

import pytest

from mesh.squad.export import render_squad_export

pytestmark = pytest.mark.unit

SQUAD = {
    "id": "sq-1",
    "name": "支付重构",
    "status": "active",
    "kind": "standing",
    "description": "重构支付链路",
    "member_count": 3,
    "primary_leader": {"id": "m-1", "name": "Leader A"},
}

EXPORTED_AT = "2026-08-07T00:00:00+00:00"


def test_header_contains_identity_and_leader():
    doc = render_squad_export(
        squad=SQUAD, tasks=[], messages=[], activity=[], exported_at=EXPORTED_AT
    )
    assert "# 小队归档：支付重构" in doc
    assert "`sq-1`" in doc
    assert "Leader A" in doc
    assert "导出时间(UTC):2026-08-07T00:00:00Z" in doc


def test_empty_sections_render_placeholders():
    doc = render_squad_export(
        squad=SQUAD, tasks=[], messages=[], activity=[], exported_at=EXPORTED_AT
    )
    assert "## 任务清单（0）" in doc
    assert "（无任务）" in doc
    assert "（无消息）" in doc
    assert "（无活动记录）" in doc


def test_tasks_render_status_issue_and_result():
    tasks = [
        {
            "title": "对账批处理",
            "status": "done",
            "issue_id": "i-1",
            "issue_identifier": "WS-42",
            "assignee": {"id": "m-2", "name": "Agent B"},
            "created_at": "2026-08-01T00:00:00+00:00",
            "started_at": "2026-08-01T01:00:00+00:00",
            "finished_at": "2026-08-01T02:00:00+00:00",
            "failure_reason": None,
            "result_summary": "三笔差异已修复",
        }
    ]
    doc = render_squad_export(
        squad=SQUAD, tasks=tasks, messages=[], activity=[], exported_at=EXPORTED_AT
    )
    assert "### 对账批处理" in doc
    assert "状态:done" in doc
    assert "Issue:WS-42" in doc
    assert "执行者:Agent B" in doc
    assert "**结果摘要：**" in doc
    assert "三笔差异已修复" in doc


def test_messages_carry_kind_labels_and_task_tags():
    messages = [
        {
            "kind": "instruction",
            "task_id": "t-9",
            "sender": {"id": "m-1", "name": "Leader A"},
            "recipient": {"id": "m-2", "name": "Agent B"},
            "body_markdown": "先做幂等",
            "created_at": "2026-08-01T00:00:00+00:00",
        },
        {
            "kind": "report",
            "task_id": "t-9",
            "sender": {"id": "m-2", "name": "Agent B"},
            "recipient": None,
            "body_markdown": "完成",
            "created_at": "2026-08-01T01:00:00+00:00",
        },
        {
            "kind": "system",
            "task_id": None,
            "sender": None,
            "recipient": None,
            "body_markdown": "任务创建",
            "created_at": "2026-08-01T02:00:00+00:00",
        },
    ]
    doc = render_squad_export(
        squad=SQUAD, tasks=[], messages=messages, activity=[], exported_at=EXPORTED_AT
    )
    assert "## 任务消息（3）" in doc
    assert "【指令】Leader A → Agent B（关联任务: `t-9`）" in doc
    assert "【汇报】Agent B（关联任务: `t-9`）" in doc
    assert "【系统】系统" in doc
    assert "  > 先做幂等" in doc


def test_timeline_rows_render_actor_and_action():
    activity = [
        {
            "created_at": "2026-08-01T00:00:00+00:00",
            "actor": {"id": "m-1", "name": "Leader A"},
            "action": "task_dispatched",
            "task_id": "t-9",
            "target_id": None,
        }
    ]
    doc = render_squad_export(
        squad=SQUAD, tasks=[], messages=[], activity=activity, exported_at=EXPORTED_AT
    )
    assert "## 时间线（1）" in doc
    assert "Leader A task_dispatched（任务 `t-9`）" in doc


def test_nul_characters_are_stripped_and_doc_ends_with_newline():
    squad = {**SQUAD, "description": "bad\x00desc"}
    doc = render_squad_export(
        squad=squad, tasks=[], messages=[], activity=[], exported_at=EXPORTED_AT
    )
    assert "\x00" not in doc
    assert doc.endswith("\n")
