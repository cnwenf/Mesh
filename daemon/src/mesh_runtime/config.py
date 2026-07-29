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
        "heartbeat_interval_seconds",
        "shutdown_grace_seconds",
        "allow_insecure_http",
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
    heartbeat_interval_seconds: float = 15.0  # default; server response wins
    shutdown_grace_seconds: float = 20.0
    allow_insecure_http: bool = False
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

        state_dir = Path(_require_str(raw, "state_dir"))
        work_dir = Path(_require_str(raw, "work_dir"))
        if not state_dir.is_absolute():
            raise ConfigError("state_dir must be an absolute path")
        if not work_dir.is_absolute():
            raise ConfigError("work_dir must be an absolute path")

        max_concurrent = int(raw.get("max_concurrent", 1))
        if max_concurrent < 1:
            raise ConfigError("max_concurrent must be >= 1")

        provider_path_raw = raw.get("provider_path")
        provider_path: Path | None = None
        if provider_path_raw is not None:
            provider_path = Path(str(provider_path_raw))
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

        return cls(
            server_url=server_url.rstrip("/"),
            state_dir=state_dir,
            work_dir=work_dir,
            max_concurrent=max_concurrent,
            provider_path=provider_path,
            provider_version=provider_version,
            heartbeat_interval_seconds=heartbeat,
            shutdown_grace_seconds=grace,
            allow_insecure_http=allow_insecure,
        )


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} is required and must be a non-empty string")
    return value


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
