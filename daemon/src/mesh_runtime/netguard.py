"""Network guard: URL normalization + IP classification + SSRF assertions.

Pure decision layer shared by the egress gateway (S-04, runtime-executor.md
§3.4) and the checkout helper (§3.2). No I/O here — callers compose these
functions with their own trusted resolver, so the rules stay unit-testable
without a network.

Rules enforced:

- URL normalization rejects userinfo, control chars / whitespace / backslash
  confusion, non-allowlisted schemes, missing hosts, and obfuscated IP
  literals (decimal ``2130706433``, hex ``0x7f000001``, octal ``0177.0.0.1``,
  non-canonical IPv6);
- every candidate IP is classified against loopback / private / link-local /
  multicast / reserved / unspecified / documentation / benchmarking ranges and
  explicit cloud-metadata addresses, normalizing IPv4-mapped IPv6 first;
- a DNS answer set is all-or-nothing: one forbidden IP rejects the request
  (§3.4 rule 4);
- well-known metadata hostnames are rejected regardless of what DNS says.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from mesh_runtime.errors import DaemonError

_DEFAULT_PORTS = {"http": 80, "https": 443, "git": 9418, "ssh": 22}

_CONTROL_WHITESPACE_OR_BACKSLASH = re.compile(r"[\x00-\x1f\x7f\s\\]")

#: Host shapes that are (attempted) numeric IP literals rather than registered
#: names: decimal/dotted quads, ``0x…`` hex forms, and IPv6 literals (colon).
#: Registered names outside these shapes pass through untouched.
_DECIMAL_HOST = re.compile(r"^[0-9.]+$")

#: Hostnames rejected outright, whatever the resolver answers (§3.4 / §3.2).
FORBIDDEN_METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "localhost",
    }
)

#: Metadata service IPs not fully covered by the generic is_* flags.
_METADATA_IPS = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP / Oracle link-local metadata
        "fd00:ec2::254",  # AWS EC2 IPv6 metadata
        "100.100.100.200",  # Alibaba Cloud metadata
    }
)

_DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)

_BENCHMARKING_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("198.18.0.0/15",)
)

#: Class E space — historically reserved, reported with its precise reason.
_RESERVED_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in ("240.0.0.0/4",))


class ForbiddenAddressError(DaemonError):
    """A URL or IP failed the network guard. Messages carry a reason code
    only — never the raw target detail an attacker could probe with."""


@dataclass(frozen=True)
class NormalizedUrl:
    scheme: str
    host: str
    port: int
    path: str
    query: str


@dataclass(frozen=True)
class IpVerdict:
    allowed: bool
    reason: str | None = None


def normalize_url(raw: str) -> NormalizedUrl:
    """Canonicalize a URL fail-closed. Any doubt raises ForbiddenAddressError."""
    if not isinstance(raw, str) or not raw:
        raise ForbiddenAddressError("empty or non-string url")
    if _CONTROL_WHITESPACE_OR_BACKSLASH.search(raw):
        raise ForbiddenAddressError("url contains control char, whitespace or backslash")
    parts = urlsplit(raw)
    if "@" in parts.netloc:
        raise ForbiddenAddressError("userinfo in url is forbidden")
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ForbiddenAddressError("scheme not allowed")
    host = parts.hostname  # lowercased, brackets stripped by urlsplit
    if not host:
        raise ForbiddenAddressError("url has no host")
    host = _canonicalize_ip_host(host)
    port = parts.port if parts.port is not None else _DEFAULT_PORTS[scheme]
    return NormalizedUrl(
        scheme=scheme, host=host, port=port, path=parts.path, query=parts.query
    )


def _canonicalize_ip_host(host: str) -> str:
    """Reject obfuscated IP literals; return canonical form for real ones.

    Numeric-looking hosts (digits / hex / dots / colons only) must parse as an
    IP address AND equal its canonical text form — this kills decimal
    (``2130706433``), hex (``0x7f000001``), octal (``0177.0.0.1``) and
    non-canonical IPv6 encodings. Registered names outside these shapes pass
    through untouched.
    """
    looks_numeric = (
        _DECIMAL_HOST.match(host) or ":" in host or host.lower().startswith("0x")
    )
    if not looks_numeric:
        return host
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise ForbiddenAddressError("obfuscated or invalid numeric host") from None
    if str(addr) != host:
        raise ForbiddenAddressError("non-canonical ip literal")
    return host


def classify_ip(raw: str) -> IpVerdict:
    """Classify one candidate IP. IPv4-mapped IPv6 is judged as its IPv4 half."""
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return IpVerdict(False, "unparseable")
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    canonical = str(addr)
    if canonical in _METADATA_IPS:
        return IpVerdict(False, "cloud_metadata")
    # Explicit ranges first: Python's is_private also covers TEST-NET and
    # benchmarking space — report the precise reason for audit clarity.
    if any(addr in net for net in _DOCUMENTATION_NETWORKS):
        return IpVerdict(False, "documentation")
    if any(addr in net for net in _BENCHMARKING_NETWORKS):
        return IpVerdict(False, "benchmarking")
    if any(addr in net for net in _RESERVED_NETWORKS):
        return IpVerdict(False, "reserved")
    if addr.is_unspecified:
        return IpVerdict(False, "unspecified")
    if addr.is_loopback:
        return IpVerdict(False, "loopback")
    if addr.is_link_local:
        return IpVerdict(False, "link_local")
    if addr.is_private:
        return IpVerdict(False, "private")
    if addr.is_multicast:
        return IpVerdict(False, "multicast")
    if addr.is_reserved:
        return IpVerdict(False, "reserved")
    if not addr.is_global:
        return IpVerdict(False, "non_global")
    return IpVerdict(True, None)


def filter_answer_set(ips: list[str]) -> list[str]:
    """All-or-nothing filter over a resolved answer set (§3.4 rule 4): one
    forbidden candidate rejects the entire request."""
    if not ips:
        raise ForbiddenAddressError("empty dns answer set")
    for ip in ips:
        verdict = classify_ip(ip)
        if not verdict.allowed:
            raise ForbiddenAddressError(f"forbidden address in answer set ({verdict.reason})")
    return list(ips)


def assert_url_host_public(raw: str) -> NormalizedUrl:
    """Static (no-DNS) public-host gate used by the checkout helper (SSRF,
    §3.2). Literal IP hosts are classified immediately; registered names are
    checked against the metadata hostname blocklist — callers must still run
    ``filter_answer_set`` on the trusted-resolver answers before connecting."""
    url = normalize_url(raw)
    if url.host in FORBIDDEN_METADATA_HOSTNAMES:
        raise ForbiddenAddressError("metadata hostname is forbidden")
    try:
        ipaddress.ip_address(url.host)
    except ValueError:
        return url  # registered name — DNS answers are filtered by the caller
    verdict = classify_ip(url.host)
    if not verdict.allowed:
        raise ForbiddenAddressError(f"literal ip host is forbidden ({verdict.reason})")
    return url
