"""Checkout helper — real local git repos; credential separation, exact-SHA
checkout, allowlist/SSRF gates (§3.2)."""

import subprocess

import pytest

from mesh_runtime.checkout import (
    CheckoutError,
    CheckoutHelper,
    FrozenRepo,
    repo_is_allowed,
)


def git(cwd, *args, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(cwd), **(env_extra or {})}
    result = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode().strip()


@pytest.fixture
def upstream(tmp_path):
    """A bare upstream repo with one commit; returns (file_url, sha)."""
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "--quiet", "--initial-branch", "main")
    git(src, "config", "user.email", "dev@example.com")
    git(src, "config", "user.name", "dev")
    (src / "app.py").write_text("print('v1')\n")
    git(src, "add", "app.py")
    git(src, "commit", "--quiet", "-m", "c1")
    sha = git(src, "rev-parse", "HEAD")
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(src), str(bare)],
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, check=True)
    return f"file://{bare}", sha


class TestRepoAllowlist:
    def test_exact_match(self):
        assert repo_is_allowed("https://g.example/a.git", ["https://g.example/a.git"])

    def test_org_prefix_match(self):
        assert repo_is_allowed("https://g.example/team/b.git", ["https://g.example/team/"])

    def test_prefix_without_slash_is_not_prefix_grant(self):
        assert not repo_is_allowed("https://g.example/teamEVIL/b.git", ["https://g.example/team"])

    def test_empty_and_junk_entries(self):
        assert not repo_is_allowed("https://g.example/a.git", [])
        assert not repo_is_allowed("https://g.example/a.git", ["", None])  # type: ignore[list-item]


class TestFrozenRepo:
    def test_from_snapshot_parses_repo_block(self):
        repo = FrozenRepo.from_snapshot({"repo": {"url": "u", "base_ref": "main", "base_sha": "abc"}})
        assert repo == FrozenRepo(url="u", base_ref="main", base_sha="abc")

    def test_from_snapshot_defaults_and_absence(self):
        assert FrozenRepo.from_snapshot({}) is None
        assert FrozenRepo.from_snapshot({"repo": {}}) is None  # no url
        repo = FrozenRepo.from_snapshot({"repo": {"url": "u"}})
        assert repo.base_ref == "main" and repo.base_sha is None


class TestPrepare:
    async def test_checks_out_exact_sha_without_credential_leak(self, tmp_path, upstream):
        url, sha = upstream
        worktree = tmp_path / "wt"
        helper = CheckoutHelper(worktree=worktree)
        result = await helper.prepare(
            FrozenRepo(url=url, base_ref="main", base_sha=sha),
            allowed_repos=[url],
            platform_managed=False,
            read_credential="rot-SECRET-value",
        )
        assert result.commit_sha == sha
        assert (worktree / "app.py").read_text() == "print('v1')\n"
        # The read credential must NOT be in the remote URL or git config.
        config = (worktree / ".git" / "config").read_text()
        assert "rot-SECRET-value" not in config
        assert "Authorization" not in config
        assert url in config  # remote URL is the plain frozen url

    async def test_refuses_url_outside_allowlist_without_side_effects(self, tmp_path, upstream):
        url, sha = upstream
        worktree = tmp_path / "wt"
        helper = CheckoutHelper(worktree=worktree)
        with pytest.raises(CheckoutError) as ei:
            await helper.prepare(
                FrozenRepo(url=url, base_ref="main", base_sha=sha),
                allowed_repos=["https://other.example/x.git"],
                platform_managed=False,
            )
        assert ei.value.reason == "repo_not_allowed"
        assert not worktree.exists()  # no git process ran at all

    async def test_platform_managed_refuses_private_address(self, tmp_path):
        helper = CheckoutHelper(worktree=tmp_path / "wt")
        with pytest.raises(CheckoutError) as ei:
            await helper.prepare(
                FrozenRepo(url="http://127.0.0.1/repo.git", base_ref="main"),
                allowed_repos=["http://127.0.0.1/repo.git"],
                platform_managed=True,
            )
        assert ei.value.reason == "private_address_forbidden"

    async def test_sha_mismatch_refused(self, tmp_path, upstream):
        url, _sha = upstream
        helper = CheckoutHelper(worktree=tmp_path / "wt")
        fake_sha = "0" * 40
        with pytest.raises(CheckoutError) as ei:
            await helper.prepare(
                FrozenRepo(url=url, base_ref="main", base_sha=fake_sha),
                allowed_repos=[url],
                platform_managed=False,
            )
        # fetch of an unknown sha fails at the fetch stage (clone_failed),
        # or — if the transport fetched — at the sha comparison.
        assert ei.value.reason in ("clone_failed", "sha_mismatch")

    async def test_unreachable_url_clone_failed(self, tmp_path):
        helper = CheckoutHelper(worktree=tmp_path / "wt", timeout=15)
        with pytest.raises(CheckoutError) as ei:
            await helper.prepare(
                FrozenRepo(url="file:///nonexistent/repo.git", base_ref="main"),
                allowed_repos=["file:///nonexistent/repo.git"],
                platform_managed=False,
            )
        assert ei.value.reason == "clone_failed"


class TestDiff:
    async def test_export_diff_reflects_worktree_changes(self, tmp_path, upstream):
        url, sha = upstream
        worktree = tmp_path / "wt"
        helper = CheckoutHelper(worktree=worktree)
        await helper.prepare(
            FrozenRepo(url=url, base_ref="main", base_sha=sha),
            allowed_repos=[url],
            platform_managed=False,
        )
        (worktree / "app.py").write_text("print('v2')\n")
        diff = await helper.export_diff()
        assert "+print('v2')" in diff
        assert "-print('v1')" in diff
