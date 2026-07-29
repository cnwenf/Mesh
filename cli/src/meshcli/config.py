"""Local configuration + credential store (cli.md §2.2, C6/C26/C27).

Two physically separate files:

    ~/.config/mesh/config.yaml        non-secret (0644 ok; dotfiles-friendly)
    ~/.config/mesh/credentials.yaml   secret — 0600 FAIL-CLOSED: a credential
        file (or parent dir) that is group/other readable/writable, not owned
        by the current uid, or a symlink is REFUSED (exit 2 + a one-line fix),
        never downgraded to a warning — the file holds long-lived PAT /
        refresh tokens, so lax permissions equal leakage.

Precedence chain (C6): flag > env (``MESH_*``) > file > default. Env parsing
is pinned: booleans accept 1/0/true/false/yes/no (case-insensitive); an EMPTY
string counts as UNSET (falls through to the next level — never an "empty
value invalid" error); enum keys share the flag value set (invalid → exit 3).

Writes are atomic: a 0600 temp file in the same directory → fsync → rename,
so a crash never leaves a half-written or transiently world-readable file.
"""

from __future__ import annotations

import os
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from meshcli.errors import EXIT_AUTH, EXIT_VALIDATION, CliError

CONFIG_VERSION = 1
CONFIG_DIR_ENV = "MESH_CONFIG"
TOKEN_ENV = "MESH_TOKEN"

CONFIG_FILENAME = "config.yaml"
CREDENTIALS_FILENAME = "credentials.yaml"

CREDENTIAL_FILE_MODE = 0o600
CREDENTIAL_DIR_MODE = 0o700

DEFAULT_API_URL = "https://mesh.example.com"
DEFAULT_OUTPUT = "table"

OUTPUT_VALUES = ("table", "json")

# Keys that may never be persisted via `mesh config set` (cli.md §5.3):
# insecure is a per-invocation escape flag only.
UNPERSISTABLE_KEYS = frozenset({"insecure"})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class CredentialFileError(CliError):
    """Fail-closed credential store violation (exit 2 + fix instruction)."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message=message, exit_code=EXIT_AUTH, hint=hint)


@dataclass(frozen=True)
class CredentialEntry:
    """One host's stored credential (credentials.yaml ``hosts[<host>]``)."""

    kind: str  # "pat" | "device_session"
    token: str
    refresh_token: str | None = None
    expires_at: str | None = None
    scopes: list[str] = field(default_factory=list)
    prefix: str | None = None  # display-only fragment, never secret
    workspace: str | None = None  # approved-bound workspace slug (device login)


def config_dir() -> Path:
    """``$MESH_CONFIG`` or the XDG default ``~/.config/mesh``."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "mesh"


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILENAME


# --- atomic writes -------------------------------------------------------------


def _atomic_write_yaml(path: Path, data: dict, *, mode: int) -> None:
    """Write YAML via temp-file → fsync → rename; the temp file is created
    with the FINAL permissions so no intermediate state is ever looser."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == CREDENTIAL_DIR_MODE or path.name == CREDENTIALS_FILENAME:
        _chmod_dir(path.parent, CREDENTIAL_DIR_MODE)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _chmod_dir(directory: Path, mode: int) -> None:
    try:
        directory.chmod(mode)
    except OSError:
        pass  # best-effort on filesystems that refuse chmod


# --- credential store (fail-closed) --------------------------------------------


