"""S-04 egress gateway — real loopback sockets, injected resolver/filter.

The pure IP classification lives in netguard (tested exhaustively there);
here the gateway is wired with a recording filter that admits loopback so the
proxy pipeline itself — allowlist, resolution, pinning, CONNECT, redirect
non-following — is exercised over real TCP. Cases marked ``real_filter`` keep
the production filter to prove end-to-end refusal without any connection.
"""

import asyncio

import pytest

from mesh_runtime.egress import EgressGateway, NetworkPolicy
from mesh_runtime.netguard import filter_answer_set

HOST = "public.example"


@pytest.fixture
async def upstream():
    """Minimal HTTP origin on loopback recording what it receives."""
    received = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        request_line = (await reader.readline()).decode()
        headers = {}
        while True:
            line = (await reader.readline()).decode()
            if line in ("\r\n", "\n", ""):
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        body = b""
        length = int(headers.get("content-length", "0"))
        if length:
            body = await reader.readexactly(length)
        received.append({"request_line": request_line.strip(), "headers": headers, "body": body})
        if headers.get("x-test-redirect"):
            payload = b"moved"
            writer.write(
                b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest\r\n"
                b"Content-Length: 5\r\nConnection: close\r\n\r\n" + payload
            )
        else:
            payload = b"hello"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\n" + payload
            )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield {"server": server, "port": port, "received": received}
    server.close()
    await server.wait_closed()


def policy_for(port: int, *, methods=("GET",), hosts=(HOST,)) -> NetworkPolicy:
    return NetworkPolicy.from_snapshot(
        {
            "allowed_schemes": ["http"],
            "allowed_hosts": list(hosts),
            "allowed_ports": [port],
            "allowed_methods": list(methods),
            "max_redirects": 5,
            "max_upload_bytes": 1024,
        }
    )


def loopback_filter(ips: list[str]) -> list[str]:
    """Admit loopback (test stand-in for public); keep the all-or-nothing rule."""
    assert ips, "gateway must resolve before filtering"
    return list(ips)


async def resolver_to_loopback(host: str) -> list[str]:
    resolver_to_loopback.calls.append(host)
    return ["127.0.0.1"]


resolver_to_loopback.calls = []


@pytest.fixture(autouse=True)
def _resolver_calls():
    resolver_to_loopback.calls = []
    yield


@pytest.fixture
async def gateway(upstream):
    gw = EgressGateway(
        policy_for(upstream["port"]),
        resolver=resolver_to_loopback,
        address_filter=loopback_filter,
    )
    await gw.start()
    yield gw
    await gw.stop()


async def proxy_get(port: int, target: str, *, body: bytes = b"", extra_headers: str = "") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = f"GET {target} HTTP/1.1\r\nHost: public.example\r\n{extra_headers}"
    if body:
        request += f"Content-Length: {len(body)}\r\n"
    request += "Connection: close\r\n\r\n"
    writer.write(request.encode() + body)
    await writer.drain()
    response = await reader.read(65536)
    writer.close()
    return response


class TestPolicyModel:
    def test_defaults_are_deny_all(self):
        policy = NetworkPolicy.from_snapshot({})
        assert policy.allowed_hosts == frozenset()
        assert policy.allowed_schemes == frozenset({"https"})
        assert policy.allowed_ports == frozenset({443})
        assert policy.max_redirects == 5

    def test_snapshot_values_override_defaults(self):
        policy = policy_for(8443, methods=("GET", "POST"))
        assert policy.allowed_ports == frozenset({8443})
        assert policy.allowed_methods == frozenset({"GET", "POST"})
        assert policy.max_upload_bytes == 1024

    def test_non_allowlisted_fields_ignored(self):
        policy = NetworkPolicy.from_snapshot({"evil_field": True, "allowed_hosts": ["a.example"]})
        assert policy.allowed_hosts == frozenset({"a.example"})


