#!/usr/bin/env python3
"""docs/specs「成员名册唯一入口」文档结构校验(agent.md §4.2/§5.1,README §6.12,T35)。

背景:agent 的创建入口与名册唯一为成员名册页(README §6.12「Agents 入口去重」)。
R4 已把创建入口收敛到成员名册,但独立「Agents」列表页(页面标题 `Agents` + 自带
`[+ 新建]`)一度回潮,形成第二套名册与第二个创建入口。本脚本扫描 docs/specs/**/*.md,
把以下三类独立 Agents 名册回归判为失败(CI 红):

规则 A(线框图):同一围栏代码块内,页面标题单元恰为 `Agents` 的线框标题行,
  与不带 Agent 后缀的创建按钮 `[+ 新建]` / `[ + 新建 ]` / `[ 新建 ]` 同块出现
  ——即「独立 Agents 列表页 + 第二创建入口」的线框形态。成员名册页线框
  (标题「成员 Members」+ `[ + 新建 Agent ]`)不命中。
规则 B(正文):出现「Agent 列表页 / Agents 列表页」表述,且同一行未携带
  否定或投影标注(「筛选投影 / 不存在 / 不是 / 不维护 / 不再 / 不得 / 禁止 /
  无独立 / 不构成 / 非独立」)——即未声明为成员名册投影的独立页面表述。
规则 C(导航 / 信息架构图):围栏代码块内同一行同时出现 `Agents` 与不带 Agent
  后缀的创建按钮(该行不含「成员」/「筛选投影」标注)——导航图里的第二入口。

用法:python3 tests/docs/check_roster_entry.py [specs_dir]
退出码:0 = 无独立 Agents 名册回归;1 = 检出回归;2 = 目录不可读。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 不带 Agent 后缀的创建按钮:`[+ 新建]` / `[ + 新建 ]` / `[ 新建 ]`(成员页入口为 `[ + 新建 Agent ]`,不命中)
CREATE_BTN_RE = re.compile(r"[\[【]\s*[+＋]?\s*新建\s*[\]】]")
# 线框标题行:页面标题单元恰为 `Agents`(如 `│  Agents ... │` / `┌ Agents ┐`)
WIREFRAME_TITLE_RE = re.compile(r"[│|┌└├]\s*Agents\s")
# 导航 / 线框行内出现的 Agents 词(规则 C)
AGENTS_WORD_RE = re.compile(r"\bAgents\b|Agents(?=[\s（(：:」】])")
# 正文中的「Agent(s) 列表页」表述(允许全角引号夹在中间,如「Agents」列表页)
ROSTER_PAGE_PHRASE_RE = re.compile(r"Agents?\s*[」」]?\s*列表页")
# 同行出现即视为「已声明为投影 / 否定独立页面」的标注词
ALLOW_MARKERS = (
    "筛选投影",
    "不存在",
    "不是",
    "不维护",
    "不再",
    "不得",
    "禁止",
    "无独立",
    "不构成",
    "非独立",
)
# 规则 C 的行内放行标注(成员页投影上下文)
ROW_ALLOW_MARKERS = ("成员", "筛选投影")


def scan_spec(md_file: Path) -> list[tuple[int, str, str]]:
    """返回该文件的回归清单 [(行号, 规则, 摘要), ...]。"""
    violations: list[tuple[int, str, str]] = []
    lines = md_file.read_text(encoding="utf-8").splitlines()

    # 围栏代码块切分:记录每个块的 (起始行号, 行列表) 与块外正文行
    blocks: list[tuple[int, list[str]]] = []
    in_fence = False
    block_start = 0
    block_lines: list[str] = []
    for lineno, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            if in_fence:
                blocks.append((block_start, block_lines))
                block_lines = []
            else:
                block_start = lineno
            in_fence = not in_fence
            continue
        if in_fence:
            block_lines.append(line)

    # 规则 A:同一代码块内「Agents 标题线框 + 不带 Agent 后缀的创建按钮」
    for start, block in blocks:
        title_hits = [
            i for i, ln in enumerate(block) if WIREFRAME_TITLE_RE.search(ln)
        ]
        btn_hits = [i for i, ln in enumerate(block) if CREATE_BTN_RE.search(ln)]
        if title_hits and btn_hits:
            violations.append(
                (
                    start + btn_hits[0],
                    "A",
                    "线框图页面标题为 `Agents` 且带 [+ 新建](无 Agent 后缀)"
                    "——独立 Agents 列表页 + 第二创建入口(应为成员名册页的「仅 Agent」筛选投影)",
                )
            )

    # 规则 C:导航 / 架构图同一行 Agents + 创建按钮(无成员 / 投影标注)
    for start, block in blocks:
        for i, ln in enumerate(block):
            if (
                AGENTS_WORD_RE.search(ln)
                and CREATE_BTN_RE.search(ln)
                and not any(mk in ln for mk in ROW_ALLOW_MARKERS)
            ):
                violations.append(
                    (
                        start + i,
                        "C",
                        "导航 / 信息架构图中 Agents 行携带 [+ 新建] 入口"
                        "(第二创建入口;agent 创建入口唯一为成员名册页)",
                    )
                )

    # 规则 B:正文「Agent 列表页」表述未同行声明为投影 / 否定
    in_fence = False
    for lineno, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if ROSTER_PAGE_PHRASE_RE.search(line) and not any(
            mk in line for mk in ALLOW_MARKERS
        ):
            violations.append(
                (
                    lineno,
                    "B",
                    "「Agent 列表页」表述未声明为成员名册页的筛选投影"
                    "(同行须含「筛选投影 / 不存在 / 不维护」等标注)",
                )
            )

    return sorted(violations, key=lambda v: v[0])


def main(argv: list[str]) -> int:
    specs_dir = Path(argv[1]) if len(argv) > 1 else Path("docs/specs")
    if not specs_dir.is_dir():
        print(f"error: specs 目录不存在: {specs_dir}", file=sys.stderr)
        return 2

    failed = False
    md_count = 0
    for md_file in sorted(specs_dir.rglob("*.md")):
        md_count += 1
        for lineno, rule, summary in scan_spec(md_file):
            failed = True
            print(f"FAIL {md_file}:{lineno} [规则{rule}] {summary}")

    if failed:
        print(
            "\n成员名册唯一入口校验失败:检出独立 Agents 名册 / 第二创建入口回归"
            "(agent.md §4.2/§5.1、README §6.12 为唯一权威)。"
        )
        return 1
    print(
        f"OK 成员名册唯一入口校验通过:扫描 {specs_dir} 下 {md_count} 份 md,"
        "无独立 Agents 列表页 / 第二创建入口回归。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
