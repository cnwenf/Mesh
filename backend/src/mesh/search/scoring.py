"""Layered match scoring (§4.6) and original-title highlight mapping (§3.2).

Scoring produces a fixed-precision INTEGER bucket so the server-side total
order is reproducible (no floats in the cursor, R2-H4). Normalization for
matching uses the same algorithm as ``public.mesh_search_norm`` (NFKD +
strip combining marks + lower) — but highlights are computed on the
ORIGINAL title and expressed in Unicode code-point offsets, never on the
normalized form (§3.2 highlight contract).
"""

from __future__ import annotations

import unicodedata

# Match-strength ladder, quantized (§4.6): text relevance dominates.
SCORE_EXACT = 8  # normalized equality
SCORE_IDENTIFIER_PIN = 9  # canonical identifier exact hit — pinned top
SCORE_PREFIX = 6  # normalized title starts with the query
SCORE_TOKEN_PREFIX = 5  # a word of the title starts with the query
SCORE_SUBSTRING = 3  # contiguous substring
SCORE_FUZZY = 1  # trigram-similarity recall only

TOKEN_SEPARATORS = frozenset(" -_/.\t")


def normalize_search_text(value: str) -> str:
    """Python mirror of public.mesh_search_norm: NFKD + unaccent + lower.

    Used for scoring/highlight bookkeeping only — index/query normalization
    happens in SQL via the single authoritative function (§2.2).
    """
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower()


def score_match(normalized_title: str, normalized_query: str) -> int:
    """Quantized match strength of ``normalized_query`` against the title."""
    if not normalized_query:
        return 0
    if normalized_title == normalized_query:
        return SCORE_EXACT
    if normalized_title.startswith(normalized_query):
        return SCORE_PREFIX
    # Token-prefix: a separated word starts with the query.
    previous = " "
    for index, ch in enumerate(normalized_title):
        if previous in TOKEN_SEPARATORS and normalized_title.startswith(
            normalized_query, index
        ):
            return SCORE_TOKEN_PREFIX
        previous = ch
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
