"""Per-attempt security orchestration (A2 wiring).

Assembles and tears down the full isolation stack for one attempt, in the
spec order (§3.1): checkout → egress → broker → sandbox (via the adapter) →
provider → redacted reflow → cleanup (§3.6). The supervisor stays lean: it
calls :meth:`AttemptSecurity.start` before the provider runs, surfaces
``confirm_required`` through :meth:`request_approval`, and calls
:meth:`AttemptSecurity.finish` once terminal — the server, not the daemon,
owns the awaiting_approval state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from mesh_runtime.broker import ToolBrokerServer
from mesh_runtime.checkout import CheckoutHelper, CheckoutResult, FrozenRepo
from mesh_runtime.cleanup import AttemptCleaner, CleanupHandles, CleanupReport, ResourceManifest
from mesh_runtime.egress import EgressGateway
from mesh_runtime.errors import DaemonError, LeaseConflictError
from mesh_runtime.netguard import filter_answer_set

logger = logging.getLogger("mesh_runtime.security")


@dataclass(frozen=True)
class SecurityConfig:
    """Frozen, claim-derived inputs for the attempt security stack."""

    attempt_id: str
    execution_id: str
    attempt_root: Path
    task_token: str | None
    issue_id: str | None
    grants: dict  # capability key -> permission
    nonce: str
    sandbox_uid: int
    cgroup_marker: str
    repo: FrozenRepo | None = None
    allowed_repos: tuple[str, ...] = ()
    platform_managed: bool = False
    read_credential: str | None = None
    network_policy: dict = field(default_factory=dict)
    spool_dir: Path | None = None  # S-08 clears it once logs are confirmed


class AttemptSecurity:
    def __init__(
        self,
        config: SecurityConfig,
        *,
        api,
        journal,
        sandbox_manager=None,
        server_base_url: str = "",
        checkout_timeout: float = 300.0,
    ) -> None:
        self.config = config
        self._api = api
        self._journal = journal
        self._sandbox_manager = sandbox_manager
        self._server_base_url = server_base_url
        self._checkout_timeout = checkout_timeout
        self.broker: ToolBrokerServer | None = None
        self.egress: EgressGateway | None = None
        self.checkout_result: CheckoutResult | None = None
        self.diff_ref: str | None = None
        self.checkout_id: str | None = None
        self._adapter_destroy = None

    # -- startup ----------------------------------------------------------------

    async def start(self, *, lease_seq: int) -> None:
        """checkout → egress → broker. Any failure propagates (attempt fails
        closed); nothing degrades to a weaker mode."""
        cfg = self.config
        if cfg.repo is not None:
            helper = CheckoutHelper(
                worktree=cfg.attempt_root / "worktree", timeout=self._checkout_timeout
            )
            await self._api.report_checkout(
                cfg.attempt_id, lease_seq=lease_seq, status="cloning",
                repo_url=cfg.repo.url, base_ref=cfg.repo.base_ref,
            )
            self.checkout_result = await helper.prepare(
                cfg.repo,
                allowed_repos=list(cfg.allowed_repos),
                platform_managed=cfg.platform_managed,
                read_credential=cfg.read_credential,
            )
            ack = await self._api.report_checkout(
                cfg.attempt_id, lease_seq=lease_seq, status="ready",
                repo_url=cfg.repo.url, base_ref=cfg.repo.base_ref,
                commit_sha=self.checkout_result.commit_sha,
                local_path=self.checkout_result.worktree,
            )
            self.checkout_id = str(ack.get("id")) if isinstance(ack, dict) else None
        from mesh_runtime.egress import NetworkPolicy

        # Bind the gateway to the per-attempt veth host IP — the sandbox's
        # ONLY exit — never a wildcard address (§3.4). The link is reserved
        # now so the IP is known before provisioning consumes it.
        listen_host = "127.0.0.1"
        if self._sandbox_manager is not None:
            link = await self._sandbox_manager.reserve_link(cfg.attempt_id)
            listen_host = link.host_ip
        self.egress = EgressGateway(
            NetworkPolicy.from_snapshot(cfg.network_policy),
            listen_host=listen_host,
            address_filter=filter_answer_set,
        )
        await self.egress.start()
        if cfg.task_token:
            self.broker = ToolBrokerServer(
                attempt_id=cfg.attempt_id,
                socket_dir=cfg.attempt_root / "run",
                sandbox_uid=cfg.sandbox_uid,
                cgroup_marker=cfg.cgroup_marker,
                nonce=cfg.nonce,
                task_token=cfg.task_token,
                server_base_url=self._server_base_url,
                issue_id=cfg.issue_id,
                grants=self._broker_grants(),
            )
            await self.broker.start()

    def _broker_grants(self) -> dict:
        """Map frozen capability_grants onto broker action names."""
        mapping = {
            "issue:read": ("issue.read", "read_only"),
            "issue:comment:write": ("issue.comment", "write"),
            "issue:status:write": ("issue.status", "write"),
            "project:read": ("project.read", "read_only"),
        }
        grants: dict = {}
        raw = self.config.grants
        if isinstance(raw, dict):
            for scope, (action, default_perm) in mapping.items():
                perm = raw.get(scope)
                if perm in ("read_only", "write"):
                    grants[action] = perm
                elif perm == "confirm_required" and default_perm == "write":
                    grants[action] = "write"
        return grants

    @property
    def egress_proxy_url(self) -> str | None:
        return self.egress.proxy_url if self.egress is not None else None

    @property
    def broker_socket_path(self) -> str | None:
        return self.broker.socket_path if self.broker is not None else None

    # -- approval protocol (§3.3) -------------------------------------------------

    async def request_approval(
        self, *, lease_seq: int, action: str, params: dict, resume_context: dict | None
    ) -> None:
        """confirm_required: cancel THIS attempt (server sets
        cancelled(awaiting_approval), lease ends, capacity released) and let
        an approved new attempt resume via resume_context. The privileged
        sandbox is NEVER parked waiting for a human."""
        try:
            await self._api.request_approval(
                self.config.execution_id,
                lease_seq=lease_seq,
                attempt_id=self.config.attempt_id,
                action_summary={"action": action, "params": params},
                resume_context=resume_context or {},
            )
        except LeaseConflictError:
            pass  # attempt already settled server-side — nothing to cancel

    # -- teardown (§3.6) ------------------------------------------------------------

    def bind_adapter_destroy(self, destroy) -> None:
        self._adapter_destroy = destroy

    async def export_diff(self, *, lease_seq: int, redactor) -> int:
        """Redact + report the worktree diff (diff_ready). Returns hit count."""
        cfg = self.config
        if cfg.repo is None:
            return 0
        helper = CheckoutHelper(worktree=cfg.attempt_root / "worktree")
        try:
            diff = await helper.export_diff()
        except DaemonError:
            return 0
        if not diff.strip():
            return 0
        result = redactor.redact(diff)
        try:
            ack = await self._api.report_checkout(
                cfg.attempt_id, lease_seq=lease_seq, status="diff_ready",
                repo_url=cfg.repo.url, diff=result.text,
            )
            self.diff_ref = str(ack.get("diff_ref")) if isinstance(ack, dict) else None
        except (DaemonError, LeaseConflictError):
            pass  # terminal fencing already won elsewhere; diff is best-effort
        return result.hit_count

    async def finish(self, *, spool_flushed: bool) -> CleanupReport:
        """Idempotent S-08 cleanup. Broker freezes FIRST (§2.2: close broker
        before ending the sandbox), then revoke, kill, remove."""
        cfg = self.config

        async def close_broker_and_egress() -> None:
            if self.broker is not None:
                await self.broker.freeze()
                await self.broker.stop()
            if self.egress is not None:
                await self.egress.stop()

        async def kill_sandbox() -> None:
            if self._adapter_destroy is not None:
                await self._adapter_destroy()
            elif self._sandbox_manager is not None:
                await self._sandbox_manager.destroy_attempt(cfg.attempt_id)
            if self._sandbox_manager is not None:
                # Drop a link reservation that provisioning never consumed
                # (attempt ended before the sandbox started).
                self._sandbox_manager.release_link(cfg.attempt_id)

        async def revoke() -> None:
            # Task token revocation happens in the server's terminal
            # transition transaction; nothing daemon-side to rotate here.
            return None

        manifest = ResourceManifest(
            attempt_root=cfg.attempt_root,
            socket_paths=(self.broker.socket_path,) if self.broker else (),
            spool_dir=cfg.spool_dir,
        )
        cleaner = AttemptCleaner(self._journal)
        return await cleaner.cleanup(
            cfg.attempt_id,
            manifest,
            CleanupHandles(
                close_broker_and_egress=close_broker_and_egress,
                revoke_credentials=revoke,
                kill_sandbox=kill_sandbox,
            ),
            spool_flushed=spool_flushed,
        )
