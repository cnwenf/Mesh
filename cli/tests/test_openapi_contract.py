"""CLI ↔ OpenAPI contract tests (cli.md §5.4 — 请求构造/响应解析不漂移).

The CLI is a thin REST client; its request construction and response parsing
must not drift from the published contract ``docs/api/openapi.yaml``. These
tests scan the CLI source (AST — every ``app.call(...)`` / ``app.call_all(...)``
/ ``client.request(...)`` / ``client.stream_request(...)`` call) and assert
against the committed spec:

1. every endpoint the CLI builds exists in the spec (method + path template,
   placeholder names tolerant: ``{ws}`` matches ``{workspace_id}``);
2. every path parameter the CLI interpolates is declared by the spec, so a
   renamed parameter fails here instead of 404-ing at runtime;
3. every endpoint declares at least one 2xx response the CLI can parse;
4. the artifact the CLI is contracted against carries zero daemon surface.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

CLI_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = CLI_ROOT.parent
OPENAPI_PATH = REPO_ROOT / "docs" / "api" / "openapi.yaml"
SOURCE_DIRS = (CLI_ROOT / "src" / "meshcli",)
HTTP_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})
# Guardrail: if the scanner silently stops finding calls (e.g. the call form
# changes), fail loudly instead of passing on an empty inventory.
MIN_EXPECTED_ENDPOINTS = 25


def _joined_str_to_template(node: ast.JoinedStr) -> str:
    """Rebuild an f-string path as an OpenAPI-style template (names erased)."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append("{param}")
        else:  # pragma: no cover - paths are plain str/f-str literals
            raise TypeError(f"unsupported path expression: {ast.dump(value)}")
    return "".join(parts)


def _literal_template(node: ast.expr) -> str | None:
    """A path literal/f-string as an OpenAPI-style template, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _joined_str_to_template(node)
    return None


def _scope_templates(scope: ast.AST) -> dict[str, str]:
    """Simple ``name = <path literal>`` bindings visible in a scope."""
    bindings: dict[str, str] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            template = _literal_template(node.value)
            if isinstance(target, ast.Name) and template is not None:
                bindings[target.id] = template
    return bindings


def _resolve_path_arg(
    path_arg: ast.expr, bindings: dict[str, str]
) -> str | None:
    template = _literal_template(path_arg)
    if template is not None:
        return template
    if isinstance(path_arg, ast.Name):
        return bindings.get(path_arg.id)
    return None


def _extract_endpoints() -> set[tuple[str, str]]:
    """Every (METHOD, path_template) the CLI constructs, via AST scan.

    Resolves one level of local binding (``path = f"..."; client.request(m, path)``)
    per function scope; paths assembled by concatenation or helpers are not
    tracked and the guardrail test fails if the inventory shrinks.
    """
    endpoints: set[tuple[str, str]] = set()
    for source_dir in SOURCE_DIRS:
        for py_file in sorted(source_dir.rglob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            module_bindings = _scope_templates(tree)
            scopes: list[ast.AST] = [tree] + [
                node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            for scope in scopes:
                bindings = {**module_bindings, **_scope_templates(scope)}
                for node in ast.walk(scope):
                    if not isinstance(node, ast.Call) or len(node.args) < 2:
                        continue
                    func = node.func
                    if not (
                        isinstance(func, ast.Attribute)
                        and func.attr in {"request", "stream_request", "call", "call_all"}
                    ):
                        continue
                    method_arg = node.args[0]
                    if not (isinstance(method_arg, ast.Constant) and method_arg.value in HTTP_METHODS):
                        continue
                    template = _resolve_path_arg(node.args[1], bindings)
                    if template is not None and template.startswith("/api/v1"):
                        endpoints.add((method_arg.value, template))
    return endpoints


def _load_spec() -> dict:
    assert OPENAPI_PATH.exists(), (
        "docs/api/openapi.yaml missing — run: python backend/scripts/export_openapi.py"
    )
    with open(OPENAPI_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile("^" + r"\{[^}/]+\}".join(re.escape(part) for part in parts) + "$")


def _match_spec_path(spec_paths: dict, template: str) -> str | None:
    matcher = _template_regex(template)
    for candidate in spec_paths:
        if matcher.match(candidate):
            return candidate
    return None


def test_cli_endpoints_exist_in_openapi_contract():
    spec = _load_spec()
    spec_paths = spec["paths"]
    missing = []
    for method, template in sorted(_extract_endpoints()):
        matched = _match_spec_path(spec_paths, template)
        if matched is None or method.lower() not in spec_paths[matched]:
            missing.append(f"{method} {template}")
    assert not missing, (
        "CLI builds requests the published contract does not define "
        f"(request-construction drift): {missing}. If the endpoint is new, "
        "regenerate: python backend/scripts/export_openapi.py"
    )


def test_cli_path_parameters_are_declared():
    spec = _load_spec()
    spec_paths = spec["paths"]
    undeclared = []
    for method, template in sorted(_extract_endpoints()):
        matched = _match_spec_path(spec_paths, template)
        if matched is None:
            continue  # covered by the existence test above
        cli_placeholders = len(re.findall(r"\{[^}]+\}", template))
        spec_placeholders = len(re.findall(r"\{[^}]+\}", matched))
        if cli_placeholders != spec_placeholders:
            undeclared.append(f"{method} {template} → spec {matched}")
        # every interpolated slot must have a matching declared path parameter
        if cli_placeholders == spec_placeholders:
            operation = spec_paths[matched].get(method.lower(), {})
            declared = {
                p["name"]
                for p in operation.get("parameters", [])
                if p.get("in") == "path"
            }
            spec_slots = set(re.findall(r"\{([^}]+)\}", matched))
            if declared and spec_slots - declared:
                undeclared.append(
                    f"{method} {template}: spec slots {sorted(spec_slots - declared)} "
                    "missing parameter declarations"
                )
    assert not undeclared, (
        "CLI interpolates path parameters the spec does not declare "
        f"(parameter-name drift): {undeclared}"
    )


def test_cli_endpoints_declare_success_response():
    spec = _load_spec()
    spec_paths = spec["paths"]
    no_success = []
    for method, template in sorted(_extract_endpoints()):
        matched = _match_spec_path(spec_paths, template)
        if matched is None:
            continue
        responses = spec_paths[matched].get(method.lower(), {}).get("responses", {})
        if not any(code.startswith("2") for code in responses):
            no_success.append(f"{method} {template}")
    assert not no_success, (
        "CLI parses 2xx responses the spec does not declare "
        f"(response-parsing drift): {no_success}"
    )


def test_contract_artifact_has_no_daemon_surface():
    # The artifact the CLI is contracted against must be the public one:
    # zero /api/v1/daemon/ paths and no daemon schemas (cli.md §5.4).
    spec = _load_spec()
    daemon_paths = [p for p in spec["paths"] if p.startswith("/api/v1/daemon/")]
    daemon_schemas = [
        name
        for name in spec.get("components", {}).get("schemas", {})
        if "daemon" in name.lower()
    ]
    assert daemon_paths == []
    assert daemon_schemas == []


def test_endpoint_inventory_guardrail():
    # Protects the AST scanner: a call-form change must fail here, not
    # silently empty the inventory and pass the drift tests vacuously.
    assert len(_extract_endpoints()) >= MIN_EXPECTED_ENDPOINTS
