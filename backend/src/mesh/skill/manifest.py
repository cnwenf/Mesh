"""Skill manifest validation (skill.md §3.1 import / §3.3 error codes).

A manifest is the parsed skill package description (``mesh.json`` at the
source root). Two failure classes, per the §3.3 error table:

* 400 ``validation_error`` — STRUCTURAL failure: not an object, wrong field
  types, arrays of non-objects ("清单 JSON Schema 校验失败");
* 422 ``manifest_invalid`` — SEMANTIC failure: structure is legal but the
  content is not (missing instructions body, unknown script runtime, bad
  SemVer, undeclared trigger type, invalid capability declaration).

Script ``runtime`` is a closed vocabulary — an unknown runtime can never
execute inside the sandbox contract, so it is rejected up front.
"""

from __future__ import annotations

import re
from typing import Any

from mesh.agent.capabilities import CapabilityInvalidError, normalize_capability_declarations
from mesh.errors import BusinessRuleError, ValidationError

# Closed script runtime vocabulary (skill.md §2.7: shell / python / …).
KNOWN_RUNTIMES = ("shell", "python", "node")

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(-[\da-zA-Z-]+(?:\.[\da-zA-Z-]+)*)?$"
)

MAX_INSTRUCTIONS_CHARS = 200_000
MAX_SCRIPTS_PER_VERSION = 64
MAX_REFERENCES_PER_VERSION = 256
MAX_TRIGGERS_PER_VERSION = 128


def _structural_error(field: str, issue: str) -> ValidationError:
    return ValidationError(
        "manifest failed schema validation",
        details={"fields": [{"field": field, "issue": issue}]},
    )


def _semantic_error(field: str, issue: str) -> BusinessRuleError:
    return BusinessRuleError(
        "manifest is semantically invalid",
        code="manifest_invalid",
        details={"fields": [{"field": field, "issue": issue}]},
    )


