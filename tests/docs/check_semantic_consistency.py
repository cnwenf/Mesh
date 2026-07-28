#!/usr/bin/env python3
"""docs/specs「语义级一致性」文档校验(MES-76 R2-M2,auth.md §2.5.1/§5.2、theme.md §2.1、search-command-palette.md §3.4)。

背景:既有 check_event_vocab.py / check_roster_entry.py 校验事件词汇与名册入口结构,
但「owner 示例漂移」(默认值语义、令牌前缀与持有者类型绑定、废弃前缀残留、深链执行层
措辞)属于语义级不一致,单纯字符串存在性扫描抓不住。本脚本扫描 docs/specs/**/*.md,
把以下语义漂移判为失败(CI 红):

规则 T(主题默认语义):同一行出现 `settings.theme`(账号主题键)且宣称默认值为
  `system`(如「默认 `system`」「default system」)——与 theme.md §2.1「账号偏好默认
  absent/null = 继承工作区默认;显式 system = 跟随 OS」冲突。工作区键
  `settings.default_theme`(默认 system)不含子串 `settings.theme`,不命中;
  显式标注「显式 system」的行不含「默认」字样,不命中。
规则 F(显示名链):出现 `users.full_name` 但同行无否定标注(无/不存在/已废)——
  users 无 full_name 列(README §6.1、member.md §2.4)。
规则 P(废弃前缀残留):出现 `rt_live_` 但同行无废弃标注(已废/此前/废弃)——
  runtime 令牌前缀唯一为 `mesh_rt_`(auth.md §2.5.1 注册表)。
规则 G(前缀 ⇄ 持有者类型绑定):围栏代码块内同时出现 agent 持有者标记
  (`owner_member_id` 指向 `mem-agent` 或块内注明 agent 运行凭证)与
  `"prefix": "mesh_pat_` / `"token": "mesh_pat_`——agent 成员令牌必须为
  `mesh_agt_` 前缀(auth.md §2.5/§2.5.1 类型语义)。
规则 R(mesh_rt_ 存储真源):同一行出现 `mesh_rt_` 与 `api_tokens.token_hash`——
  runtime 令牌唯一真源为 `runtimes.runtime_token_hash`,不入 api_tokens(R2-H2)。
规则 D(深链执行层措辞):出现「302 语义」——前端旧→新跳转为路由器 replace
  navigation,不得称 302 语义(search-command-palette.md §3.4,R2-M1)。
规则 U(搜索结果 agent URL):JSON 示例中 `"url":` 值含 `/agents/` 且后随 `mem`
  开头的 id(members.id)——成员类结果 URL 必须为 `/members/{member_id}`(R2-M1)。

用法:python3 tests/docs/check_semantic_consistency.py [specs_dir]
退出码:0 = 无语义漂移;1 = 检出漂移;2 = 目录不可读。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

THEME_DEFAULT_SYSTEM_RE = re.compile(r"默认\s*[`'\"]?system[`'\"]?", re.IGNORECASE)
SETTINGS_THEME_RE = re.compile(r"settings\.theme(?![_a-z])")  # 不匹配 settings.default_theme 之外变体
# 规则 T 的语境放行:描述存量迁移/旧实现/否定语义的行(非宣称当前默认 system)
THEME_LEGACY_MARKERS = ("存量", "旧实现", "迁移", "从未选择", "跟随 OS", "不得", "不再")
FULL_NAME_RE = re.compile(r"users\.full_name|`full_name`")
NEGATION_MARKERS = ("无", "不存在", "已废", "没有")
RT_LIVE_RE = re.compile(r"rt_live_")
DEPRECATION_MARKERS = ("已废", "此前", "废弃", "弃用")
MESH_RT_APITOKENS_RE = re.compile(r"mesh_rt_")
APITOKENS_HASH_RE = re.compile(r"api_tokens\.token_hash")
REDIRECT_302_SEMANTICS_RE = re.compile(r"302\s*语义")
# 规则 D 的语境放行:否定/禁止措辞(「不得称 302 语义」正是本规则要求的写法)
REDIRECT_NEGATION_MARKERS = ("不得称", "不得", "不称", "勿称", "禁止")
AGENT_URL_MEMBER_ID_RE = re.compile(r'"url"\s*:\s*"[^"]*/agents/mem[_-]')

FENCE_RE = re.compile(r"^\s*```")
AGENT_HOLDER_RE = re.compile(r"owner_member_id.*mem[-_]agent|agent 运行凭证")
PAT_EXAMPLE_RE = re.compile(r'"(prefix|token)"\s*:\s*"mesh_pat_')


def scan_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"{path}: 无法读取: {exc}"]

    in_fence = False
    fence_block: list[tuple[int, str]] = []

    def check_block(block: list[tuple[int, str]]) -> None:
        text = "\n".join(line for _, line in block)
        if AGENT_HOLDER_RE.search(text) and PAT_EXAMPLE_RE.search(text):
            first_line = block[0][0]
            violations.append(
                f"{path}:{first_line}: 规则 G: agent 持有者令牌示例使用 mesh_pat_ 前缀(应为 mesh_agt_)"
            )

    for lineno, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            if in_fence:
                check_block(fence_block)
                fence_block = []
            in_fence = not in_fence
            continue
        if in_fence:
            fence_block.append((lineno, line))
            continue

        # 规则 T:账号主题键宣称默认 system(存量迁移/否定语境放行)
        if (
            SETTINGS_THEME_RE.search(line)
            and THEME_DEFAULT_SYSTEM_RE.search(line)
            and not any(m in line for m in THEME_LEGACY_MARKERS)
        ):
            violations.append(
                f"{path}:{lineno}: 规则 T: 账号主题 settings.theme 宣称默认 system(应为 absent/null = 继承工作区)"
            )
        # 规则 F:users.full_name 无否定标注
        if FULL_NAME_RE.search(line) and not any(m in line for m in NEGATION_MARKERS):
            violations.append(f"{path}:{lineno}: 规则 F: 引用 users.full_name 但无否定标注(users 无此列)")
        # 规则 P:rt_live_ 无废弃标注
        if RT_LIVE_RE.search(line) and not any(m in line for m in DEPRECATION_MARKERS):
            violations.append(f"{path}:{lineno}: 规则 P: 废弃前缀 rt_live_ 残留(唯一 runtime 前缀为 mesh_rt_)")
        # 规则 R:mesh_rt_ 与 api_tokens.token_hash 同行
        if MESH_RT_APITOKENS_RE.search(line) and APITOKENS_HASH_RE.search(line):
            violations.append(
                f"{path}:{lineno}: 规则 R: mesh_rt_ 令牌指向 api_tokens.token_hash(唯一真源为 runtimes.runtime_token_hash)"
            )
        # 规则 D:「302 语义」措辞(否定语境放行)
        if REDIRECT_302_SEMANTICS_RE.search(line) and not any(
            m in line for m in REDIRECT_NEGATION_MARKERS
        ):
            violations.append(f"{path}:{lineno}: 规则 D: 出现「302 语义」措辞(应为路由器 replace navigation)")
        # 规则 U:搜索结果 agent URL 用 /agents/ + members.id
        if AGENT_URL_MEMBER_ID_RE.search(line):
            violations.append(
                f"{path}:{lineno}: 规则 U: 成员类(members.id)结果 URL 使用 /agents/(应为 /members/{{member_id}})"
            )

    if in_fence and fence_block:
        check_block(fence_block)
    return violations


def main() -> int:
    specs_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/specs")
    if not specs_dir.is_dir():
        print(f"ERROR: specs 目录不可读: {specs_dir}", file=sys.stderr)
        return 2

    md_files = sorted(specs_dir.rglob("*.md"))
    all_violations: list[str] = []
    for md in md_files:
        all_violations.extend(scan_file(md))

    if all_violations:
        print(f"FAIL 语义级一致性校验发现 {len(all_violations)} 处漂移:", file=sys.stderr)
        for violation in all_violations:
            print(f"  {violation}", file=sys.stderr)
        return 1

    print(f"OK 语义级一致性校验通过:扫描 {specs_dir} 下 {len(md_files)} 份 md,无语义漂移。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
