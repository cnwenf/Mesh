#!/usr/bin/env python3
"""docs/specs「语义级一致性」文档校验(MES-76 R2-M2 建立,R3-M2 扩充:覆盖权威 .sql + 坏样例自测)。

背景:check_event_vocab.py / check_roster_entry.py 校验事件词汇与名册入口结构,
但「owner 示例漂移」(默认值语义、令牌前缀与持有者类型绑定、废弃前缀残留、深链执行层
措辞、验证 SQL 与模型背离)属于语义级不一致,单纯字符串存在性扫描抓不住。本脚本扫描
docs/specs/**/*.md 与 docs/specs/**/*.sql(含 validation 权威 SQL),把以下语义漂移判为
失败(CI 红):

规则 T(主题默认语义):同一行出现 `settings.theme`(账号主题键)且宣称默认值为
  `system`——与 theme.md §2.1「账号偏好默认 absent/null = 继承工作区默认;显式
  system = 跟随 OS」冲突。存量迁移/否定语境(存量/旧实现/迁移/跟随 OS 等)放行。
规则 F(显示名链):出现 `users.full_name` / `full_name` 列但同行无否定标注
  (无/不存在/已废/没有)——users 无 full_name 列(README §6.1、auth.md §2.2、
  member.md §2.4)。**同时覆盖验证 SQL**(R3-M2:此前漏掉验证脚本中的 full_name DDL)。
规则 P(废弃前缀残留):出现 `rt_live_` 但同行无废弃标注(已废/此前/废弃/弃用)——
  runtime 令牌前缀唯一为 `mesh_rt_`(auth.md §2.5.1 注册表)。
规则 G(前缀 ⇄ 持有者类型绑定):围栏代码块内同时出现 agent 持有者标记
  (`owner_member_id` 指向 `mem-agent` 或块内注明 agent 运行凭证)与
  `"prefix": "mesh_pat_` / `"token": "mesh_pat_`——agent 成员令牌必须为
  `mesh_agt_` 前缀(auth.md §2.5/§2.5.1 类型语义)。
规则 R(mesh_rt_ 存储真源):同一行出现 `mesh_rt_` 与 `api_tokens.token_hash`——
  runtime 令牌唯一真源为 `runtimes.runtime_token_hash`,不入 api_tokens(R2-H2)。
规则 D(深链执行层措辞):出现「302 语义」——前端旧→新跳转为路由器 replace
  navigation,不得称 302 语义(search-command-palette.md §3.4,R2-M1);否定语境放行。
规则 U(搜索结果 agent URL):JSON 示例中 `"url":` 值含 `/agents/` 且后随 `mem`
  开头的 id(members.id)——成员类结果 URL 必须为 `/members/{member_id}`(R2-M1)。
规则 V(runtime 令牌停用经 api_tokens):同一行出现 runtime 令牌词(runtime_token /
  mesh_rt_)+ `api_tokens` + `revoked_at`——runtime 令牌停用为 runtime 状态 +
  `runtimes.runtime_token_hash` 清除/轮换,不经 api_tokens(R3-H4;此前漏掉
  runtime.md §3.5「停用 token → api_tokens.revoked_at」残留)。
规则 W(CLI/runtime 环境变量并列):出现「CLI/runtime」(斜杠并列)——runtime 机器令牌
  不经 api_tokens / CLI 环境变量通道(与 mesh_rt_ 唯一真源冲突,R4-M3;此前 auth.md
  §4.5/§5.5「API token 由 CLI/runtime 从环境变量读取」残留未被旧规则捕获)。
规则 X(CSRF token 残留):同一行出现「CSRF token」+ SameSite/cookie 会话——Web 会话
  CSRF 防护契约为 SameSite=Strict + Origin/Referer 同源校验,无独立 CSRF token
  (R4-M3;否定语境「无独立 CSRF token」等放行)。
规则 Y(登录响应体 refresh 残留):`/auth/login` 行或「登录…返回」行出现 refresh 且无
  「仅经/Set-Cookie/绝不/不含/无」标记——密码登录仅 Web cookie-only,响应体绝无
  refresh 明文(R4-H1/R4-M3)。
规则 Z(auth.md 文件级:sessions 登记表完整性):auth.md 必须含 sessions-registry
  标记块,且块内必需条目齐全(login/logout-all/refresh/token/reset-password/
  change-password/sessions/WS 握手/HTML 入口)——白名单不闭合会使 logout-all、
  reset-password、个性化 HTML 入口等已登记端点/首帧链路无法实现(R5-H2)。
规则 AA(validation SQL 文件级:T38 pg_depend 断言):schema_r2_validation.sql 含 T38
  则必须含 pg_depend + refobjid 逐条 OID 绑定断言——否则 9 条函数表达式索引迁移
  未被真正验证(R5-H3)。

**坏样例自测(R3-M2)**:每条规则携带一条注入坏样例,每次运行时先对坏样例语料执行
全部规则,断言每条规则**必然命中**——避免「绿灯只证明正则没命中」(规则写坏/表达式
漂移导致永久静默)。自测失败 = 脚本自身缺陷,退出 1。

用法:python3 tests/docs/check_semantic_consistency.py [specs_dir]
退出码:0 = 自测通过且无语义漂移;1 = 检出漂移或自测失败;2 = 目录不可读。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ----------------------------- 规则表达式 -----------------------------
THEME_DEFAULT_SYSTEM_RE = re.compile(r"默认\s*[`'\"]?system[`'\"]?", re.IGNORECASE)
SETTINGS_THEME_RE = re.compile(r"settings\.theme(?![_a-z])")  # 不匹配 settings.default_theme
# 规则 T 的语境放行:描述存量迁移/旧实现/否定语义的行(非宣称当前默认 system)
THEME_LEGACY_MARKERS = ("存量", "旧实现", "迁移", "从未选择", "跟随 OS", "不得", "不再")

FULL_NAME_RE = re.compile(r"users\.full_name|(?<![\w.])full_name(?![\w])")
NEGATION_MARKERS = ("无", "不存在", "已废", "没有")

RT_LIVE_RE = re.compile(r"rt_live_")
DEPRECATION_MARKERS = ("已废", "此前", "废弃", "弃用")

MESH_RT_RE = re.compile(r"mesh_rt_")
APITOKENS_HASH_RE = re.compile(r"api_tokens\.token_hash")

REDIRECT_302_SEMANTICS_RE = re.compile(r"302\s*语义")
# 规则 D 的语境放行:否定/禁止措辞(「不得称 302 语义」正是本规则要求的写法)
REDIRECT_NEGATION_MARKERS = ("不得称", "不得", "不称", "勿称", "禁止")

AGENT_URL_MEMBER_ID_RE = re.compile(r'"url"\s*:\s*"[^"]*/agents/mem[_-]')

RUNTIME_TOKEN_WORD_RE = re.compile(r"runtime_token|mesh_rt_")
REVOKED_AT_RE = re.compile(r"revoked_at")
APITOKENS_RE = re.compile(r"api_tokens")

CLI_SLASH_RUNTIME_RE = re.compile(r"CLI\s*/\s*runtime", re.IGNORECASE)

CSRF_TOKEN_RE = re.compile(r"CSRF\s*token", re.IGNORECASE)
CSRF_CONTEXT_RE = re.compile(r"SameSite|cookie 会话")
CSRF_NEGATION_MARKERS = ("无独立", "不再用", "不需要", "无 CSRF token", "不提供")

LOGIN_REFRESH_RE = re.compile(r"auth/login|登录[\s\S]{0,12}返回")
REFRESH_WORD_RE = re.compile(r"refresh", re.IGNORECASE)
LOGIN_REFRESH_ALLOW_MARKERS = ("仅经", "Set-Cookie", "绝不", "不含", "绝无", "无 refresh", "只有 access")

FENCE_RE = re.compile(r"^\s*```")
AGENT_HOLDER_RE = re.compile(r"owner_member_id.*mem[-_]agent|agent 运行凭证")
PAT_EXAMPLE_RE = re.compile(r'"(prefix|token)"\s*:\s*"mesh_pat_')


def scan_lines(lines: list[str], source: str) -> list[str]:
    """对一组行(文件或注入样例)执行全部规则,返回违规描述。"""
    violations: list[str] = []
    in_fence = False
    fence_block: list[tuple[int, str]] = []

    def check_block(block: list[tuple[int, str]]) -> None:
        text = "\n".join(line for _, line in block)
        if AGENT_HOLDER_RE.search(text) and PAT_EXAMPLE_RE.search(text):
            first_line = block[0][0]
            violations.append(f"{source}:{first_line}: 规则 G: agent 持有者令牌示例使用 mesh_pat_ 前缀(应为 mesh_agt_)")

    for lineno, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            if in_fence:
                check_block(fence_block)
                fence_block = []
            in_fence = not in_fence
            continue
        if in_fence:
            fence_block.append((lineno, line))
            # 围栏内同样执行行级规则(验证 SQL 全文不在围栏内,md 的 SQL 块亦需覆盖)
        # 规则 T:账号主题键宣称默认 system(存量迁移/否定语境放行)
        if (
            SETTINGS_THEME_RE.search(line)
            and THEME_DEFAULT_SYSTEM_RE.search(line)
            and not any(m in line for m in THEME_LEGACY_MARKERS)
        ):
            violations.append(f"{source}:{lineno}: 规则 T: 账号主题 settings.theme 宣称默认 system(应为 absent/null = 继承工作区)")
        # 规则 F:full_name 无否定标注
        if FULL_NAME_RE.search(line) and not any(m in line for m in NEGATION_MARKERS):
            violations.append(f"{source}:{lineno}: 规则 F: 引用 full_name 但无否定标注(users 无此列,显示名链为 display_name → email)")
        # 规则 P:rt_live_ 无废弃标注
        if RT_LIVE_RE.search(line) and not any(m in line for m in DEPRECATION_MARKERS):
            violations.append(f"{source}:{lineno}: 规则 P: 废弃前缀 rt_live_ 残留(唯一 runtime 前缀为 mesh_rt_)")
        # 规则 R:mesh_rt_ 与 api_tokens.token_hash 同行
        if MESH_RT_RE.search(line) and APITOKENS_HASH_RE.search(line):
            violations.append(f"{source}:{lineno}: 规则 R: mesh_rt_ 令牌指向 api_tokens.token_hash(唯一真源为 runtimes.runtime_token_hash)")
        # 规则 D:「302 语义」措辞(否定语境放行)
        if REDIRECT_302_SEMANTICS_RE.search(line) and not any(m in line for m in REDIRECT_NEGATION_MARKERS):
            violations.append(f"{source}:{lineno}: 规则 D: 出现「302 语义」措辞(应为路由器 replace navigation)")
        # 规则 U:搜索结果 agent URL 用 /agents/ + members.id
        if AGENT_URL_MEMBER_ID_RE.search(line):
            violations.append(f"{source}:{lineno}: 规则 U: 成员类(members.id)结果 URL 使用 /agents/(应为 /members/{{member_id}})")
        # 规则 V:runtime 令牌停用经 api_tokens.revoked_at(「mesh_rt_ 不入 api_tokens」等否定语境放行)
        if (
            RUNTIME_TOKEN_WORD_RE.search(line)
            and APITOKENS_RE.search(line)
            and REVOKED_AT_RE.search(line)
            and not any(m in line for m in ("不入", "不经", "不进", "无该行", "不共"))
        ):
            violations.append(f"{source}:{lineno}: 规则 V: runtime 令牌停用指向 api_tokens.revoked_at(应为 runtime 状态 + runtime_token_hash 清除/轮换)")
        # 规则 W:CLI/runtime 环境变量并列(runtime 令牌不经 CLI 环境变量通道)
        if CLI_SLASH_RUNTIME_RE.search(line):
            violations.append(f"{source}:{lineno}: 规则 W: 「CLI/runtime」并列残留(runtime 机器令牌 mesh_rt_ 不经 api_tokens / CLI 环境变量通道)")
        # 规则 X:CSRF token 残留(新契约为 SameSite + Origin/Referer,否定语境放行)
        if (
            CSRF_TOKEN_RE.search(line)
            and CSRF_CONTEXT_RE.search(line)
            and not any(m in line for m in CSRF_NEGATION_MARKERS)
        ):
            violations.append(f"{source}:{lineno}: 规则 X: 「CSRF token」残留(Web 会话 CSRF 防护为 SameSite=Strict + Origin/Referer 同源校验,无独立 CSRF token)")
        # 规则 Y:登录响应体 refresh 残留(密码登录仅 Web cookie-only,响应体绝无 refresh)
        if (
            LOGIN_REFRESH_RE.search(line)
            and REFRESH_WORD_RE.search(line)
            and not any(m in line for m in LOGIN_REFRESH_ALLOW_MARKERS)
        ):
            violations.append(f"{source}:{lineno}: 规则 Y: 登录端点/描述出现响应体 refresh(密码登录仅 Web cookie-only,refresh 仅经 Set-Cookie 下发,绝不进响应体)")

    if in_fence and fence_block:
        check_block(fence_block)
    return violations


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: 无法读取: {exc}"]
    return scan_lines(text.splitlines(), str(path)) + scan_file_level(path, text)


# --------------------- 文件级语义规则(R5-H2/R5-H3)---------------------
SESSIONS_REGISTRY_REQUIRED = (
    "auth/login",
    "logout-all",
    "auth/refresh",
    "auth/token",
    "reset-password",
    "change-password",
    "/sessions",
    "握手",          # WS 握手鉴权
    "HTML 入口",     # 个性化 HTML 入口中间件
)


def check_sessions_registry(text: str) -> list[str]:
    """auth.md:sessions 生命周期操作登记表完整性(R5-H2:标记块 + 必需路径齐全)。"""
    violations: list[str] = []
    start = text.find("<!-- sessions-registry:start -->")
    end = text.find("<!-- sessions-registry:end -->")
    if start < 0 or end < 0 or end < start:
        violations.append("auth.md: 规则 Z: 缺少 sessions-registry 标记块(会话生命周期操作登记表未登记或不完整)")
        return violations
    block = text[start:end]
    for required in SESSIONS_REGISTRY_REQUIRED:
        if required not in block:
            violations.append(f"auth.md: 规则 Z: sessions 登记表缺少必需条目「{required}」(白名单不闭合会使已登记端点/首帧链路无法实现)")
    return violations


def check_t38_pg_depend(text: str) -> list[str]:
    """schema_r2_validation.sql:T38 必须含 pg_depend 逐条 OID 绑定断言(R5-H3)。"""
    if "T38" in text and ("pg_depend" not in text or "refobjid" not in text):
        return ["schema_r2_validation.sql: 规则 AA: T38 缺少 pg_depend/refobjid 逐条 OID 绑定断言(9 条函数表达式索引迁移未被真正验证)"]
    return []


def scan_file_level(path: Path, text: str) -> list[str]:
    name = path.name
    if name == "auth.md":
        return check_sessions_registry(text)
    if name == "schema_r2_validation.sql":
        return check_t38_pg_depend(text)
    return []


# ----------------------------- 坏样例自测(R3-M2) -----------------------------
# 每条规则一条注入坏样例:脚本每次运行先断言全部规则命中坏样例,
# 防止规则表达式写坏/漂移后永久静默(绿灯只证明正则没命中)。
SELF_TEST_BAD_LINES = {
    "规则 T": "| 账号主题 | `users.settings.theme` | string | 默认 `system` | auth.md | 校验 |",
    "规则 F": "  full_name TEXT NULL,",
    "规则 P": "runtime 令牌前缀为 rt_live_a1b2,激活时写入。",
    "规则 R": "`mesh_rt_` 令牌存储于 `api_tokens.token_hash`(SHA-256)。",
    "规则 D": "既有扁平路由保留为应用内别名,访问时 replaceState(302 语义)至规范路由。",
    "规则 U": '{ "type": "agent", "id": "mem_b2", "url": "/w/acme/agents/mem_b2" }',
    "规则 V": "runtime 进入 paused 时停用其 runtime_token(api_tokens.revoked_at 置位)。",
    "规则 W": "- [ ] 防 XSS 窃取:refresh 优先 httpOnly + Secure cookie,access 放内存;API token 由 CLI/runtime 从环境变量读取。",
    "规则 X": "- [ ] 防 CSRF:OAuth 用 state + PKCE;cookie 会话用 `SameSite` + CSRF token。",
    "规则 Y": "| POST | `/api/v1/auth/login` | 登录,返回 access + refresh | ✅ |",
}
SELF_TEST_BAD_BLOCK = {
    "规则 G": [
        "```json",
        '// Request(agent 运行凭证:owner_member_id 指向该 agent 的 member 行)',
        '{ "name": "x", "owner_member_id": "mem-agent-1" }',
        '// Response',
        '{ "data": { "prefix": "mesh_pat_Ab3", "token": "mesh_pat_Ab3Xy9..." } }',
        "```",
    ],
}


SELF_TEST_BAD_FILES = {
    "规则 Z": (
        "auth.md",
        # 登记表缺 logout-all 条目 → 必须失败
        "前言\n<!-- sessions-registry:start -->\n| POST /auth/login | 写:创建会话 |\n"
        "| POST /auth/refresh | 读+写:轮换 |\n<!-- sessions-registry:end -->\n后记",
    ),
    "规则 Z(无标记块)": (
        "auth.md",
        "| POST /auth/logout-all | 批量撤销 |  # 登记表在标记块外,视为未登记",
    ),
    "规则 AA": (
        "schema_r2_validation.sql",
        "-- T38:词典升级 smoke test(仅改名切换,无依赖断言)\nALTER FUNCTION x RENAME TO y;\n",
    ),
}


def run_self_tests() -> list[str]:
    failures: list[str] = []
    for rule, bad_line in SELF_TEST_BAD_LINES.items():
        hits = scan_lines([bad_line], "<self-test>")
        if not any(rule in h for h in hits):
            failures.append(f"自测失败:{rule} 未命中其注入坏样例(规则表达式可能已失效):{bad_line!r}")
    for rule, bad_block in SELF_TEST_BAD_BLOCK.items():
        hits = scan_lines(bad_block, "<self-test>")
        if not any(rule in h for h in hits):
            failures.append(f"自测失败:{rule} 未命中其注入坏代码块(规则表达式可能已失效)")
    for rule, (filename, bad_content) in SELF_TEST_BAD_FILES.items():
        hits = scan_lines(bad_content.splitlines(), "<self-test>") + scan_file_level(
            Path(filename), bad_content
        )
        rule_key = rule.split("(")[0]
        if not any(rule_key in h for h in hits):
            failures.append(f"自测失败:{rule} 未命中其注入坏文件(文件级规则可能已失效)")
    return failures


def main() -> int:
    self_test_failures = run_self_tests()
    if self_test_failures:
        for failure in self_test_failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    specs_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/specs")
    if not specs_dir.is_dir():
        print(f"ERROR: specs 目录不可读: {specs_dir}", file=sys.stderr)
        return 2

    target_files = sorted(list(specs_dir.rglob("*.md")) + list(specs_dir.rglob("*.sql")))
    all_violations: list[str] = []
    for target in target_files:
        all_violations.extend(scan_file(target))

    if all_violations:
        print(f"FAIL 语义级一致性校验发现 {len(all_violations)} 处漂移:", file=sys.stderr)
        for violation in all_violations:
            print(f"  {violation}", file=sys.stderr)
        return 1

    print(f"OK 语义级一致性校验通过(规则自测全命中;扫描 {specs_dir} 下 {len(target_files)} 份 md/sql,无语义漂移)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