def validate_credential_store() -> None:
    """Fail closed on ANY of: symlinked file/dir, non-owner, group/other
    access bits. The check runs before every credentials read (C21/§5.3)."""
    path = credentials_path()
    if not path.exists() and not path.is_symlink():
        return  # no store yet — nothing to protect
    parent = path.parent

    # Parent directory must be 0700-equivalent and owned by us.
    try:
        parent_stat = os.lstat(parent)
    except OSError as exc:
        raise CredentialFileError(
            f"cannot stat credential directory {parent}",
            hint=f"mkdir -p {parent} && chmod 700 {parent}",
        ) from exc
    if stat.S_ISLNK(parent_stat.st_mode):
        raise CredentialFileError(
            f"credential directory {parent} is a symlink (refused)",
            hint=f"rm {parent} && mkdir -p {parent} && chmod 700 {parent}",
        )
    if parent_stat.st_uid != os.getuid():
        raise CredentialFileError(
            f"credential directory {parent} is not owned by you",
            hint=f"chown {os.getuid()} {parent} && chmod 700 {parent}",
        )
    if parent_stat.st_mode & 0o077:
        raise CredentialFileError(
            f"credential directory {parent} is accessible by group/others",
            hint=f"chmod 700 {parent} && chmod 600 {path}",
        )

    if not path.exists():
        return
    try:
        file_stat = os.lstat(path)
    except OSError as exc:
        raise CredentialFileError(
            f"cannot stat credential file {path}",
            hint=f"chmod 700 {parent} && chmod 600 {path}",
        ) from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise CredentialFileError(
            f"credential file {path} is a symlink (refused — an attacker could "
            "point it at a shared or controlled path)",
            hint=f"rm {path}  # then re-run `mesh auth login`",
        )
    if file_stat.st_uid != os.getuid():
        raise CredentialFileError(
            f"credential file {path} is not owned by you",
            hint=f"chown {os.getuid()} {path} && chmod 600 {path}",
        )
    if file_stat.st_mode & 0o077:
        raise CredentialFileError(
            f"credential file {path} is readable/writable by group/others",
            hint=f"chmod 700 {parent} && chmod 600 {path}",
        )


def load_credentials_raw() -> dict:
    """Read credentials.yaml AFTER the fail-closed validation."""
    validate_credential_store()
    path = credentials_path()
    if not path.exists():
        return {"version": CONFIG_VERSION, "hosts": {}}
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data.setdefault("version", CONFIG_VERSION)
    data.setdefault("hosts", {})
    return data


def load_credential(host: str) -> CredentialEntry | None:
    """The stored credential for ``host`` — None when absent.

    ``MESH_TOKEN`` short-circuits the file entirely (CI): when set, the file
    is NOT read for this host (§2.2)."""
    env_token = os.environ.get(TOKEN_ENV)
    if env_token:
        return CredentialEntry(kind="pat", token=env_token, prefix=env_token[:12])
    hosts = load_credentials_raw().get("hosts", {})
    entry = hosts.get(host)
    if entry is None:
        return None
    return CredentialEntry(
        kind=entry.get("kind", "pat"),
        token=entry["token"],
        refresh_token=entry.get("refresh_token"),
        expires_at=entry.get("expires_at"),
        scopes=list(entry.get("scopes", [])),
        prefix=entry.get("prefix"),
        workspace=entry.get("workspace"),
    )


def save_credential(host: str, entry: CredentialEntry) -> None:
    data = load_credentials_raw()
    record: dict[str, Any] = {"kind": entry.kind, "token": entry.token}
    if entry.refresh_token is not None:
        record["refresh_token"] = entry.refresh_token
    if entry.expires_at is not None:
        record["expires_at"] = entry.expires_at
    if entry.scopes:
        record["scopes"] = list(entry.scopes)
    if entry.prefix is not None:
        record["prefix"] = entry.prefix
    if entry.workspace is not None:
        record["workspace"] = entry.workspace
    data["hosts"][host] = record
    _atomic_write_yaml(credentials_path(), data, mode=CREDENTIAL_FILE_MODE)


def clear_credential(host: str) -> None:
    data = load_credentials_raw()
    if host in data.get("hosts", {}):
        del data["hosts"][host]
        _atomic_write_yaml(credentials_path(), data, mode=CREDENTIAL_FILE_MODE)


# --- config (non-secret) --------------------------------------------------------


def load_config_raw() -> dict:
    path = config_path()
    if not path.exists():
        return {"version": CONFIG_VERSION}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def save_config_raw(data: dict) -> None:
    data = dict(data)
    data.setdefault("version", CONFIG_VERSION)
    _atomic_write_yaml(config_path(), data, mode=0o644)


# --- precedence resolution (flag > env > file > default) -------------------------

# key → (env var, default)
KNOWN_KEYS: dict[str, tuple[str, str]] = {
    "api_url": ("MESH_API_URL", DEFAULT_API_URL),
    "workspace": ("MESH_WORKSPACE", ""),
    "output": ("MESH_OUTPUT", DEFAULT_OUTPUT),
}


def parse_env_bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise CliError(
        f"invalid boolean env value {raw!r}",
        exit_code=EXIT_VALIDATION,
        hint="Use one of: 1/0/true/false/yes/no.",
    )


