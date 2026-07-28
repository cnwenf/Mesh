"""SemVer helper tests (skill.md §4.4 auto-update gate)."""

from __future__ import annotations

import pytest

from mesh.skill.semver import compare_semver, is_non_breaking_patch, parse_semver


class TestParse:
    def test_plain_version(self) -> None:
        parsed = parse_semver("1.2.3")
        assert parsed is not None
        assert (parsed.major, parsed.minor, parsed.patch) == (1, 2, 3)
        assert not parsed.is_prerelease

    def test_prerelease_version(self) -> None:
        parsed = parse_semver("2.0.0-rc.1")
        assert parsed is not None
        assert parsed.prerelease == "rc.1"

    @pytest.mark.parametrize("bad", ["v1.2.3", "1.2", "1", "01.2.3", "a.b.c", "", None])
    def test_invalid_versions(self, bad: object) -> None:
        assert parse_semver(bad) is None


class TestCompare:
    def test_ordering(self) -> None:
        assert compare_semver("1.0.0", "1.0.1") == -1
        assert compare_semver("1.1.0", "1.0.9") == 1
        assert compare_semver("1.2.3", "1.2.3") == 0
        assert compare_semver("2.0.0", "10.0.0") == -1

    def test_prerelease_sorts_before_release(self) -> None:
        assert compare_semver("1.0.0-alpha", "1.0.0") == -1
        assert compare_semver("1.0.0", "1.0.0-alpha") == 1
        assert compare_semver("1.0.0-alpha", "1.0.0-beta") == -1

    def test_unparseable_returns_none(self) -> None:
        assert compare_semver("nope", "1.0.0") is None


class TestNonBreakingPatch:
    def test_patch_bump_is_non_breaking(self) -> None:
        assert is_non_breaking_patch("1.2.3", "1.2.4")

    def test_minor_bump_is_breaking_for_auto_update(self) -> None:
        assert not is_non_breaking_patch("1.2.3", "1.3.0")

    def test_major_bump_is_breaking(self) -> None:
        assert not is_non_breaking_patch("1.2.3", "2.0.0")

    def test_downgrade_is_not_an_update(self) -> None:
        assert not is_non_breaking_patch("1.2.4", "1.2.3")

    def test_prerelease_never_auto_followed(self) -> None:
        assert not is_non_breaking_patch("1.2.3", "1.2.4-rc.1")
        assert not is_non_breaking_patch("1.2.3-rc.1", "1.2.4")

    def test_invalid_inputs(self) -> None:
        assert not is_non_breaking_patch("garbage", "1.2.4")