class TestPlainHttpProxy:
    async def test_allows_listed_get_and_pins_origin_form(self, gateway, upstream):
        response = await proxy_get(gateway.port, f"http://{HOST}:{upstream['port']}/path?q=1")
        assert b"200 OK" in response
        assert response.endswith(b"hello")
        hit = upstream["received"][0]
        assert hit["request_line"] == "GET /path?q=1 HTTP/1.1"  # origin-form, not absolute
        assert hit["headers"]["host"] == HOST  # original host preserved for the origin
        assert resolver_to_loopback.calls == [HOST]  # trusted resolver was used

    async def test_rejects_unlisted_host_without_connecting(self, gateway, upstream):
        response = await proxy_get(gateway.port, "http://evil.example/")
        assert b"403" in response
        assert upstream["received"] == []
        assert gateway.stats["denied"] >= 1

    async def test_rejects_unlisted_scheme(self, gateway, upstream):
        response = await proxy_get(gateway.port, f"ftp://{HOST}/file")
        assert b"403" in response
        assert upstream["received"] == []

    async def test_rejects_unlisted_method(self, gateway, upstream):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(
            (
                f"POST http://{HOST}:{upstream['port']}/x HTTP/1.1\r\nHost: {HOST}\r\n"
                "Content-Length: 2\r\nConnection: close\r\n\r\n"
            ).encode()
            + b"hi"
        )
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert b"403" in response
        assert upstream["received"] == []

    async def test_upload_size_limit_enforced(self, upstream):
        policy = policy_for(upstream["port"], methods=("POST",))
        policy = NetworkPolicy(
            allowed_schemes=policy.allowed_schemes,
            allowed_hosts=policy.allowed_hosts,
            allowed_ports=policy.allowed_ports,
            allowed_methods=policy.allowed_methods,
            max_redirects=policy.max_redirects,
            max_upload_bytes=8,
        )
        gw = EgressGateway(policy, resolver=resolver_to_loopback, address_filter=loopback_filter)
        await gw.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", gw.port)
            writer.write(
                (
                    f"POST http://{HOST}:{upstream['port']}/upload HTTP/1.1\r\nHost: {HOST}\r\n"
                    "Content-Length: 100\r\nConnection: close\r\n\r\n"
                ).encode()
                + b"x" * 100
            )
            await writer.drain()
            response = await reader.read(65536)
            writer.close()
            assert b"403" in response or response == b""  # denied or dropped mid-body
            assert gw.stats["denied"] >= 1
            bodies = [r["body"] for r in upstream["received"]]
            assert all(len(body) < 100 for body in bodies)  # never fully uploaded
        finally:
            await gw.stop()

    async def test_redirect_is_not_followed(self, gateway, upstream):
        target = f"http://{HOST}:{upstream['port']}/"
        response = await proxy_get(gateway.port, target, extra_headers="X-Test-Redirect: 1\r\n")
        assert b"302 Found" in response
        assert b"169.254.169.254" in response  # returned verbatim to the client
        # The client's follow-up hop is a NEW request and gets re-validated:
        response2 = await proxy_get(gateway.port, "http://169.254.169.254/latest")
        assert b"403" in response2
        assert len(upstream["received"]) == 1  # only the first hop reached the origin


