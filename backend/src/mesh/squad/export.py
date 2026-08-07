"""Squad archive export — task messages + timeline as a markdown document.

Renders the squad's durable record (header, task list with outcomes, message
log with kind markers and related-task tags, activity timeline) into a single
markdown archive (squad.md §4.5 parity item L486). Pure string building — no
I/O — so the format is unit-testable without a database.

Bodies are already markdown source (``body_markdown`` is the stored truth),
so they are embedded verbatim; the export is a document, never rendered as
HTML, and carries no executable content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# §4.2 kind labels: instruction/report carry a related-task tag in the UI;
# the archive marks every row with its kind for grep-ability.
_KIND_LABELS = {
    "instruction": "指令",
    "report": "汇报",
    "chat": "闲聊",
    "system": "系统",
    "context": "上下文",
}


def _ts(value: str | None) -> str:
    if not value:
        return "—"
    # Stored ISO-8601 carries offset; keep the raw value (UTC-truth, §6.18).
    return value.replace("+00:00", "Z")


def _name(snapshot: Mapping | None) -> str:
    if not snapshot:
        return "未知成员"
    return str(snapshot.get("name") or snapshot.get("id") or "未知成员")


def _task_ref(message: Mapping) -> str:
    task_id = message.get("task_id")
    return f"（关联任务: `{task_id}`）" if task_id else ""


def _render_header(squad: Mapping, exported_at: str) -> list[str]:
    lines = [
        f"# 小队归档：{squad.get('name')}",
        "",
        f"- 小队 ID：`{squad.get('id')}`",
        f"- 状态:{squad.get('status')}｜形态:{squad.get('kind')}",
    ]
    description = squad.get("description")
    if description:
        lines.append(f"- 描述:{description}")
    leader = squad.get("primary_leader")
    if leader:
        lines.append(f"- Leader:{_name(leader)}")
    lines.extend(
        [
            f"- 成员数:{squad.get('member_count')}",
            f"- 导出时间(UTC):{_ts(exported_at)}",
            "",
        ]
    )
    return lines


def _render_tasks(tasks: Sequence[Mapping]) -> list[str]:
    lines = [f"## 任务清单（{len(tasks)}）", ""]
    if not tasks:
        lines.extend(["（无任务）", ""])
        return lines
    for task in tasks:
        lines.append(f"### {task.get('title') or '(未命名任务)'}")
        lines.append("")
        lines.append(f"- 状态:{task.get('status')}")
        if task.get("issue_identifier"):
            lines.append(f"- Issue:{task['issue_identifier']}（`{task.get('issue_id')}`）")
        if task.get("assignee"):
            lines.append(f"- 执行者:{_name(task['assignee'])}")
        if task.get("created_at"):
            lines.append(f"- 创建:{_ts(task.get('created_at'))}")
        if task.get("started_at"):
            lines.append(f"- 开始:{_ts(task.get('started_at'))}")
        if task.get("finished_at"):
            lines.append(f"- 结束:{_ts(task.get('finished_at'))}")
        if task.get("failure_reason"):
            lines.append(f"- 失败原因:{task['failure_reason']}")
        if task.get("result_summary"):
            lines.extend(["", "**结果摘要：**", "", str(task["result_summary"]), ""])
        lines.append("")
    return lines


def _render_messages(messages: Sequence[Mapping]) -> list[str]:
    lines = [f"## 任务消息（{len(messages)}）", ""]
    if not messages:
        lines.extend(["（无消息）", ""])
        return lines
    for message in messages:
        kind = message.get("kind") or "chat"
        label = _KIND_LABELS.get(kind, kind)
        sender = _name(message.get("sender")) if message.get("sender") else "系统"
        recipient = message.get("recipient")
        arrow = f" → {_name(recipient)}" if recipient else ""
        lines.append(
            f"- [{_ts(message.get('created_at'))}]【{label}】{sender}{arrow}"
            f"{_task_ref(message)}"
        )
        body = str(message.get("body_markdown") or "")
        if body:
            # Indent the body as a quoted block so multi-line markdown keeps
            # its shape without breaking the list structure.
            for body_line in body.splitlines() or [""]:
                lines.append(f"  > {body_line}")
        lines.append("")
    return lines


def _render_timeline(activity: Sequence[Mapping]) -> list[str]:
    lines = [f"## 时间线（{len(activity)}）", ""]
    if not activity:
        lines.extend(["（无活动记录）", ""])
        return lines
    for row in activity:
        actor = _name(row.get("actor")) if row.get("actor") else "系统"
        target = f" `{row.get('target_id')}`" if row.get("target_id") else ""
        task = f"（任务 `{row.get('task_id')}`）" if row.get("task_id") else ""
        lines.append(
            f"- [{_ts(row.get('created_at'))}] {actor} {row.get('action')}"
            f"{target}{task}"
        )
    lines.append("")
    return lines


def render_squad_export(
    *,
    squad: Mapping,
    tasks: Sequence[Mapping],
    messages: Sequence[Mapping],
    activity: Sequence[Mapping],
    exported_at: str,
) -> str:
    """Compose the full markdown archive (NUL-free, trailing newline)."""
    lines: list[str] = []
    lines.extend(_render_header(squad, exported_at))
    lines.extend(_render_tasks(tasks))
    lines.extend(_render_messages(messages))
    lines.extend(_render_timeline(activity))
    return "\n".join(lines).replace("\x00", "") + "\n"


__all__ = ["render_squad_export"]
