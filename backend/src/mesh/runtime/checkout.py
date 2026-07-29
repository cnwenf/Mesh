"""Repository checkout security — runtime.md §2.2 H1 / README §6.16.

Two hard gates on every checkout:

1. **Workspace ``allowed_repos`` whitelist** — the repo URL frozen into
   ``config_snapshot.repo.url`` at enqueue time (auditable, README §6.11)
   must appear in ``workspaces.settings["allowed_repos"]``; anything else is
   403. The daemon-reported URL is validated against the frozen snapshot
   value, never trusted on its own.
2. **SSRF guard for platform-managed runtimes** — checkout targets may not be
   RFC1918 / loopback / link-local / cloud-metadata addresses (169.254.169.254
   and friends).
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.runtime import (
    RepoCheckout,
    Runtime,
    TaskExecution,
)
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ForbiddenError

# Hostnames that resolve to cloud metadata / internal services — forbidden
# regardless of DNS (defense in depth alongside the IP-range check).
FORBIDDEN_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "localhost",
    }
)


def is_forbidden_host(host: str) -> bool:
    """True for private / loopback / link-local / metadata targets."""
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        return True
    if normalized in FORBIDDEN_HOSTNAMES:
        return True
    # IPv6 zone / bracket forms.
    candidate = normalized.strip("[]")
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        # Non-IP hostname: only the known-bad list above is rejected here;
        # DNS-level blocking is enforced at the sandbox network layer.
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        # IPv4-mapped IPv6 (::ffff:10.0.0.1) — unwrap and re-check.
        or (
            addr.version == 6
            and addr.ipv4_mapped is not None
            and is_forbidden_host(str(addr.ipv4_mapped))
        )
    )


def assert_public_url(url: str) -> None:
    """SSRF red line: platform-managed checkouts may only target public hosts."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http", "git", "ssh"):
        raise ForbiddenError(
            "checkout url scheme not allowed",
            code="private_address_forbidden",
            details={"scheme": parsed.scheme},
        )
    host = parsed.hostname or ""
    if is_forbidden_host(host):
        raise ForbiddenError(
            "checkout target address is forbidden",
            code="private_address_forbidden",
            details={"host": host},
        )
    port = parsed.port
    if port is not None and port in (22,) and parsed.scheme == "http":
        raise ForbiddenError(
            "checkout target address is forbidden",
            code="private_address_forbidden",
        )


async def load_allowed_repos(session: AsyncSession, workspace_id: uuid.UUID) -> list[str]:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        return []
    settings = workspace.settings or {}
    allowed = settings.get("allowed_repos") or []
    if not isinstance(allowed, list):
        return []
    return [str(url).strip() for url in allowed if str(url).strip()]


def repo_is_allowed(repo_url: str, allowed_repos: list[str]) -> bool:
    normalized = repo_url.strip()
    for allowed in allowed_repos:
        if normalized == allowed:
            return True
        # Prefix match supports org-level allowlisting (".../team/" covers the
        # team's repositories) — the allowlist entry must end with '/'.
        if allowed.endswith("/") and normalized.startswith(allowed):
            return True
    return False


async def report_checkout(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    attempt_id: uuid.UUID,
    runtime: Runtime,
    lease_seq: int,
    status: str,
    repo_url: str | None = None,
    base_ref: str | None = None,
    commit_sha: str | None = None,
    local_path: str | None = None,
    diff: str | None = None,
    storage: object | None = None,
    signing_secret: str = "",
) -> dict:
    """Daemon: report checkout lifecycle (cloning → ready → diff_ready)."""
    from mesh.runtime.attempts import _assert_lease, _load_daemon_attempt

    workspace_id = runtime.workspace_id
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        attempt = await _load_daemon_attempt(session, attempt_id=attempt_id, runtime=runtime)
        if attempt.status not in ("claimed", "running", "cancelling"):
            raise BusinessRuleError(
                "attempt not in flight",
                code="invalid_state_transition",
                details={"status": attempt.status},
            )
        _assert_lease(attempt, lease_seq)
        execution = (
            await session.execute(
                select(TaskExecution).where(TaskExecution.id == attempt.execution_id)
            )
        ).scalar_one()

        # The FROZEN snapshot repo is the source of truth (README §6.11).
        snapshot_repo = (execution.config_snapshot or {}).get("repo") or {}
        frozen_url = snapshot_repo.get("url")
        if not frozen_url:
            raise BusinessRuleError(
                "execution has no repository configured",
                code="checkout_not_configured",
            )
        if repo_url is not None and repo_url.strip() != frozen_url:
            # The daemon may only checkout the repo the execution pinned.
            raise ForbiddenError(
                "repository not allowed for this execution",
                code="repo_not_allowed",
            )

        allowed_repos = await load_allowed_repos(session, workspace_id)
        if not repo_is_allowed(frozen_url, allowed_repos):
            raise ForbiddenError(
                "repository not in workspace allowlist",
                code="repo_not_allowed",
            )
        if runtime.kind == "platform_managed":
            assert_public_url(frozen_url)

        now = datetime.now(UTC)
        working_branch = attempt.working_branch or f"agent/{execution.id}/a{attempt.attempt_number}"
        checkout = (
            await session.execute(
                select(RepoCheckout).where(RepoCheckout.attempt_id == attempt.id)
            )
        ).scalar_one_or_none()
        if checkout is None:
            checkout = RepoCheckout(
                workspace_id=workspace_id,
                attempt_id=attempt.id,
                repo_url=frozen_url,
                base_ref=base_ref or snapshot_repo.get("base_ref") or "main",
                working_branch=working_branch,
                status="cloning",
            )
            session.add(checkout)
        checkout.status = status
        checkout.commit_sha = commit_sha or checkout.commit_sha
        checkout.local_path = local_path or checkout.local_path
        checkout.updated_at = now
        if status == "recycled":
            checkout.recycled_at = now

        diff_ref = checkout.diff_ref
        if diff and status == "diff_ready" and storage is not None:
            # §2.5 S-06: server-side fallback redaction before persisting
            # diff to object storage (daemon redacts first, server again).
            if signing_secret:
                from mesh.runtime.redaction import redact_diff_text

                diff, _diff_hits = await redact_diff_text(
                    session,
                    workspace_id=workspace_id,
                    diff=diff,
                    signing_secret=signing_secret,
                )
            key = f"logs/{workspace_id}/diffs/{attempt.id.hex}.diff"
            await storage.put_bytes(  # type: ignore[attr-defined]
                key, diff.encode("utf-8"), content_type="text/x-diff"
            )
            checkout.diff_ref = key
            diff_ref = key

        await session.flush()
        return {
            "id": str(checkout.id),
            "status": checkout.status,
            "repo_url": checkout.repo_url,
            "working_branch": checkout.working_branch,
            "diff_ref": diff_ref,
        }
