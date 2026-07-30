"""Checkout helper — exact-SHA checkout with read/write credential separation
(runtime-executor.md §3.2).

Runs OUTSIDE the sandbox before the provider starts:

- the repo URL must equal the frozen snapshot value and be in the workspace
  ``allowed_repos`` list — the checkout helper never trusts task-supplied URLs;
- on platform-managed runtimes the URL additionally passes the public-address
  SSRF gate: trusted resolution of the repo host, all-or-nothing IP filtering
  of the WHOLE answer set (§1.3 "解析 IP 不合规即失败"), and the fetch is
  PINNED to the verified IPs via ``http.curloptResolve`` so git's own
  resolver never gets a second (rebindable) look; a scheme that cannot be
  pinned fails closed;
- self-hosted runtimes intentionally skip the public-address gate (their git
  servers may legitimately be internal) — the heartbeat reports
  ``checkout_public_address_gate`` so the server can dispatch accordingly;
- the frozen snapshot MUST carry ``base_sha``: the helper fetches that exact
  SHA and verifies it, never a moving branch ref (§2.1/§2.6 fail-closed);
- read-only credentials travel ONLY in the git subprocess environment
  (``GIT_CONFIG_COUNT``-scoped ``http.extraHeader``) — never in the remote
  URL, never in .git/config, never in provider env; after the fetch the
  process is gone and so is the credential;
- the sandbox gets no write credential, so ``git push`` from inside fails
  even with shell access — pushes go through the ActionBroker after human
  approval (§3.3).
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.egress import Resolver, _default_resolver
from mesh_runtime.errors import DaemonError
from mesh_runtime.netguard import (
    ForbiddenAddressError,
    assert_url_host_public,
    filter_answer_set,
)

_DEFAULT_TIMEOUT_SECONDS = 300.0
_RESOLVE_TIMEOUT_SECONDS = 10.0
_DIFF_MAX_BYTES = 2 * 1024 * 1024

#: libcurl-backed schemes whose connections git can pin to verified IPs
#: (``http.curloptResolve``). Anything else cannot be rebinding-protected and
#: is refused on platform-managed runtimes.
_PINNABLE_SCHEMES = frozenset({"http", "https"})


class CheckoutError(DaemonError):
    """Checkout refused or failed. ``reason`` is a fixed code (no URL/path
    echo): repo_not_allowed | private_address_forbidden | unpinnable_scheme |
    base_sha_required | clone_failed | sha_mismatch."""

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
        resolver: Resolver | None = None,
    ) -> None:
        self._git = git_bin
        self._worktree = Path(worktree)
        self._timeout = timeout
        self._resolver = resolver or _default_resolver

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
        # §2.1/§2.6 fail-closed: a snapshot without base_sha would force
        # fetching a MOVING ref and skipping verification — refuse outright.
        if not repo.base_sha:
            raise CheckoutError(
                "frozen snapshot carries no base_sha; refusing a moving-ref fetch",
                reason="base_sha_required",
            )
        extra_configs: list[tuple[str, str]] = []
        if platform_managed:
            try:
                url = assert_url_host_public(repo.url)
                answers = await self._resolve_public(url.host)
            except ForbiddenAddressError:
                raise CheckoutError(
                    "repo url failed the public-address gate", reason="private_address_forbidden"
                ) from None
            # §3.4 pin discipline: git must connect ONLY to the verified IPs.
            # A scheme we cannot pin would leave a rebinding window — refuse.
            if url.scheme not in _PINNABLE_SCHEMES:
                raise CheckoutError(
                    "repo scheme cannot be IP-pinned on platform runtimes",
                    reason="unpinnable_scheme",
                )
            extra_configs.append(
                ("http.curloptResolve", f"{url.host}:{url.port}:{','.join(answers)}")
            )
        env = self._git_env(read_credential, extra_configs)
        await self._run(env, "init", "--quiet")
        await self._run(env, "remote", "add", "origin", repo.url)  # URL carries NO credential
        try:
            await self._run(env, "fetch", "--quiet", "--depth", "1", "origin", repo.base_sha)
        except CheckoutError:
            raise
        except DaemonError as exc:
            raise CheckoutError("git fetch failed", reason="clone_failed") from exc
        sha = await self._run(env, "rev-parse", "FETCH_HEAD")
        sha = sha.strip()
        if sha != repo.base_sha:  # belt and braces — never trust the transport
            raise CheckoutError("fetched SHA does not match the frozen base_sha", reason="sha_mismatch")
        await self._run(env, "checkout", "--quiet", "-B", working_branch, sha)
        return CheckoutResult(commit_sha=sha, worktree=str(self._worktree))

    async def _resolve_public(self, host: str) -> list[str]:
        """Trusted-resolver + all-answer IP filtering for the repo host
        (§1.3: resolved IPs non-compliant => fail). Literal IP hosts are
        classified without DNS. The answers are then PINNED into git so no
        second, attacker-influenced resolution happens at connect time."""
        try:
            canonical = str(ipaddress.ip_address(host))
        except ValueError:
            pass
        else:
            return filter_answer_set([canonical])
        try:
            answers = await asyncio.wait_for(
                self._resolver(host), timeout=_RESOLVE_TIMEOUT_SECONDS
            )
        except (TimeoutError, OSError) as exc:
            raise ForbiddenAddressError("untrusted resolution failed") from exc
        return filter_answer_set(answers)  # one forbidden IP rejects all

    async def export_diff(self) -> str:
        """git diff of worktree changes, capped at the frozen diff budget."""
        out = await self._run(self._git_env(None), "diff", "HEAD")
        if len(out.encode("utf-8")) > _DIFF_MAX_BYTES:
            return out.encode("utf-8")[:_DIFF_MAX_BYTES].decode("utf-8", errors="ignore")
        return out

    @staticmethod
    def _git_env(
        read_credential: str | None,
        extra_configs: Sequence[tuple[str, str]] = (),
    ) -> dict:
        """Env-scoped, short-lived git config plumbing. Values exist only
        inside the git subprocess — never in the remote URL, .git/config, or
        anything the sandbox can later read (§3.2)."""
        env = {
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",  # never prompt; fail instead
            "HOME": "/nonexistent",  # no host gitconfig/credentials
        }
        # §3.2 SSRF hardening: git must NEVER follow a cross-host redirect on
        # fetch. The public-address gate validates only the ORIGINAL url; an
        # allowlisted repo returning 3xx could otherwise steer the fetch (and,
        # on libcurl<8.0, the Authorization header) to an internal/metadata
        # endpoint, bypassing the gate. Set UNCONDITIONALLY — independent of
        # whether a read credential is present.
        configs: list[tuple[str, str]] = [("http.followRedirects", "false")]
        if read_credential:
            configs.append(("http.extraHeader", f"Authorization: Bearer {read_credential}"))
        configs.extend(extra_configs)
        env["GIT_CONFIG_COUNT"] = str(len(configs))
        for index, (key, value) in enumerate(configs):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
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
