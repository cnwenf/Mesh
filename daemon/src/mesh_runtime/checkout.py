"""Checkout helper — exact-SHA checkout with read/write credential separation
(runtime-executor.md §3.2).

Runs OUTSIDE the sandbox before the provider starts:

- the repo URL must equal the frozen snapshot value, be in the workspace
  ``allowed_repos`` list, and (platform-managed runtimes) pass the public-
  address SSRF gate — the checkout helper never trusts task-supplied URLs;
- read-only credentials travel ONLY in the git subprocess environment
  (``GIT_CONFIG_COUNT``-scoped ``http.extraHeader``) — never in the remote
  URL, never in .git/config, never in provider env; after the fetch the
  process is gone and so is the credential;
- the worktree lands on the frozen base SHA; the sandbox gets no write
  credential, so ``git push`` from inside fails even with shell access —
  pushes go through the ActionBroker after human approval (§3.3).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.errors import DaemonError
from mesh_runtime.netguard import ForbiddenAddressError, assert_url_host_public

_DEFAULT_TIMEOUT_SECONDS = 300.0
_DIFF_MAX_BYTES = 2 * 1024 * 1024


class CheckoutError(DaemonError):
    """Checkout refused or failed. ``reason`` is a fixed code (no URL/path
    echo): repo_not_allowed | private_address_forbidden | clone_failed |
    sha_mismatch."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FrozenRepo:
    url: str
    base_ref: str
    base_sha: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> FrozenRepo | None:
        repo = snapshot.get("repo") if isinstance(snapshot, dict) else None
        if not isinstance(repo, dict):
            return None
        url = repo.get("url")
        if not isinstance(url, str) or not url:
            return None
        base_ref = repo.get("base_ref")
        base_sha = repo.get("base_sha")
        return cls(
            url=url,
            base_ref=base_ref if isinstance(base_ref, str) and base_ref else "main",
            base_sha=base_sha if isinstance(base_sha, str) and base_sha else None,
        )


@dataclass(frozen=True)
class CheckoutResult:
    commit_sha: str
    worktree: str


def repo_is_allowed(url: str, allowed_repos: list[str]) -> bool:
    """Workspace allowlist semantics (server parity): exact match, or prefix
    match when the allowlist entry ends with '/' (org-level grant)."""
    for entry in allowed_repos:
        if not isinstance(entry, str) or not entry:
            continue
        if entry.endswith("/"):
            if url.startswith(entry):
                return True
        elif url == entry:
            return True
    return False


class CheckoutHelper:
    def __init__(
        self,
        *,
        git_bin: str = "git",
        worktree: Path,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._git = git_bin
        self._worktree = Path(worktree)
        self._timeout = timeout

    async def prepare(
        self,
        repo: FrozenRepo,
        *,
        allowed_repos: list[str],
        platform_managed: bool,
        read_credential: str | None = None,
        working_branch: str = "agent/local",
    ) -> CheckoutResult:
        # Gate BEFORE any git process exists (no side effects on refusal).
        if not repo_is_allowed(repo.url, allowed_repos):
            raise CheckoutError("repo url is not in the workspace allowlist", reason="repo_not_allowed")
        if platform_managed:
            try:
                assert_url_host_public(repo.url)
            except ForbiddenAddressError:
                raise CheckoutError(
                    "repo url failed the public-address gate", reason="private_address_forbidden"
                ) from None
        env = self._git_env(read_credential)
        await self._run(env, "init", "--quiet")
        await self._run(env, "remote", "add", "origin", repo.url)  # URL carries NO credential
        ref = repo.base_sha or repo.base_ref
        try:
            await self._run(env, "fetch", "--quiet", "--depth", "1", "origin", ref)
        except CheckoutError:
            raise
        except DaemonError as exc:
            raise CheckoutError("git fetch failed", reason="clone_failed") from exc
        sha = await self._run(env, "rev-parse", "FETCH_HEAD")
        sha = sha.strip()
        if repo.base_sha and sha != repo.base_sha:
            raise CheckoutError("fetched SHA does not match the frozen base_sha", reason="sha_mismatch")
        await self._run(env, "checkout", "--quiet", "-B", working_branch, sha)
        return CheckoutResult(commit_sha=sha, worktree=str(self._worktree))

    async def export_diff(self) -> str:
        """git diff of worktree changes, capped at the frozen diff budget."""
        out = await self._run(self._git_env(None), "diff", "HEAD")
        if len(out.encode("utf-8")) > _DIFF_MAX_BYTES:
            return out.encode("utf-8")[:_DIFF_MAX_BYTES].decode("utf-8", errors="ignore")
        return out

    @staticmethod
    def _git_env(read_credential: str | None) -> dict:
        """Env-scoped, short-lived read-only credential plumbing. The value
        exists only inside the git subprocess — never in the remote URL,
        .git/config, or anything the sandbox can later read (§3.2)."""
        env = {
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",  # never prompt; fail instead
            "HOME": "/nonexistent",  # no host gitconfig/credentials
        }
        if read_credential:
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
            env["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {read_credential}"
        return env

    async def _run(self, env: dict, *args: str) -> str:
        self._worktree.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            self._git, *args,
            cwd=str(self._worktree),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise CheckoutError("git operation timed out", reason="clone_failed") from None
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
            # Fixed reason codes only — never echo git's stderr upstream.
            raise CheckoutError(
                f"git {args[0]} exited {proc.returncode}: {detail[-1][:120] if detail else ''}",
                reason="clone_failed",
            )
        return stdout.decode("utf-8", errors="replace")
