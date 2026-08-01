"""Fail-closed repository source-provenance scanner.

Pattern rules are deliberately absent from this module. CI injects them from a
repository-level secret; local audits may use an external file. Diagnostics
report only a rule number and location so a match is never copied into logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PATTERN_ENV = "MESH_FORBIDDEN_SOURCE_PATTERNS"


class ConfigurationError(RuntimeError):
    """Raised when the external audit configuration or repository is invalid."""


@dataclass(frozen=True)
class Violation:
    source: str
    line: int
    rule: int

    def as_dict(self) -> dict[str, str | int]:
        return {"source": self.source, "line": self.line, "rule": self.rule}


@dataclass(frozen=True)
class ScanResult:
    files_scanned: int
    violations: tuple[Violation, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "failed" if self.violations else "passed",
            "files_scanned": self.files_scanned,
            "git_metadata_scanned": True,
            "violations": [item.as_dict() for item in self.violations],
        }


def load_patterns(pattern_file: Path | None) -> list[re.Pattern[str]]:
    """Load newline-delimited regex rules from an external source."""
    if pattern_file is None:
        raw = os.environ.get(PATTERN_ENV, "")
    else:
        try:
            raw = pattern_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError("cannot read external pattern file") from exc
    lines = [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ConfigurationError("external pattern source is required")

    patterns: list[re.Pattern[str]] = []
    for number, line in enumerate(lines, 1):
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error as exc:
            raise ConfigurationError(f"invalid rule {number}") from exc
    return patterns


def scan_text(source: str, text: str, patterns: list[re.Pattern[str]]) -> list[Violation]:
    """Return redacted violation locations for one text source."""
    violations: list[Violation] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule_number, pattern in enumerate(patterns, 1):
            if pattern.search(line):
                violations.append(Violation(source=source, line=line_number, rule=rule_number))
    return violations


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ConfigurationError(f"git {args[0]} failed")
    return completed.stdout


def _tracked_texts(root: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_name in _git(root, "ls-files", "-z").split(b"\0"):
        if not raw_name:
            continue
        relative = raw_name.decode("utf-8", errors="surrogateescape")
        path = root / relative
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            entries.append((relative, os.readlink(path)))
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        entries.append((relative, data.decode("utf-8", errors="replace")))
    return entries


def scan_repository(root: Path, patterns: list[re.Pattern[str]]) -> ScanResult:
    """Scan current tracked text plus complete commit messages/authors and refs."""
    root = root.resolve()
    if not root.is_dir():
        raise ConfigurationError("repository root is not a directory")

    tracked = _tracked_texts(root)
    violations: list[Violation] = []
    for source, text in tracked:
        violations.extend(scan_text(source, text, patterns))

    log_text = _git(root, "log", "--all", "--format=%H%n%B%n%an%n%ae%n--END--").decode(
        "utf-8", errors="replace"
    )
    ref_text = _git(root, "for-each-ref", "--format=%(refname)").decode("utf-8", errors="replace")
    violations.extend(scan_text("<git-log>", log_text, patterns))
    violations.extend(scan_text("<git-refs>", ref_text, patterns))
    return ScanResult(files_scanned=len(tracked), violations=tuple(violations))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan repository source-provenance policy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--patterns-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = scan_repository(args.root, load_patterns(args.patterns_file))
    except ConfigurationError as exc:
        print(f"source provenance scan configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 1 if result.violations else 0


if __name__ == "__main__":  # pragma: no cover - exercised by the workflow entrypoint
    raise SystemExit(main())
