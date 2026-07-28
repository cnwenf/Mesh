"""SSRF guard + pinned-IP fetcher for skill source fetches (skill.md §5.3, README §6.16).

Two classic SSRF defeat modes are closed here by construction:

* **Redirect bypass** — we never hand the URL to a client that auto-follows
  redirects (``urllib.request.urlopen``'s default ``HTTPRedirectHandler``
  follows 3xx *inside a single call*, so a per-hop check wrapped around it
  is dead code). Instead we issue ONE request per hop with ``http.client``
  and read the status ourselves; every 3xx ``Location`` is fed back through
  :func:`resolve_pinned` and re-validated before the next hop is contacted.
* **DNS rebinding (TOCTOU)** — :func:`resolve_pinned` resolves the host
  exactly ONCE, validates EVERY returned address, and returns the *pinned*
  address list. The fetcher then connects to a pinned IP via a custom
  ``http.client`` connection (TLS ``server_hostname`` = original hostname so
  SNI + cert verification stay correct). The hostname is never resolved a
  second time at connect time, so a malicious authoritative DNS that answers
  the validation query with a public record and the fetch query with
  ``127.0.0.1`` / ``169.254.169.254`` cannot redirect the connection.

Policy: RFC1918 / loopback / link-local (incl. cloud metadata
``169.254.169.254``) / IPv6 ULA / link-local / unspecified / reserved are
refused; only genuinely public addresses pass — UNLESS the host is on the
explicit operator allowlist (``settings.skill_source_host_allowlist``), the
documented escape hatch for intranet registries / loopback fixtures.

Every refusal collapses to the neutral 502 ``source_unreachable``; internal
resolution / topology details never leak (skill.md §5.3).
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from mesh.errors import MeshError

# Schemes the fetcher may speak. https everywhere; http ONLY for allowlisted
# hosts (local test fixtures / explicitly trusted intranet registries).
ALLOWED_SCHEMES = ("https", "http")

DEFAULT_DNS_TIMEOUT_SECONDS = 5.0
DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 3
USER_AGENT = "mesh-skill-import/0.1"

# Resolver signature: (hostname, port) -> sorted list of IP strings.
Resolver = Callable[[str, int], list[str]]


class SourceUnreachableError(MeshError):
    """502 — the skill source cannot be fetched (or its address is refused).

    One neutral code for "unreachable" AND "refused by the SSRF policy":
    distinguishing them would leak which internal hosts resolve (§5.3).
    """

    status_code = 502
    code = "source_unreachable"
    message = "skill source is unreachable"


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for routable public addresses (the allowlist bypass aside)."""
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_reserved
        and not ip.is_multicast
        and not ip.is_unspecified
    )


@dataclass(frozen=True)
class PinnedTarget:
    """A validated source hop with its connection addresses pinned.

    ``pinned_ips`` are the ONLY addresses the fetcher may connect to for this
    hop — the hostname is not re-resolved at connect time (rebinding defence).
    """

    scheme: str
    hostname: str
    port: int
    path: str
    pinned_ips: tuple[str, ...]


def _default_resolver(hostname: str, port: int) -> list[str]:
    """Resolve ``hostname`` to IP strings via the system resolver."""
    infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def _check_syntax(uri: str) -> tuple[str, str, int, str]:
    """Return (scheme, hostname, port, path) or raise the neutral 502."""
    if not isinstance(uri, str) or not uri.strip():
        raise SourceUnreachableError("skill source URI is missing")
    parsed = urlparse(uri.strip())
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SourceUnreachableError("skill source is unreachable")
    hostname = parsed.hostname
    if not hostname:
        raise SourceUnreachableError("skill source is unreachable")
    # Credentials in the URL are a smuggling vector — refuse.
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise SourceUnreachableError("skill source is unreachable")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return parsed.scheme, hostname, port, path


def resolve_pinned(
    uri: str,
    *,
    allowlist: frozenset[str] | None = None,
    resolver: Resolver | None = None,
) -> PinnedTarget:
    """Parse + validate + resolve a URI ONCE, returning pinned addresses.

    The single resolve here is the rebind window's closure: callers MUST
    connect to ``pinned_ips`` and never re-resolve ``hostname``.

    Allowlisted hosts are resolved (so the fetcher has a concrete address)
    but skip the public-IP requirement; every other host must resolve to
    exclusively public addresses (a mixed answer is refused).
    """
    allowlist = allowlist or frozenset()
    scheme, hostname, port, path = _check_syntax(uri)
    host_key = hostname.lower()
    allowlisted = host_key in allowlist

    if not allowlisted and scheme != "https":
        # Plain http requires explicit allowlisting (mixed-content / downgrade
        # surface — README §6.16 is https-first for user-controlled URLs).
        raise SourceUnreachableError("skill source is unreachable")

    resolve = resolver or _default_resolver
    try:
        addresses = resolve(hostname, port)
    except OSError as exc:
        raise SourceUnreachableError("skill source is unreachable") from exc
    if not addresses:
        raise SourceUnreachableError("skill source is unreachable")

    if not allowlisted:
        for raw in addresses:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise SourceUnreachableError("skill source is unreachable") from exc
            # EVERY resolved address must be public — a mixed answer is a
            # rebinding hole and is refused wholesale.
            if not _is_public_address(ip):
                raise SourceUnreachableError("skill source is unreachable")

    return PinnedTarget(
        scheme=scheme,
        hostname=hostname,
        port=port,
        path=path,
        pinned_ips=tuple(addresses),
    )


