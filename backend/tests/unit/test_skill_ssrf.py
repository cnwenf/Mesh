"""SSRF guard unit tests (skill.md §5.3, README §6.16).

The guard decides which server-side source fetches are allowed: public
address space only, with an explicit host allowlist escape hatch. Every
refusal reason collapses into one neutral 502 ``source_unreachable`` so
internal topology never leaks.
"""

from __future__ import annotations

import pytest

from mesh.skill.ssrf import (
    PinnedTarget,
    SourceUnreachableError,
    guarded_fetch,
    resolve_pinned,
    validate_source_uri,
)


def _resolver(*addresses: str):
    def resolve(_hostname: str, _port: int = 0) -> list[str]:
        return list(addresses)

    return resolve


class TestAllowed:
    def test_public_https_uri_passes(self) -> None:
        uri = validate_source_uri(
            "https://skills.example.com/manifest.json",
            resolver=_resolver("93.184.216.34"),
        )
        assert uri == "https://skills.example.com/manifest.json"

    def test_public_ipv6_passes(self) -> None:
        uri = validate_source_uri(
            "https://skills.example.com/m.json",
            resolver=_resolver("2606:2800:220:1::248"),
        )
        assert uri.startswith("https://")

    def test_allowlisted_host_bypasses_public_check(self) -> None:
        # Loopback is normally refused; the explicit allowlist (README §6.16)
        # is the documented trust escape hatch used by tests / intranets.
        uri = validate_source_uri(
            "http://127.0.0.1:8000/manifest.json",
            allowlist=frozenset({"127.0.0.1"}),
        )
        assert uri == "http://127.0.0.1:8000/manifest.json"

    def test_allowlist_is_case_insensitive(self) -> None:
        uri = validate_source_uri(
            "https://Registry.Corp/skill.json",
            allowlist=frozenset({"registry.corp"}),
            resolver=_resolver("10.0.0.5"),  # private — only allowed via list
        )
        assert uri.endswith("skill.json")


class TestRefused:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # loopback
            "10.1.2.3",  # RFC1918
            "172.16.0.9",  # RFC1918
            "192.168.1.1",  # RFC1918
            "169.254.169.254",  # link-local / cloud metadata
            "100.64.0.1",  # carrier-grade NAT (not global)
            "::1",  # IPv6 loopback
            "fc00::1",  # IPv6 ULA
            "fe80::1",  # IPv6 link-local
            "0.0.0.0",  # unspecified
        ],
    )
    def test_non_public_addresses_refused(self, address: str) -> None:
        with pytest.raises(SourceUnreachableError) as exc_info:
            validate_source_uri(
                "https://evil.example.com/manifest.json", resolver=_resolver(address)
            )
        assert exc_info.value.status_code == 502
        assert exc_info.value.code == "source_unreachable"

    def test_mixed_public_and_private_answer_refused(self) -> None:
        # DNS-rebinding shape: one public + one private record. EVERY address
        # must be public or the fetch is refused.
        with pytest.raises(SourceUnreachableError):
            validate_source_uri(
                "https://rebind.example.com/m.json",
                resolver=_resolver("93.184.216.34", "127.0.0.1"),
            )

    def test_empty_answer_refused(self) -> None:
        with pytest.raises(SourceUnreachableError):
            validate_source_uri("https://nope.example.com/m.json", resolver=_resolver())

    def test_dns_failure_refused(self) -> None:
        import socket

        def resolve(_hostname: str, _port: int = 0) -> list[str]:
            raise socket.gaierror("name resolution failed")

        with pytest.raises(SourceUnreachableError):
            validate_source_uri("https://unresolvable.example.com/m.json", resolver=resolve)

    @pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "javascript", "data"])
    def test_bad_scheme_refused(self, scheme: str) -> None:
        with pytest.raises(SourceUnreachableError):
            validate_source_uri(f"{scheme}://example.com/manifest.json")

    def test_plain_http_without_allowlist_refused(self) -> None:
        with pytest.raises(SourceUnreachableError):
            validate_source_uri(
                "http://skills.example.com/m.json", resolver=_resolver("93.184.216.34")
            )

    def test_credentials_in_url_refused(self) -> None:
        with pytest.raises(SourceUnreachableError):
            validate_source_uri(
                "https://token@skills.example.com/m.json",
                resolver=_resolver("93.184.216.34"),
            )

    def test_missing_host_refused(self) -> None:
        with pytest.raises(SourceUnreachableError):
            validate_source_uri("https:///manifest.json")

    def test_empty_uri_refused(self) -> None:
        with pytest.raises(SourceUnreachableError):
            validate_source_uri("   ")

    def test_error_message_does_not_leak_address(self) -> None:
        with pytest.raises(SourceUnreachableError) as exc_info:
            validate_source_uri(
                "https://metadata.example.com/latest",
                resolver=_resolver("169.254.169.254"),
            )
        # Neutral message — no resolved address, no policy detail.
        assert "169.254" not in exc_info.value.message
        assert "unreachable" in exc_info.value.message


