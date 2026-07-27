"""Manifest validation tests (skill.md §3.3: validation_error vs manifest_invalid).

Structural problems (wrong shapes) → 400 ``validation_error``; semantic
problems (missing instructions, unknown runtime, bad SemVer) → 422
``manifest_invalid``.
"""

from __future__ import annotations

import pytest

from mesh.errors import BusinessRuleError, ValidationError
from mesh.skill.manifest import validate_manifest


def _valid_manifest(**overrides) -> dict:
    manifest = {
        "name": "发布检查清单",
        "version": "1.3.0",
        "summary": "发布前的标准检查流程",
        "instructions": "## 发布前检查\n1. 运行回归测试",
        "scripts": [
            {
                "path": "scripts/check.sh",
                "runtime": "shell",
                "entrypoint": True,
                "required_capabilities": ["exec:shell"],
            }
        ],
        "references": [{"path": "docs/runbook.md", "media_type": "text/markdown"}],
        "triggers": [{"trigger_type": "keyword", "pattern": "发布 release", "weight": 1.5}],
        "tags": ["release"],
        "required_capabilities": ["exec:shell", "net:outbound"],
    }
    manifest.update(overrides)
    return manifest


class TestValid:
    def test_full_manifest_normalizes(self) -> None:
        normalized = validate_manifest(_valid_manifest())
        assert normalized["name"] == "发布检查清单"
        assert normalized["version"] == "1.3.0"
        assert len(normalized["scripts"]) == 1
        assert normalized["scripts"][0]["runtime"] == "shell"
        assert normalized["triggers"][0]["weight"] == 1.5

    def test_minimal_manifest_fills_defaults(self) -> None:
        normalized = validate_manifest(
            {"name": "s", "version": "0.1.0", "instructions": "do the thing"}
        )
        assert normalized["scripts"] == []
        assert normalized["references"] == []
        assert normalized["triggers"] == []
        assert normalized["tags"] == []
        assert normalized["required_capabilities"] == []
        assert normalized["summary"] == ""

    def test_prerelease_version_accepted(self) -> None:
        normalized = validate_manifest(
            {"name": "s", "version": "2.0.0-beta.1", "instructions": "x"}
        )
        assert normalized["version"] == "2.0.0-beta.1"

    def test_object_capability_declarations_accepted(self) -> None:
        normalized = validate_manifest(
            _valid_manifest(
                required_capabilities=[
                    {"capability": "exec:shell", "permission": "read_only"}
                ]
            )
        )
        assert normalized["required_capabilities"][0]["permission"] == "read_only"


class TestStructuralErrors:
    def test_not_an_object(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_manifest(["not", "a", "manifest"])
        assert exc_info.value.status_code == 400

    def test_missing_name(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest({"version": "1.0.0", "instructions": "x"})

    def test_scripts_not_a_list(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest(_valid_manifest(scripts={"path": "x.sh"}))

    def test_script_entry_not_an_object(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest(_valid_manifest(scripts=["scripts/check.sh"]))

    def test_script_path_missing(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest(_valid_manifest(scripts=[{"runtime": "shell"}]))

    def test_trigger_weight_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest(
                _valid_manifest(triggers=[{"trigger_type": "keyword", "pattern": "x",
                                          "weight": "high"}])
            )

    def test_tags_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest(_valid_manifest(tags="release"))

    def test_capability_declaration_wrong_shape(self) -> None:
        with pytest.raises(ValidationError):
            validate_manifest(_valid_manifest(required_capabilities={"exec:shell": True}))


class TestSemanticErrors:
    def test_missing_instructions(self) -> None:
        with pytest.raises(BusinessRuleError) as exc_info:
            validate_manifest({"name": "s", "version": "1.0.0"})
        assert exc_info.value.code == "manifest_invalid"
        assert exc_info.value.status_code == 422

    def test_empty_instructions(self) -> None:
        with pytest.raises(BusinessRuleError) as exc_info:
            validate_manifest({"name": "s", "version": "1.0.0", "instructions": "  "})
        assert exc_info.value.code == "manifest_invalid"

    def test_invalid_semver(self) -> None:
        with pytest.raises(BusinessRuleError) as exc_info:
            validate_manifest({"name": "s", "version": "v1.0", "instructions": "x"})
        assert exc_info.value.code == "manifest_invalid"

    def test_unknown_runtime(self) -> None:
        with pytest.raises(BusinessRuleError) as exc_info:
            validate_manifest(
                _valid_manifest(scripts=[{"path": "x.ps1", "runtime": "powershell"}])
            )
        assert exc_info.value.code == "manifest_invalid"

    def test_unknown_trigger_type(self) -> None:
        with pytest.raises(BusinessRuleError):
            validate_manifest(
                _valid_manifest(triggers=[{"trigger_type": "vibes", "pattern": "x"}])
            )

    def test_unsafe_script_path_traversal(self) -> None:
        with pytest.raises(BusinessRuleError):
            validate_manifest(
                _valid_manifest(scripts=[{"path": "../escape.sh", "runtime": "shell"}])
            )

    def test_absolute_script_path_refused(self) -> None:
        with pytest.raises(BusinessRuleError):
            validate_manifest(
                _valid_manifest(scripts=[{"path": "/etc/passwd", "runtime": "shell"}])
            )

    def test_duplicate_script_paths(self) -> None:
        with pytest.raises(BusinessRuleError):
            validate_manifest(
                _valid_manifest(
                    scripts=[
                        {"path": "s/a.sh", "runtime": "shell"},
                        {"path": "s/a.sh", "runtime": "python"},
                    ]
                )
            )

    def test_invalid_capability_permission(self) -> None:
        with pytest.raises(BusinessRuleError) as exc_info:
            validate_manifest(
                _valid_manifest(
                    required_capabilities=[
                        {"capability": "exec:shell", "permission": "god_mode"}
                    ]
                )
            )
        assert exc_info.value.code == "manifest_invalid"
