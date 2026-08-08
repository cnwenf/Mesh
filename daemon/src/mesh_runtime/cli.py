"""``mesh-runtime`` command line (spec §4.1 / §4.3).

Commands:
  mesh-runtime version
  mesh-runtime doctor   --config PATH
  mesh-runtime activate --config PATH (--activation-code-file PATH | --activation-code-stdin)
  mesh-runtime run      --config PATH

Activation codes are read ONLY from a 0600 file or stdin — never argv
(runtime.md §3.1 install safety). The runtime token never appears in argv,
logs or stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
from pathlib import Path

from mesh_runtime import __version__
from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.app import RuntimeApp, heartbeat_metadata
from mesh_runtime.config import DaemonConfig
from mesh_runtime.doctor import run_checks
from mesh_runtime.errors import DaemonError
from mesh_runtime.inventory import Inventory, probe_binary
from mesh_runtime.journal import Journal
from mesh_runtime.logging_config import configure_logging
from mesh_runtime.providers.base import ExecutorAdapter
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.token_store import FileTokenStore


def read_activation_code(path: Path) -> str:
    """Read the one-time code from a 0600 regular file (fail-closed)."""
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise DaemonError("activation code file must be a regular file (no symlink)")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise DaemonError("activation code file must be mode 0600")
    code = path.read_text(encoding="utf-8").strip()
    if not code:
        raise DaemonError("activation code file is empty")
    return code


def runtime_id_path(config: DaemonConfig) -> Path:
    return config.state_dir / "runtime_id"


def write_runtime_id(config: DaemonConfig, runtime_id: str) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    p = runtime_id_path(config)
    p.write_text(runtime_id, encoding="utf-8")
    os.chmod(p, 0o600)


def read_runtime_id(config: DaemonConfig) -> str | None:
    p = runtime_id_path(config)
    if not p.exists():
        return None
    value = p.read_text(encoding="utf-8").strip()
    return value or None


def build_adapters(config: DaemonConfig) -> list[ExecutorAdapter]:
    """Adapters probed at activation/heartbeat and offered to the server.

    With ``provider_manifest`` configured the pinned Claude Code adapter is
    registered (A3, §1.4/§5.4); otherwise the A1 fake provider keeps the
    contract path alive. Inventory reports only providers that PASS probing —
    a digest/version/flag mismatch degrades the runtime, never claims (§1.4).
    """
    if config.provider_manifest is not None:
        from mesh_runtime.manifest import load_provider_manifest
        from mesh_runtime.providers.claude_code import ClaudeCodeAdapter

        if config.provider_path is None:
            raise DaemonError("provider_manifest requires provider_path")
        manifest = load_provider_manifest(config.provider_manifest)
        return [
            ClaudeCodeAdapter(manifest=manifest, binary_path=str(config.provider_path))
        ]
    return [FakeProvider(events=[])]


async def cmd_doctor(config: DaemonConfig) -> int:
    from mesh_runtime.doctor import Check, CheckReport

    try:
        adapters = build_adapters(config)
    except DaemonError as exc:
        report = CheckReport(
            checks=(
                Check(
                    "provider_manifest",
                    False,
                    str(exc),
                    "fix the pinned provider manifest (mesh-runtime manifest hash --binary PATH)",
                ),
            )
        )
        print(report.render())
        return 1
    inventory = await Inventory.probe(adapters)
    if config.provider_path is not None:
        probe = await probe_binary(str(config.provider_path))
        base = await run_checks(config, inventory)
        extra = Check(
            "provider_binary",
            probe.ok,
            probe.reason or f"version={probe.version} sha256={probe.sha256}",
            "" if probe.ok else "install the pinned provider at the configured absolute path",
        )
        report = CheckReport(checks=base.checks + (extra,))
    else:
        report = await run_checks(config, inventory)
    print(report.render())
    return 0 if report.all_ok() else 1


async def cmd_activate(
    config: DaemonConfig, activation_code: str, *, api: RuntimeApiClient | None = None
) -> int:
    owns_api = api is None
    if api is None:
        api = RuntimeApiClient(config.server_url, token=None)
    try:
        inventory = await Inventory.probe(build_adapters(config))
        metadata = heartbeat_metadata(config, inventory)
        resp = await api.activate(activation_code, metadata)
        store = FileTokenStore(config.token_path, expected_uid=os.getuid())
        await store.save(resp.runtime_token)  # atomic 0600 before anything else
        write_runtime_id(config, resp.runtime_id)
        print(f"activated runtime {resp.runtime_id}")
        return 0
    finally:
        if owns_api:
            await api.close()


async def cmd_run(config: DaemonConfig) -> int:
    store = FileTokenStore(config.token_path, expected_uid=os.getuid())
    token = await store.load()  # fail-closed security checks
    if token is None:
        print("no runtime token — run mesh-runtime activate first", file=sys.stderr)
        return 2
    runtime_id = read_runtime_id(config)
    if runtime_id is None:
        print("no runtime id — run mesh-runtime activate first", file=sys.stderr)
        return 2
    api = RuntimeApiClient(config.server_url, token)
    journal = Journal(config.journal_path)
    await journal.open()
    sandbox_manager = None
    if config.sandbox_backend == "linux_ns":
        from mesh_runtime.sandbox import SandboxManager

        sandbox_manager = SandboxManager(
            state_root=config.state_dir / "sandbox",
            sandbox_uid=config.sandbox_uid,
            sandbox_gid=config.sandbox_gid,
        )
        await sandbox_manager.start()
    try:
        inventory = await Inventory.probe(build_adapters(config))
        app = RuntimeApp(
            config, api, journal, inventory, build_adapters(config),
            # Defense in depth (§3.8): the long-lived runtime token itself is
            # a redaction secret, so any accidental echo into a relayed log
            # line is masked even before the per-attempt secret set applies.
            redaction_secrets=[token],
            sandbox_manager=sandbox_manager,
        )
        app.set_runtime_id(runtime_id)
        loop = asyncio.get_running_loop()
        for sig in ("SIGTERM", "SIGINT"):
            try:
                loop.add_signal_handler(getattr(__import__("signal"), sig), app.request_shutdown)
            except NotImplementedError:  # pragma: no cover — non-UNIX
                pass
        await app.run()
        # TD-D: the heartbeat loop exhausted its in-process self-heal budget
        # and asked for a whole-process restart. Exit NON-ZERO so the process
        # manager (docker restart policy / systemd) brings the daemon back —
        # a clean 0 here would look like a normal stop and leave the runtime
        # alive-but-unheard (the MES-190 CLOSE-WAIT incident).
        if app.self_heal_exit:
            print(
                "heartbeat connection could not be healed — exiting for process-manager restart",
                file=sys.stderr,
            )
            return 3
        return 0
    finally:
        if sandbox_manager is not None:
            await sandbox_manager.shutdown()
        await journal.close()
        await api.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesh-runtime", description="Mesh local execution daemon")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print version")

    p_doctor = sub.add_parser("doctor", help="run local capability checks")
    p_doctor.add_argument("--config", required=True)

    p_activate = sub.add_parser("activate", help="exchange a one-time code for the runtime token")
    p_activate.add_argument("--config", required=True)
    source = p_activate.add_mutually_exclusive_group(required=True)
    source.add_argument("--activation-code-file")
    source.add_argument("--activation-code-stdin", action="store_true")

    p_run = sub.add_parser("run", help="run the daemon")
    p_run.add_argument("--config", required=True)

    p_manifest = sub.add_parser(
        "manifest", help="operator helpers for the pinned provider manifest"
    )
    manifest_sub = p_manifest.add_subparsers(dest="manifest_command", required=True)
    p_hash = manifest_sub.add_parser(
        "hash", help="print the SHA-256 + version to pin for a provider binary"
    )
    p_hash.add_argument("--binary", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "manifest":
        if args.manifest_command == "hash":
            return asyncio.run(cmd_manifest_hash(Path(args.binary)))
        parser.error(f"unknown manifest command {args.manifest_command}")  # pragma: no cover

    config = DaemonConfig.load(Path(args.config))

    # §4.3 log_level: install structured logging BEFORE any command runs so
    # doctor/activate/run failures are visible; the default root configuration
    # would swallow everything below WARNING (the daemon ran effectively
    # silent — heartbeat failures and self-heal escalations produced no output).
    configure_logging(config.log_level)

    if args.command == "doctor":
        return asyncio.run(cmd_doctor(config))

    if args.command == "activate":
        if args.activation_code_stdin:
            code = sys.stdin.read().strip()
            if not code:
                print("empty activation code on stdin", file=sys.stderr)
                return 2
        else:
            code = read_activation_code(Path(args.activation_code_file))
        return asyncio.run(cmd_activate(config, code))

    if args.command == "run":
        return asyncio.run(cmd_run(config))

    parser.error(f"unknown command {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


async def cmd_manifest_hash(binary: Path) -> int:
    """Operator helper: prints the values to pin in a provider manifest
    (binary_sha256 + reported version). Never runs the binary beyond a
    bare-env ``--version`` read (§1.4 step 2)."""
    probe = await probe_binary(str(binary))
    if not probe.ok:
        print(f"binary rejected: {probe.reason}", file=sys.stderr)
        return 2
    print(f"binary_sha256 = \"{probe.sha256}\"")
    print(f"version = \"{(probe.version or '').split()[0]}\"")
    return 0
