#!/usr/bin/env python3
"""公开发布 OpenAPI 契约暴露面门禁(cli.md §5.4)。

对随仓库发布的 docs/api/openapi.yaml 做三项硬断言:

1. **daemon 路径零命中**:`^/api/v1/daemon/` 前缀路径一个也不允许出现。
   内部机器接口(daemon 协议,首方 mesh-runtime 二进制专用)是「整体剔除」
   而非 `x-internal: true` 标记——标记后 schema 仍随公开产物分发,等于泄漏
   内部接口的路径/参数/错误码全表面。
2. **daemon 残留零命中**:components.schemas 不得残留仅被 daemon 路由引用
   的孤儿 schema(其 description 常含内部端点描述),全文不得出现 daemon 字样。
3. **CLI 依赖端点齐全**:cli.md §3.1 映射表落地的每个端点模板必须存在
   (占位符名不敏感,`{ws}` 与 `{workspace_id}` 等价),保证公开契约对
   `mesh` CLI 可用,路径改名/删除在此即红。

用法:python3 tests/docs/check_openapi_surface.py [openapi_yaml_path]
退出码:0 = 全部通过;1 = 暴露面违规或端点缺失;2 = 文件缺失/解析失败。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML with the backend deps
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OPENAPI_PATH = REPO_ROOT / "docs" / "api" / "openapi.yaml"

# 内部命名空间:整体剔除,公开产物零命中(cli.md §5.4 安全评审计策)。
INTERNAL_PREFIXES = ("/api/v1/daemon/",)

# CLI 依赖端点表(cli.md §3.1 映射表 + 登录/凭证流)。占位符以 {name} 归一,
# 匹配时放宽为任意 {…},不锁定参数名。每条 = (method, path_template)。
CLI_REQUIRED_ENDPOINTS: tuple[tuple[str, str], ...] = (
    # auth:设备码流(RFC 8628)+ PAT/会话自省自撤销 + refresh
    ("POST", "/api/v1/auth/device/code"),
    ("POST", "/api/v1/auth/device/token"),
    ("GET", "/api/v1/auth/device"),  # 确认页数据
    ("POST", "/api/v1/auth/device/approve"),
    ("POST", "/api/v1/auth/device/deny"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/auth/token"),
    ("DELETE", "/api/v1/auth/token"),
    # identity / workspaces
    ("GET", "/api/v1/me"),
    ("GET", "/api/v1/workspaces"),
    ("GET", "/api/v1/workspaces/by-slug/{slug}"),
    # project / member / agent
    ("GET", "/api/v1/workspaces/{ws}/projects"),
    ("POST", "/api/v1/workspaces/{ws}/projects"),
    ("GET", "/api/v1/projects/{project_id}"),
    ("GET", "/api/v1/workspaces/{ws}/members"),
    ("GET", "/api/v1/workspaces/{ws}/agents"),
    # issue 命令族
    ("GET", "/api/v1/workspaces/{ws}/issues"),
    ("POST", "/api/v1/workspaces/{ws}/issues"),
    ("GET", "/api/v1/workspaces/{ws}/issues/by-identifier/{identifier}"),
    ("GET", "/api/v1/issues/{issue_id}"),
    ("PATCH", "/api/v1/issues/{issue_id}"),
    ("GET", "/api/v1/issues/{issue_id}/children"),
    ("GET", "/api/v1/issues/{issue_id}/dependencies"),
    ("GET", "/api/v1/issues/{issue_id}/comments"),
    ("POST", "/api/v1/issues/{issue_id}/comments"),
    # runtime 控制台(影子记录/只读排障,daemon 域不触达)
    ("POST", "/api/v1/workspaces/{ws}/runtimes"),
    ("GET", "/api/v1/workspaces/{ws}/runtimes/{runtime_id}"),
    # execution
    ("GET", "/api/v1/workspaces/{ws}/executions"),
    ("GET", "/api/v1/workspaces/{ws}/executions/{execution_id}"),
    ("POST", "/api/v1/workspaces/{ws}/executions/{execution_id}:cancel"),
    ("GET", "/api/v1/workspaces/{ws}/executions/{execution_id}/logs"),
    ("GET", "/api/v1/workspaces/{ws}/executions/{execution_id}/logs/stream"),
    # export / import(data-jobs)
    ("POST", "/api/v1/data-jobs/export"),
    ("POST", "/api/v1/data-jobs/import"),
    ("POST", "/api/v1/data-jobs/import/{job_id}/validate"),
    ("POST", "/api/v1/data-jobs/import/{job_id}/run"),
    ("GET", "/api/v1/data-jobs/{job_id}"),
    ("GET", "/api/v1/data-jobs/{job_id}/download"),
    # attachment(导入闸门三阶段直传)
    ("POST", "/api/v1/attachments/upload-requests"),
    ("POST", "/api/v1/attachments/{attachment_id}/complete"),
)


def _template_regex(template: str) -> re.Pattern[str]:
    """Compile a path template to a regex with placeholder-name tolerance."""
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile(
        "^" + r"\{[^}/]+\}".join(re.escape(part) for part in parts) + "$"
    )


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OPENAPI_PATH
    if not path.exists():
        sys.stderr.write(
            f"FAIL: {path} missing — regenerate with "
            "python backend/scripts/export_openapi.py\n"
        )
        return 2
    try:
        raw = path.read_text(encoding="utf-8")
        spec = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        sys.stderr.write(f"FAIL: {path} is not valid YAML: {exc}\n")
        return 2
    if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3.1"):
        sys.stderr.write(f"FAIL: {path} is not an OpenAPI 3.1 document\n")
        return 2

    failures: list[str] = []

    # 1. daemon 路径零命中(§5.4 明文 CI 断言)。
    paths = spec.get("paths", {})
    internal_hits = sorted(
        p for p in paths if any(p.startswith(prefix) for prefix in INTERNAL_PREFIXES)
    )
    if internal_hits:
        failures.append(
            "internal paths leaked into the public contract: "
            + ", ".join(internal_hits[:8])
        )

    # 2. daemon 残留零命中(孤儿 schema / 描述文本)。
    schema_names = sorted(spec.get("components", {}).get("schemas", {}))
    daemon_schemas = [name for name in schema_names if "daemon" in name.lower()]
    if daemon_schemas:
        failures.append("daemon schemas present: " + ", ".join(daemon_schemas[:8]))
    if re.search(r"daemon", raw, re.IGNORECASE):
        line_no = next(
            (i for i, line in enumerate(raw.splitlines(), 1) if "daemon" in line.lower()),
            0,
        )
        failures.append(f"'daemon' surface text remains in the artifact (line {line_no})")

    # 3. CLI 依赖端点齐全(占位符名不敏感)。
    path_matchers = [(method, tpl, _template_regex(tpl)) for method, tpl in CLI_REQUIRED_ENDPOINTS]
    missing = [
        f"{method} {tpl}"
        for method, tpl, matcher in path_matchers
        if not any(
            matcher.match(candidate) and method.lower() in operations
            for candidate, operations in paths.items()
        )
    ]
    if missing:
        failures.append("CLI-required endpoints missing: " + ", ".join(missing[:10]))

    if failures:
        for failure in failures:
            sys.stderr.write(f"FAIL: {failure}\n")
        sys.stderr.write(
            "regenerate the artifact: python backend/scripts/export_openapi.py\n"
        )
        return 1

    print(
        f"PASS: {len(paths)} public paths, 0 internal paths, "
        f"{len(schema_names)} schemas, {len(CLI_REQUIRED_ENDPOINTS)} CLI endpoints present"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
