"""Daemon-side first-layer redaction (runtime-executor.md §2.5 / §3.9).

Nothing leaves the host without passing through :class:`RedactionPipeline`:
provider stream summaries, tool output, diffs, results. The server redacts
again on arrival — daemon redaction is the first of two mandatory layers, and
``start_offset`` for log uploads is computed over REDACTED utf-8 bytes (§3.9).

Matchers:
1. exact secret literals (longest first, like the server guard);
2. standard-base64 of each secret (≥8 chars — short encodings are too common
   as innocent substrings);
3. percent-encoded (URL) form of each secret.

Hit counts never carry the original secret text.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import quote

REDACTED = "***"
_MIN_ENCODED_MATCH_LEN = 8


@dataclass(frozen=True)
class RedactionResult:
    text: str
    hit_count: int


class RedactionPipeline:
    def __init__(self, *, secrets: list[str] | tuple[str, ...], rule_version: str) -> None:
        self.rule_version = rule_version
        cleaned = sorted(
            {s for s in secrets if s and s.strip()},
            key=len,
            reverse=True,  # longest first: greedy, no partial-survivor artifacts
        )
        self._patterns: list[str] = []
        for secret in cleaned:
            self._patterns.append(secret)
            if len(secret) >= _MIN_ENCODED_MATCH_LEN:
                encoded_b64 = base64.b64encode(secret.encode("utf-8")).decode("ascii")
                if encoded_b64 not in self._patterns:
                    self._patterns.append(encoded_b64)
                encoded_url = quote(secret, safe="")
                if encoded_url != secret and encoded_url not in self._patterns:
                    self._patterns.append(encoded_url)

    def add_secret(self, secret: str) -> None:
        """Add a secret discovered after construction (e.g. a task token
        rotated by a lease renewal, §2.6). Same encoding expansion as init;
        duplicates are no-ops."""
        if not secret or not secret.strip():
            return
        new_patterns = [secret]
        if len(secret) >= _MIN_ENCODED_MATCH_LEN:
            encoded_b64 = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            if encoded_b64 not in new_patterns:
                new_patterns.append(encoded_b64)
            encoded_url = quote(secret, safe="")
            if encoded_url != secret and encoded_url not in new_patterns:
                new_patterns.append(encoded_url)
        for pattern in new_patterns:
            if pattern not in self._patterns:
                self._patterns.insert(0, pattern)  # newest first is fine; order
                # only matters among overlapping lengths and the rotated token
                # never overlaps the claim-time secrets it supplements.

    def redact(self, text: str) -> RedactionResult:
        hits = 0
        for pattern in self._patterns:
            count = text.count(pattern)
            if count:
                text = text.replace(pattern, REDACTED)
                hits += count
        return RedactionResult(text=text, hit_count=hits)

    def redact_lines(self, lines: list[str]) -> tuple[list[str], int]:
        total = 0
        out: list[str] = []
        for line in lines:
            result = self.redact(line)
            out.append(result.text)
            total += result.hit_count
        return out, total