def validate_source_uri(
    uri: str,
    *,
    allowlist: frozenset[str] | None = None,
    resolver: Resolver | None = None,
) -> str:
    """Syntax/scheme/credential + (resolving) address-policy check.

    Kept as a cheap pre-check (used at import start before any fetch). The
    fetcher does NOT trust this result for connecting — it calls
    :func:`resolve_pinned` per hop and pins the addresses itself.
    """
    resolve_pinned(uri, allowlist=allowlist, resolver=resolver)
    return uri.strip() if isinstance(uri, str) else uri


# --- pinned-IP connections (no re-resolve at connect time) ---------------------


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials a pre-validated IP, not the hostname."""

    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials a pinned IP with SNI/cert = hostname."""

    def __init__(
        self, host: str, port: int, pinned_ip: str, context: ssl.SSLContext, timeout: float
    ) -> None:
        super().__init__(host, port, context=context, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            # server_hostname=self.host keeps SNI + check_hostname verification
            # against the ORIGINAL hostname while the TCP peer is the pinned IP.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


# Request seam: (target, timeout) -> (status, headers-getter, body-bytes).
# Injectable so tests can drive redirect / rebinding fixtures deterministically
# against the REAL guarded_fetch loop without real sockets.
RequestFn = Callable[[PinnedTarget, float], tuple[int, Callable[[str], str | None], bytes]]


def _http_request(target: PinnedTarget, timeout: float) -> tuple[int, Callable[[str], str | None], bytes]:
    """Production request seam: one pinned-IP request, no auto-follow."""
    if target.scheme == "https":
        context = ssl.create_default_context()  # check_hostname=True by default
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
            target.hostname, target.port, target.pinned_ips[0], context, timeout
        )
    else:
        conn = _PinnedHTTPConnection(target.hostname, target.port, target.pinned_ips[0], timeout)
    try:
        conn.request("GET", target.path, headers={"Host": target.hostname, "User-Agent": USER_AGENT})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader, body
    except (OSError, http.client.HTTPException) as exc:
        raise SourceUnreachableError("skill source is unreachable") from exc
    finally:
        try:
            conn.close()
        except OSError:
            pass


def fetch_pinned(
    target: PinnedTarget,
    *,
    allowlist: frozenset[str] | None = None,
    max_body_bytes: int | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    resolver: Resolver | None = None,
    request_fn: RequestFn | None = None,
) -> bytes:
    """Fetch ``target``, following redirects MANUALLY with per-hop re-pin.

    Each 3xx ``Location`` is resolved + re-validated through
    :func:`resolve_pinned` before the next hop; a redirect into private /
    non-allowlisted space raises 502 and the target body is never returned.
    """
    do_request = request_fn or _http_request
    current = target
    for _ in range(MAX_REDIRECTS + 1):
        status, getheader, body = do_request(current, timeout)
        if 300 <= status < 400:
            location = getheader("Location") or getheader("location")
            if not location:
                raise SourceUnreachableError("skill source is unreachable")
            # Re-validate + re-pin the redirect target (the rebinding/redirect
            # defence: this hop's address policy is checked anew).
            from urllib.parse import urljoin  # local to avoid cycle at import

            current = resolve_pinned(
                urljoin(_reconstruct(current), location),
                allowlist=allowlist,
                resolver=resolver,
            )
            continue
        if not (200 <= status < 300):
            raise SourceUnreachableError("skill source is unreachable")
        if max_body_bytes is not None and len(body) > max_body_bytes:
            raise SourceUnreachableError("skill source is unreachable")
        return body
    raise SourceUnreachableError("skill source is unreachable")


def _reconstruct(target: PinnedTarget) -> str:
    return f"{target.scheme}://{target.hostname}:{target.port}{target.path}"


def guarded_fetch(
    uri: str,
    allowlist: frozenset[str],
    *,
    max_body_bytes: int | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    resolver: Resolver | None = None,
    request_fn: RequestFn | None = None,
) -> bytes:
    """Validate + fetch ``uri`` with redirect- and rebinding-safe pinning."""
    target = resolve_pinned(uri, allowlist=allowlist, resolver=resolver)
    return fetch_pinned(
        target,
        allowlist=allowlist,
        max_body_bytes=max_body_bytes,
        timeout=timeout,
        resolver=resolver,
        request_fn=request_fn,
    )


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_REDIRECTS",
    "PinnedTarget",
    "SourceUnreachableError",
    "fetch_pinned",
    "guarded_fetch",
    "resolve_pinned",
    "validate_source_uri",
]
