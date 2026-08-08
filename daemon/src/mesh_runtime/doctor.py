"""``mesh-runtime doctor`` — actionable local capability checks (spec §4.1).

The daemon and the CLI share this implementation (spec §1.4). Every check
returns a precise reason and a next action; a generic "failed" status is not
allowed to mask a broken sandbox, provider, token file or egress setup.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
from dataclasses import dataclass
from urllib.parse import urlparse

from mesh_runtime.config import DaemonConfig
from mesh_runtime.inventory import Inventory
from mesh_runtime.token_store import FileTokenStore, TokenStoreError

#: git < 2.31 ignores the ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_N``/
#: ``GIT_CONFIG_VALUE_N`` env vars the checkout helper uses to inject
#: ``http.followRedirects=false`` — on such a git the §3.2 cross-host-redirect
#: SSRF guard would be SILENTLY ineffective. doctor must fail-closed on it.
_MIN_GIT_VERSION = (2, 31, 0)
#: libcurl < 8.0 forwards the Authorization header across hosts on a redirect;
#: the guard disables redirects entirely so this is hardening (a warning), not
#: a live hole.
_LIBCURL_AUTH_STRIP_VERSION = (8, 0)
_TOOL_TIMEOUT_SECONDS = 10.0


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


def _check_egress_mode(config: DaemonConfig) -> Check:
    """TD-E (§3.4): the egress gateway is default-on (``strict``). doctor
    fail-closes the combination that CANNOT honour it — strict mode on a
    backend with no forced route — instead of letting it claim enforcement."""
    from mesh_runtime.config import EGRESS_MODE_OFF

    mode = config.egress_gateway_mode
    if mode == EGRESS_MODE_OFF:
        return Check(
            "egress_gateway",
            True,
            "mode off — egress_enforced=false is reported; the server will not "
            "dispatch network-requiring executions to this runtime (§3.4)",
        )
    if config.sandbox_backend == "linux_ns":
        return Check(
            "egress_gateway",
            True,
            "mode strict — per-attempt gateway enforced (no default route out "
            "of the sandbox netns)",
        )
    return Check(
        "egress_gateway",
        False,
        f"mode {mode} requires sandbox_backend=linux_ns, but the backend is "
        f"{config.sandbox_backend!r} — a sandboxless daemon cannot prove the "
        "forced egress route, so strict mode cannot hold",
        "run with sandbox_backend=linux_ns (root + cgroup v2 delegation), or "
        'explicitly set egress_gateway_mode="off" and accept that this runtime '
        "receives no network-requiring executions",
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


def _parse_version(text: str) -> tuple[int, ...] | None:
    """First ``X[.Y[.Z]]`` run in ``text`` as an int tuple (None if absent)."""
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if match is None:
        return None
    return tuple(int(group) for group in match.groups() if group is not None)


async def _run_tool(argv: list[str]) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TOOL_TIMEOUT_SECONDS)
    except (OSError, TimeoutError):
        return None
    return stdout.decode("utf-8", "replace")


async def _detect_libcurl_version() -> str | None:
    """Best-effort libcurl version from the curl CLI (``libcurl/X.Y.Z``). Git
    links its own libcurl, so this is a proxy indicator, reported for parity."""
    output = await _run_tool(["curl", "--version"])
    if output is None:
        return None
    match = re.search(r"libcurl/([\d.]+)", output)
    return match.group(1) if match else None


async def _check_git_toolchain() -> Check:
    """Fail-closed on a git too old to honour the checkout redirect guard;
    report libcurl version as a cross-toolchain redirect-semantics signal."""
    if shutil.which("git") is None:
        return Check(
            "git_toolchain", False, "git not found on PATH",
            "install git >= 2.31 (checkout depends on it)",
        )
    version_text = await _run_tool(["git", "--version"])
    if version_text is None:
        return Check(
            "git_toolchain", False, "could not run `git --version`",
            "install/repair git >= 2.31",
        )
    version = _parse_version(version_text)
    if version is None:
        return Check(
            "git_toolchain", False, f"unparseable git version {version_text.strip()!r}",
            "install git >= 2.31",
        )
    if version < _MIN_GIT_VERSION:
        return Check(
            "git_toolchain", False,
            "git " + ".".join(map(str, version)) + " < 2.31 — GIT_CONFIG_COUNT is "
            "unavailable, so the http.followRedirects=false checkout SSRF guard "
            "would be silently ignored",
            "upgrade git to >= 2.31 so the checkout redirect guard takes effect",
        )
    detail = "git " + ".".join(map(str, version))
    libcurl = await _detect_libcurl_version()
    if libcurl is not None:
        detail += f", libcurl {libcurl}"
        libcurl_version = _parse_version(libcurl)
        if libcurl_version is not None and libcurl_version < _LIBCURL_AUTH_STRIP_VERSION:
            detail += (
                " (libcurl < 8.0 forwards Authorization across hosts on redirect; "
                "redirects are disabled by the checkout guard, so this is a "
                "hardening note, not a live exposure)"
            )
    return Check("git_toolchain", True, detail)


async def run_checks(config: DaemonConfig, inventory: Inventory) -> CheckReport:
    checks = [
        _check_server_url(config.server_url),
        _check_egress_mode(config),
        _check_dir("state_dir", config.state_dir),
        _check_dir("work_dir", config.work_dir),
        await _check_token_file(config),
        _check_providers(inventory),
        await _check_git_toolchain(),
    ]
    return CheckReport(checks=tuple(checks))
