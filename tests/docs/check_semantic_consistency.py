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
- 规则 Z(auth.md,R7-M2 三元组):sessions 登记表解析 (首格, purpose) 二元组——
  精确行集合(register/login/oauth start+callback/device token+approve+deny/
  refresh/logout 与 logout-all 分行/reset-password/change-password/reauth/
  token GET 与 DELETE 分行/sessions GET 与 DELETE 分行/WS 握手/HTML 入口),
  每行 purpose 须含读/写标注;step-up 闸门行 purpose 须含精确受保护路由标记
  (api-tokens/2fa/oauth)且声明 change-password 不在预闸门(R7-M1)——同前缀条目
  不得顶替,「路径正确但 purpose 错」坏样例必失败。
- 规则 AA(validation SQL,R7-M2 标记截段):以唯一 `T38:start/end` 标记截段(文件头
  总览注释不得冒充 T38 段),段内含 pg_depend + refobjid、v_new_bound = 9 与
  v_old_bound = 9、v_old_deps = 0、9 条规范表达式索引名全集、事务外 DROP INDEX
  CONCURRENTLY、旧函数实际删除,且阶段顺序(建 _next → 切换前断言 → 删 _prev →
  零依赖 → 删旧函数)——「总览先出现 T38 而标记段缺断言」「阶段乱序」坏样例必失败。

**坏样例自测**:每条规则携带注入坏样例,每次运行先断言全部规则必中坏样例——
避免「绿灯只证明正则没命中」。自测失败 = 脚本缺陷,退出 1。

