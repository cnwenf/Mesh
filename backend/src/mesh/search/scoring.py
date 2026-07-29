"""Layered match scoring (§4.6) and original-title highlight mapping (§3.2).

Scoring produces a fixed-precision INTEGER bucket so the server-side total
order is reproducible (no floats in the cursor, R2-H4). Normalization for
matching uses the same algorithm as ``public.mesh_search_norm`` (NFKD +
strip combining marks + lower) — but highlights are computed on the
ORIGINAL title and expressed in Unicode code-point offsets, never on the
normalized form (§3.2 highlight contract).
"""

from __future__ import annotations

import re
import unicodedata

# Match-strength ladder, quantized (§4.6): text relevance dominates. These
# values MIRROR the DB function public.mesh_search_text_score exactly (M6 —
# the SQL SELECTs use that function as score_bucket; this Python ladder is
# its twin for unit tests, with identical separator handling and tiers).
SCORE_EXACT = 8  # normalized equality
SCORE_IDENTIFIER_PIN = 9  # canonical identifier exact hit — pinned top
SCORE_PREFIX = 7  # normalized title starts with the query
SCORE_TOKEN_PREFIX = 6  # every query token prefixes some title token
SCORE_ACRONYM = 5  # query chars = initials of successive title tokens
SCORE_SUBSTRING = 3  # contiguous substring
SCORE_FUZZY = 1  # trigram-similarity recall only

# Token boundaries (§4.6 词边界/驼峰/路径分隔): - _ / . and whitespace.
_TOKEN_SPLIT = re.compile(r"[-_/. ]+")


def normalize_search_text(value: str) -> str:
    """Python mirror of public.mesh_search_norm: NFKD + unaccent + lower.

    Used for scoring/highlight bookkeeping only — index/query normalization
    happens in SQL via the single authoritative function (§2.2).
    """
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower()


def _tokens(value: str) -> list[str]:
    return [tok for tok in _TOKEN_SPLIT.split(value) if tok]


def score_match(normalized_title: str, normalized_query: str) -> int:
    """Quantized match strength — exact mirror of mesh_search_text_score."""
    if not normalized_query:
        return SCORE_FUZZY
    if normalized_title == normalized_query:
        return SCORE_EXACT
    if normalized_title.startswith(normalized_query):
        return SCORE_PREFIX
    tokens_t = _tokens(normalized_title)
    tokens_q = _tokens(normalized_query)
    # Token-prefix: every query token prefixes SOME title token.
    if tokens_q and all(
        any(tt.startswith(tq) for tt in tokens_t) for tq in tokens_q
    ):
        return SCORE_TOKEN_PREFIX
    # Acronym: the query's characters (separators stripped) match the first
    # characters of successive title tokens, in order.
    flat_q = _TOKEN_SPLIT.sub("", normalized_query)
    if flat_q and len(tokens_t) >= len(flat_q):
        matched = 0
        for tok in tokens_t:
            if matched >= len(flat_q):
                break
            if tok and tok[0] == flat_q[matched]:
                matched += 1
        if matched >= len(flat_q):
            return SCORE_ACRONYM
    if normalized_query in normalized_title:
        return SCORE_SUBSTRING
    return SCORE_FUZZY


def _normalized_spans(title: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize per code point, tracking each source code point's span.

    Returns ``(normalized, spans)`` where ``spans[i] = (start, end)`` is the
    half-open offset range in ``normalized`` produced by source code point
    ``i`` (empty spans ``(k, k)`` are possible when a code point normalizes
    to nothing, e.g. a stripped combining mark).
    """
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    offset = 0
    for ch in title:
        norm = normalize_search_text(ch)
        parts.append(norm)
        spans.append((offset, offset + len(norm)))
        offset += len(norm)
    return "".join(parts), spans


def highlight_ranges(title: str, query: str) -> list[tuple[int, int]]:
    """Half-open code-point ranges on the ORIGINAL title to highlight.

    Matches are located on the normalized form (NFKD/unaccent/lower) and
    mapped back to source code points, so precomposed input (``José``) and
    decomposed storage still align; offsets are in ``Array.from(title)``
    units for the frontend (§3.2). Returns ``[]`` when nothing matches.
    """
    normalized_query = normalize_search_text(query)
    if not normalized_query or not title:
        return []
    normalized_title, spans = _normalized_spans(title)

    match_spans: list[tuple[int, int]] = []
    tokens = [t for t in normalized_query.split() if t]
    for token in tokens or [normalized_query]:
        start = 0
        while True:
            idx = normalized_title.find(token, start)
            if idx < 0:
                break
            match_spans.append((idx, idx + len(token)))
            start = idx + max(1, len(token))

    if not match_spans:
        return []

    codepoints = len(title)
    ranges: list[tuple[int, int]] = []
    for match_start, match_end in match_spans:
        src_start = next(
            (i for i, (_, end) in enumerate(spans) if end > match_start), None
        )
        src_end = next(
            (i + 1 for i in range(codepoints - 1, -1, -1) if spans[i][0] < match_end),
            None,
        )
        if src_start is None or src_end is None or src_start >= src_end:
            continue
        if ranges and src_start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], src_end))
        else:
            ranges.append((src_start, src_end))
    return ranges
