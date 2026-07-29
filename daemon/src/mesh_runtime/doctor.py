"""``mesh-runtime doctor`` — actionable local capability checks (spec §4.1).

The daemon and the CLI share this implementation (spec §1.4). Every check
returns a precise reason and a next action; a generic "failed" status is not
allowed to mask a broken sandbox, provider, token file or egress setup.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from urllib.parse import urlparse

from mesh_runtime.config import DaemonConfig
from mesh_runtime.inventory import Inventory
from mesh_runtime.token_store import FileTokenStore, TokenStoreError


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


@dataclass(frozen=True)
class CheckReport:
    checks: tuple[Check, ...]

    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def render(self) -> str:
        lines = []
        for check in self.checks:
            mark = "OK  " if check.ok else "FAIL"
            line = f"[{mark}] {check.name}: {check.detail}"
            if not check.ok and check.hint:
                line += f"\n       -> {check.hint}"
            lines.append(line)
        return "\n".join(lines)


def _check_dir(name: str, path) -> Check:
    if not path.exists():
        return Check(name, False, f"{path} does not exist", f"mkdir -p {path} && chmod 0700 {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return Check(
            name,
            False,
            f"{path} permissions {oct(mode)} are too open (must be 0700)",
            f"chmod 0700 {path}",
        )
    return Check(name, True, f"{path} exists with mode 0700")


def _check_server_url(url: str) -> Check:
    scheme = urlparse(url).scheme
    if scheme in ("http", "https"):
        return Check("server_url", True, f"{url}")
    return Check(
        "server_url",
        False,
        f"{url} uses unsupported scheme {scheme!r}",
        "set server_url to an https origin",
    )


async def _check_token_file(config: DaemonConfig) -> Check:
    if not config.token_path.exists():
        return Check("token_file", True, "no token stored yet (not activated)")
    store = FileTokenStore(config.token_path, expected_uid=os.getuid())
    try:
        token = await store.load()
    except TokenStoreError as exc:
        return Check(
            "token_file",
            False,
            str(exc),
            "fix file owner/mode (0600, parent 0700) or re-activate with a fresh code",
        )
    if token is None:
        return Check("token_file", False, "token file vanished during check", "re-activate")
    return Check("token_file", True, "runtime token present and passes security checks")


def _check_providers(inventory: Inventory) -> Check:
    if inventory.healthy():
        keys = ", ".join(inventory.capability_keys()) or "none"
        return Check("providers", True, f"all providers available (capabilities: {keys})")
    reasons = "; ".join(inventory.degraded_reasons())
    return Check(
        "providers",
        False,
        f"degraded: {reasons}",
        "install/verify the pinned provider binary at the configured absolute path, "
        "then re-run mesh-runtime doctor",
    )


async def run_checks(config: DaemonConfig, inventory: Inventory) -> CheckReport:
    checks = [
        _check_server_url(config.server_url),
        _check_dir("state_dir", config.state_dir),
        _check_dir("work_dir", config.work_dir),
        await _check_token_file(config),
        _check_providers(inventory),
    ]
    return CheckReport(checks=tuple(checks))