用法:python3 tests/docs/check_semantic_consistency.py [specs_dir]
退出码:0 = 自测通过且无语义漂移;1 = 检出漂移或自测失败;2 = 目录不可读。
"""

from __future__ import annotations

import itertools
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


# 规则 Z canonical row → 精确 read/write purpose 映射(R8-M2:method/path/purpose 三元组,
# 具体诊断码 Z:MISSING_ROW / Z:PURPOSE / Z:MISSING_CONTAINS / Z:GATE_*)
REGISTRY_ROW_PURPOSE: dict[str, tuple[str, ...]] = {
    "POST /api/v1/auth/register": ("写",),
    "POST /api/v1/auth/login": ("写",),
    "POST /api/v1/auth/device/token": ("写",),
    "POST /api/v1/auth/device/approve": ("读",),
    "POST /api/v1/auth/device/deny": ("读",),
    "POST /api/v1/auth/refresh": ("读", "写"),
    "POST /api/v1/auth/logout": ("读", "写"),
    "POST /api/v1/auth/logout-all": ("读", "写"),
    "POST /api/v1/auth/reset-password": ("读", "写"),
    "POST /api/v1/auth/change-password": ("读", "写"),
    "POST /api/v1/auth/reauth": ("读", "写"),
    "GET /api/v1/auth/token": ("读",),
    "DELETE /api/v1/auth/token": ("写",),
    "GET /api/v1/sessions": ("读",),
    "DELETE /api/v1/sessions/{id}": ("写",),
}
# 首格按包含匹配的行(oauth 合并写法 / WS / HTML 入口)→ 必需 purpose 标记
REGISTRY_CONTAINS_PURPOSE: dict[str, tuple[str, ...]] = {
    "/api/v1/auth/oauth/{provider}/start": ("读", "写"),
    "/api/v1/auth/oauth/{provider}/callback": ("读", "写"),
    "/ws": ("读",),
    "HTML 入口": ("读",),
}
STEPUP_GATE_REQUIRED_TOKENS = ("api-tokens", "2fa", "oauth")


def _registry_rows(block: str) -> list[tuple[str, str]]:
    """解析登记表区块表格行,返回 (规范化首格, purpose 全文) 二元组。

    purpose 取首格之后的全部列重新拼接(purpose 内允许含 `|`,如「web|cli」)。
    """
    rows: list[tuple[str, str]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0].replace("`", "").strip()
        purpose = " | ".join(cells[1:])
        if first and not set(first) <= set("-: "):  # 跳过表头分隔行
            rows.append((first, purpose))
    return rows


def check_sessions_registry(text: str) -> list[str]:
    """auth.md:sessions 登记表 method/path/purpose 精确三元组(R6-H2/R7-M2/R8-M2)。"""
    violations: list[str] = []
    start = text.find("<!-- sessions-registry:start -->")
    end = text.find("<!-- sessions-registry:end -->")
    if start < 0 or end < 0 or end < start:
        violations.append("Z:NO_REGISTRY: auth.md 缺少 sessions-registry 标记块")
        return violations
    rows = _registry_rows(text[start:end])
    purpose_by_first = {first: purpose for first, purpose in rows}
    # 精确行 → 精确 read/write purpose
    for row, markers in REGISTRY_ROW_PURPOSE.items():
        if row not in purpose_by_first:
            violations.append(f"Z:MISSING_ROW:{row}: 登记表缺少精确行(同前缀条目不得顶替)")
            continue
        purpose = purpose_by_first[row]
        for marker in markers:
            if marker not in purpose:
                violations.append(f"Z:PURPOSE:{row}: purpose 应含「{marker}」标注,实际 {purpose[:40]!r}")
    # 包含匹配行 → purpose 校验(oauth start/callback / WS / HTML 入口)
    for token, markers in REGISTRY_CONTAINS_PURPOSE.items():
        matched = [p for f, p in rows if token in f]
        if not matched:
            violations.append(f"Z:MISSING_CONTAINS:{token}: 登记表缺少含「{token}」的条目")
            continue
        purpose = " | ".join(matched)
        for marker in markers:
            if marker not in purpose:
                violations.append(f"Z:PURPOSE:{token}: purpose 应含「{marker}」标注")
    # step-up 闸门行:精确受保护路由集合 + change-password 不在预闸门
    gate_purpose = " ".join(p for f, p in rows if "step-up" in f or "step-up" in p)
    if not gate_purpose:
        violations.append("Z:GATE_MISSING: 缺少 step-up 闸门中间件登记行")
    else:
        for token in STEPUP_GATE_REQUIRED_TOKENS:
            if token not in gate_purpose:
                violations.append(f"Z:GATE_TOKEN:{token}: 闸门 purpose 缺少受保护路由标记")
        if "不在预闸门" not in gate_purpose:
            violations.append("Z:GATE_PRE_GATE: 闸门未声明 change-password 不在预闸门集合(R7-M1 口径)")
    return violations


CANONICAL_EXPRESSION_INDEXES = (
    "idx_issues_title_trgm", "idx_issues_title_prefix", "idx_issues_identifier_prefix",
    "idx_projects_name_trgm", "idx_projects_name_prefix",
    "idx_views_name_trgm", "idx_views_name_prefix",
    "idx_chat_sessions_title_trgm", "idx_chat_sessions_title_prefix",
)


def check_t38_pg_depend(text: str) -> list[str]:
    """schema_r2_validation.sql:T38:start/end 标记截段 + 精确断言 + 阶段顺序(R7-M2/R8-M2 具体诊断码)。"""
    start_count = text.count("-- T38:start")
    end_count = text.count("-- T38:end")
    if start_count == 0 and "T38" in text:
        return ["AA:MARKER_MISSING: 含 T38 但缺少 T38:start 标记(总览注释不得冒充 T38 段)"]
    if start_count == 0:
        return []
    violations: list[str] = []
    if start_count != 1:
        violations.append(f"AA:MARKER_DUP:T38:start 出现 {start_count} 次,应恰好一次")
    if end_count != 1:
        violations.append(f"AA:MARKER_DUP:T38:end 出现 {end_count} 次,应恰好一次")
    start = text.find("-- T38:start")
    end = text.find("-- T38:end")
    if end < start:
        violations.append("AA:MARKER_ORDER: T38:end 在 T38:start 之前")
        return violations
    t38 = text[start:end]
    # 存在性与顺序均仅对可执行语句判定(剔除注释行)——注释里的「断言」是假断言(R7-M2)
    code_only = "\n".join(
        line for line in t38.splitlines() if not line.lstrip().startswith("--")
    )
    if "pg_depend" not in code_only or "refobjid" not in code_only:
        violations.append("AA:NO_PG_DEPEND: T38 段缺少 pg_depend/refobjid OID 绑定断言(可执行语句)")
    if "v_new_bound = 9" not in code_only or "v_old_bound = 9" not in code_only:
        violations.append("AA:NO_BOUNDS: T38 段缺少 v_new_bound = 9 / v_old_bound = 9 精确绑定计数断言")
    if "v_old_deps = 0" not in code_only:
        violations.append("AA:NO_OLD_DEPS: T38 段缺少 v_old_deps = 0 旧函数零依赖断言")
    if "DROP INDEX CONCURRENTLY" not in code_only:
        violations.append("AA:NO_DROP_INDEX: T38 段缺少事务外 DROP INDEX CONCURRENTLY")
    if "DROP FUNCTION public.mesh_search_norm_prev" not in code_only:
        violations.append("AA:NO_DROP_FUNCTION: T38 段缺少旧函数实际删除")
    missing = [name for name in CANONICAL_EXPRESSION_INDEXES if name not in code_only]
    if missing:
        violations.append(f"AA:MISSING_INDEX: T38 段未锚定全部 9 条规范表达式索引,缺 {missing}")
    # 阶段顺序:建 _next → 切换前断言 → 事务外删 _prev → 零依赖删旧
    stages = [
        ("CREATE INDEX CONCURRENTLY", "事务外建 _next 索引"),
        ("ASSERT v_new_bound = 9", "切换前新函数绑定断言"),
        ("DROP INDEX CONCURRENTLY", "事务外删 _prev 索引"),
        ("ASSERT v_old_deps = 0", "旧函数零依赖断言"),
        ("DROP FUNCTION public.mesh_search_norm_prev", "删旧函数"),
    ]
    positions = []
    for needle, label in stages:
        pos = code_only.find(needle)
        if pos < 0:
            break
        positions.append((pos, label))
    if len(positions) == len(stages):
        for (pos_a, label_a), (pos_b, label_b) in itertools.pairwise(positions):
            if pos_a > pos_b:
                violations.append(f"AA:STAGE_ORDER: 「{label_a}」应在「{label_b}」之前")
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
    "GET /api/v1/auth/oauth/{provider}/start",
    "GET/POST /api/v1/auth/oauth/{provider}/callback",
    "POST /api/v1/auth/device/token",
    "POST /api/v1/auth/device/approve", "POST /api/v1/auth/device/deny",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout", "POST /api/v1/auth/logout-all",
    "POST /api/v1/auth/reset-password", "POST /api/v1/auth/change-password",
    "POST /api/v1/auth/reauth", "GET /api/v1/auth/token",
    "DELETE /api/v1/auth/token", "GET /api/v1/sessions",
    "DELETE /api/v1/sessions/{id}",
)
_REGISTRY_TAIL_ROWS = (
    "`/ws` 握手鉴权",
    "个性化 HTML 入口中间件",
)
_GATE_ROW = (
    "| step-up 闸门中间件 | **读**:受保护路由 api-tokens 创建/撤销(web|cli)、2fa 启停(web)、"
    "oauth 换绑/解绑(web);change-password 不在预闸门集合 |"
)


def _registry_doc(
    rows: tuple[str, ...],
    gate: str = _GATE_ROW,
    purpose_override: dict[str, str] | None = None,
) -> str:
    """构造合法表格语法的登记表(各行带前导 |);purpose_override 按行覆盖 purpose 格。"""
    overrides = purpose_override or {}
    body = "".join(
        f"| `{row}` | {overrides.get(row, '**读 + 写**:目的')} |\n"
        if row not in overrides
        else f"| `{row}` | {overrides[row]} |\n"
        for row in rows
    )
    tail = "".join(f"| {row} | **读**:目的 |\n" for row in _REGISTRY_TAIL_ROWS)
    return "<!-- sessions-registry:start -->\n" + body + tail + gate + "\n<!-- sessions-registry:end -->"


SELF_TEST_BAD_FILES: dict[str, tuple[str, str, str]] = {
    # name → (filename, bad content, expected diagnostic code)
    "规则 Z(无标记块)": (
        "auth.md",
        "| POST /api/v1/auth/logout-all | 批量撤销 |  # 登记表在标记块外,视为未登记",
        "Z:NO_REGISTRY",
    ),
    "规则 Z(同前缀缺项:logout-all 顶替 logout)": (
        "auth.md",
        _registry_doc(tuple(r for r in _REGISTRY_GOOD_ROWS if r != "POST /api/v1/auth/logout")),
        "Z:MISSING_ROW:POST /api/v1/auth/logout",
    ),
    "规则 Z(同前缀缺项:缺 DELETE /api/v1/auth/token)": (
        "auth.md",
        _registry_doc(tuple(r for r in _REGISTRY_GOOD_ROWS if r != "DELETE /api/v1/auth/token")),
        "Z:MISSING_ROW:DELETE /api/v1/auth/token",
    ),
    "规则 Z(缺 device/approve 登记)": (
        "auth.md",
        _registry_doc(tuple(r for r in _REGISTRY_GOOD_ROWS if r != "POST /api/v1/auth/device/approve")),
        "Z:MISSING_ROW:POST /api/v1/auth/device/approve",
    ),
    "规则 Z(register purpose 错:写→读)": (
        "auth.md",
        _registry_doc(_REGISTRY_GOOD_ROWS, purpose_override={"POST /api/v1/auth/register": "**读**:目的"}),
        "Z:PURPOSE:POST /api/v1/auth/register",
    ),
    "规则 Z(callback purpose 整段为空)": (
        "auth.md",
        _registry_doc(_REGISTRY_GOOD_ROWS, purpose_override={"GET/POST /api/v1/auth/oauth/{provider}/callback": ""}),
        "Z:PURPOSE:/api/v1/auth/oauth/{provider}/callback",
    ),
    "规则 Z(闸门缺受保护路由标记 2fa)": (
        "auth.md",
        _registry_doc(
            _REGISTRY_GOOD_ROWS,
            gate="| step-up 闸门中间件 | **读**:受保护路由 api-tokens 创建/撤销、oauth 换绑;change-password 不在预闸门集合 |",
        ),
        "Z:GATE_TOKEN:2fa",
    ),
    "规则 Z(闸门未声明 change-password 不在预闸门)": (
        "auth.md",
        _registry_doc(
            _REGISTRY_GOOD_ROWS,
            gate="| step-up 闸门中间件 | **读**:受保护路由 api-tokens、2fa、oauth |",
        ),
        "Z:GATE_PRE_GATE",
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
        "规则 Y:",
    ),
    "规则 AA(总览先出现 T38、标记段缺计数断言)": (
        "schema_r2_validation.sql",
        (
            "-- 文件头总览:T38 升级 smoke test……\n"
            "-- T38:start\nSELECT * FROM pg_depend WHERE refobjid = v_oid;\n"
            "-- 无计数/无索引集合/无删除\n-- T38:end\n"
        ),
        "AA:NO_BOUNDS",
    ),
    "规则 AA(缺事务外 DROP INDEX CONCURRENTLY)": (
        "schema_r2_validation.sql",
        (
            "-- T38:start\n-- v_new_bound = 9 / v_old_bound = 9 / v_old_deps = 0\n"
            + "CREATE INDEX CONCURRENTLY x ON t(c);\n"
            + "".join(f"-- {name}\n" for name in CANONICAL_EXPRESSION_INDEXES)
            + "SELECT count(*) FROM pg_depend WHERE refobjid = x;\n"
            + "DROP FUNCTION public.mesh_search_norm_prev(TEXT);\n-- T38:end\n"
        ),
        "AA:NO_DROP_INDEX",
    ),
    "规则 AA(阶段顺序错误:先删后断言)": (
        "schema_r2_validation.sql",
        (
            "-- T38:start\n"
            + "CREATE INDEX CONCURRENTLY x ON t(c);\n"
            + "DROP INDEX CONCURRENTLY idx_prev;\n"  # 删除早于切换前断言 → 顺序错误
            + "ASSERT v_new_bound = 9;\nASSERT v_old_bound = 9;\nASSERT v_old_deps = 0;\n"
            + "".join(f"-- {name}\n" for name in CANONICAL_EXPRESSION_INDEXES)
            + "pg_depend refobjid\nDROP FUNCTION public.mesh_search_norm_prev(TEXT);\n-- T38:end\n"
        ),
        "AA:STAGE_ORDER",
    ),
    "规则 AA(T38:start 重复出现)": (
        "schema_r2_validation.sql",
        (
            "-- T38:start\n-- T38:start\n"
            + "CREATE INDEX CONCURRENTLY x ON t(c);\n"
            + "ASSERT v_new_bound = 9;\nASSERT v_old_bound = 9;\nASSERT v_old_deps = 0;\n"
            + "DROP INDEX CONCURRENTLY idx_prev;\n"
            + "".join(f"{name} x\n" for name in CANONICAL_EXPRESSION_INDEXES)
            + "pg_depend refobjid\nDROP FUNCTION public.mesh_search_norm_prev(TEXT);\n-- T38:end\n"
        ),
        "AA:MARKER_DUP",
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
    for rule, (filename, bad_content, expected_code) in SELF_TEST_BAD_FILES.items():
        hits = scan_lines(bad_content.splitlines(), "<self-test>") + scan_file_level(
            Path(filename), bad_content
        )
        # R8-M2:断言具体诊断码,而非「任意规则诊断」——掩盖错因的自测即缺陷
        if not any(expected_code in h for h in hits):
            failures.append(
                f"自测失败:{rule} 未命中期望诊断码 {expected_code!r};实际诊断:{hits}"
            )
    # 正对照:完整登记表(正确 purpose)不应触发规则 Z
    good_hits = scan_file_level(Path("auth.md"), _registry_doc(_REGISTRY_GOOD_ROWS))
    z_hits = [h for h in good_hits if h.startswith("Z:")]
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
