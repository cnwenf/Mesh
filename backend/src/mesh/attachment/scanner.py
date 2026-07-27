"""Anti-virus scan hook (attachment.md §3.3 病毒扫描钩子 / A11).

Pluggable by protocol so a deployment can swap in a real engine (e.g. a
ClamAV daemon) without touching the quarantine pipeline. The default
heuristic scanner detects:

- the EICAR standard anti-virus test pattern (the industry-standard way to
  exercise an AV pipeline end-to-end — a hit marks the blob ``infected``);
- executable containers (PE/ELF/Mach-O) smuggled under a non-executable
  sniffed MIME — the upload allowlist already rejects declared executables,
  so executable magic bytes under e.g. ``image/png`` means forged content.

A ``clean`` verdict means "no known-bad signature matched"; it is NOT a
certification of innocence — deployments with compliance requirements wire a
full engine through the same protocol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from mesh.attachment.mime import EXECUTABLE_MIMES

logger = logging.getLogger("mesh.attachment.scanner")

# EICAR anti-virus test file content (standard, harmless, 68 bytes). Every
# conforming AV engine — and this heuristic — flags it as a test threat.
EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

ENGINE_NAME = "mesh-heuristic-av"


@dataclass(frozen=True)
class ScanVerdict:
    """Result of one AV pass over blob bytes."""

    infected: bool
    engine: str
    result: str  # free-form detail: "clean" | "eicar-test-signature" | ...


class VirusScanner(Protocol):
    """Anything that can judge raw bytes (sync — runs in a worker thread)."""

    def scan(self, data: bytes, *, sniffed_mime: str) -> ScanVerdict: ...


class HeuristicScanner:
    """Signature + container heuristics (default, dependency-free)."""

    def scan(self, data: bytes, *, sniffed_mime: str) -> ScanVerdict:
        if EICAR_SIGNATURE in data:
            logger.warning("AV hit: EICAR test signature detected")
            return ScanVerdict(
                infected=True, engine=ENGINE_NAME, result="eicar-test-signature"
            )
        # F5: 可执行容器一律判定 infected —— 上传白名单在 request 阶段即拒
        # 可执行 MIME(415),因此任何可执行魔数抵达扫描器都意味着伪造内容
        # (如 png 扩展名包裹的真 EXE:嗅探得到 x-msdownload,此前分支因嗅探
        # 与魔数判定偏移一致而成为死代码)。下载侧仍强制 attachment(§3.6 兜底)。
        if sniffed_mime in EXECUTABLE_MIMES or _has_executable_magic(data):
            logger.warning(
                "AV hit: executable container (sniffed %s)", sniffed_mime
            )
            return ScanVerdict(
                infected=True, engine=ENGINE_NAME, result="executable-container"
            )
        return ScanVerdict(infected=False, engine=ENGINE_NAME, result="clean")


def _has_executable_magic(data: bytes) -> bool:
    return (
        data[:2] == b"MZ"
        or data[:4] == b"\x7fELF"
        or data[:4] in {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}
        or data[:3] == b"\xfe\xed\xfa"
    )
