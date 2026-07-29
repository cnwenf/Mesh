"""S-04 egress gateway (runtime-executor.md §3.4).

Per-attempt forward proxy that lives OUTSIDE the sandbox's network namespace.
The sandbox has no default route — this gateway is its only exit, so per-hop
re-validation is automatic: every request (including a redirect follow-up the
client issues itself) traverses the full pipeline again:

    URL allowlist → trusted resolve → filter EVERY answer IP → connect to a
    pinned IP (original host kept for Host/SNI) → pipe bytes.

Design notes:

- 3xx responses are returned verbatim, never followed (§3.4 rule 7); the
  client's next request re-enters the pipeline and is re-validated.
- No cross-request connection pooling: each request resolves and connects
  fresh, which is trivially inside the DNS-TTL pin window (§3.4 rule 6).
- CONNECT tunnels are pinned the same way; arbitrary TCP tunneling is
  impossible because host:port must pass the allowlist first (§3.4 rule 8).
- Refusals carry a fixed status line and NO target detail (§4.2: security
  failures use fixed reason codes, no IP/path echo).
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable, Coroutine
from dataclasses import dataclass

from mesh_runtime.netguard import ForbiddenAddressError, filter_answer_set, normalize_url

Resolver = Callable[[str], Coroutine[object, object, list[str]]]
AddressFilter = Callable[[list[str]], list[str]]

_DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_REDIRECTS = 5
_DEFAULT_SCHEMES = frozenset({"https"})
_DEFAULT_PORTS = frozenset({443})
_DEFAULT_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_MAX_HEADER_LINES = 64
_CHUNK = 65536
_IO_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class NetworkPolicy:
    """Frozen egress rules from the claim's ``config_snapshot.network_policy``.

    Missing or malformed fields fall back to the deny-all baseline — never to
    a relaxation (§2.6).
    """

    allowed_schemes: frozenset[str] = _DEFAULT_SCHEMES
    allowed_hosts: frozenset[str] = frozenset()
    allowed_ports: frozenset[int] = _DEFAULT_PORTS
    allowed_methods: frozenset[str] = _DEFAULT_METHODS
    max_redirects: int = _DEFAULT_MAX_REDIRECTS
    max_upload_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES

    @classmethod
    def from_snapshot(cls, raw: dict) -> NetworkPolicy:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            allowed_schemes=_str_set(raw.get("allowed_schemes")) or _DEFAULT_SCHEMES,
            allowed_hosts=_str_set(raw.get("allowed_hosts")),
            allowed_ports=_int_set(raw.get("allowed_ports")) or _DEFAULT_PORTS,
            allowed_methods=_str_set(raw.get("allowed_methods"), upper=True) or _DEFAULT_METHODS,
            max_redirects=_bounded_int(raw.get("max_redirects"), _DEFAULT_MAX_REDIRECTS, low=0),
            max_upload_bytes=_bounded_int(
                raw.get("max_upload_bytes"), _DEFAULT_MAX_UPLOAD_BYTES, low=0
            ),
        )

    def host_allowed(self, host: str) -> bool:
        return host.lower() in self.allowed_hosts


def _str_set(value: object, *, upper: bool = False) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    out = {v.upper() if upper else v for v in value if isinstance(v, str) and v}
    return frozenset(out)


def _int_set(value: object) -> frozenset[int]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(v for v in value if isinstance(v, int) and not isinstance(v, bool) and v > 0)


def _bounded_int(value: object, default: int, *, low: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= low:
        return value
    return default


async def _default_resolver(host: str) -> list[str]:
    """Trusted resolver: the daemon's configured system resolver, returning
    the full A/AAAA answer set (CNAME chains collapse to their addresses)."""
    infos = await asyncio.to_thread(
        socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP
    )
    return sorted({info[4][0] for info in infos})


class EgressGateway:
    def __init__(
        self,
        policy: NetworkPolicy,
        *,
        resolver: Resolver | None = None,
        address_filter: AddressFilter = filter_answer_set,
        listen_host: str = "127.0.0.1",
        timeout: float = _IO_TIMEOUT_SECONDS,
    ) -> None:
        self._policy = policy
        self._resolver = resolver or _default_resolver
        self._filter = address_filter
        self._listen_host = listen_host
        self._timeout = timeout
        self._server: asyncio.Server | None = None
        self._port = 0
        self.stats: dict[str, int] = {"allowed": 0, "denied": 0}

    @property
    def port(self) -> int:
        return self._port

    @property
    def proxy_url(self) -> str:
        return f"http://{self._listen_host}:{self._port}"

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._on_connection, self._listen_host, 0
        )
        self._port = self._server.sockets[0].getsockname()[1]
        return self._port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # -- connection handling -------------------------------------------------

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
            parts = request_line.decode("latin-1").strip().split()
            headers = await self._read_headers(reader)
            if len(parts) != 3:
                self._deny(writer, 400)
                return
            method, target, _version = parts
            method = method.upper()
            if method == "CONNECT":
                await self._handle_connect(reader, writer, target)
            else:
                await self._handle_plain(reader, writer, method, target, headers)
        except (OSError, TimeoutError, UnicodeDecodeError, ValueError):
            self._deny(writer, 403)
        finally:
            await self._close(writer)

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        for _ in range(_MAX_HEADER_LINES + 1):
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                return headers
            name, _, value = line.decode("latin-1").partition(":")
            headers.append((name.strip(), value.strip()))
        raise ValueError("too many header lines")

    @staticmethod
    def _header_value(headers: list[tuple[str, str]], name: str) -> str | None:
        for key, value in headers:
            if key.lower() == name:
                return value
        return None

    # -- plain HTTP (absolute-form) ------------------------------------------

    async def _handle_plain(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
    ) -> None:
        try:
            url = normalize_url(target)
        except ForbiddenAddressError:
            self._deny(writer, 403)
            return
        if url.scheme not in ("http", "https"):  # proxy sees the real scheme
            self._deny(writer, 403)
            return
        if not self._authorized(method, url.scheme, url.host, url.port):
            self._deny(writer, 403)
            return
        ip = await self._resolve_pinned(url.host)
        if ip is None:
            self._deny(writer, 403)
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(ip, url.port), timeout=self._timeout
            )
        except OSError:
            self._deny(writer, 502)
            return
        try:
            path = url.path or "/"
            if url.query:
                path = f"{path}?{url.query}"
            forwarded = [f"{method} {path} HTTP/1.1"]
            has_host = False
            for name, value in headers:
                if name.lower() == "host":
                    has_host = True
                forwarded.append(f"{name}: {value}")
            if not has_host:
                forwarded.append(f"Host: {url.host}")
            forwarded.append("Connection: close")
            upstream_writer.write(("\r\n".join(forwarded) + "\r\n\r\n").encode("latin-1"))
            await upstream_writer.drain()
            length = int(self._header_value(headers, "content-length") or "0")
            if length > 0:
                await self._forward_request_body(reader, upstream_writer, length)
            await self._stream_response(upstream_reader, writer)
            self.stats["allowed"] += 1
        except _UploadTooLarge:
            self._deny(writer, 403)
        except (OSError, TimeoutError):
            pass  # client/upstream went away mid-transfer; nothing to report
        finally:
            await self._close(upstream_writer)

    async def _forward_request_body(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, length: int
    ) -> None:
        if length > self._policy.max_upload_bytes:
            raise _UploadTooLarge()
        remaining = length
        while remaining > 0:
            chunk = await reader.read(min(_CHUNK, remaining))
            if not chunk:
                return
            writer.write(chunk)
            await writer.drain()
            remaining -= len(chunk)

    @staticmethod
    async def _stream_response(
        upstream_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        while True:
            chunk = await upstream_reader.read(_CHUNK)
            if not chunk:
                return
            client_writer.write(chunk)
            await client_writer.drain()

    # -- CONNECT tunnel --------------------------------------------------------

    async def _handle_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str
    ) -> None:
        host, sep, port_raw = target.rpartition(":")
        if not sep or not host:
            self._deny(writer, 400)
            return
        try:
            port = int(port_raw)
        except ValueError:
            self._deny(writer, 400)
            return
        host = host.strip("[]").lower()
        # CONNECT has no scheme/method to gate; host+port allowlist is the gate.
        if not (
            self._policy.host_allowed(host) and port in self._policy.allowed_ports
        ):
            self._deny(writer, 403)
            return
        ip = await self._resolve_pinned(host)
        if ip is None:
            self._deny(writer, 403)
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=self._timeout
            )
        except OSError:
            self._deny(writer, 502)
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        self.stats["allowed"] += 1
        await self._pipe_both(reader, writer, upstream_reader, upstream_writer)

    async def _pipe_both(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            while True:
                chunk = await src.read(_CHUNK)
                if not chunk:
                    return
                dst.write(chunk)
                await dst.drain()

        tasks = [
            asyncio.create_task(pump(client_reader, upstream_writer)),
            asyncio.create_task(pump(upstream_reader, client_writer)),
        ]
        try:
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)  # drain cancellations
        finally:
            await self._close(upstream_writer)

    # -- shared gate -----------------------------------------------------------

    def _authorized(self, method: str, scheme: str, host: str, port: int) -> bool:
        policy = self._policy
        return (
            scheme in policy.allowed_schemes
            and policy.host_allowed(host)
            and port in policy.allowed_ports
            and method in policy.allowed_methods
        )

    async def _resolve_pinned(self, host: str) -> str | None:
        """Trusted resolve → filter EVERY answer → return the pinned IP to
        connect to. Any failure refuses the whole request (fail-closed)."""
        try:
            answers = await asyncio.wait_for(self._resolver(host), timeout=self._timeout)
            verified = self._filter(list(answers))
        except (ForbiddenAddressError, TimeoutError, OSError):
            self.stats["denied"] += 1
            return None
        if not verified:
            self.stats["denied"] += 1
            return None
        return verified[0]

    def _deny(self, writer: asyncio.StreamWriter, status: int) -> None:
        self.stats["denied"] += 1
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(
            status, "Forbidden"
        )
        try:
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n"
                f"Connection: close\r\n\r\n".encode("latin-1")
            )
        except OSError:
            pass  # peer already gone — refusal stands regardless

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass


class _UploadTooLarge(Exception):
    """Request body exceeded the frozen upload budget."""
