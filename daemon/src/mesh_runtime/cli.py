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
    """A1 ships the fake provider; the pinned Claude Code adapter lands in A3."""
    return [FakeProvider(events=[])]


async def cmd_doctor(config: DaemonConfig) -> int:
    inventory = await Inventory.probe(build_adapters(config))
    if config.provider_path is not None:
        probe = await probe_binary(str(config.provider_path))
        from mesh_runtime.doctor import Check, CheckReport

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
    try:
        inventory = await Inventory.probe(build_adapters(config))
        app = RuntimeApp(
            config, api, journal, inventory, build_adapters(config),
            # Defense in depth (§3.8): the long-lived runtime token itself is
            # a redaction secret, so any accidental echo into a relayed log
            # line is masked even before the per-attempt secret set applies.
            redaction_secrets=[token],
        )
        app.set_runtime_id(runtime_id)
        loop = asyncio.get_running_loop()
        for sig in ("SIGTERM", "SIGINT"):
            try:
                loop.add_signal_handler(getattr(__import__("signal"), sig), app.request_shutdown)
            except NotImplementedError:  # pragma: no cover — non-UNIX
                pass
        await app.run()
        return 0
    finally:
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    config = DaemonConfig.load(Path(args.config))

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