class TestRedirectHopCap:
    """§3.4 rule 7 / §2.1: the frozen max_redirects is a real hop cap — once
    the gateway has relayed max_redirects 3xx responses for an attempt,
    further redirects are REFUSED, not relayed."""

    @pytest.fixture
    async def redirect_origin(self):
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            writer.write(
                b"HTTP/1.1 302 Found\r\nLocation: http://public.example/next\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        yield port
        server.close()
        await server.wait_closed()

    async def test_redirect_chain_capped_at_frozen_max(self, redirect_origin):
        policy = NetworkPolicy.from_snapshot(
            {
                "allowed_schemes": ["http"],
                "allowed_hosts": [HOST],
                "allowed_ports": [redirect_origin],
                "allowed_methods": ["GET"],
                "max_redirects": 3,
            }
        )
        gw = EgressGateway(
            policy, resolver=resolver_to_loopback, address_filter=loopback_filter
        )
        await gw.start()
        try:
            statuses = []
            for _ in range(6):
                resp = await proxy_get(gw.port, f"http://{HOST}:{redirect_origin}/hop")
                statuses.append(resp.split(b" ", 2)[1])
            # exactly max_redirects 3xx relayed; everything after is refused
            assert statuses[:3] == [b"302"] * 3
            assert statuses[3:] == [b"403"] * 3
            assert gw.stats["redirects_capped"] == 3
        finally:
            await gw.stop()

    async def test_non_redirect_responses_do_not_consume_the_cap(self, upstream):
        gw = EgressGateway(
            policy_for(upstream["port"]),
            resolver=resolver_to_loopback,
            address_filter=loopback_filter,
        )
        await gw.start()
        try:
            for _ in range(10):  # 200 OK responses, far beyond max_redirects=5
                resp = await proxy_get(gw.port, f"http://{HOST}:{upstream['port']}/ok")
                assert resp.startswith(b"HTTP/1.1 200")
            assert gw.stats["redirects_capped"] == 0
        finally:
            await gw.stop()


class TestVethBind:
    """§3.4: the gateway binds the per-attempt veth host IP — never a
    wildcard address a host-side peer could borrow."""

    async def test_binds_specified_host_ip_not_wildcard(self, upstream):
        gw = EgressGateway(
            policy_for(upstream["port"]),
            resolver=resolver_to_loopback,
            address_filter=loopback_filter,
            listen_host="169.254.254.253",  # not on-link yet — needs IP_FREEBIND
        )
        port = await gw.start()
        try:
            bound = gw._server.sockets[0].getsockname()
            assert bound[0] == "169.254.254.253"  # exact-IP bind, not 0.0.0.0
            assert bound[1] == port > 0
            assert gw.proxy_url == f"http://169.254.254.253:{port}"
        finally:
            await gw.stop()


class TestConnectTunnel:
    async def test_connect_tunnels_when_host_port_allowed(self, upstream):
        policy = policy_for(upstream["port"])
        gw = EgressGateway(policy, resolver=resolver_to_loopback, address_filter=loopback_filter)
        await gw.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", gw.port)
            writer.write(f"CONNECT {HOST}:{upstream['port']} HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
            await writer.drain()
            status_line = await reader.readline()
            assert b"200" in status_line
            await reader.readline()  # blank line after the status
            # The tunnel now pipes raw bytes to the origin: send a bare HTTP request.
            writer.write(b"GET /tunneled HTTP/1.1\r\nHost: public.example\r\nConnection: close\r\n\r\n")
            await writer.drain()
            response = await reader.read(65536)
            assert b"200 OK" in response
            writer.close()
            assert upstream["received"][-1]["request_line"] == "GET /tunneled HTTP/1.1"
        finally:
            await gw.stop()

    async def test_connect_rejects_unlisted_port(self, gateway):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(f"CONNECT {HOST}:22 HTTP/1.1\r\nHost: {HOST}:22\r\n\r\n".encode())
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert b"403" in response

    async def test_connect_rejects_unlisted_host(self, gateway):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example\r\n\r\n")
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert b"403" in response


class TestResolutionFiltering:
    async def test_mixed_answer_set_refused_with_real_filter(self, upstream):
        # Production filter: one loopback IP in the answer set refuses the
        # whole request — and no connection is ever attempted (§3.4 rule 4).
        async def mixed_resolver(host: str) -> list[str]:
            return ["93.184.216.34", "127.0.0.1"]

        gw = EgressGateway(
            policy_for(upstream["port"]), resolver=mixed_resolver, address_filter=filter_answer_set
        )
        await gw.start()
        try:
            response = await proxy_get(gw.port, f"http://{HOST}/")
            assert b"403" in response
            assert upstream["received"] == []
            assert gw.stats["denied"] >= 1
        finally:
            await gw.stop()

    async def test_ipv4_mapped_loopback_refused_with_real_filter(self, upstream):
        async def mapped_resolver(host: str) -> list[str]:
            return ["::ffff:127.0.0.1"]

        gw = EgressGateway(
            policy_for(upstream["port"]), resolver=mapped_resolver, address_filter=filter_answer_set
        )
        await gw.start()
        try:
            response = await proxy_get(gw.port, f"http://{HOST}/")
            assert b"403" in response
        finally:
            await gw.stop()

    async def test_empty_answer_set_refused(self, gateway):
        async def empty_resolver(host: str) -> list[str]:
            return []

        gw = EgressGateway(
            policy_for(1), resolver=empty_resolver, address_filter=filter_answer_set
        )
        await gw.start()
        try:
            response = await proxy_get(gw.port, f"http://{HOST}/")
            assert b"403" in response
        finally:
            await gw.stop()

    async def test_default_empty_policy_refuses_everything(self):
        gw = EgressGateway(
            NetworkPolicy.from_snapshot({}),
            resolver=resolver_to_loopback,
            address_filter=loopback_filter,
        )
        await gw.start()
        try:
            response = await proxy_get(gw.port, "https://anything.example/")
            assert b"403" in response
        finally:
            await gw.stop()


class TestGatewayLifecycle:
    async def test_start_reports_port_and_stop_closes(self, gateway):
        assert gateway.port > 0
        assert gateway.proxy_url.startswith("http://127.0.0.1:")
        await gateway.stop()
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", gateway.port)

    async def test_garbage_request_is_denied_not_crashing(self, gateway, upstream):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(b"\x00\x01garbage\r\n\r\n")
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert response == b"" or b"400" in response or b"403" in response
        # Gateway still serves a valid request afterwards.
        response = await proxy_get(gateway.port, f"http://{HOST}:{upstream['port']}/")
        assert b"200 OK" in response


class TestEdgePaths:
    async def test_unreachable_upstream_returns_502(self, upstream):
        # Port 1 on loopback: admitted by the allowlist + injected filter, but
        # nothing listens — the gateway must answer 502, never hang or drop.
        policy = NetworkPolicy.from_snapshot(
            {"allowed_schemes": ["http"], "allowed_hosts": [HOST], "allowed_ports": [1]}
        )
        gw = EgressGateway(policy, resolver=resolver_to_loopback, address_filter=loopback_filter)
        await gw.start()
        try:
            response = await proxy_get(gw.port, f"http://{HOST}:1/")
            assert b"502" in response
        finally:
            await gw.stop()

    async def test_connect_without_port_is_400(self, gateway):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(f"CONNECT {HOST} HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert b"400" in response

    async def test_connect_with_non_numeric_port_is_400(self, gateway):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(f"CONNECT {HOST}:https HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert b"400" in response

    async def test_resolver_failure_refused(self, upstream):
        async def failing_resolver(host: str) -> list[str]:
            raise OSError("dns down")

        gw = EgressGateway(
            policy_for(upstream["port"]), resolver=failing_resolver, address_filter=loopback_filter
        )
        await gw.start()
        try:
            response = await proxy_get(gw.port, f"http://{HOST}:{upstream['port']}/")
            assert b"403" in response
            assert gw.stats["denied"] >= 1
        finally:
            await gw.stop()

    async def test_relative_form_request_is_403(self, gateway):
        # A well-behaved proxy client uses absolute-form; origin-form has no
        # host to gate on — refuse.
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(b"GET /relative HTTP/1.1\r\nHost: public.example\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await reader.read(65536)
        writer.close()
        assert b"403" in response or b"400" in response

    async def test_policy_from_non_dict_snapshot_is_deny_all(self):
        policy = NetworkPolicy.from_snapshot("garbage")  # type: ignore[arg-type]
        assert policy.allowed_hosts == frozenset()
        gw = EgressGateway(policy, resolver=resolver_to_loopback, address_filter=loopback_filter)
        await gw.start()
        try:
            response = await proxy_get(gw.port, "https://anything.example/")
            assert b"403" in response
        finally:
            await gw.stop()


class TestTransferRobustness:
    async def test_client_disconnect_mid_response_is_tolerated(self, upstream):
        # Client hangs up while the response streams — the gateway must not
        # crash and must keep serving the next request.
        gw = EgressGateway(
            policy_for(upstream["port"]), resolver=resolver_to_loopback, address_filter=loopback_filter
        )
        await gw.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", gw.port)
            writer.write(
                f"GET http://{HOST}:{upstream['port']}/ HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()  # abandon before reading the response
            await asyncio.sleep(0.2)
            response = await proxy_get(gw.port, f"http://{HOST}:{upstream['port']}/")
            assert b"200 OK" in response  # gateway still healthy
        finally:
            await gw.stop()
