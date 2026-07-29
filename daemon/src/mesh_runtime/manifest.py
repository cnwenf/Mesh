"""Immutable provider capability manifest (runtime-executor.md §1.4, §5.4).

Each provider version ships a release-pinned manifest: exact provider name and
version, the binary SHA-256, the required §1.4 flag set and hard-limit
capability assertions. The daemon validates it at startup and refuses to claim
tasks for a provider whose binary/version/digest/flags disagree — PATH search,
auto-upgrade and runtime download are impossible by construction (§5.4).

Fields map 1:1 onto the §1.4 YAML capability manifest; the wire encoding is
TOML (stdlib ``tomllib`` — no new dependency, no widened supply-chain surface).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.errors import DaemonError
from mesh_runtime.provider_env import FORBIDDEN_ESCALATION_ARGS

#: Providers this daemon knows how to adapt. New providers must implement the
#: same contract AND pass the same gates before being listed (§1.4).
KNOWN_PROVIDERS = frozenset({"claude-code"})

#: The frozen §1.4 argv set — the only flags a manifest may require. Anything
#: outside it is a supply-chain red flag (fail-closed).
PINNABLE_FLAGS = frozenset(
    {
        "--print",
        "--output-format",
        "--input-format",
        "--verbose",
        "--bare",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--setting-sources",
        "--strict-mcp-config",
        "--mcp-config",
        "--settings",
        "--system-prompt-file",
        "--tools",
        "--disallowed-tools",
        "--permission-mode",
        "--max-budget-usd",
    }
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ManifestError(DaemonError):
    """The pinned manifest is missing/invalid. The provider is unavailable;
    the runtime degrades and never claims its tasks (§1.4)."""


@dataclass(frozen=True)
class ProviderManifest:
    provider: str
    version: str
    binary_sha256: str
    required_flags: tuple[str, ...]
    hard_limits_usd_budget: bool
    hard_limits_wall_timeout: bool

    def fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "provider": self.provider,
                "version": self.version,
                "binary_sha256": self.binary_sha256,
                "required_flags": list(self.required_flags),
                "hard_limits": {
                    "usd_budget": self.hard_limits_usd_budget,
                    "wall_timeout": self.hard_limits_wall_timeout,
                },
            },
            sort_keys=True,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def capabilities(self) -> tuple[str, ...]:
        """Capability keys reported via heartbeat — server matches tasks on
        these (§1.4 step 5)."""
        caps = ["coding_cli.claude_code", "stream.json", "usage.cost"]
        if self.hard_limits_usd_budget:
            caps.append("budget.usd_hard")
        if self.hard_limits_wall_timeout:
            caps.append("budget.wall_hard")
        return tuple(caps)


def load_provider_manifest(path: Path) -> ProviderManifest:
    """Load and validate a pinned manifest. Fail-closed on any doubt."""
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"provider manifest not found: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"provider manifest does not parse as TOML: {exc}") from exc
    return _validate(raw)


def _validate(raw: dict) -> ProviderManifest:
    provider = raw.get("provider")
    if not isinstance(provider, str) or provider not in KNOWN_PROVIDERS:
        raise ManifestError(
            f"manifest provider must be one of {sorted(KNOWN_PROVIDERS)}"
        )
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise ManifestError("manifest version must be a non-empty pinned string")
    sha = raw.get("binary_sha256")
    if not isinstance(sha, str) or not _SHA256_PATTERN.fullmatch(sha):
        raise ManifestError("manifest binary_sha256 must be 64 lowercase hex chars")
    flags_raw = raw.get("required_flags")
    if not isinstance(flags_raw, list) or not flags_raw:
        raise ManifestError("manifest required_flags must be a non-empty list")
    flags: list[str] = []
    for flag in flags_raw:
        if not isinstance(flag, str):
            raise ManifestError("manifest required_flags entries must be strings")
        if flag.split("=", 1)[0] in FORBIDDEN_ESCALATION_ARGS:
            raise ManifestError(
                f"manifest requires escalation flag {flag} — refused (§1.5 rule 4)"
            )
        if flag not in PINNABLE_FLAGS:
            raise ManifestError(f"manifest requires unknown flag {flag} (not in §1.4 set)")
        flags.append(flag)
    hard_limits = raw.get("hard_limits")
    if not isinstance(hard_limits, dict):
        raise ManifestError("manifest missing [hard_limits] table")
    usd_budget = hard_limits.get("usd_budget")
    wall_timeout = hard_limits.get("wall_timeout")
    if usd_budget is not True:
        raise ManifestError(
            "manifest hard_limits.usd_budget must be true — real providers run only "
            "with a hard USD budget (§3.5)"
        )
    if wall_timeout is not True:
        raise ManifestError("manifest hard_limits.wall_timeout must be true (§3.5)")
    return ProviderManifest(
        provider=provider,
        version=version,
        binary_sha256=sha,
        required_flags=tuple(flags),
        hard_limits_usd_budget=usd_budget,
        hard_limits_wall_timeout=wall_timeout,
    )
