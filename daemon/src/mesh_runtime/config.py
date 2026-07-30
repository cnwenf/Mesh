"""Daemon configuration (runtime-executor.md §4.3 table).

Loaded from a TOML file (``--config`` / ``MESH_RUNTIME_CONFIG``). Server-owned
values (heartbeat/lease/poll intervals, max_concurrent ceiling) arrive in
protocol responses and are NEVER configured here — the daemon applies only
bounds and jitter, never relaxations.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from mesh_runtime.errors import DaemonError

_KNOWN_KEYS = frozenset(
    {
        "server_url",
        "state_dir",
        "work_dir",
        "max_concurrent",
        "provider_path",
        "provider_version",
        "provider_manifest",
        "provider_env_file",
        "heartbeat_interval_seconds",
        "shutdown_grace_seconds",
        "allow_insecure_http",
        "sandbox_uid",
        "sandbox_gid",
        "sandbox_backend",
        "sandbox_memory_bytes",
        "sandbox_cpu_quota_us",
        "sandbox_pids_max",
        "sandbox_tmp_bytes",
        "runtime_kind",
    }
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(DaemonError):
    """Invalid or missing configuration. Fail before doing anything else."""


@dataclass(frozen=True)
class DaemonConfig:
    server_url: str
    state_dir: Path
    work_dir: Path
    max_concurrent: int = 1
    provider_path: Path | None = None
    provider_version: str | None = None
    # A3: pinned provider capability manifest (§1.4/§5.4) and the 0600 file
    # holding administrator-owned provider credentials (§5.4.7). Setting the
    # manifest switches the daemon from the fake provider to the pinned real
    # provider; provider_path is then REQUIRED.
    provider_manifest: Path | None = None
    provider_env_file: Path | None = None
    heartbeat_interval_seconds: float = 15.0  # default; server response wins
    shutdown_grace_seconds: float = 20.0
    allow_insecure_http: bool = False
    sandbox_uid: int = 65534  # nobody — the sandbox drops to this uid
    sandbox_gid: int = 65534  # nogroup
    sandbox_backend: str = "linux_ns"  # "linux_ns" (fail-closed) | "none" (dev only)
    # Daemon-local cgroup ceilings for one attempt (§4.3: the frozen snapshot
    # may impose STRICTER limits; these are the local ceiling, never a floor).
    sandbox_memory_bytes: int = 512 * 1024 * 1024
    sandbox_cpu_quota_us: int = 100_000
    sandbox_pids_max: int = 256
    sandbox_tmp_bytes: int = 256 * 1024 * 1024
    runtime_kind: str = "self_hosted"  # self_hosted | platform_managed
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def token_path(self) -> Path:
        return self.state_dir / "runtime.token"

    @property
    def journal_path(self) -> Path:
        return self.state_dir / "ledger.sqlite3"

    @property
    def spool_dir(self) -> Path:
        return self.state_dir / "spool"

    @classmethod
    def load(cls, path: Path) -> DaemonConfig:
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config file does not parse as TOML: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> DaemonConfig:
        unknown = set(raw) - _KNOWN_KEYS
        if unknown:
            raise ConfigError(f"unknown config keys: {sorted(unknown)}")

        server_url = _require_str(raw, "server_url")
        allow_insecure = bool(raw.get("allow_insecure_http", False))
        _validate_server_url(server_url, allow_insecure=allow_insecure)

        state_dir = _require_path(raw, "state_dir")
        work_dir = _require_path(raw, "work_dir")
        if not state_dir.is_absolute():
            raise ConfigError("state_dir must be an absolute path")
        if not work_dir.is_absolute():
            raise ConfigError("work_dir must be an absolute path")

        max_concurrent = int(raw.get("max_concurrent", 1))
        if max_concurrent < 1:
            raise ConfigError("max_concurrent must be >= 1")

        provider_path: Path | None = None
        if raw.get("provider_path") is not None:
            provider_path = _require_path(raw, "provider_path")
            if not provider_path.is_absolute():
                raise ConfigError("provider_path must be an absolute path")

        heartbeat = float(raw.get("heartbeat_interval_seconds", 15.0))
        if heartbeat <= 0:
            raise ConfigError("heartbeat_interval_seconds must be > 0")
        grace = float(raw.get("shutdown_grace_seconds", 20.0))
        if grace < 0:
            raise ConfigError("shutdown_grace_seconds must be >= 0")

        provider_version = raw.get("provider_version")
        if provider_version is not None:
            provider_version = str(provider_version)

        provider_manifest: Path | None = None
        if raw.get("provider_manifest") is not None:
            provider_manifest = _require_path(raw, "provider_manifest")
            if not provider_manifest.is_absolute():
                raise ConfigError("provider_manifest must be an absolute path")
            if provider_path is None:
                raise ConfigError(
                    "provider_manifest requires provider_path (the pinned binary location)"
                )
        provider_env_file: Path | None = None
        if raw.get("provider_env_file") is not None:
            provider_env_file = _require_path(raw, "provider_env_file")
            if not provider_env_file.is_absolute():
                raise ConfigError("provider_env_file must be an absolute path")

        sandbox_memory_bytes = int(raw.get("sandbox_memory_bytes", 512 * 1024 * 1024))
        sandbox_cpu_quota_us = int(raw.get("sandbox_cpu_quota_us", 100_000))
        sandbox_pids_max = int(raw.get("sandbox_pids_max", 256))
        sandbox_tmp_bytes = int(raw.get("sandbox_tmp_bytes", 256 * 1024 * 1024))
        for name, value in (
            ("sandbox_memory_bytes", sandbox_memory_bytes),
            ("sandbox_cpu_quota_us", sandbox_cpu_quota_us),
            ("sandbox_pids_max", sandbox_pids_max),
            ("sandbox_tmp_bytes", sandbox_tmp_bytes),
        ):
            if value <= 0:
                raise ConfigError(f"{name} must be > 0")

        sandbox_backend = str(raw.get("sandbox_backend", "linux_ns"))
        if sandbox_backend not in ("linux_ns", "none"):
            raise ConfigError("sandbox_backend must be 'linux_ns' or 'none'")
        runtime_kind = str(raw.get("runtime_kind", "self_hosted"))
        if runtime_kind not in ("self_hosted", "platform_managed"):
            raise ConfigError("runtime_kind must be 'self_hosted' or 'platform_managed'")
        sandbox_uid = int(raw.get("sandbox_uid", 65534))
        sandbox_gid = int(raw.get("sandbox_gid", 65534))
        if sandbox_uid <= 0:
            raise ConfigError("sandbox_uid must be an unprivileged uid > 0")

        return cls(
            server_url=server_url.rstrip("/"),
            state_dir=state_dir,
            work_dir=work_dir,
            max_concurrent=max_concurrent,
            provider_path=provider_path,
            provider_version=provider_version,
            provider_manifest=provider_manifest,
            provider_env_file=provider_env_file,
            heartbeat_interval_seconds=heartbeat,
            shutdown_grace_seconds=grace,
            allow_insecure_http=allow_insecure,
            sandbox_uid=sandbox_uid,
            sandbox_gid=sandbox_gid,
            sandbox_backend=sandbox_backend,
            sandbox_memory_bytes=sandbox_memory_bytes,
            sandbox_cpu_quota_us=sandbox_cpu_quota_us,
            sandbox_pids_max=sandbox_pids_max,
            sandbox_tmp_bytes=sandbox_tmp_bytes,
            runtime_kind=runtime_kind,
        )


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} is required and must be a non-empty string")
    return value


def _require_path(raw: dict, key: str) -> Path:
    value = raw.get(key)
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        return Path(value)
    raise ConfigError(f"{key} is required and must be a non-empty path")


def _validate_server_url(url: str, *, allow_insecure: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http":
        # Local development only: explicit opt-in AND loopback host. Production
        # stays on the TLS red line (runtime-executor.md §4.3).
        host = (parsed.hostname or "").lower()
        if not allow_insecure:
            raise ConfigError("server_url must use https (set allow_insecure_http for local dev)")
        if host not in _LOOPBACK_HOSTS:
            raise ConfigError("allow_insecure_http permits loopback hosts only")
    else:
        raise ConfigError("server_url must use https (or http loopback in local dev)")
    if not parsed.hostname:
        raise ConfigError("server_url has no host")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConfigError("server_url must be an origin only (no path/query)")
