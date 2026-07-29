#!/usr/bin/env python3
"""docs/specs「语义级一致性」文档校验(MES-76 R2-M2 建立,R3-M2 覆盖 .sql + 坏样例自测,
R6-M1 升级为精确集合解析 + 同形坏样例)。

本脚本扫描 docs/specs/**/*.md 与 docs/specs/**/*.sql(含 validation 权威 SQL),
把语义漂移判为失败(CI 红)。行级规则:T(主题默认语义)/ F(full_name 残留)/
P(rt_live_ 残留)/ G(前缀⇄持有者类型绑定,围栏块)/ R(mesh_rt_ 存储真源)/
D(302 语义措辞)/ U(搜索结果 agent URL)/ V(runtime 停用经 api_tokens,否定语境放行)/
W(CLI/runtime 环境变量并列)/ X(CSRF token 残留,否定语境放行)。

文件级规则(R6 精确化,子串匹配已撤销):
- 规则 Y(auth.md):登录示例**代码块上下文**校验——登录示例块不得含 `"refresh_token"`
  字段且须含 `Set-Cookie`(端点标题与字段分行即漏检的逐行匹配已废弃;注入与历史
  残留同形的跨行坏样例必失败)。
- 规则 Z(auth.md):sessions 登记表**精确行集合**——解析标记块内表格行首格,
  精确匹配 method + 完整 `/api/v1` 路径(register/login/oauth callback/device token/
  refresh/logout 与 logout-all 分别成行/reset-password/change-password/reauth/
  token GET 与 DELETE 分别成行/sessions GET 与 DELETE 分别成行/WS 握手/HTML 入口/
  step-up 闸门)——同前缀条目不得顶替。
- 规则 AA(validation SQL):T38 段锚定精确断言——pg_depend + refobjid、
  v_new_bound = 9 与 v_old_bound = 9、v_old_deps = 0、9 条规范表达式索引名全集、
  事务外 DROP INDEX CONCURRENTLY、旧函数实际删除——虚假 pg_depend 坏样例必失败。

**坏样例自测**:每条规则携带注入坏样例,每次运行先断言全部规则必中坏样例——
避免「绿灯只证明正则没命中」。自测失败 = 脚本缺陷,退出 1。

用法:python3 tests/docs/check_semantic_consistency.py [specs_dir]
退出码:0 = 自测通过且无语义漂移;1 = 检出漂移或自测失败;2 = 目录不可读。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ----------------------------- 行级规则表达式 -----------------------------
THEME_DEFAULT_SYSTEM_RE = re.compile(r"默认\s*[`'\"]?system[`'\"]?", re.IGNORECASE)
SETTINGS_THEME_RE = re.compile(r"settings\.theme(?![_a-z])")  # 不匹配 settings.default_theme
THEME_LEGACY_MARKERS = ("存量", "旧实现", "迁移", "从未选择", "跟随 OS", "不得", "不再")

FULL_NAME_RE = re.compile(r"users\.full_name|(?<![\w.])full_name(?![\w])")
NEGATION_MARKERS = ("无", "不存在", "已废", "没有")

RT_LIVE_RE = re.compile(r"rt_live_")
DEPRECATION_MARKERS = ("已废", "此前", "废弃", "弃用")

MESH_RT_RE = re.compile(r"mesh_rt_")
APITOKENS_HASH_RE = re.compile(r"api_tokens\.token_hash")

REDIRECT_302_SEMANTICS_RE = re.compile(r"302\s*语义")
REDIRECT_NEGATION_MARKERS = ("不得称", "不得", "不称", "勿称", "禁止")

AGENT_URL_MEMBER_ID_RE = re.compile(r'"url"\s*:\s*"[^"]*/agents/mem[_-]')

RUNTIME_TOKEN_WORD_RE = re.compile(r"runtime_token|mesh_rt_")
REVOKED_AT_RE = re.compile(r"revoked_at")
APITOKENS_RE = re.compile(r"api_tokens")
RULE_V_NEGATION = ("不入", "不经", "不进", "无该行", "不共")

CLI_SLASH_RUNTIME_RE = re.compile(r"CLI\s*/\s*runtime", re.IGNORECASE)

CSRF_TOKEN_RE = re.compile(r"CSRF\s*token", re.IGNORECASE)
CSRF_CONTEXT_RE = re.compile(r"SameSite|cookie 会话")
CSRF_NEGATION_MARKERS = ("无独立", "不再用", "不需要", "无 CSRF token", "不提供")

FENCE_RE = re.compile(r"^\s*```")
AGENT_HOLDER_RE = re.compile(r"owner_member_id.*mem[-_]agent|agent 运行凭证")
PAT_EXAMPLE_RE = re.compile(r'"(prefix|token)"\s*:\s*"mesh_pat_')


def scan_lines(lines: list[str], source: str) -> list[str]:
    """对一组行(文件或注入样例)执行全部行级规则,返回违规描述。"""
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
        if (
            SETTINGS_THEME_RE.search(line)
            and THEME_DEFAULT_SYSTEM_RE.search(line)
            and not any(m in line for m in THEME_LEGACY_MARKERS)
        ):
            violations.append(f"{source}:{lineno}: 规则 T: 账号主题 settings.theme 宣称默认 system(应为 absent/null = 继承工作区)")
        if FULL_NAME_RE.search(line) and not any(m in line for m in NEGATION_MARKERS):
            violations.append(f"{source}:{lineno}: 规则 F: 引用 full_name 但无否定标注(users 无此列,显示名链为 display_name → email)")
        if RT_LIVE_RE.search(line) and not any(m in line for m in DEPRECATION_MARKERS):
            violations.append(f"{source}:{lineno}: 规则 P: 废弃前缀 rt_live_ 残留(唯一 runtime 前缀为 mesh_rt_)")
        if MESH_RT_RE.search(line) and APITOKENS_HASH_RE.search(line):
            violations.append(f"{source}:{lineno}: 规则 R: mesh_rt_ 令牌指向 api_tokens.token_hash(唯一真源为 runtimes.runtime_token_hash)")
        if REDIRECT_302_SEMANTICS_RE.search(line) and not any(m in line for m in REDIRECT_NEGATION_MARKERS):
            violations.append(f"{source}:{lineno}: 规则 D: 出现「302 语义」措辞(应为路由器 replace navigation)")
        if AGENT_URL_MEMBER_ID_RE.search(line):
            violations.append(f"{source}:{lineno}: 规则 U: 成员类(members.id)结果 URL 使用 /agents/(应为 /members/{{member_id}})")
        if (
            RUNTIME_TOKEN_WORD_RE.search(line)
            and APITOKENS_RE.search(line)
            and REVOKED_AT_RE.search(line)
            and not any(m in line for m in RULE_V_NEGATION)
        ):
            violations.append(f"{source}:{lineno}: 规则 V: runtime 令牌停用指向 api_tokens.revoked_at(应为 runtime 状态 + runtime_token_hash 清除/轮换)")
        if CLI_SLASH_RUNTIME_RE.search(line):
            violations.append(f"{source}:{lineno}: 规则 W: 「CLI/runtime」并列残留(runtime 机器令牌不经 api_tokens / CLI 环境变量通道)")
        if (
            CSRF_TOKEN_RE.search(line)
            and CSRF_CONTEXT_RE.search(line)
            and not any(m in line for m in CSRF_NEGATION_MARKERS)
        ):
            violations.append(f"{source}:{lineno}: 规则 X: 「CSRF token」残留(Web 会话 CSRF 防护为 SameSite=Strict + Origin/Referer,无独立 CSRF token)")

    if in_fence and fence_block:
        check_block(fence_block)
    return violations


# ----------------------------- 文件级规则(R6 精确化)-----------------------------

def check_login_example(text: str) -> list[str]:
    """auth.md:登录示例代码块上下文校验(R6-H1/M1)。"""
    lines = text.splitlines()
    violations: list[str] = []
    for idx, line in enumerate(lines):
        if "POST /api/v1/auth/login" in line and "登录" in line and line.strip().startswith("**"):
            block_lines: list[str] = []
            in_block = False
            for follower in lines[idx + 1: idx + 30]:
                if follower.strip().startswith("```"):
                    if in_block:
                        break
                    in_block = True
                    continue
                if in_block:
                    block_lines.append(follower)
            block = "\n".join(block_lines)
            if '"refresh_token"' in block:
                violations.append(
                    f"auth.md:~{idx + 1}: 规则 Y: 登录示例代码块含 refresh_token 字段"
                    "(Web cookie-only:refresh 仅经 Set-Cookie 响应头下发,响应体绝无 refresh 明文)"
                )
            if in_block and "Set-Cookie" not in block:
                violations.append(f"auth.md:~{idx + 1}: 规则 Y: 登录示例代码块缺少 Set-Cookie 响应头示意")
            break
    return violations


SESSIONS_REGISTRY_REQUIRED_ROWS = (
    "POST /api/v1/auth/register",
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/device/token",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",          # 独立行(logout-all 不得顶替)
    "POST /api/v1/auth/logout-all",
    "POST /api/v1/auth/reset-password",
    "POST /api/v1/auth/change-password",
    "POST /api/v1/auth/reauth",
    "GET /api/v1/auth/token",            # GET/DELETE 须分别成行
    "DELETE /api/v1/auth/token",
    "GET /api/v1/sessions",
    "DELETE /api/v1/sessions/{id}",
)
SESSIONS_REGISTRY_REQUIRED_CONTAINS = (
    "/api/v1/auth/oauth/{provider}/callback",  # 允许 GET/POST 合并写法
    "/ws",                                      # WS 握手鉴权行
    "HTML 入口",                                 # 个性化 HTML 入口中间件行
    "step-up",                                  # step-up 闸门中间件行
)


def _registry_rows(block: str) -> list[str]:
    """解析登记表区块表格行,返回规范化首格(去反引号/首尾空白)。"""
    rows: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0].replace("`", "").strip()
        if first and not set(first) <= set("-: "):  # 跳过表头分隔行
            rows.append(first)
    return rows


def check_sessions_registry(text: str) -> list[str]:
    """auth.md:sessions 生命周期登记表精确行集合(R6-H2/M1)。"""
    violations: list[str] = []
    start = text.find("<!-- sessions-registry:start -->")
    end = text.find("<!-- sessions-registry:end -->")
    if start < 0 or end < 0 or end < start:
        violations.append("auth.md: 规则 Z: 缺少 sessions-registry 标记块(登记表未登记)")
        return violations
    rows = _registry_rows(text[start:end])
    row_set = set(rows)
    for required in SESSIONS_REGISTRY_REQUIRED_ROWS:
        if required not in row_set:
            violations.append(
                f"auth.md: 规则 Z: 登记表缺少精确行「{required}」(同前缀条目不得顶替,method+完整路径逐条穷举)"
            )
    for required in SESSIONS_REGISTRY_REQUIRED_CONTAINS:
        if not any(required in row for row in rows):
            violations.append(f"auth.md: 规则 Z: 登记表缺少含「{required}」的条目")
    return violations


CANONICAL_EXPRESSION_INDEXES = (
    "idx_issues_title_trgm", "idx_issues_title_prefix", "idx_issues_identifier_prefix",
    "idx_projects_name_trgm", "idx_projects_name_prefix",
    "idx_views_name_trgm", "idx_views_name_prefix",
    "idx_chat_sessions_title_trgm", "idx_chat_sessions_title_prefix",
)


def check_t38_pg_depend(text: str) -> list[str]:
    """schema_r2_validation.sql:T38 段锚定精确迁移断言(R6-H4/M1)。"""
    t38_at = text.find("T38")
    if t38_at < 0:
        return []
    t38 = text[t38_at:]
    violations: list[str] = []
    if "pg_depend" not in t38 or "refobjid" not in t38:
        violations.append("schema_r2_validation.sql: 规则 AA: T38 缺少 pg_depend/refobjid OID 绑定断言")
    if "v_new_bound = 9" not in t38 or "v_old_bound = 9" not in t38:
        violations.append("schema_r2_validation.sql: 规则 AA: T38 缺少新/旧函数精确绑定计数断言(v_new_bound = 9 与 v_old_bound = 9)")
    if "v_old_deps = 0" not in t38:
        violations.append("schema_r2_validation.sql: 规则 AA: T38 缺少旧函数零依赖断言(v_old_deps = 0)")
    if "DROP INDEX CONCURRENTLY" not in t38:
        violations.append("schema_r2_validation.sql: 规则 AA: T38 缺少事务外 DROP INDEX CONCURRENTLY(生产契约删除路径)")
    if "DROP FUNCTION public.mesh_search_norm_prev" not in t38:
        violations.append("schema_r2_validation.sql: 规则 AA: T38 缺少旧函数实际删除(DROP FUNCTION mesh_search_norm_prev)")
    missing = [name for name in CANONICAL_EXPRESSION_INDEXES if name not in t38]
    if missing:
        violations.append(f"schema_r2_validation.sql: 规则 AA: T38 未锚定全部 9 条规范表达式索引,缺 {missing}")
    return violations


def scan_file_level(path: Path, text: str) -> list[str]:
    name = path.name
    if name == "auth.md":
        return check_sessions_registry(text) + check_login_example(text)
    if name == "schema_r2_validation.sql":
        return check_t38_pg_depend(text)
    return []


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: 无法读取: {exc}"]
    return scan_lines(text.splitlines(), str(path)) + scan_file_level(path, text)


# ----------------------------- 坏样例自测 -----------------------------
SELF_TEST_BAD_LINES = {
    "规则 T": "| 账号主题 | `users.settings.theme` | string | 默认 `system` | auth.md | 校验 |",
    "规则 F": "  full_name TEXT NULL,",
    "规则 P": "runtime 令牌前缀为 rt_live_a1b2,激活时写入。",
    "规则 R": "`mesh_rt_` 令牌存储于 `api_tokens.token_hash`(SHA-256)。",
    "规则 D": "既有扁平路由保留为应用内别名,访问时 replaceState(302 语义)至规范路由。",
    "规则 U": '{ "type": "agent", "id": "mem_b2", "url": "/w/acme/agents/mem_b2" }',
    "规则 V": "runtime 进入 paused 时停用其 runtime_token(api_tokens.revoked_at 置位)。",
    "规则 W": "- [ ] 防 XSS 窃取:API token 由 CLI/runtime 从环境变量读取。",
    "规则 X": "- [ ] 防 CSRF:cookie 会话用 `SameSite` + CSRF token。",
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

_REGISTRY_GOOD_ROWS = (
    "POST /api/v1/auth/register", "POST /api/v1/auth/login",
    "GET/POST /api/v1/auth/oauth/{provider}/callback",
    "POST /api/v1/auth/device/token", "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout", "POST /api/v1/auth/logout-all",
    "POST /api/v1/auth/reset-password", "POST /api/v1/auth/change-password",
    "POST /api/v1/auth/reauth", "GET /api/v1/auth/token",
    "DELETE /api/v1/auth/token", "GET /api/v1/sessions",
    "DELETE /api/v1/sessions/{id}",
)
_REGISTRY_TAIL_ROWS = ("`/ws` 握手鉴权", "个性化 HTML 入口中间件", "step-up 闸门中间件")


def _registry_doc(rows: tuple[str, ...]) -> str:
    body = "".join(f"| `{row}` | 目的 |\n" for row in rows)
    tail = "".join(f"| {row} | 目的 |\n" for row in _REGISTRY_TAIL_ROWS)
    return "<!-- sessions-registry:start -->\n" + body + tail + "<!-- sessions-registry:end -->"


SELF_TEST_BAD_FILES = {
    "规则 Z(无标记块)": (
        "auth.md",
        "| POST /api/v1/auth/logout-all | 批量撤销 |  # 登记表在标记块外,视为未登记",
    ),
    "规则 Z(同前缀缺项:logout-all 顶替 logout)": (
        "auth.md",
        _registry_doc(tuple(r for r in _REGISTRY_GOOD_ROWS if r != "POST /api/v1/auth/logout")),
    ),
    "规则 Z(同前缀缺项:缺 DELETE /api/v1/auth/token)": (
        "auth.md",
        _registry_doc(tuple(r for r in _REGISTRY_GOOD_ROWS if r != "DELETE /api/v1/auth/token")),
    ),
    "规则 Y(跨行 refresh 字段,与历史残留同形)": (
        "auth.md",
        (
            "**登录** `POST /api/v1/auth/login`\n```json\n// Request\n"
            + '{ "email": "a@b.c", "password": "..." }\n'
            + "// 200 Response\n"
            + '{ "data": { "access_token": "eyJ...",\n'
            + '            "expires_in": 900, "refresh_token": "mesh_rft_..." } }\n```\n'
        ),
    ),
    "规则 AA(虚假 pg_depend:无计数断言)": (
        "schema_r2_validation.sql",
        "-- T38: SELECT * FROM pg_depend WHERE refobjid = v_oid;\n-- 无计数/无索引集合/无删除\n",
    ),
    "规则 AA(缺事务外 DROP INDEX CONCURRENTLY)": (
        "schema_r2_validation.sql",
        "-- T38:\n-- v_new_bound = 9 / v_old_bound = 9 / v_old_deps = 0\n"
        + "".join(f"-- {name}\n" for name in CANONICAL_EXPRESSION_INDEXES)
        + "SELECT count(*) FROM pg_depend WHERE refobjid = x;\n"
        + "DROP FUNCTION public.mesh_search_norm_prev(TEXT);\n",
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
    # 正对照:完整登记表不应触发规则 Z
    good_hits = scan_file_level(Path("auth.md"), _registry_doc(_REGISTRY_GOOD_ROWS))
    z_hits = [h for h in good_hits if "规则 Z" in h]
    if z_hits:
        failures.append(f"自测失败:完整登记表误触发规则 Z:{z_hits}")
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

    print(f"OK 语义级一致性校验通过(规则自测全命中,含同形坏样例;扫描 {specs_dir} 下 {len(target_files)} 份 md/sql,无语义漂移)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
