"""netguard — URL normalization + IP classification + SSRF guard (S-04 §3.4,
checkout §3.2). Pure functions, no I/O: the egress gateway and the checkout
helper compose them with their own resolvers."""

import pytest

from mesh_runtime.errors import DaemonError
from mesh_runtime.netguard import (
    ForbiddenAddressError,
    assert_url_host_public,
    classify_ip,
    filter_answer_set,
    normalize_url,
)


class TestNormalizeUrl:
    def test_accepts_https_with_default_port(self):
        url = normalize_url("https://api.example.com/v1/x?q=1")
        assert url.scheme == "https"
        assert url.host == "api.example.com"
        assert url.port == 443
        assert url.path == "/v1/x"
        assert url.query == "q=1"

    def test_accepts_explicit_port(self):
        assert normalize_url("https://h.example:8443/").port == 8443

    def test_accepts_ipv6_literal_canonical(self):
        url = normalize_url("https://[2606:2800:220:1:248:1893:25c8:1946]/")
        assert url.host == "2606:2800:220:1:248:1893:25c8:1946"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "https://user:pass@example.com/",          # userinfo forbidden
            "https://evil.com\x00.example/",            # control char
            "https://evil .com/",                       # whitespace
            "http://evil\\@good.example/",              # backslash confusion
            "ftp://example.com/",                       # scheme not allowed
            "https:///path-only",                       # no host
            "http://2130706433/",                       # decimal-encoded 127.0.0.1
            "http://0x7f000001/",                       # hex-encoded 127.0.0.1
            "http://0177.0.0.1/",                       # octal-encoded 127.0.0.1
            "https://[::0:1]/",                         # non-canonical IPv6 literal
        ],
    )
    def test_rejects_hostile_shapes(self, raw):
        with pytest.raises(ForbiddenAddressError):
            normalize_url(raw)

    def test_error_is_daemon_error(self):
        with pytest.raises(DaemonError):
            normalize_url("ftp://x.example/")


class TestClassifyIp:
    @pytest.mark.parametrize(
        "ip",
        ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "8.8.8.8"],
    )
    def test_allows_public_addresses(self, ip):
        verdict = classify_ip(ip)
        assert verdict.allowed is True
        assert verdict.reason is None

    @pytest.mark.parametrize(
        ("ip", "reason"),
        [
            ("127.0.0.1", "loopback"),
            ("::1", "loopback"),
            ("10.0.0.1", "private"),
            ("172.16.0.1", "private"),
            ("192.168.1.1", "private"),
            ("169.254.1.1", "link_local"),
            ("169.254.169.254", "cloud_metadata"),   # AWS/Azure/GCP metadata
            ("fd00:ec2::254", "cloud_metadata"),      # AWS EC2 IPv6 metadata
            ("100.100.100.200", "cloud_metadata"),    # Alibaba metadata
            ("224.0.0.1", "multicast"),
            ("0.0.0.0", "unspecified"),
            ("::", "unspecified"),
            ("240.0.0.1", "reserved"),
            ("192.0.2.1", "documentation"),           # TEST-NET-1
            ("198.51.100.1", "documentation"),        # TEST-NET-2
            ("203.0.113.1", "documentation"),         # TEST-NET-3
            ("2001:db8::1", "documentation"),
            ("198.18.0.1", "benchmarking"),
            ("::ffff:127.0.0.1", "loopback"),         # IPv4-mapped loopback
            ("::ffff:10.0.0.1", "private"),           # IPv4-mapped private
            ("::ffff:169.254.169.254", "cloud_metadata"),
            ("999.1.1.1", "unparseable"),
        ],
    )
    def test_rejects_forbidden_addresses(self, ip, reason):
        verdict = classify_ip(ip)
        assert verdict.allowed is False
        assert verdict.reason == reason


class TestFilterAnswerSet:
    def test_all_public_passes_through(self):
        ips = ["93.184.216.34", "8.8.8.8"]
        assert filter_answer_set(ips) == ips

    def test_mixed_answer_rejects_the_whole_request(self):
        # §3.4 rule 4: one forbidden IP in the answer set rejects everything.
        with pytest.raises(ForbiddenAddressError):
            filter_answer_set(["93.184.216.34", "127.0.0.1"])

    def test_empty_answer_set_rejected(self):
        with pytest.raises(ForbiddenAddressError):
            filter_answer_set([])


class TestAssertUrlHostPublic:
    def test_accepts_public_hostname(self):
        url = assert_url_host_public("https://api.github.com/repos")
        assert url.host == "api.github.com"

    @pytest.mark.parametrize(
        "raw",
        [
            "https://metadata.google.internal/computeMetadata/v1/",
            "http://metadata.goog/",
            "http://instance-data/latest/meta-data/",
            "http://localhost:8080/",
            "http://127.0.0.1/",             # literal loopback IP host
            "http://[::1]/",                 # literal IPv6 loopback
            "http://169.254.169.254/latest/meta-data/",
            "http://2130706433/",            # obfuscated IP caught in normalize
            "ftp://public.example.com/",
        ],
    )
    def test_rejects_private_or_metadata_hosts(self, raw):
        with pytest.raises(ForbiddenAddressError):
            assert_url_host_public(raw)