def _check_capabilities(raw: Any, field: str) -> list[Any]:
    """Validate a capability declaration block via the §3.3 normalizer."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _structural_error(field, "invalid_type")
    try:
        normalize_capability_declarations(raw)
    except CapabilityInvalidError as exc:
        raise _semantic_error(field, str(exc)) from exc
    return raw


def validate_manifest(raw: Any) -> dict[str, Any]:
    """Validate and normalize a raw parsed manifest.

    Returns the normalized manifest dict (defaults filled). Raises
    :class:`~mesh.errors.ValidationError` (400) for structural problems and
    :class:`~mesh.errors.BusinessRuleError` with code ``manifest_invalid``
    (422) for semantic ones.
    """
    if not isinstance(raw, dict):
        raise _structural_error("$", "manifest must be a JSON object")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _structural_error("name", "required_string")
    if len(name) > 200:
        raise _structural_error("name", "too_long")

    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise _structural_error("version", "required_string")
    if not SEMVER_PATTERN.match(version):
        raise _semantic_error("version", "invalid_semver")

    summary = raw.get("summary")
    if summary is None:
        summary = ""
    if not isinstance(summary, str):
        raise _structural_error("summary", "invalid_type")
    if len(summary) > 1000:
        raise _structural_error("summary", "too_long")

    instructions = raw.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        # Structurally present-but-empty vs missing both count as SEMANTIC
        # ("缺指令正文", §3.3 manifest_invalid example).
        if "instructions" not in raw or not isinstance(instructions, str):
            raise _semantic_error("instructions", "missing_instructions")
        raise _semantic_error("instructions", "empty_instructions")
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        raise _semantic_error("instructions", "too_long")

    scripts = _validate_scripts(raw.get("scripts"))
    references = _validate_references(raw.get("references"))
    triggers = _validate_triggers(raw.get("triggers"))

    tags = raw.get("tags")
    if tags is None:
        tags = []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise _structural_error("tags", "invalid_type")

    io_contract = raw.get("io_contract")
    if io_contract is not None and not isinstance(io_contract, dict):
        raise _structural_error("io_contract", "invalid_type")

    required_capabilities = _check_capabilities(raw.get("required_capabilities"),
                                                "required_capabilities")
    # Script-level declarations must normalize too.
    for index, script in enumerate(scripts):
        _check_capabilities(script.get("required_capabilities"),
                            f"scripts[{index}].required_capabilities")

    changelog = raw.get("changelog")
    if changelog is not None and not isinstance(changelog, str):
        raise _structural_error("changelog", "invalid_type")

    return {
        "name": name.strip(),
        "version": version,
        "summary": summary,
        "instructions": instructions,
        "scripts": scripts,
        "references": references,
        "triggers": triggers,
        "tags": tags,
        "io_contract": io_contract,
        "required_capabilities": required_capabilities,
        "changelog": changelog,
    }


def _validate_scripts(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _structural_error("scripts", "invalid_type")
    if len(raw) > MAX_SCRIPTS_PER_VERSION:
        raise _semantic_error("scripts", "too_many_scripts")
    scripts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(raw):
        field = f"scripts[{index}]"
        if not isinstance(item, dict):
            raise _structural_error(field, "invalid_type")
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise _structural_error(f"{field}.path", "required_string")
        if path.startswith("/") or ".." in path.split("/"):
            raise _semantic_error(f"{field}.path", "unsafe_path")
        if path in seen_paths:
            raise _semantic_error(f"{field}.path", "duplicate_path")
        seen_paths.add(path)
        runtime = item.get("runtime", "shell")
        if not isinstance(runtime, str):
            raise _structural_error(f"{field}.runtime", "invalid_type")
        if runtime not in KNOWN_RUNTIMES:
            raise _semantic_error(f"{field}.runtime", "unknown_runtime")
        entrypoint = item.get("entrypoint", False)
        if not isinstance(entrypoint, bool):
            raise _structural_error(f"{field}.entrypoint", "invalid_type")
        scripts.append(
            {
                "path": path,
                "runtime": runtime,
                "entrypoint": entrypoint,
                "required_capabilities": item.get("required_capabilities") or [],
            }
        )
    return scripts


def _validate_references(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _structural_error("references", "invalid_type")
    if len(raw) > MAX_REFERENCES_PER_VERSION:
        raise _semantic_error("references", "too_many_references")
    references: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        field = f"references[{index}]"
        if not isinstance(item, dict):
            raise _structural_error(field, "invalid_type")
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise _structural_error(f"{field}.path", "required_string")
        media_type = item.get("media_type", "text/markdown")
        if not isinstance(media_type, str):
            raise _structural_error(f"{field}.media_type", "invalid_type")
        summary = item.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise _structural_error(f"{field}.summary", "invalid_type")
        references.append({"path": path, "media_type": media_type, "summary": summary})
    return references


def _validate_triggers(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _structural_error("triggers", "invalid_type")
    if len(raw) > MAX_TRIGGERS_PER_VERSION:
        raise _semantic_error("triggers", "too_many_triggers")
    triggers: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        field = f"triggers[{index}]"
        if not isinstance(item, dict):
            raise _structural_error(field, "invalid_type")
        trigger_type = item.get("trigger_type", "keyword")
        if not isinstance(trigger_type, str):
            raise _structural_error(f"{field}.trigger_type", "invalid_type")
        if trigger_type not in ("keyword", "semantic", "tag"):
            raise _semantic_error(f"{field}.trigger_type", "unknown_trigger_type")
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise _structural_error(f"{field}.pattern", "required_string")
        weight = item.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
            raise _structural_error(f"{field}.weight", "invalid_type")
        triggers.append(
            {"trigger_type": trigger_type, "pattern": pattern.strip(), "weight": float(weight)}
        )
    return triggers


__all__ = [
    "KNOWN_RUNTIMES",
    "MAX_INSTRUCTIONS_CHARS",
    "SEMVER_PATTERN",
    "validate_manifest",
]