def _validate_value(key: str, value: str) -> str:
    if key == "output" and value not in OUTPUT_VALUES:
        raise CliError(
            f"invalid --output value {value!r}",
            exit_code=EXIT_VALIDATION,
            hint=f"Expected one of: {', '.join(OUTPUT_VALUES)}.",
        )
    return value


@dataclass(frozen=True)
class Resolved:
    value: str
    source: str  # "flag" | "env" | "file" | "default"


def resolve_key(key: str, *, flag_value: str | None, config: dict | None = None) -> Resolved:
    """Resolve one key through flag > env > file > default (C6).

    An EMPTY env value is treated as UNSET (falls through — never an error).
    """
    env_var, default = KNOWN_KEYS[key]
    if flag_value is not None and flag_value != "":
        return Resolved(_validate_value(key, flag_value), "flag")
    env_raw = os.environ.get(env_var)
    if env_raw is not None and env_raw != "":
        return Resolved(_validate_value(key, env_raw), "env")
    config = config if config is not None else load_config_raw()
    # api_url lives under `current_host` in the file (§2.2 config.yaml shape).
    file_value = config.get("current_host") if key == "api_url" else config.get(key)
    if file_value is None:
        # hosts[current_host].<key> shadow (per-host workspace, §2.2)
        current_host = config.get("current_host")
        if current_host:
            file_value = (config.get("hosts", {}) or {}).get(current_host, {}).get(key)
    if file_value is not None and str(file_value) != "":
        return Resolved(_validate_value(key, str(file_value)), "file")
    return Resolved(default, "default")


# --- config set/get/unset --------------------------------------------------------


def config_set(key: str, value: str) -> None:
    if key in UNPERSISTABLE_KEYS:
        raise CliError(
            f"`{key}` cannot be persisted",
            exit_code=EXIT_VALIDATION,
            hint="`--insecure` is a per-invocation flag only; it is never stored.",
        )
    if key == "proxy" and "@" in value.split("://", 1)[-1]:
        raise CliError(
            "authenticated proxy credentials cannot be persisted",
            exit_code=EXIT_VALIDATION,
            hint="Set the proxy (with userinfo) via HTTPS_PROXY/HTTP_PROXY env only.",
        )
    _validate_value(key, value)
    data = load_config_raw()
    if key == "api_url":
        data["current_host"] = value
    else:
        data[key] = value
    save_config_raw(data)


def config_unset(key: str) -> None:
    data = load_config_raw()
    removed = False
    if key == "api_url" and "current_host" in data:
        del data["current_host"]
        removed = True
    elif key in data:
        del data[key]
        removed = True
    if removed:
        save_config_raw(data)


def config_get(key: str) -> Resolved:
    if key not in KNOWN_KEYS:
        raise CliError(
            f"unknown config key {key!r}",
            exit_code=EXIT_VALIDATION,
            hint=f"Known keys: {', '.join(sorted(KNOWN_KEYS))}.",
        )
    return resolve_key(key, flag_value=None)


def config_list_all() -> list[dict[str, str]]:
    """Every known key with its effective value AND source (排障基线, C6)."""
    return [
        {"key": key, **resolve_key(key, flag_value=None).__dict__}
        for key in sorted(KNOWN_KEYS)
    ]


# --- aliases (single-level config sugar, C27) ------------------------------------


def load_aliases(config: dict | None = None) -> dict[str, str]:
    config = config if config is not None else load_config_raw()
    aliases = config.get("aliases", {}) or {}
    return {str(k): str(v) for k, v in aliases.items()}


def expand_alias(argv: list[str], aliases: dict[str, str]) -> list[str]:
    """Expand argv[0] through the alias map — ONE level only (no recursion,
    no nest-expansion; positional args pass through untouched)."""
    if not argv:
        return argv
    head, rest = argv[0], argv[1:]
    target = aliases.get(head)
    if target is None:
        return argv
    return [*target.split(), *rest]


def did_you_mean(name: str, candidates: list[str], *, max_distance: int = 3) -> str | None:
    """Nearest candidate by Levenshtein distance (for usage errors)."""
    best: str | None = None
    best_distance = max_distance + 1
    for candidate in candidates:
        distance = _levenshtein(name, candidate)
        if distance < best_distance:
            best, best_distance = candidate, distance
    return best


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def new_request_id() -> str:
    """A client-generated idempotency key fragment (README §6.5)."""
    return uuid.uuid4().hex
