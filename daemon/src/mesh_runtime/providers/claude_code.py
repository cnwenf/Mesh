"""Pinned Claude Code provider adapter (runtime-executor.md §1.4, §3.5, §3.9,
§5.4 — A3).

The daemon is the ONLY author of the provider's argv, environment and config
files:

- PROBE is fail-closed supply-chain enforcement: the binary must sit at an
  administrator-configured absolute path, match the manifest's SHA-256 and
  pinned version EXACTLY, and prove every required §1.4 flag in a bare-env
  ``--help`` read. Results cache on (dev, ino, mtime, size); any inode/mtime/
  hash change invalidates the cache immediately (§1.4 step 5).
- RUN assembles the frozen §1.4 argv (prompt travels via stdin only — never
  argv, never a shell), writes the three platform-owned read-only config
  files into the attempt run dir, builds the sandbox environment FROM EMPTY
  plus the administrator-owned provider credentials (§5.4.7 — values are
  redaction secrets upstream, never task-derived), then parses the vendor
  ``stream-json`` stream through the strict §3.9 schema into unified events.
- S-07 daemon-layer budget: provider-reported usage and wall/idle clocks are
  checked live; a violation TERM→KILLs the provider and terminates with the
  frozen ``budget_exceeded`` / ``timeout`` vocabulary (§3.5, §3.9).

The process launch is behind a launcher seam: production runs inside the real
A2 namespace/cgroup sandbox (``SandboxProcessLauncher``); tests substitute a
plain-subprocess launcher. The parse/budget/argv logic is identical on both
paths.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path

from mesh_runtime.budget import BudgetGuard, BudgetLimits, BudgetViolation
from mesh_runtime.inventory import read_binary_version, verify_binary_static
from mesh_runtime.manifest import ProviderManifest
from mesh_runtime.provider_env import (
    ProviderLaunchSpec,
    _scrub_provider_env,
    _validate_provider_env_name,
    build_provider_argv,
    build_sandbox_env,
    build_stream_json_input,
    validate_no_escalation_args,
    write_provider_configs,
)
from mesh_runtime.providers.base import (
    ExecutorEvent,
    FinalResult,
    ProbeResult,
    ProtocolWarning,
    ProviderExited,
    RunRequest,
    UsageObserved,
)
from mesh_runtime.providers.sandboxed import SandboxLaunchError
from mesh_runtime.sandbox import SandboxManager, SandboxSpec, SandboxUnavailableError
from mesh_runtime.stream_json import parse_stream_record
from mesh_runtime.timeutil import Clock, SystemClock

logger = logging.getLogger("mesh_runtime.provider.claude_code")

# Help text advertises long flags as ``--name`` or with an optional suffix as
# ``--name[-suffix]`` (the latter implies BOTH ``--name`` and ``--name-suffix``).
_FLAG_TOKEN = re.compile(r"--[a-z][a-z0-9-]*(?:\[-[a-z0-9-]+\])?")


def _flags_from_help(help_text: str) -> set[str]:
    """Expand a ``--help`` dump into the concrete flag set it advertises."""
    flags: set[str] = set()
    for token in _FLAG_TOKEN.findall(help_text):
        match = re.fullmatch(r"(--[a-z0-9-]+?)(?:\[(-[a-z0-9-]+)\])?", token)
        if match is None:
            continue
        base, optional_suffix = match.groups()
        flags.add(base)
        if optional_suffix:
            flags.add(base + optional_suffix)
    return flags

_HELP_TIMEOUT_SECONDS = 10.0
_HELP_OUTPUT_MAX = 256 * 1024
_POLL_SECONDS = 0.25
_TERM_GRACE_SECONDS = 5.0
_STDERR_TAIL_BYTES = 4096
_STDERR_CAP_BYTES = 64 * 1024

#: Sandbox-side mount point of the attempt run dir (sandbox_init bind-mounts
#: ``<attempt_root>/run`` read-only at this path).
SANDBOX_RUN_DIR = "/run"
SANDBOX_WORKTREE_CWD = "/worktree"

#: Tool name the provider uses for platform task broker calls (§3.3): the ONLY
#: MCP server registered in mcp.json. Appended to the daemon-generated
#: allowlist per attempt when a broker socket exists.
BROKER_MCP_TOOL = "mcp__mesh-task-broker"


@dataclass(frozen=True)
class ClaudeLaunchPlan:
    """Per-attempt launch context. Every field is daemon-derived — nothing
    here may come from the worktree or provider output (§1.4)."""

    attempt_id: str
    execution_id: str
    host_run_dir: Path  # daemon-side; bind-mounted ro into the sandbox
    sandbox_run_dir: str  # SANDBOX_RUN_DIR
    worktree_cwd: str  # SANDBOX_WORKTREE_CWD
    broker_socket_sandbox_path: str | None
    broker_nonce: str | None
    proxy_url: str | None  # per-attempt egress gateway
    provider_env: dict  # administrator-owned credentials (validated)
    budget: BudgetLimits
    context_boundary: str | None = None  # server-generated when present (§3.7)


class ProcessLauncher:
    """Launch/destroy the provider process. Production: the real sandbox."""

    async def spawn(self, *, argv: list[str], env: dict) -> asyncio.subprocess.Process:
        raise NotImplementedError

    async def destroy(self) -> None:
        raise NotImplementedError


class SandboxProcessLauncher(ProcessLauncher):
    """Runs the provider INSIDE the A2 namespace/cgroup sandbox. Provision
    failures raise SandboxUnavailableError — the supervisor fails the attempt
    as sandbox_violation, never bare (§5.2)."""

    def __init__(
        self,
        *,
        sandbox_manager: SandboxManager,
        attempt_id: str,
        attempt_root: Path,
        uid: int,
        gid: int,
        ro_binds: tuple[str, ...],
        memory_bytes: int,
        cpu_quota_us: int,
        cpu_period_us: int,
        pids_max: int,
        tmp_bytes: int,
        gateway_port: int = 0,
        stdin_pipe: bool = True,  # §1.4: prompt travels via stdin ONLY
        security=None,  # AttemptSecurity; egress port resolved at spawn time
    ) -> None:
        self._manager = sandbox_manager
        self._security = security
        self._spec_kwargs = dict(
            attempt_id=attempt_id,
            root=attempt_root,
            uid=uid,
            gid=gid,
            ro_binds=ro_binds,
            memory_bytes=memory_bytes,
            cpu_quota_us=cpu_quota_us,
            cpu_period_us=cpu_period_us,
            pids_max=pids_max,
            tmp_bytes=tmp_bytes,
            gateway_port=gateway_port,
            stdin_pipe=stdin_pipe,
        )
        self._proc: asyncio.subprocess.Process | None = None

    async def spawn(self, *, argv: list[str], env: dict) -> asyncio.subprocess.Process:
        kwargs = dict(self._spec_kwargs)
        # The egress gateway is started by AttemptSecurity.start() AFTER the
        # adapter is constructed but BEFORE run()/spawn() — resolve the live
        # port here (an explicit gateway_port still wins, e.g. tests).
        if not kwargs["gateway_port"] and self._security is not None:
            egress = getattr(self._security, "egress", None)
            if egress is not None:
                kwargs["gateway_port"] = egress.port
        spec = SandboxSpec(argv=tuple(argv), env=env, **kwargs)
        handle = await self._manager.provision(spec)
        self._proc = handle.proc
        return handle.proc

    async def destroy(self) -> None:
        await self._manager.destroy_attempt(self._spec_kwargs["attempt_id"])


class ClaudeCodeAdapter:
    name = "claude-code"

    def __init__(
        self,
        *,
        manifest: ProviderManifest,
        binary_path: str,
        launcher: ProcessLauncher | None = None,
        plan: ClaudeLaunchPlan | None = None,
        clock: Clock | None = None,
        probe_env_extra: dict | None = None,  # test seam; production: None
        security=None,  # AttemptSecurity; broker/proxy resolved at run time
    ) -> None:
        self._manifest = manifest
        self._binary_path = binary_path
        self._launcher = launcher
        self._plan = plan
        self._clock = clock or SystemClock()
        self._probe_env_extra = dict(probe_env_extra or {})
        self._security = security
        self._probe_cache: tuple[tuple, ProbeResult] | None = None
        self.probe_executions = 0  # diagnostics: full binary probes run

    # -- probe (§1.4 supply-chain gate) --------------------------------------

    async def probe(self) -> ProbeResult:
        identity = self._file_identity()
        if identity is None:
            return self._unavailable("binary not found at the configured absolute path")
        if self._probe_cache is not None and self._probe_cache[0] == identity:
            return self._probe_cache[1]
        result = await self._full_probe(identity)
        self._probe_cache = (identity, result)
        return result

    def _file_identity(self) -> tuple | None:
        """(dev, ino, mtime_ns, size) — the cache key that invalidates on any
        inode/mtime change (§1.4 step 5)."""
        path = Path(self._binary_path)
        try:
            st = path.lstat()
        except OSError:
            return None
        return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)

    async def _full_probe(self, identity: tuple) -> ProbeResult:
        self.probe_executions += 1
        # §1.4 step 2: static verification (path/owner/mode + SHA-256) FIRST —
        # the binary is NEVER executed until its digest matches the pinned
        # manifest, so an attacker-planted binary is never run.
        static = verify_binary_static(self._binary_path)
        if not static.ok:
            return self._unavailable(static.reason or "binary probe failed")
        if static.sha256 != self._manifest.binary_sha256:
            return self._unavailable(
                "binary sha256 does not match the pinned manifest — refusing"
            )
        # Only the digest-verified pinned binary is executed below. ``--version``
        # and ``--help`` are local operations (no network); running them on the
        # verified release binary is safe.
        ver = await read_binary_version(self._binary_path)
        if not ver.ok:
            return self._unavailable(ver.reason or "version check failed")
        reported_version = (ver.version or "").split()[0]
        if reported_version != self._manifest.version:
            return self._unavailable(
                f"binary version {reported_version!r} does not match pinned "
                f"{self._manifest.version!r} — refusing"
            )
        missing = await self._missing_required_flags()
        if missing:
            return self._unavailable(
                "binary help is missing required flag(s): " + ", ".join(missing)
            )
        return ProbeResult(
            available=True,
            name=self.name,
            version=self._manifest.version,
            binary_sha256=static.sha256,
            capabilities=self._manifest.capabilities(),
            reason=None,
        )

    async def _missing_required_flags(self) -> list[str]:
        """Read ``--help`` in a no-network, EMPTY-HOME probe environment and
        verify every manifest-required flag is supported (§1.4 step 2-3).

        Help text uses an optional-suffix shorthand — ``--system-prompt[-file]``
        documents BOTH ``--system-prompt`` and ``--system-prompt-file`` — so the
        advertised flag set is expanded before membership is checked."""
        help_text = await self._read_help()
        if help_text is None:
            return list(self._manifest.required_flags)  # unreadable → fail all
        supported = _flags_from_help(help_text)
        return [flag for flag in self._manifest.required_flags if flag not in supported]

    async def _read_help(self) -> str | None:
        path = Path(self._binary_path)
        with tempfile.TemporaryDirectory(prefix="mesh-probe-") as empty_home:
            env = {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "HOME": empty_home,
                "XDG_CONFIG_HOME": os.path.join(empty_home, "config"),
                "XDG_DATA_HOME": os.path.join(empty_home, "data"),
                "XDG_CACHE_HOME": os.path.join(empty_home, "cache"),
            }
            env.update(self._probe_env_extra)
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(path), "--help",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
            except OSError:
                return None
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_HELP_TIMEOUT_SECONDS
                )
            except TimeoutError:
                proc.kill()  # don't leak a hung --help child (cf. probe_binary)
                await proc.wait()
                return None
        if proc.returncode != 0:
            return None
        return stdout[:_HELP_OUTPUT_MAX].decode("utf-8", errors="replace")

    def _unavailable(self, reason: str) -> ProbeResult:
        logger.warning("provider %s unavailable: %s", self.name, reason)
        return ProbeResult(
            available=False,
            name=self.name,
            version=self._manifest.version,
            binary_sha256=self._manifest.binary_sha256,
            capabilities=(),
            reason=reason,
        )

    # -- run (§1.4 fixed argv + §3.9 stream parsing + §3.5 live budget) ------

    def _resolve_plan(self, plan: ClaudeLaunchPlan) -> ClaudeLaunchPlan:
        """Fill in security-dependent fields (broker socket, egress proxy) that
        only exist once ``AttemptSecurity.start()`` has run. The supervisor
        starts security BEFORE calling ``run()``, so resolving here (not at
        adapter construction) sees the live broker socket and gateway port."""
        if self._security is None:
            return plan
        broker_socket = plan.broker_socket_sandbox_path
        if broker_socket is None and self._security.broker_socket_path:
            broker_socket = (
                plan.sandbox_run_dir + "/"
                + self._security.broker_socket_path.rsplit("/", 1)[-1]
            )
        proxy_url = plan.proxy_url or self._security.egress_proxy_url
        if broker_socket == plan.broker_socket_sandbox_path and proxy_url == plan.proxy_url:
            return plan
        return replace(plan, broker_socket_sandbox_path=broker_socket, proxy_url=proxy_url)

    async def run(self, request: RunRequest) -> AsyncIterator[ExecutorEvent]:
        if self._launcher is None or self._plan is None:
            raise RuntimeError(
                "ClaudeCodeAdapter needs a launcher and launch plan to run"
            )
        plan = self._resolve_plan(self._plan)
        # §1.4 step 5 / §5.4: re-verify the binary STILL matches the pinned
        # digest at run time — a swap between probe and run (even one that
        # spoofs mtime/size to defeat the probe cache) is caught here, before
        # the binary is ever launched. Fail-closed.
        static = verify_binary_static(self._binary_path)
        if not static.ok or static.sha256 != self._manifest.binary_sha256:
            raise SandboxLaunchError(
                "provider binary no longer matches the pinned manifest — refusing to run"
            )
        argv = self._build_argv(request, plan)
        env = self._build_env(plan)
        await write_provider_configs(
            plan.host_run_dir,
            system_prompt=request.system_prompt,
            broker_socket_path=plan.broker_socket_sandbox_path or "",
            settings={},
        )
        try:
            proc = await self._launcher.spawn(argv=argv, env=env)
        except SandboxUnavailableError as exc:
            # §5.2 red line: sandbox not provisioned/verified → the attempt
            # fails as sandbox_violation. NEVER degrade to a bare run.
            raise SandboxLaunchError(str(exc)) from exc
        try:
            async for event in self._drive(proc, request, plan):
                yield event
        finally:
            if proc.returncode is None:
                await self._terminate(proc)

    def _build_argv(self, request: RunRequest, plan: ClaudeLaunchPlan) -> list[str]:
        run = plan.sandbox_run_dir
        tools_allow = request.tools_allowlist
        if plan.broker_socket_sandbox_path and BROKER_MCP_TOOL not in tools_allow:
            tools_allow = (*tools_allow, BROKER_MCP_TOOL)
        spec = ProviderLaunchSpec(
            provider_path=self._binary_path,
            version=self._manifest.version,
            model=None,
            effort=None,
            budget_usd=request.max_budget_usd,
            tools_allow=tools_allow,
            tools_deny=(),
            mcp_config_path=f"{run}/mcp.json",
            settings_path=f"{run}/settings.json",
            system_prompt_path=f"{run}/system.md",
        )
        argv = build_provider_argv(spec)
        # Defense in depth: the argv builder is fixed, but the gate is cheap.
        validate_no_escalation_args(argv)
        return argv

    def _build_env(self, plan: ClaudeLaunchPlan) -> dict:
        # Base sandbox env (FROM EMPTY) WITHOUT the proxy — provider credentials
        # merge next, then the daemon RE-ASSERTS the egress proxy + broker
        # pointer last so administrator env can never redirect them (§3.8).
        env = build_sandbox_env(
            attempt_id=plan.attempt_id,
            execution_id=plan.execution_id,
            home="/home",
            xdg_root="/xdg",
        )
        # Administrator-owned provider credentials (§5.4.7): loaded from the
        # 0600 provider env file, re-filtered here with the credential-aware
        # gate (third pass — values are redaction secrets upstream, never
        # task-derived). Credential-shaped names (ANTHROPIC_API_KEY, …) pass;
        # the daemon-owned proxy/broker/CA pointers are rejected outright and
        # then re-asserted below.
        for name, value in _scrub_provider_env(plan.provider_env).items():
            _validate_provider_env_name(name)
            env[name] = value
        # Daemon-owned egress + broker pointers win (defense in depth on top of
        # the reserved-name rejection and the netns no-default-route guarantee).
        if plan.proxy_url:
            env["HTTP_PROXY"] = plan.proxy_url
            env["HTTPS_PROXY"] = plan.proxy_url
            env.pop("NO_PROXY", None)
            env.pop("no_proxy", None)
        if plan.broker_socket_sandbox_path:
            env["MESH_BROKER_SOCKET"] = plan.broker_socket_sandbox_path
        if plan.broker_nonce:
            env["MESH_BROKER_NONCE"] = plan.broker_nonce
        return env

    async def _drive(self, proc: asyncio.subprocess.Process, request: RunRequest,
                     plan: ClaudeLaunchPlan):
        if proc.stdin is None or proc.stdout is None:
            # A launcher that cannot carry the stdin prompt / stdout stream
            # breaks the §1.4 contract — fail closed like a sandbox failure.
            raise SandboxLaunchError("provider stdio pipes unavailable")
        # Prompt travels via stdin ONLY: one stream-json user message with the
        # untrusted context wrapped in boundary markers (§1.4, §3.7). If the
        # provider already exited (startup crash / config error) the write
        # raises BrokenPipe/ConnectionReset — swallow it; the stdout loop below
        # observes EOF and reports the failure instead of hanging.
        try:
            proc.stdin.write(
                build_stream_json_input(
                    request.untrusted_context, boundary=plan.context_boundary
                ).encode("utf-8")
            )
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        stderr_chunks: list[bytes] = []
        stderr_task = asyncio.create_task(self._drain_stderr(proc, stderr_chunks))
        guard = BudgetGuard(plan.budget, clock=self._clock)
        guard.mark_started()
        dropped = 0
        result_seen = False
        violation: BudgetViolation | None = None

        try:
            while True:
                # S-07: enforce the frozen wall/idle budget on EVERY iteration
                # — a chatty provider must not outrun the wall cap (checking
                # only in the read-timeout branch would let it bypass S-07).
                violation = guard.check_time()
                if violation is not None:
                    break
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=_POLL_SECONDS
                    )
                except TimeoutError:
                    continue
                except ValueError:
                    # A line exceeded the transport limit — readline() already
                    # discarded it (buffer cleared); count an oversize drop and
                    # keep streaming (the parser would have dropped it anyway).
                    dropped += 1
                    yield ProtocolWarning(raw_type="oversize:line")
                    continue
                if not line:
                    break  # EOF
                guard.mark_activity()
                record = parse_stream_record(line.decode("utf-8", errors="replace"))
                if record.dropped is not None:
                    dropped += 1
                    yield ProtocolWarning(raw_type=f"{record.dropped}:{record.raw_type}")
                    continue
                for event in record.events:
                    if isinstance(event, UsageObserved):
                        violation = guard.check_usage(event)
                        if violation is not None:
                            break
                    if isinstance(event, FinalResult):
                        result_seen = True
                    yield event
                if violation is not None:
                    break
        finally:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

        if violation is not None:
            # S-07 live truncation: TERM first, KILL after the grace window.
            await self._terminate(proc)
            logger.info(
                "attempt %s provider truncated: %s", plan.attempt_id, violation.detail
            )
            yield FinalResult(
                summary=f"truncated by frozen budget: {violation.kind}",
                exit_code=1,
                termination=violation.termination,
            )
        else:
            await proc.wait()
            if not result_seen:
                tail = b"".join(stderr_chunks)[-_STDERR_TAIL_BYTES:].decode(
                    "utf-8", errors="replace"
                )
                exit_code = self._exit_code(proc)
                yield FinalResult(
                    summary=tail.strip() or "provider exited without a result record",
                    exit_code=exit_code if exit_code != 0 else 1,
                    termination="failed",
                )
        if dropped:
            logger.debug(
                "attempt %s dropped %d non-conforming stream records",
                plan.attempt_id, dropped,
            )
        yield ProviderExited(exit_code=self._exit_code(proc))

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process,
                            chunks: list[bytes]) -> None:
        """Bound the in-memory stderr tail; raw provider diagnostics are never
        relayed (they may embed thinking — §3.7). Only the tail survives for a
        failure summary, which the supervisor redacts before upload."""
        assert proc.stderr is not None
        total = 0
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _STDERR_CAP_BYTES:
                    excess = total - _STDERR_CAP_BYTES
                    while excess > 0 and len(chunks) > 1:
                        excess -= len(chunks.pop(0))
        except asyncio.CancelledError:
            raise  # cancellation must propagate (supervisor teardown)
        except Exception:  # pragma: no cover — drain must NEVER kill the run
            pass  # a stderr read error (e.g. EIO) only loses diagnostics

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERM_GRACE_SECONDS)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()

    @staticmethod
    def _exit_code(proc: asyncio.subprocess.Process) -> int:
        rc = proc.returncode
        if rc is None:
            return 1
        if rc < 0:  # killed by signal → shell convention 128+signum
            return 128 + abs(rc)
        return rc

    # -- teardown ------------------------------------------------------------

    async def destroy(self) -> None:
        if self._launcher is not None:
            await self._launcher.destroy()