# --- CRITICAL-1 (redirect bypass) + CRITICAL-2 (DNS rebinding) regressions ------
#
# These exercise the REAL guarded_fetch / fetch_pinned loop with an injected
# request seam so the redirect-follow and address-pinning logic is proven
# without real sockets. The historical implementation used urllib.request.urlopen
# which (a) auto-followed 3xx inside one call (per-hop check = dead code) and
# (b) re-resolved the hostname at connect time (rebinding window). Both are
# asserted closed below.


def _recording_request(responses_by_host: dict[str, tuple[int, dict[str, str], bytes]]):
    """Request seam keyed by hostname; records every pinned IP it is handed."""

    calls: list[tuple[str, str]] = []

    def request_fn(target: PinnedTarget, _timeout: float):
        calls.append((target.hostname, target.pinned_ips[0]))
        status, headers, body = responses_by_host[target.hostname]
        return status, lambda h: headers.get(h), body

    return request_fn, calls


class TestRedirectBypassCritical1:
    def test_redirect_into_loopback_refused_and_secret_not_fetched(self) -> None:
        # Hop1 allowlisted loopback answers 302 → hop2 = 127.0.0.2 (loopback, NOT
        # allowlisted). The redirect target MUST be re-validated and refused, and
        # the secret body on hop2 must NEVER be returned / requested.
        secret = b"INTERNAL_SECRET_REACHED"
        request_fn, calls = _recording_request(
            {
                "127.0.0.1": (302, {"Location": "http://127.0.0.2/secret"}, b""),
                "127.0.0.2": (200, {}, secret),
            }
        )
        allowlist = frozenset({"127.0.0.1"})
        with pytest.raises(SourceUnreachableError):
            guarded_fetch(
                "http://127.0.0.1/manifest.json",
                allowlist,
                resolver=_resolver_map({"127.0.0.1": ["127.0.0.1"], "127.0.0.2": ["127.0.0.2"]}),
                request_fn=request_fn,
            )
        # Hop2's secret server was never contacted (redirect re-validation
        # blocked it before any connect).
        assert [host for host, _ip in calls] == ["127.0.0.1"]

    def test_redirect_chain_within_allowlist_followed(self) -> None:
        request_fn, calls = _recording_request(
            {
                "a.public": (301, {"Location": "http://b.public/x"}, b""),
                "b.public": (200, {}, b"OK_BODY"),
            }
        )
        resolver = _resolver_map({"a.public": ["1.1.1.1"], "b.public": ["2.2.2.2"]})
        body = guarded_fetch(
            "http://a.public/m.json",
            frozenset({"a.public", "b.public"}),
            resolver=resolver,
            request_fn=request_fn,
        )
        assert body == b"OK_BODY"
        assert [host for host, _ip in calls] == ["a.public", "b.public"]

    def test_redirect_to_metadata_endpoint_refused(self) -> None:
        request_fn, calls = _recording_request(
            {"public.example": (302, {"Location": "http://169.254.169.254/latest"}, b"")}
        )
        with pytest.raises(SourceUnreachableError):
            guarded_fetch(
                "https://public.example/m.json",
                frozenset({"public.example"}),
                resolver=_resolver_map(
                    {"public.example": ["93.184.216.34"], "169.254.169.254": ["169.254.169.254"]}
                ),
                request_fn=request_fn,
            )
        assert [host for host, _ip in calls] == ["public.example"]


class TestDnsRebindingCritical2:
    def test_pinned_ip_used_not_rebound_answer(self) -> None:
        # Resolver yields a PUBLIC ip on the validation call and a private ip on
        # any subsequent call (the rebinding attack). With pinning, the fetcher
        # must connect to the PUBLIC ip and must NOT perform a second resolve —
        # so the private "rebound" answer is never consulted.
        public_ip = "93.184.216.34"
        private_ip = "127.0.0.1"
        answers = iter([[public_ip], [private_ip]])
        resolve_calls: list[str] = []

        def rebind_resolver(_hostname: str, _port: int = 0) -> list[str]:
            resolve_calls.append(_hostname)
            return next(answers)

        rebind_host = "rebind.example"
        request_fn, calls = _recording_request({rebind_host: (200, {}, b"BODY")})

        body = guarded_fetch(
            "https://rebind.example/m.json",
            frozenset(),  # NOT allowlisted → public-ip check enforced on the pin
            resolver=rebind_resolver,
            request_fn=request_fn,
        )
        assert body == b"BODY"
        # Exactly ONE resolve (the pin); the rebound answer was never drawn.
        assert resolve_calls == [rebind_host]
        # The connection used the pinned PUBLIC ip, never the private rebound ip.
        assert calls == [(rebind_host, public_ip)]
        assert all(ip != private_ip for _host, ip in calls)

    def test_resolve_pinned_returns_pinned_addresses(self) -> None:
        target = resolve_pinned(
            "https://skills.example.com/m.json", resolver=_resolver("93.184.216.34")
        )
        assert target.pinned_ips == ("93.184.216.34",)
        assert target.hostname == "skills.example.com"
        assert target.port == 443


def _resolver_map(table: dict[str, list[str]]):
    def resolve(hostname: str, _port: int = 0) -> list[str]:
        return list(table[hostname])

    return resolve
