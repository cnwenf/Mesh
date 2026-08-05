"""RuntimeApp — the daemon root (design §3).

Wires heartbeat + claim scheduling + per-attempt supervision under one
TaskGroup and owns graceful shutdown: stop claiming FIRST, then finish or
terminate in-flight attempts inside the grace window (§3.1).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from mesh_runtime import PROTOCOL_VERSION, __version__
from mesh_runtime.api import ClaimResponse, RuntimeApiClient
from mesh_runtime.attempt import AttemptContext, AttemptSupervisor
from mesh_runtime.budget import DaemonCaps
from mesh_runtime.config import DaemonConfig
from mesh_runtime.errors import DaemonError, LeaseConflictError
from mesh_runtime.heartbeat import HeartbeatLoop
from mesh_runtime.inventory import Inventory
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.operational import OperationalGuard
from mesh_runtime.providers.base import ExecutorAdapter, RunRequest
from mesh_runtime.reconcile import reconcile_on_startup
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.scheduler import ClaimScheduler
from mesh_runtime.spool import DEFAULT_SPOOL_MAX_BYTES, LogSpool
from mesh_runtime.timeutil import Clock, SystemClock

logger = logging.getLogger("mesh_runtime")

#: Daemon-local safety ceilings (§4.3): the frozen snapshot may be stricter,
#: never looser. A real provider that runs with no wall/idle ceiling at all is
#: a daemon misconfiguration — these caps make that fail-safe.
_DAEMON_BUDGET_CAPS = DaemonCaps(wall_seconds=3600.0, idle_seconds=600.0)

#: Built-in tools the daemon allows the provider inside the locked sandbox
#: (§1.4 ``--tools <daemon-generated-allowlist>``). All of them are confined
#: by the mount/cgroup/net isolation; the platform task broker MCP server is
#: appended per attempt when a broker socket exists.
DEFAULT_TOOL_ALLOWLIST = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

#: Trusted platform notices appended to the system prompt per frozen
#: squad_role (squad.md §4.4 wakes). Derived from the server's task_spec —
#: platform metadata, never task content (§3.7 S-09 boundary preserved).
_SQUAD_ROLE_NOTICES: dict[str, str] = {
    "orchestrator": (
        "[Mesh platform metadata — trusted] Squad role for THIS run: "
        "ORCHESTRATOR (decompose phase). The squad tools (squad_members, "
        "squad_subtasks) ARE granted in this run."
    ),
    "aggregator": (
        "[Mesh platform metadata — trusted] Squad role for THIS run: "
        "AGGREGATOR (summary phase, the subtasks already finished). The "
        "squad decomposition tools are NOT granted in this run — summarize "
        "and close via issue_comment / issue_status per your instructions."
    ),
    "executor": (
        "[Mesh platform metadata — trusted] Squad role for THIS run: "
        "EXECUTOR (member subtask). Report via issue_comment per your "
        "instructions."
    ),
}


@dataclass(frozen=True)
class RuntimeMetadata:
    hostname: str
    os_name: str


def serialize_untrusted_context(ctx: object) -> str:
    """Render the server's structured untrusted context (triggers.py §6.15)
    into text for the provider.

    The server delivers ``task_spec.untrusted_context`` as a dict whose
    externally-sourced fields (issue title/description, comments, labels,
    attachments) are ALREADY wrapped in the server's ``UNTRUSTED_DATA`` markers;
    this renders the notice plus those fields verbatim. A plain string is passed
    through; anything else yields "" (fail-safe: no task context rather than a
    crash). The daemon NEVER splices this into trusted instructions (§3.7)."""
    if isinstance(ctx, str):
        return ctx
    if not isinstance(ctx, dict):
        return ""
    parts: list[str] = []
    notice = ctx.get("notice")
    if isinstance(notice, str) and notice:
        parts.append(notice)
    issue = ctx.get("issue")
    if isinstance(issue, dict):
        issue_id = issue.get("id")
        identifier = issue.get("identifier")
        title = issue.get("title")
        description = issue.get("description")
        label = f"Issue {identifier}" if identifier else "Issue"
        # The frozen issue id — the task broker's issue/squad tools take it
        # as an argument (resource scope is still server-pinned; this only
        # lets the model name the resource it is already scoped to).
        if isinstance(issue_id, str) and issue_id:
            parts.append(f"{label} id: {issue_id}")
        if isinstance(title, str) and title:
            parts.append(f"{label} title: {title}")
        if isinstance(description, str) and description:
            parts.append(f"{label} description: {description}")
    for key in ("comments", "labels", "attachments"):
        items = ctx.get(key)
        if isinstance(items, list):
            rendered = [str(x) for x in items if x]
            if rendered:
                parts.append(f"{key}: " + " | ".join(rendered))
    return "\n".join(parts)


def build_run_request(claim: ClaimResponse) -> RunRequest:
    """Assemble the frozen prompt layers from the claim (spec §8.1).

    Trusted system instructions come from the frozen snapshot; the task
    content lives in ``execution.task_spec.untrusted_context`` (where the
    server's enqueue actually places it — triggers.py §6.15) and is rendered
    as UNTRUSTED data so it can never be parsed as instructions (§3.7 S-09).
    The tool allowlist is daemon-generated from the frozen grants (§1.4) —
    never from task output.
    """
    snapshot = claim.config_snapshot
    execution = claim.execution
    system_instructions = snapshot.get("system_instructions")
    system_prompt = system_instructions if isinstance(system_instructions, str) else ""
    task_spec = execution.get("task_spec")
    untrusted_raw = task_spec.get("untrusted_context") if isinstance(task_spec, dict) else None
    untrusted_context = serialize_untrusted_context(untrusted_raw)
    # Trusted PLATFORM metadata (from the frozen task_spec — server-derived,
    # NOT task content, so it belongs in the trusted layer, §3.7): the squad
    # role tells the model which wake phase this run is, so a leader doesn't
    # burn budget probing tools its current phase doesn't grant.
    squad_role = task_spec.get("squad_role") if isinstance(task_spec, dict) else None
    notice = _SQUAD_ROLE_NOTICES.get(squad_role) if isinstance(squad_role, str) else None
    if notice:
        system_prompt = f"{system_prompt}\n\n{notice}" if system_prompt else notice
    budget = snapshot.get("budget") or {}
    max_budget = budget.get("max_cost_usd") if isinstance(budget, dict) else None
    tools = DEFAULT_TOOL_ALLOWLIST
    raw_tools = snapshot.get("tools_allow")
    if isinstance(raw_tools, list) and raw_tools:
        tools = tuple(t for t in raw_tools if isinstance(t, str) and t) or tools
    return RunRequest(
        attempt_id=claim.attempt_id,
        system_prompt=system_prompt,
        untrusted_context=untrusted_context,
        max_turns=int((budget.get("max_turns") if isinstance(budget, dict) else None) or 0),
        max_budget_usd=str(max_budget or "0.000000"),
        tools_allowlist=tools,
    )


class RuntimeApp:
    def __init__(
        self,
        config: DaemonConfig,
        api: RuntimeApiClient,
        journal: Journal,
        inventory: Inventory | None,
        adapters: list[ExecutorAdapter],
        *,
        clock: Clock | None = None,
        metadata: RuntimeMetadata | None = None,
        redaction_secrets: list[str] | None = None,
        rule_version: str = "redaction-v1",
        sandbox_manager=None,  # SandboxManager | None — enables the A2 stack
    ) -> None:
        self.config = config
        self._api = api
        self._journal = journal
        self._inventory = inventory or Inventory([])
        self._adapters = adapters
        self._clock = clock or SystemClock()
        self._metadata = metadata
        self._redaction_secrets = redaction_secrets or []
        self._rule_version = rule_version
        self._sandbox_manager = sandbox_manager
        self._supervisors: dict[str, AttemptSupervisor] = {}
        self._contexts: dict[str, AttemptContext] = {}
        self._attempt_tasks: dict[str, asyncio.Task] = {}
        self._shutdown = asyncio.Event()
        self._runtime_id: str | None = None
        self._operational = OperationalGuard(
            config.state_dir / "operational-state.json",
            self._inventory,
        )
        # A3: the pinned provider manifest and administrator-owned provider
        # credentials (§1.4, §5.4.7). Credentials are in-memory only and every
        # value joins the redaction secret set for all egress channels.
        self._provider_manifest = None
        if config.provider_manifest is not None:
            from mesh_runtime.manifest import load_provider_manifest

            self._provider_manifest = load_provider_manifest(config.provider_manifest)
        self._provider_env: dict = {}
        if config.provider_env_file is not None:
            import os as _os

            from mesh_runtime.provider_env import load_provider_env_file

            self._provider_env = load_provider_env_file(
                config.provider_env_file, expected_uid=_os.getuid()
            )
            self._redaction_secrets = [
                *self._redaction_secrets,
                *[v for v in self._provider_env.values() if isinstance(v, str) and v],
            ]

    # -- runtime id ---------------------------------------------------------

    def set_runtime_id(self, runtime_id: str) -> None:
        self._runtime_id = runtime_id

    # -- main loop ----------------------------------------------------------

    async def run(self) -> None:
        if self._runtime_id is None:
            raise RuntimeError("runtime id not set — activate first")
        from mesh_runtime.residual import ResidualPaths

        cgroup_base = getattr(self._sandbox_manager, "cgroup_base", None)
        residual_paths = ResidualPaths(
            work_root=self.config.work_dir,
            spool_root=self.config.spool_dir,
            **({"cgroup_base": cgroup_base} if cgroup_base is not None else {}),
        )
        await reconcile_on_startup(
            self._journal, self._api, self._runtime_id, paths=residual_paths
        )
        # A latched incident survives the process that observed it.  A fresh
        # daemon may recover only after residual reconciliation and the same
        # complete local checks used by ``mesh-runtime doctor`` pass while no
        # attempt is in flight.
        if self._operational.isolated:
            from mesh_runtime.doctor import run_checks

            report = await run_checks(self.config, self._inventory)
            self._operational.recover_after_checks(
                checks_ok=report.all_ok(),
                inflight=len(self._supervisors),
            )

        heartbeat = HeartbeatLoop(
            self._api,
            self._runtime_id,
            interval_seconds=self.config.heartbeat_interval_seconds,
            inventory=self._inventory,
            operational_guard=self._operational,
            on_operational_incident=self._operational.isolate,
            clock=self._clock,
            inflight_source=lambda: list(self._supervisors.keys()),
            on_cancel=self._handle_cancel,
        )
        scheduler = ClaimScheduler(
            self._api,
            self._runtime_id,
            max_concurrent=self.config.max_concurrent,
            clock=self._clock,
            on_claimed=self._spawn_attempt,
            on_attempt_error=lambda claim, exc: self._operational.isolate(
                "sandbox_security_failed"
            ),
            claim_allowed=self._operational.claim_allowed,
        )

        async with asyncio.TaskGroup() as group:
            hb_task = group.create_task(heartbeat.run(self._shutdown), name="heartbeat")
            claim_task = group.create_task(scheduler.run(self._shutdown), name="claim")
            await self._shutdown.wait()
            # Graceful drain (§3.1): stop claiming first, then in-flight work.
            scheduler.request_stop()
            await self._drain_supervisors()
            heartbeat.request_stop()
            claim_task.cancel()
            hb_task.cancel()
            for task in (claim_task, hb_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def request_shutdown(self) -> None:
        self._shutdown.set()

    # -- attempt spawning ---------------------------------------------------

    async def _spawn_attempt(self, claim: ClaimResponse) -> None:
        """Runs inside the scheduler's per-claim task; awaiting the full
        attempt lifecycle here keeps the scheduler's concurrency slot held
        until the attempt is terminal."""
        attempt_id = claim.attempt_id
        # Short path segments: the broker unix socket path must stay under the
        # 108-byte AF_UNIX limit even with deep state roots. The journal keeps
        # full ids; the directory names are an implementation detail.
        attempt_root = (
            self.config.work_dir / claim.execution_id[:8] / attempt_id[:8]
        )
        ctx = AttemptContext(
            attempt_id=attempt_id,
            execution_id=claim.execution_id,
            runtime_id=self._runtime_id or "",
            lease_seq=claim.lease_seq,
            work_dir=str(attempt_root),
        )
        # §2.5/§3.8: credential values + task token are redaction secrets —
        # in-memory only, never logged.
        secrets = list(self._redaction_secrets)
        for cred in claim.credentials:
            value = cred.get("value")
            if isinstance(value, str) and value:
                secrets.append(value)
        if claim.task_token:
            secrets.append(claim.task_token)
        redactor = RedactionPipeline(secrets=secrets, rule_version=self._rule_version)
        # Per-attempt tmpfs spool: redacted batches are durable BEFORE upload and
        # only cleared on server ack, so a crash/network blip never loses logs
        # (§3.9.3). Scoped to a subdirectory so the frozen cap is per attempt.
        spool = LogSpool(
            self.config.spool_dir / attempt_id, max_bytes=DEFAULT_SPOOL_MAX_BYTES
        )
        logs = LogUploader(self._api, self._journal, redactor, clock=self._clock, spool=spool)
        security = self._build_security(claim, attempt_root, redactor)
        try:
            adapter = self._select_adapter(claim, attempt_root, security)
        except DaemonError as exc:
            # Fail-closed adapter construction (frozen budget missing, sandbox
            # ceiling invalid, …): report a FENCED terminal with the claim's
            # lease_seq and let the server own the state (§3.1).
            logger.error(
                "attempt %s adapter unavailable: %s", attempt_id, type(exc).__name__
            )
            try:
                await self._api.transition(
                    attempt_id, lease_seq=claim.lease_seq, status="failed",
                    failure_reason="executor_unavailable",
                )
            except (DaemonError, LeaseConflictError):
                pass  # lease already reclaimed — server reaper owns it
            if security is not None:
                try:
                    await security.finish(spool_flushed=True)
                except DaemonError:
                    pass
            return
        provider_version = (
            self._provider_manifest.version
            if self._provider_manifest is not None
            else (self.config.provider_version or "0.0.0-a2")
        )
        frozen_model = claim.config_snapshot.get("model")
        supervisor = AttemptSupervisor(
            self._api,
            self._journal,
            logs,
            self._clock,
            provider_name=adapter.name,
            provider_version=provider_version,
            model=frozen_model if isinstance(frozen_model, str) and frozen_model else "unknown",
            rule_version=self._rule_version,
            security=security,
            redactor=redactor,
            on_operational_incident=self._operational.isolate,
        )
        self._supervisors[attempt_id] = supervisor
        self._contexts[attempt_id] = ctx
        current = asyncio.current_task()
        if current is not None:
            self._attempt_tasks[attempt_id] = current
        await logs.start_ticking()  # §3.9.2: interval arm flushes sparse streams
        try:
            outcome = await supervisor.supervise(ctx, adapter, build_run_request(claim))
            if outcome.terminal_reported and supervisor.spool_flushed:
                await self._journal.delete(attempt_id)  # only after confirmed terminal (§3.6)
                await logs.drain_attempt(attempt_id)  # drop residual spooled batches
            elif outcome.terminal_reported:
                # Terminal reported but the log stream is not sealed/complete
                # (sealed flush failed past retries): KEEP the journal row and
                # the spooled batches so startup reconciliation makes a
                # best-effort replay+seal before cleanup (§3.9.3).
                await self._journal.update(attempt_id, status="terminal_seal_pending")
            logger.info("attempt %s finished: %s", attempt_id, outcome.status)
        finally:
            await logs.stop_ticking()
            self._supervisors.pop(attempt_id, None)
            self._contexts.pop(attempt_id, None)
            self._attempt_tasks.pop(attempt_id, None)

    def _build_security(self, claim: ClaimResponse, attempt_root, redactor):
        """Assemble the A2 isolation stack (None when the daemon runs without
        a sandbox manager — the A1 contract path)."""
        if self._sandbox_manager is None:
            return None
        from mesh_runtime.broker import new_nonce
        from mesh_runtime.checkout import FrozenRepo
        from mesh_runtime.security import AttemptSecurity, SecurityConfig

        snapshot = claim.config_snapshot
        grants: dict = {}
        raw_grants = snapshot.get("capability_grants")
        if isinstance(raw_grants, list):
            for grant in raw_grants:
                if isinstance(grant, dict):
                    cap = grant.get("capability")
                    perm = grant.get("permission")
                    if isinstance(cap, str) and isinstance(perm, str):
                        grants[cap] = perm
        repo = FrozenRepo.from_snapshot(snapshot)
        read_credential = None
        for cred in claim.credentials:
            if cred.get("kind") == "repo_token" and isinstance(cred.get("value"), str):
                read_credential = cred["value"]
                break
        config = SecurityConfig(
            attempt_id=claim.attempt_id,
            execution_id=claim.execution_id,
            attempt_root=attempt_root,
            task_token=claim.task_token,
            issue_id=str(claim.execution.get("issue_id") or "") or None,
            grants=grants,
            nonce=new_nonce(),
            sandbox_uid=self.config.sandbox_uid,
            cgroup_marker=f"mesh-{claim.attempt_id}",
            repo=repo,
            # Server-side report_checkout enforces the workspace allowlist in
            # the same transaction; the daemon gate validates shape here.
            allowed_repos=(repo.url,) if repo else (),
            platform_managed=self.config.runtime_kind == "platform_managed",
            read_credential=read_credential,
            network_policy=(
                snapshot.get("network_policy")
                if isinstance(snapshot.get("network_policy"), dict) else {}
            ),
            spool_dir=self.config.spool_dir / claim.attempt_id,
        )
        return AttemptSecurity(
            config,
            api=self._api,
            journal=self._journal,
            sandbox_manager=self._sandbox_manager,
            server_base_url=self.config.server_url,
        )

    def _select_adapter(
        self, claim: ClaimResponse | None = None, attempt_root=None, security=None
    ) -> ExecutorAdapter:
        """Pinned Claude Code adapter in the real sandbox when a manifest is
        configured (A3); the A2 plain-sandboxed adapter for the dev/fake
        provider; injected adapters on the A1 contract path."""
        if self._sandbox_manager is not None and claim is not None and security is not None:
            if self.config.provider_path is None:
                raise RuntimeError("sandbox_backend=linux_ns requires provider_path")
            if self._provider_manifest is not None:
                return self._build_claude_adapter(claim, attempt_root, security)
            return self._build_sandboxed_fake_adapter(claim, attempt_root, security)
        if not self._adapters:
            raise RuntimeError("no provider adapters registered")
        return self._adapters[0]

    def _build_claude_adapter(self, claim: ClaimResponse, attempt_root, security):
        """§1.4/§5.4: the pinned provider runs INSIDE the A2 sandbox with the
        daemon-authored argv/env/configs and frozen S-07 budget enforcement."""
        from mesh_runtime.budget import BudgetLimits
        from mesh_runtime.provider_env import SANDBOX_RUN_DIR
        from mesh_runtime.providers.claude_code import (
            SANDBOX_WORKTREE_CWD,
            ClaudeCodeAdapter,
            ClaudeLaunchPlan,
            SandboxProcessLauncher,
        )

        assert self._provider_manifest is not None and self.config.provider_path is not None
        # S-07: frozen budget, stricter-of-two with daemon caps; a real
        # provider without a hard USD limit is refused (fail-closed, §3.5).
        budget = BudgetLimits.from_snapshot(
            claim.config_snapshot, _DAEMON_BUDGET_CAPS, require_usd=True
        )
        raw_boundary = claim.config_snapshot.get("context_boundary")
        # NOTE: the broker socket + egress gateway are started by
        # AttemptSecurity.start() (in supervise) AFTER this adapter is built.
        # Their concrete values are therefore resolved LAZILY at run()/spawn()
        # time (see ClaudeCodeAdapter._resolve_plan / SandboxProcessLauncher),
        # never captured here — capturing them now would see None/0.
        launcher = SandboxProcessLauncher(
            sandbox_manager=self._sandbox_manager,
            attempt_id=claim.attempt_id,
            attempt_root=attempt_root,
            uid=self.config.sandbox_uid,
            gid=self.config.sandbox_gid,
            # Provider binaries mount read-only at their host path; the
            # provider dir must be dedicated (no secrets inside).
            ro_binds=(str(self.config.provider_path.parent),),
            memory_bytes=self.config.sandbox_memory_bytes,
            cpu_quota_us=self.config.sandbox_cpu_quota_us,
            cpu_period_us=100_000,
            pids_max=self.config.sandbox_pids_max,
            tmp_bytes=self.config.sandbox_tmp_bytes,
            security=security,
        )
        plan = ClaudeLaunchPlan(
            attempt_id=claim.attempt_id,
            execution_id=claim.execution_id,
            host_run_dir=attempt_root / "run",
            sandbox_run_dir=SANDBOX_RUN_DIR,
            worktree_cwd=SANDBOX_WORKTREE_CWD,
            broker_socket_sandbox_path=None,  # resolved at run() from security
            broker_nonce=security.config.nonce,
            proxy_url=None,  # resolved at run() from security
            provider_env=dict(self._provider_env),
            budget=budget,
            context_boundary=raw_boundary
            if isinstance(raw_boundary, str) and raw_boundary
            else None,
        )
        adapter = ClaudeCodeAdapter(
            manifest=self._provider_manifest,
            binary_path=str(self.config.provider_path),
            launcher=launcher,
            plan=plan,
            clock=self._clock,
            security=security,
        )
        security.bind_adapter_destroy(adapter.destroy)
        return adapter

    def _build_sandboxed_fake_adapter(self, claim: ClaimResponse, attempt_root, security):
        """A2 dev path: a plain script provider inside the real sandbox (no
        pinned manifest configured)."""
        from mesh_runtime.provider_env import build_sandbox_env
        from mesh_runtime.providers.sandboxed import SandboxedProcessAdapter
        from mesh_runtime.sandbox import SandboxSpec

        provider_path = str(self.config.provider_path)

        def spec_builder(request: RunRequest) -> SandboxSpec:
            env = build_sandbox_env(
                attempt_id=claim.attempt_id,
                execution_id=claim.execution_id,
                home="/home",
                xdg_root="/xdg",
            )
            env["MESH_BROKER_NONCE"] = security.config.nonce
            if security.broker_socket_path:
                # Sandbox-side path: /run is the attempt run dir mounted in.
                env["MESH_BROKER_SOCKET"] = "/run/" + security.broker_socket_path.rsplit("/", 1)[-1]
            return SandboxSpec(
                attempt_id=claim.attempt_id,
                root=attempt_root,
                uid=self.config.sandbox_uid,
                gid=self.config.sandbox_gid,
                argv=(provider_path,),
                env=env,
                ro_binds=(str(self.config.provider_path.parent),),
                memory_bytes=self.config.sandbox_memory_bytes,
                cpu_quota_us=self.config.sandbox_cpu_quota_us,
                cpu_period_us=100_000,
                pids_max=self.config.sandbox_pids_max,
                tmp_bytes=self.config.sandbox_tmp_bytes,
                gateway_port=security.egress.port if security.egress is not None else 0,
            )

        adapter = SandboxedProcessAdapter(
            sandbox_manager=self._sandbox_manager,
            spec_builder=spec_builder,
            provider_name="sandboxed",
            provider_version=self.config.provider_version or "0.0.0-a2",
        )
        security.bind_adapter_destroy(adapter.destroy)
        return adapter

    # -- cancel + drain -----------------------------------------------------

    async def _handle_cancel(self, attempt_id: str, grace_seconds: float) -> None:
        supervisor = self._supervisors.get(attempt_id)
        ctx = self._contexts.get(attempt_id)
        if supervisor is None or ctx is None:
            return  # unknown / already finished — cancel commands may repeat
        # Fenced: reports a ``cancelled`` terminal, then tears the provider down.
        await supervisor.stop(ctx)

    async def _drain_supervisors(self) -> None:
        grace = self.config.shutdown_grace_seconds
        for attempt_id, supervisor in list(self._supervisors.items()):
            ctx = self._contexts.get(attempt_id)
            if ctx is None:
                continue
            try:
                await asyncio.wait_for(supervisor.stop(ctx), timeout=grace)
            except (TimeoutError, DaemonError):
                logger.warning("graceful stop timed out for attempt %s", attempt_id)
        tasks = list(self._attempt_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def heartbeat_metadata(config: DaemonConfig, inventory: Inventory) -> dict:
    """Diagnostics reported at activation / heartbeat (never secrets)."""
    import os
    import platform

    sandboxed = config.sandbox_backend == "linux_ns"
    return {
        "daemon_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "inventory_hash": inventory.inventory_hash(),
        "inventory": inventory.heartbeat_payload(),
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_cores": os.cpu_count() or 0,
        "max_concurrent": config.max_concurrent,
        # A2 security capabilities (§4.3): server dispatches accordingly.
        "sandbox": config.sandbox_backend,
        "egress_enforced": sandboxed,
        "broker": "unix" if sandboxed else "none",
        "runtime_kind": config.runtime_kind,
        # §3.2/§1.3: the public-address + resolved-IP SSRF gate on checkout
        # applies to platform-managed runtimes; self-hosted runtimes may
        # legitimately reach internal git servers, so they report the gate as
        # off and the server dispatches with that knowledge.
        "checkout_public_address_gate": config.runtime_kind == "platform_managed",
    }
