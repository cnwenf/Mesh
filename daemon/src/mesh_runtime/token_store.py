"""Runtime token persistence (runtime-executor.md §2.3).

The long-lived ``mesh_rt_`` token lives ONLY in this store. Reads perform the
spec-mandated ``lstat/open/fstat`` cross-check — no symlink, regular file,
exact owner, exact 0600 mode, parent directory 0700 — and any mismatch is
fail-closed: the daemon exits rather than "repair and continue".
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from mesh_runtime import RUNTIME_TOKEN_PREFIX
from mesh_runtime.errors import DaemonError


class TokenStoreError(DaemonError):
    """Token file failed a security check. Never log the token itself."""


class FileTokenStore:
    def __init__(self, path: Path, *, expected_uid: int | None = None) -> None:
        self.path = Path(path)
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid

    async def save(self, token: str) -> None:
        if not token.startswith(RUNTIME_TOKEN_PREFIX):
            raise TokenStoreError("refusing to store a token without the runtime prefix")
        await asyncio.to_thread(self._save_sync, token)

    def _save_sync(self, token: str) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
        tmp = parent / f".{self.path.name}.tmp"
        # Open with restrictive mode from the start; write, fsync, atomic swap.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, token.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)  # umask may have diluted the create mode
        os.replace(tmp, self.path)

    async def load(self) -> str | None:
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> str | None:
        try:
            st = self.path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(st.st_mode):
            raise TokenStoreError("token file is a symlink — refusing")
        if not stat.S_ISREG(st.st_mode):
            raise TokenStoreError("token file is not a regular file — refusing")
        if st.st_uid != self.expected_uid:
            raise TokenStoreError("token file owner mismatch — refusing")
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise TokenStoreError("token file mode must be exactly 0600 — refusing")
        parent_mode = stat.S_IMODE(self.path.parent.stat().st_mode)
        if parent_mode & 0o077:
            raise TokenStoreError("token parent directory must not grant group/other access — refusing")
        # Re-verify through the opened fd (TOCTOU between lstat and open).
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode) or fst.st_uid != self.expected_uid:
                raise TokenStoreError("token file changed underneath us — refusing")
            raw = os.read(fd, 4096)
        finally:
            os.close(fd)
        token = raw.decode("utf-8").strip()
        if not token:
            raise TokenStoreError("token file is empty — refusing")
        if not token.startswith(RUNTIME_TOKEN_PREFIX):
            raise TokenStoreError(
                "token file does not contain a runtime token (wrong prefix) — refusing"
            )
        return token

    async def clear(self) -> None:
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
