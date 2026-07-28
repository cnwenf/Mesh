"""Minimal SemVer helpers for the update / auto-update policy (skill.md §4.4).

``auto_update=true`` follows ONLY non-breaking PATCH versions: same major,
same minor, higher patch, no pre-release tags involved, AND (checked by the
caller) every script ``content_hash`` unchanged. Anything else requires an
explicit upgrade — or, for untrusted sources with changed scripts, a fresh
human approval round.
"""

from __future__ import annotations

from dataclasses import dataclass

from mesh.skill.manifest import SEMVER_PATTERN


@dataclass(frozen=True)
class SemVer:
    """A parsed semantic version (pre-release awareness, no build metadata)."""

    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease is not None

    def core_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def parse_semver(version: str) -> SemVer | None:
    """Parse ``X.Y.Z[-pre]``; None when the string is not valid SemVer."""
    if not isinstance(version, str):
        return None
    match = SEMVER_PATTERN.match(version)
    if match is None:
        return None
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    prerelease = match.group(4)
    return SemVer(
        major=major,
        minor=minor,
        patch=patch,
        prerelease=prerelease[1:] if prerelease else None,
    )


def compare_semver(left: str, right: str) -> int | None:
    """Return -1 / 0 / 1 for ``left`` vs ``right``; None when unparseable.

    Pre-release versions sort BEFORE their release (SemVer §11) — a
    pre-release never counts as a plain PATCH update.
    """
    parsed_left = parse_semver(left)
    parsed_right = parse_semver(right)
    if parsed_left is None or parsed_right is None:
        return None
    if parsed_left.core_tuple() != parsed_right.core_tuple():
        return -1 if parsed_left.core_tuple() < parsed_right.core_tuple() else 1
    # Same core version: release > pre-release; two pre-releases compare
    # lexically (good enough for the update gate, which rejects both).
    if parsed_left.prerelease == parsed_right.prerelease:
        return 0
    if parsed_left.prerelease is None:
        return 1
    if parsed_right.prerelease is None:
        return -1
    return -1 if parsed_left.prerelease < parsed_right.prerelease else 1


def is_non_breaking_patch(current: str, candidate: str) -> bool:
    """True when ``candidate`` is a pure PATCH bump over ``current``.

    Same major, same minor, strictly higher patch, NEITHER side a
    pre-release. Used by the auto-update gate (skill.md §4.4) — the caller
    additionally requires unchanged script content hashes.
    """
    parsed_current = parse_semver(current)
    parsed_candidate = parse_semver(candidate)
    if parsed_current is None or parsed_candidate is None:
        return False
    if parsed_current.is_prerelease or parsed_candidate.is_prerelease:
        return False
    return (
        parsed_candidate.major == parsed_current.major
        and parsed_candidate.minor == parsed_current.minor
        and parsed_candidate.patch > parsed_current.patch
    )


__all__ = ["SemVer", "compare_semver", "is_non_breaking_patch", "parse_semver"]
