"""In-child sandbox setup (runtime-executor.md §1.2/§1.5).

Exec'd by :mod:`mesh_runtime.sandbox` as a separate process. Sequence:

1. unshare mount/ipc/uts/net namespaces; report NETNS_READY; wait for GO
   (the daemon places us in the attempt cgroup and wires the veth pair
   while we wait — ordering that cannot race);
2. unshare the pid namespace and fork (the child becomes pid-1 of the new
   pidns);
3. build a minimal root: bind the daemon-provided system dirs read-only,
   pivot_root into the attempt root, mount a fresh /proc (new pidns),
   tmpfs /tmp and /dev with only null/zero/urandom, minimal /etc/hosts —
   NO host /etc, NO host home, NO daemon state;
4. drop supplementary groups + gid + uid (never to return);
5. report SANDBOX_READY <pid>, then execve the provider with the
   daemon-scrubbed environment. Prompt content reaches the provider via
   stdin/files only.

Every failure exits non-zero BEFORE dropping privileges and BEFORE exec —
the daemon observes the failed handshake and fails the attempt closed.
This program holds no token or credential of any kind.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from typing import NoReturn

# Mount flags (Linux ABI).
_MS_RDONLY = 1
_MS_NOSUID = 2
_MS_NODEV = 4
_MS_NOEXEC = 8
_MS_REC = 16384
_MS_PRIVATE = 1 << 18
_MS_BIND = 4096
_MS_REMOUNT = 32
_MNT_DETACH = 2

_SYSTEM_DIRS = ("/usr",)
# Public CA trust stores (read-only). The provider performs TLS verification
# against the pinned egress destination (§3.4); these directories hold ONLY
# public root certificates — no host config, no secrets.
_CA_CERT_DIRS = ("/etc/ssl/certs", "/etc/pki/tls/certs")
_USR_SYMLINKS = {"bin": "usr/bin", "lib": "usr/lib", "lib64": "usr/lib64", "sbin": "usr/sbin"}

_libc = ctypes.CDLL("libc.so.6", use_errno=True)


def _mount(source: str, target: str, fstype: str = "", flags: int = 0, data: str = "") -> None:
    rc = _libc.mount(
        source.encode(), target.encode(),
        fstype.encode() if fstype else None,
        ctypes.c_ulong(flags),
        data.encode() if data else None,
    )
    if rc != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"mount {source or fstype!r} -> {target}: {os.strerror(errno)}")


def _umount2(target: str, flags: int) -> None:
    rc = _libc.umount2(target.encode(), ctypes.c_int(flags))
    if rc != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"umount {target}: {os.strerror(errno)}")


def _pivot_root(new_root: str, put_old: str) -> None:
    rc = _libc.pivot_root(new_root.encode(), put_old.encode())
    if rc != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"pivot_root {new_root}: {os.strerror(errno)}")


def _status(fd: int, message: str) -> None:
    os.write(fd, (message + "\n").encode())


def _fail(status_fd: int, message: str) -> NoReturn:
    try:
        _status(status_fd, f"ERROR {message}")
    finally:
        os._exit(127)


def _bind_ro(source: str, target: str) -> None:
    os.makedirs(target, exist_ok=True)
    _mount(source, target, flags=_MS_BIND | _MS_REC)
    _mount(source, target, flags=_MS_BIND | _MS_REMOUNT | _MS_RDONLY | _MS_NOSUID | _MS_NODEV | _MS_REC)


def _setup_mounts(spec: dict) -> None:
    root = spec["root"]
    # Stop mounts leaking to the host, then make the attempt root a mount point.
    _mount("", "/", "", _MS_REC | _MS_PRIVATE, "")
    _mount(root, root, "", _MS_BIND | _MS_REC, "")
    # Read-only system "image" — host system files, no secrets.
    for src in _SYSTEM_DIRS:
        if os.path.exists(src):
            _bind_ro(src, root + src)
    # Extra read-only binds (provider binaries) at their host paths. Bound
    # PRE-pivot (host paths vanish afterwards). NOTE: paths under /tmp are
    # shadowed by the sandbox tmpfs — providers must live elsewhere, matching
    # the /opt/mesh/providers/... production layout (§4.3).
    for src in tuple(spec.get("ro_binds", [])):
        if os.path.exists(src):
            os.makedirs(root + src, exist_ok=True)
            _bind_ro(src, root + src)
    # Provider run dir (settings/mcp/system + broker socket): bind read-only.
    run_dir = os.path.join(root, "run")
    _mount(run_dir, run_dir, "", _MS_BIND | _MS_REC, "")
    _mount(run_dir, run_dir, "", _MS_BIND | _MS_REMOUNT | _MS_RDONLY | _MS_NOSUID | _MS_NODEV | _MS_REC)
    # Worktree: its own rw bind so the root fs itself can go read-only.
    worktree = os.path.join(root, "worktree")
    _mount(worktree, worktree, "", _MS_BIND | _MS_REC, "")
    # merged-/usr convenience links + minimal /etc INSIDE the new root, before
    # the root goes read-only.
    for name, target in _USR_SYMLINKS.items():
        link = os.path.join(root, name)
        if os.path.exists(os.path.join(root, target)) and not os.path.lexists(link):
            os.symlink(target, link)
    os.makedirs(os.path.join(root, "etc"), exist_ok=True)
    with open(os.path.join(root, "etc", "hosts"), "w", encoding="utf-8") as fh:
        fh.write("127.0.0.1 localhost\n::1 localhost\n")
    # Public CA trust store (read-only) so the provider can verify the TLS
    # certificate of the pinned egress destination. Bound pre-pivot; host paths
    # vanish afterwards. Public root CAs only — no secrets (§1.2 read-only image).
    for src in _CA_CERT_DIRS:
        if os.path.exists(src):
            _bind_ro(src, root + src)
    os.makedirs(os.path.join(root, "proc"), exist_ok=True)
    os.makedirs(os.path.join(root, "tmp"), exist_ok=True)
    os.makedirs(os.path.join(root, "dev"), exist_ok=True)
    os.makedirs(os.path.join(root, "home"), exist_ok=True)
    os.makedirs(os.path.join(root, "xdg"), exist_ok=True)
    # pivot into the attempt root.
    put_old = os.path.join(root, ".old_root")
    os.makedirs(put_old, exist_ok=True)
    _pivot_root(root, put_old)
    os.chdir("/")
    _umount2("/.old_root", _MNT_DETACH)
    os.rmdir("/.old_root")
    # Fresh /proc under the new pid namespace: sandbox sees ONLY itself.
    _mount("proc", "/proc", "proc", _MS_NOSUID | _MS_NOEXEC | _MS_NODEV, "")
    # tmpfs /tmp sized by the frozen budget.
    tmp_kb = max(int(spec.get("tmp_bytes", 64 * 1024 * 1024)) // 1024, 1024)
    _mount("tmpfs", "/tmp", "tmpfs", _MS_NOSUID | _MS_NODEV, f"size={tmp_kb}k,mode=1777")
    # Private EMPTY HOME + XDG on tmpfs, owned by the sandbox user (§1.5 rule
    # 1): no host user dirs, no daemon HOME, no historical provider state.
    uid = int(spec["uid"])
    gid = int(spec["gid"])
    _mount("tmpfs", "/home", "tmpfs", _MS_NOSUID | _MS_NODEV,
           f"size=16m,mode=700,uid={uid},gid={gid}")
    _mount("tmpfs", "/xdg", "tmpfs", _MS_NOSUID | _MS_NODEV,
           f"size=16m,mode=700,uid={uid},gid={gid}")
    # Root fs read-only LAST (NON-recursive: the worktree/tmp/dev/proc/home
    # submounts keep their own flags). §1.2: sandbox root fs is read-only.
    _mount("", "/", "", _MS_REMOUNT | _MS_BIND | _MS_RDONLY | _MS_NOSUID | _MS_NODEV, "")
    # Minimal /dev: null, zero, urandom — nothing else.
    _mount("tmpfs", "/dev", "tmpfs", _MS_NOSUID | _MS_NOEXEC, "size=64k,mode=755")
    import stat as _stat

    for name, major, minor in (("null", 1, 3), ("zero", 1, 5), ("urandom", 1, 9)):
        os.mknod(f"/dev/{name}", _stat.S_IFCHR | 0o666, os.makedev(major, minor))


def _drop_privileges(spec: dict) -> None:
    os.setgroups([])
    os.setgid(spec["gid"])
    os.setuid(spec["uid"])
    # Prove the drop is irreversible-by-env: no saved-setuid trick available
    # to the exec'd provider (Python execve replaces the image anyway).
    if os.getuid() != spec["uid"] or os.geteuid() != spec["uid"]:
        raise PermissionError("setuid did not stick")


def main() -> int:
    spec_path = sys.argv[1]
    try:
        with open(spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        os.unlink(spec_path)  # the spec never lingers in the sandbox
    except (OSError, ValueError):
        os._exit(127)

    status_fd = int(spec["status_fd"])
    control_fd = int(spec["control_fd"])
    try:
        os.unshare(os.CLONE_NEWNS | os.CLONE_NEWIPC | os.CLONE_NEWUTS | os.CLONE_NEWNET)
        _status(status_fd, "NETNS_READY")
        control = os.read(control_fd, 16)
        if control.strip() != b"GO":
            _fail(status_fd, "missing GO from daemon")
        # New pid namespace: fork so the child becomes init of the pidns.
        os.unshare(os.CLONE_NEWPID)
        pid = os.fork()
        if pid > 0:  # outer child: reap and mirror the provider's exit status
            # fork() returned the inner child's pid AS SEEN FROM THE DAEMON'S
            # namespace — report it: the daemon verifies via the host /proc.
            # (NSpid self-reporting is unreliable in nested containers.)
            _status(status_fd, f"INNER_PID {pid}")
            os.close(status_fd)
            os.close(control_fd)
            _, exit_status = os.waitpid(pid, 0)
            code = os.waitstatus_to_exitcode(exit_status)
            os._exit(code if 0 <= code <= 255 else 1)
        # Inner child: the actual sandbox.
        _setup_mounts(spec)
        _drop_privileges(spec)
        os.chdir("/worktree")
        argv = list(spec["argv"])
        # Fail closed BEFORE reporting ready: no PATH search (absolute only),
        # the binary must exist and be executable for the sandbox user.
        if not argv or not argv[0].startswith("/"):
            _fail(status_fd, "provider argv[0] must be an absolute path")
        if not os.access(argv[0], os.X_OK):
            _fail(status_fd, "provider binary missing or not executable")
        _status(status_fd, f"SANDBOX_READY {os.getpid()}")
        # Wait for the daemon's verification BEFORE exec: the provider never
        # runs unless the daemon has confirmed uid/cgroup/namespaces.
        go_exec = os.read(control_fd, 16)
        if go_exec.strip() != b"EXEC":
            _fail(status_fd, "missing EXEC from daemon")
        os.close(status_fd)
        os.close(control_fd)
        env = dict(spec.get("env", {}))
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        argv = list(spec["argv"])
        os.execve(argv[0], argv, env)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed boundary, pre-exec
        _fail(status_fd, f"{type(exc).__name__}: {exc}"[:220])
    return 127  # unreachable: _fail exits


if __name__ == "__main__":
    sys.exit(main())
