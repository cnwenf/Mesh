#!/usr/bin/env python3
"""docs/specs 事件词汇零漂移校验(README §6.7 注册表 = 唯一权威)。

扫描 docs/specs/**/*.md 中反引号包裹的形如 `<entity>.<action>` 的事件名引用,
与 README §6.7「事件词汇注册表」比对:出现未登记事件名即失败(CI 红)。

判定规则(避免误报):
- 只校验「实体名在注册表实体集合内」的点号 token——实体名不在集合内的点号串
  (如 `mesh.workspace_id` GUC、`users.settings.locale`、`issues.assignee_id` 等
  表列/配置引用,多用复数表名或非事件实体)不视为事件名,跳过;
- outbox/内部 event_type(`issue.assigned`、`execution.enqueue`、`notification.fanout`、
  `data_job.enqueue`、`data_job.resume` 等)不是实时事件名,属 §6.6 领域事件词汇,
  列入 OUTBOX_EVENT_TYPES 白名单(新增此类 event_type 须同步本清单);
- 文件引用(*.md / *.py / *.sql / *.yaml / *.json / *.csv)与代码块内的 SQL 列引用跳过;
- 注册表内的 `error`/`ping` 为 §6.8 流式协议流内帧名,无点号,不参与点号 token 校验。

用法:python3 tests/docs/check_event_vocab.py [specs_dir]
退出码:0 = 全部命中注册表;1 = 存在未登记事件名;2 = 注册表解析失败。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# `<entity>.<action>` 形态的反引号 token(实体/动作均为小写字母与下划线)
EVENT_TOKEN_RE = re.compile(r"`([a-z][a-z_]*\.[a-z][a-z_]*)`")
# README §6.7 注册表表格行内反引号事件名(含流内帧 error/ping)
REGISTRY_ENTRY_RE = re.compile(r"`([a-z][a-z_]*(?:\.[a-z][a-z_]*)?)`")
# 文件扩展名引用(如 integrations.md)不当事件名
FILE_SUFFIX_RE = re.compile(r"\.(md|py|sql|yaml|yml|json|csv|txt|sh)$")

# outbox / 内部领域 event_type(README §6.6/§6.9/§3.x 使用,非 §6.7 实时事件名)
# 新增此类 event_type 时必须同步本清单——这是「领域事件词汇」而非「实时事件词汇」
OUTBOX_EVENT_TYPES = frozenset(
    {
        "issue.assigned",  # §6.6/§7/agent.md §3.3:分派领域事件(outbox event_type)
        "issue.status_changed",  # autopilot.md §3.x:issue 状态变更 outbox event_type(autopilot 事件驱动触发源)
        "execution.enqueue",  # §6.6:执行入队 outbox event_type
        "execution.finished",  # squad.md §4.4:执行终态观察 outbox event_type(squad_task 映射 done/failed)
        "notification.fanout",  # §6.6:通知 fan-out outbox event_type
        "attachment.scan_requested",  # attachment.md §3.x:移交隔离区扫描 outbox event_type
        "data_job.enqueue",  # import-export.md §3.8:数据作业入队
        "data_job.resume",  # import-export.md §3.8:数据作业恢复重投
        "squad.plan_decided",  # squad.md §6.10:计划审批决议 outbox event_type(approve/reject/expired → 根任务流转)
        "chat.generation_finished",  # chat-session.md §4.4:chat 生成终态回写 outbox event_type
    }
)

# 外部平台事件类型(第三方平台的入站事件名,非 Mesh 事件词汇;integrations.md 摄取时归一为内部事件)
EXTERNAL_PLATFORM_EVENTS = frozenset(
    {
        "message.channels",  # Slack Events API 事件类型(integrations.md §1.2/§2.4/§5.2 引用)
    }
)

# schema 引用(table.column)的列名特征:第二段满足其一即视为列引用而非事件名
# (只作用于「未登记」token——已登记事件名先于本规则放行,故 inbox.unread_count 等不受影响)
COLUMN_SUFFIXES = ("_id", "_at", "_by", "_seq", "_ref", "_key", "_tokens", "_count", "_preview", "_status")
COLUMN_EXACT = frozenset(
    {"key", "status", "name", "id", "kind", "scope", "settings", "config", "hash", "position", "role", "version"}
)


def looks_like_column(token: str) -> bool:
    seg = token.split(".", 1)[1]
    return seg in COLUMN_EXACT or seg.endswith(COLUMN_SUFFIXES)

REGISTRY_HEADING_MARKER = "事件词汇注册表(唯一权威"
REGISTRY_END_MARKER = "词汇漂移零容忍"


def parse_registry(readme: Path) -> set[str]:
    """从 README §6.7 注册表段落提取全部登记事件名。"""
    text = readme.read_text(encoding="utf-8")
    start = text.find(REGISTRY_HEADING_MARKER)
    if start == -1:
        raise ValueError("README 中未找到「事件词汇注册表」段落")
    end = text.find(REGISTRY_END_MARKER, start)
    section = text[start : end if end != -1 else len(text)]
    names: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for m in REGISTRY_ENTRY_RE.finditer(line):
            token = m.group(1)
            if "." in token or token in ("error", "ping"):
                names.add(token)
    if not names:
        raise ValueError("注册表段落未解析出任何事件名")
    return names


def scan_spec(md_file: Path, registered: set[str], entities: set[str]) -> list[tuple[int, str]]:
    """返回该文件内未登记的事件名引用 [(行号, token), ...]。"""
    violations: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(md_file.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:  # 代码块内(SQL/JSON 示例)不做词汇校验
            continue
        for m in EVENT_TOKEN_RE.finditer(line):
            token = m.group(1)
            if FILE_SUFFIX_RE.search(token):
                continue
            entity = token.split(".", 1)[0]
            if entity not in entities:
                continue  # 非事件实体(表列/配置/GUC 引用),跳过
            if token in registered or token in OUTBOX_EVENT_TYPES or token in EXTERNAL_PLATFORM_EVENTS:
                continue
            if looks_like_column(token):
                continue  # table.column 形态的 schema 引用(如 autopilot_runs.execution_id)
            violations.append((lineno, token))
    return violations


def main(argv: list[str]) -> int:
    specs_dir = Path(argv[1]) if len(argv) > 1 else Path("docs/specs")
    if not specs_dir.is_dir():
        print(f"error: specs 目录不存在: {specs_dir}", file=sys.stderr)
        return 2
    readme = specs_dir / "README.md"
    try:
        registered = parse_registry(readme)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # 实体集合 = 登记事件名的实体部分(复数表名/非事件实体天然不在集合内)
    entities = {name.split(".", 1)[0] for name in registered if "." in name}

    total_refs = 0
    failed = False
    for md_file in sorted(specs_dir.rglob("*.md")):
        refs = sum(len(EVENT_TOKEN_RE.findall(ln)) for ln in md_file.read_text(encoding="utf-8").splitlines())
        total_refs += refs
        violations = scan_spec(md_file, registered, entities)
        for lineno, token in violations:
            failed = True
            print(f"FAIL {md_file}:{lineno} 未登记事件名 `{token}`(README §6.7 注册表无此名,请先登记再引用)")

    if failed:
        print("\n事件词汇校验失败:存在未登记事件名(README §6.7 为唯一权威)。")
        return 1
    print(
        f"OK 事件词汇校验通过:注册表 {len(registered)} 个事件名,"
        f"扫描 {specs_dir} 下 {sum(1 for _ in specs_dir.rglob('*.md'))} 份 md,无未登记引用。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
