"""S-01 / S-10: provider isolation surface (runtime-executor.md §1.4~1.5, §3.8).

The daemon is the ONLY author of the provider's argv, environment and config
files:

- ``build_provider_argv`` emits the frozen §1.4 flag set — ``--bare``,
  ``--setting-sources ""``, ``--strict-mcp-config`` + explicit
  ``--mcp-config``, attempt-private ``--settings``/``--system-prompt-file``,
  allow/deny tool lists, ``bypassPermissions`` (which closes the provider's
  OWN prompts and nothing else — §1.2) and the frozen USD budget;
- ``validate_env_name`` / ``scrub_env`` enforce the NEW-M1 name rules plus the
  §3.8 reserved set (HOME/XDG, dynamic loading, cloud credentials, platform
  prefixes); the sandbox environment is built FROM EMPTY, never inherited;
- ``write_provider_configs`` creates settings.json / mcp.json / system.md in
  the attempt's private run dir: daemon-owned, mode 0400, later bind-mounted
  read-only — the task cannot rewrite them; mcp.json registers the platform
  task broker and nothing else;
- ``scan_repo_for_hostile_files`` enumerates (NEVER loads/executes) the repo
  files §1.5 rule 5 demotes to plain files — the ISO-09 negative fixture
  asserts none of them take effect.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.errors import DaemonError

_ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

#: Exact reserved names (server parity NEW-M1 + daemon §3.8 additions,
#: including the proxy family — the ONLY proxy pointer the provider may see
#: is the daemon-assembled egress address from build_sandbox_env).
_RESERVED_EXACT = frozenset(
    {
        "PATH",
        "NODE_OPTIONS",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    }
)

#: Reserved prefixes — dynamic loading, interpreter injection, platform
#: internals and cloud credentials (§3.8).
_RESERVED_PREFIXES = (
    "LD_",
    "DYLD_",
    "PYTHON",
    "MESH_DAEMON_",
    "MESH_INTERNAL_",
    "XDG_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCP_",
    "ALIBABA_",
    "ALIYUN_",
)

#: Generic sensitive suffixes (§3.8: token/credential/password names, from
#: whatever vendor or CI system minted them — defense in depth on top of the
#: provider-env-built-from-empty guarantee).
_RESERVED_SUFFIXES = (
    "_TOKEN",
    "_SECRET",
    "_KEY",
    "_KEYS",
    "_CREDENTIAL",
    "_CREDENTIALS",
    "_PASSWORD",
    "_PASSWD",
    "_APIKEY",
)


def _is_reserved_name(name: str) -> bool:
    return (
        name in _RESERVED_EXACT
        or name.startswith(_RESERVED_PREFIXES)
        or name.endswith(_RESERVED_SUFFIXES)
    )

#: Provider flags that would widen the loading surface — rejected from ANY
#: externally influenced input (§1.5 rule 4).
FORBIDDEN_ESCALATION_ARGS = frozenset(
    {
        "--add-dir",
        "--plugin-dir",
        "--plugin-url",
        "--agent",
        "--dangerously-skip-permissions",
        "--mcp-config-override",
    }
)

_SCAN_MAX_FILES = 20_000


class ReservedEnvError(DaemonError):
    """An env name or provider arg failed the reserved-name / escalation
    gate. Fail-closed; the rejected value is NOT echoed upstream."""


@dataclass(frozen=True)
class ProviderLaunchSpec:
    """Daemon-assembled, frozen launch inputs. No field may come from the
    worktree, the provider itself, or task output."""

    provider_path: str
    version: str
    model: str | None
    effort: str | None
    budget_usd: str  # decimal string, frozen at enqueue
    tools_allow: tuple[str, ...]
    tools_deny: tuple[str, ...]
    mcp_config_path: str
    settings_path: str
    system_prompt_path: str


def validate_env_name(name: str) -> None:
    if not isinstance(name, str) or not _ENV_NAME_PATTERN.match(name):
        raise ReservedEnvError("env name must match ^[A-Z][A-Z0-9_]{0,63}$")
    if _is_reserved_name(name):
        raise ReservedEnvError("reserved env name")


def scrub_env(merged: dict) -> dict:
    """§3.8 second pass: after any merge, drop every reserved or malformed
    name again. The merge rules cannot be trusted to have been correct."""
    out: dict = {}
    for key, value in merged.items():
        if not isinstance(key, str) or not _ENV_NAME_PATTERN.match(key):
            continue
        if _is_reserved_name(key):
            continue
        out[key] = value
    return out


def build_sandbox_env(
    *,
    attempt_id: str,
    execution_id: str,
    home: str,
    xdg_root: str,
    proxy_url: str | None = None,
    locale: str = "C.UTF-8",
) -> dict:
    """Construct the provider environment FROM EMPTY (§3.8). Only fixed
    locale, attempt IDs, the private HOME/XDG and the egress proxy pointer —
    never tokens, credentials or inherited daemon state."""
    env = scrub_env(
        {
            "LC_ALL": locale,
            "LANG": locale,
            "MESH_ATTEMPT_ID": attempt_id,
            "MESH_EXECUTION_ID": execution_id,
        }
    )
    # HOME/XDG are reserved names for EXTERNAL input — here the daemon sets
    # them itself to the attempt-private empty dirs created by the sandbox.
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = f"{xdg_root}/config"
    env["XDG_DATA_HOME"] = f"{xdg_root}/data"
    env["XDG_CACHE_HOME"] = f"{xdg_root}/cache"
    if proxy_url:
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
    return env


def validate_no_escalation_args(tokens: list[str]) -> None:
    for token in tokens:
        if isinstance(token, str) and token.split("=", 1)[0] in FORBIDDEN_ESCALATION_ARGS:
            raise ReservedEnvError("load-expanding provider flag is forbidden")


def build_provider_argv(spec: ProviderLaunchSpec) -> list[str]:
    """The fixed §1.4 argv. Prompt content travels via stdin/files only —
    never argv, never a shell (§1.4)."""
    allow = ",".join(spec.tools_allow) if spec.tools_allow else "none"
    deny = ",".join(spec.tools_deny) if spec.tools_deny else "none"
    return [
        spec.provider_path,
        "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--bare",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--setting-sources", "",  # load NO user/project/local sources
        "--strict-mcp-config",
        "--mcp-config", spec.mcp_config_path,
        "--settings", spec.settings_path,
        "--system-prompt-file", spec.system_prompt_path,
        "--tools", allow,
        "--disallowed-tools", deny,
        # bypassPermissions silences the provider's OWN interactive prompts
        # inside the locked sandbox — it never bypasses kernel isolation, the
        # egress gateway, credential boundaries or Mesh approvals (§1.2).
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", spec.budget_usd,
    ]


@dataclass(frozen=True)
class ProviderConfigPaths:
    settings_json: Path
    mcp_json: Path
    system_md: Path


async def write_provider_configs(
    root: Path,
    *,
    system_prompt: str,
    broker_socket_path: str,
    settings: dict | None = None,
) -> ProviderConfigPaths:
    """Write the three platform-owned config files into the attempt's private
    run dir: daemon-owned, 0444, later bind-mounted read-only (§1.4/§2.3).
    mcp.json registers the platform task broker and NOTHING else (§1.5)."""

    def _write_sync() -> ProviderConfigPaths:
        root.mkdir(parents=True, exist_ok=True)
        mcp = {
            "mcpServers": {
                "mesh-task-broker": {
                    "type": "unix-socket",
                    "path": broker_socket_path,
                }
            }
        }
        paths = ProviderConfigPaths(
            settings_json=root / "settings.json",
            mcp_json=root / "mcp.json",
            system_md=root / "system.md",
        )
        _write_private(paths.settings_json, json.dumps(settings or {}))
        _write_private(paths.mcp_json, json.dumps(mcp))
        _write_private(paths.system_md, system_prompt)
        return paths

    return await asyncio.to_thread(_write_sync)


def _write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o444)
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o444)  # world-readable, nobody-writable; ro-mounted anyway


@dataclass(frozen=True)
class HostileFinding:
    path: str  # worktree-relative
    kind: str


#: Repo files §1.5 rule 5 demotes to PLAIN files. Detection is a pure stat —
#: contents are never read, parsed, interpreted or executed. Matching is by
#: basename (nested copies stay hostile); ``.claude/settings*`` must sit
#: under a ``.claude`` directory.
_HOSTILE_BASENAME = {
    ".mcp.json": "mcp_config",
    "CLAUDE.md": "project_instructions",
}

_HOSTILE_UNDER_CLAUDE_DIR = {
    "settings.json": "project_settings",
    "settings.local.json": "local_settings",
}


def scan_repo_for_hostile_files(worktree: Path) -> list[HostileFinding]:
    """Enumerate hostile-looking repo files so isolation tests can assert
    they stay inert. Bounded walk; stat-only; never opens file contents."""
    findings: list[HostileFinding] = []
    visited = 0
    for dirpath, dirnames, filenames in os.walk(worktree):
        visited += len(filenames) + len(dirnames)
        if visited > _SCAN_MAX_FILES:
            break
        rel_dir = Path(dirpath).relative_to(worktree)
        in_claude_dir = ".claude" in rel_dir.parts
        for name in filenames:
            rel = name if str(rel_dir) == "." else str(rel_dir / name)
            if name in _HOSTILE_BASENAME:
                findings.append(HostileFinding(rel, _HOSTILE_BASENAME[name]))
            elif in_claude_dir and name in _HOSTILE_UNDER_CLAUDE_DIR:
                findings.append(HostileFinding(rel, _HOSTILE_UNDER_CLAUDE_DIR[name]))
        for name in dirnames:
            rel = name if str(rel_dir) == "." else str(rel_dir / name)
            if rel == ".claude/hooks":
                findings.append(HostileFinding(rel, "hooks"))
    return sorted(findings, key=lambda f: (f.kind, f.path))
