from __future__ import annotations

import importlib
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _module():
    return importlib.import_module("mesh.compliance.source_provenance")


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def test_docker_builder_meets_declared_node_engine() -> None:
    package = json.loads((REPO_ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    minimum = tuple(map(int, re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", package["engines"]["node"]).groups()))
    dockerfile = (REPO_ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^FROM node:(\d+)\.(\d+)\.(\d+)-alpine AS build$", dockerfile, re.MULTILINE)

    assert image is not None, "builder must pin an exact Node patch release"
    assert tuple(map(int, image.groups())) >= minimum


def test_clean_room_spec_requires_external_pattern_source() -> None:
    rules = (REPO_ROOT / "docs/specs/frontend/clean-room-rules.md").read_text(encoding="utf-8")

    assert "SENSITIVE_PATTERNS=(" not in rules
    assert 'PATTERNS="' not in rules
    assert "MESH_FORBIDDEN_SOURCE_PATTERNS" in rules
    assert "仓库外" in rules


def test_pattern_loading_fails_closed_without_external_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _module()
    monkeypatch.delenv("MESH_FORBIDDEN_SOURCE_PATTERNS", raising=False)

    with pytest.raises(checker.ConfigurationError, match="external pattern source is required"):
        checker.load_patterns(None)


def test_pattern_loading_rejects_invalid_regex(tmp_path: Path) -> None:
    checker = _module()
    pattern_file = tmp_path / "patterns.txt"
    pattern_file.write_text("[invalid\n", encoding="utf-8")

    with pytest.raises(checker.ConfigurationError, match="invalid rule 1"):
        checker.load_patterns(pattern_file)


def test_pattern_loading_reports_unreadable_external_file(tmp_path: Path) -> None:
    checker = _module()

    with pytest.raises(checker.ConfigurationError, match="cannot read external pattern file"):
        checker.load_patterns(tmp_path / "missing.txt")


def test_pattern_loading_accepts_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _module()
    monkeypatch.setenv("MESH_FORBIDDEN_SOURCE_PATTERNS", "# managed externally\nfirst-rule\nsecond-rule\n")

    patterns = checker.load_patterns(None)

    assert [pattern.pattern for pattern in patterns] == ["first-rule", "second-rule"]


def test_scan_diagnostics_do_not_echo_pattern_or_source_line() -> None:
    checker = _module()
    marker = "synthetic-blocked.invalid"
    violations = checker.scan_text("sample.txt", f"safe\n{marker}\n", [re.compile(marker, re.IGNORECASE)])

    assert [item.as_dict() for item in violations] == [{"source": "sample.txt", "line": 2, "rule": 1}]
    assert marker not in repr(violations)


def test_repository_scan_covers_current_commit_messages_and_refs(tmp_path: Path) -> None:
    checker = _module()
    marker = "synthetic-blocked.invalid"
    _run("git", "init", "-q", cwd=tmp_path)
    _run("git", "config", "user.name", "Test User", cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("safe\n", encoding="utf-8")
    _run("git", "add", "tracked.txt", cwd=tmp_path)
    _run("git", "commit", "-qm", marker, cwd=tmp_path)
    _run("git", "branch", f"audit/{marker}", cwd=tmp_path)

    result = checker.scan_repository(tmp_path, [re.compile(re.escape(marker), re.IGNORECASE)])
    sources = {item.source for item in result.violations}

    assert result.files_scanned == 1
    assert sources == {"<git-log>", "<git-refs>"}


def test_repository_scan_and_cli_cover_commit_unique_to_another_ref(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _module()
    marker = "synthetic-blocked.invalid"
    _run("git", "init", "-q", "-b", "main", cwd=tmp_path)
    _run("git", "config", "user.name", "Test User", cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("safe\n", encoding="utf-8")
    _run("git", "add", "tracked.txt", cwd=tmp_path)
    _run("git", "commit", "-qm", "safe main commit", cwd=tmp_path)

    _run("git", "switch", "-q", "-c", "audit-only", cwd=tmp_path)
    alternate = tmp_path / "alternate.txt"
    alternate.write_text("safe alternate content\n", encoding="utf-8")
    _run("git", "add", "alternate.txt", cwd=tmp_path)
    _run("git", "commit", "-qm", marker, cwd=tmp_path)
    _run("git", "switch", "-q", "main", cwd=tmp_path)

    assert marker not in _run("git", "log", "--format=%B", "HEAD", cwd=tmp_path).stdout
    assert marker in _run("git", "log", "--all", "--format=%B", cwd=tmp_path).stdout

    patterns = [re.compile(re.escape(marker), re.IGNORECASE)]
    result = checker.scan_repository(tmp_path, patterns)
    sources = {item.source for item in result.violations}

    pattern_file = tmp_path / "external-patterns.txt"
    pattern_file.write_text(re.escape(marker), encoding="utf-8")
    exit_code = checker.main(["--root", str(tmp_path), "--patterns-file", str(pattern_file)])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result.files_scanned == 1
    assert (sources, exit_code, payload["status"]) == ({"<git-log>"}, 1, "failed")
    assert {item["source"] for item in payload["violations"]} == {"<git-log>"}
    assert marker not in output


def test_repository_scan_reports_current_tracked_file_and_skips_binary(tmp_path: Path) -> None:
    checker = _module()
    marker = "synthetic-blocked.invalid"
    _run("git", "init", "-q", cwd=tmp_path)
    _run("git", "config", "user.name", "Test User", cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path)
    (tmp_path / "tracked.txt").write_text(f"safe\n{marker}\n", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"\x00" + marker.encode())
    _run("git", "add", "tracked.txt", "asset.bin", cwd=tmp_path)
    _run("git", "commit", "-qm", "clean commit", cwd=tmp_path)

    result = checker.scan_repository(tmp_path, [re.compile(re.escape(marker), re.IGNORECASE)])

    assert result.files_scanned == 1
    assert [item.as_dict() for item in result.violations] == [{"source": "tracked.txt", "line": 2, "rule": 1}]


def test_repository_scan_handles_deleted_file_and_scans_symlink_text(tmp_path: Path) -> None:
    checker = _module()
    marker = "synthetic-blocked.invalid"
    _run("git", "init", "-q", cwd=tmp_path)
    _run("git", "config", "user.name", "Test User", cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path)
    deleted = tmp_path / "deleted.txt"
    deleted.write_text("safe\n", encoding="utf-8")
    (tmp_path / "audit-link").symlink_to(marker)
    _run("git", "add", "deleted.txt", "audit-link", cwd=tmp_path)
    _run("git", "commit", "-qm", "clean commit", cwd=tmp_path)
    deleted.unlink()

    result = checker.scan_repository(tmp_path, [re.compile(re.escape(marker), re.IGNORECASE)])

    assert result.files_scanned == 1
    assert [item.as_dict() for item in result.violations] == [{"source": "audit-link", "line": 1, "rule": 1}]


def test_repository_scan_rejects_invalid_root_and_non_git_directory(tmp_path: Path) -> None:
    checker = _module()
    patterns = [re.compile("synthetic-rule", re.IGNORECASE)]

    with pytest.raises(checker.ConfigurationError, match="repository root is not a directory"):
        checker.scan_repository(tmp_path / "missing", patterns)
    with pytest.raises(checker.ConfigurationError, match="git ls-files failed"):
        checker.scan_repository(tmp_path, patterns)


def test_cli_is_fail_closed_and_emits_redacted_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _module()
    marker = "synthetic-blocked.invalid"
    monkeypatch.delenv("MESH_FORBIDDEN_SOURCE_PATTERNS", raising=False)

    assert checker.main(["--root", str(tmp_path)]) == 2
    assert "external pattern source is required" in capsys.readouterr().err

    _run("git", "init", "-q", cwd=tmp_path)
    _run("git", "config", "user.name", "Test User", cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("safe\n", encoding="utf-8")
    _run("git", "add", "tracked.txt", cwd=tmp_path)
    _run("git", "commit", "-qm", "clean commit", cwd=tmp_path)
    pattern_file = tmp_path / "external-patterns.txt"
    pattern_file.write_text(marker, encoding="utf-8")

    assert checker.main(["--root", str(tmp_path), "--patterns-file", str(pattern_file)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"

    tracked.write_text(f"{marker}\n", encoding="utf-8")
    assert checker.main(["--root", str(tmp_path), "--patterns-file", str(pattern_file)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "failed"
    assert marker not in output
