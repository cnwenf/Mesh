"""Capability manifest tests (runtime-executor.md §1.4, §5.4 supply chain).

The manifest is the IMMUTABLE release-pinned contract: exact provider/version,
binary SHA-256, the required §1.4 flag set, hard-limit capability assertions.
Loading is fail-closed on any doubt.
"""

import json
from pathlib import Path

import pytest

from mesh_runtime.manifest import (
    ManifestError,
    ProviderManifest,
    load_provider_manifest,
)

GOOD_SHA = "a" * 64

_REQUIRED_FLAGS = [
    "--print", "--output-format", "--input-format", "--verbose", "--bare",
    "--disable-slash-commands", "--no-session-persistence", "--setting-sources",
    "--strict-mcp-config", "--mcp-config", "--settings", "--system-prompt-file",
    "--tools", "--disallowed-tools", "--permission-mode", "--max-budget-usd",
]

GOOD_TOML = f"""
provider = "claude-code"
version = "2.1.218"
binary_sha256 = "{GOOD_SHA}"
required_flags = {json.dumps(_REQUIRED_FLAGS)}

[hard_limits]
usd_budget = true
wall_timeout = true
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "manifest.toml"
    p.write_text(content, encoding="utf-8")
    return p


class TestLoadValid:
    def test_loads_pinned_manifest(self, tmp_path):
        manifest = load_provider_manifest(_write(tmp_path, GOOD_TOML))
        assert manifest.provider == "claude-code"
        assert manifest.version == "2.1.218"
        assert manifest.binary_sha256 == GOOD_SHA
        assert "--bare" in manifest.required_flags
        assert "--max-budget-usd" in manifest.required_flags
        assert manifest.hard_limits_usd_budget is True
        assert manifest.hard_limits_wall_timeout is True

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ManifestError):
            load_provider_manifest(tmp_path / "absent.toml")

    def test_unparseable_toml_raises(self, tmp_path):
        with pytest.raises(ManifestError):
            load_provider_manifest(_write(tmp_path, "not [ toml"))


class TestFailClosedValidation:
    @pytest.mark.parametrize(
        ("mutation", "reason_part"),
        [
            ('provider = 5\n', "provider"),
            ('version = ""\n', "version"),
            ('binary_sha256 = "abc"\n', "binary_sha256"),
            ('binary_sha256 = "' + "Z" * 64 + '"\n', "binary_sha256"),
            ("required_flags = []\n", "required_flags"),
            ('required_flags = ["--add-dir"]\n', "escalation"),
            ('required_flags = ["--unknown-flag"]\n', "flag"),
            ("[hard_limits]\nusd_budget = false\nwall_timeout = true\n", "usd_budget"),
            ("[hard_limits]\nusd_budget = true\nwall_timeout = false\n", "wall_timeout"),
        ],
    )
    def test_rejects_invalid_manifests(self, tmp_path, mutation, reason_part):
        content_lines = GOOD_TOML.splitlines()
        for mutation_line in mutation.splitlines():
            key = mutation_line.split("=", 1)[0].strip().lstrip("[").rstrip("]").strip()
            replaced = False
            for i, existing in enumerate(content_lines):
                existing_key = existing.split("=", 1)[0].strip().lstrip("[").rstrip("]").strip()
                if existing_key == key:
                    content_lines[i] = mutation_line
                    replaced = True
                    break
            if not replaced:
                content_lines.append(mutation_line)
        with pytest.raises(ManifestError, match=reason_part):
            load_provider_manifest(_write(tmp_path, "\n".join(content_lines)))

    def test_missing_hard_limits_section_raises(self, tmp_path):
        content = GOOD_TOML.split("[hard_limits]")[0]
        with pytest.raises(ManifestError, match="hard_limits"):
            load_provider_manifest(_write(tmp_path, content))

    def test_unknown_provider_name_raises(self, tmp_path):
        content = GOOD_TOML.replace('"claude-code"', '"something-else"')
        with pytest.raises(ManifestError, match="provider"):
            load_provider_manifest(_write(tmp_path, content))


class TestManifestObject:
    def test_fingerprint_is_stable_and_sha_prefixed(self):
        manifest = ProviderManifest(
            provider="claude-code",
            version="1.0.0",
            binary_sha256=GOOD_SHA,
            required_flags=("--print",),
            hard_limits_usd_budget=True,
            hard_limits_wall_timeout=True,
        )
        fp = manifest.fingerprint()
        assert fp.startswith("sha256:")
        assert fp == manifest.fingerprint()

    def test_capabilities_report_hard_limits(self):
        manifest = ProviderManifest(
            provider="claude-code",
            version="1.0.0",
            binary_sha256=GOOD_SHA,
            required_flags=("--print",),
            hard_limits_usd_budget=True,
            hard_limits_wall_timeout=True,
        )
        caps = manifest.capabilities()
        assert "coding_cli.claude_code" in caps
        assert "budget.usd_hard" in caps
        assert "budget.wall_hard" in caps
