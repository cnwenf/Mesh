"""Layered match scoring + highlight ranges (spec §4.6 / §3.2).

Match-strength ladder (strong → weak), quantized to integer buckets so the
server-side total order is reproducible (no floating point in the keyset
tuple, §4.6 R2-H4):

    identifier exact (fast path, pinned) > exact > prefix > token prefix
    > acronym > word boundary / camel > substring > subsequence

Candidates recalled through the trigram path that the ladder cannot classify
still keep a fuzzy floor (trigram similarity IS the fuzzy signal). Buckets
are the ``score_bucket`` factor of the total order
``(score_bucket DESC, title_len ASC, title_lex ASC, type ASC, id ASC)``.

Highlights are computed on the ORIGINAL title in Unicode code points — the
normalization is recall/rank-only and never feeds offset mapping (§3.2).
Only literal-occurrence classes emit ranges; acronym / subsequence / trigram
floor matches are fuzzy-only and carry no highlight.
"""

from __future__ import annotations

import re
import unicodedata

from mesh.search.norm import norm_with_map, search_norm

# -- Score buckets (integer, reproducible) -----------------------------------
BUCKET_IDENTIFIER_EXACT = 95  # fast-path hit, always pinned first
BUCKET_EXACT = 90
BUCKET_PREFIX = 80
BUCKET_TOKEN_PREFIX = 70
BUCKET_ACRONYM = 60
BUCKET_WORD_BOUNDARY = 50
BUCKET_SUBSTRING = 40
BUCKET_SUBSEQUENCE = 20
BUCKET_TRIGRAM_FLOOR = 10  # trigram recall the ladder cannot classify
NO_MATCH = 0

# Classes with a literal occurrence of the query in the title → highlightable.
_LITERAL_CLASSES = frozenset(
    {
        BUCKET_EXACT,
        BUCKET_PREFIX,
        BUCKET_TOKEN_PREFIX,
        BUCKET_WORD_BOUNDARY,
        BUCKET_SUBSTRING,
    }
)

# Separators that delimit coarse tokens (spec §4.6: 空格/-/_//.).
_SEPARATOR_SPLIT = re.compile(r"[\s\-_/.]+")


def coarse_tokens(casefolded: str) -> list[str]:
    """Split normalized (lowered) text on separators; empty chunks dropped."""
    return [chunk for chunk in _SEPARATOR_SPLIT.split(casefolded) if chunk]


def _boundary_positions(case_kept: str) -> list[int]:
    """Code-point offsets that start a word (0, post-separator, camel edge)."""
    positions = [0]
    prev_separator = False
    prev_lower = False
    for index, char in enumerate(case_kept):
        if _SEPARATOR_SPLIT.fullmatch(char):
            prev_separator = True
            prev_lower = False
            continue
        if prev_separator:
            positions.append(index)
        elif prev_lower and char.isupper():
            positions.append(index)
        prev_separator = False
        prev_lower = char.islower() or char.isdigit()
    return positions


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(char in it for char in needle)


def _multi_token_bucket(q_tokens: list[str], title: str, norm_title: str) -> int:
    """Ladder for multi-word queries (e.g. ``saf cri`` → 「Safari 崩溃」)."""
    title_tokens = coarse_tokens(norm_title)
    if all(any(tt.startswith(qt) for tt in title_tokens) for qt in q_tokens):
        return BUCKET_TOKEN_PREFIX
    if all(any(qt in tt for tt in title_tokens) for qt in q_tokens):
        return BUCKET_SUBSTRING
    if _is_subsequence(search_norm(" ".join(q_tokens)), norm_title):
        return BUCKET_SUBSEQUENCE
    return NO_MATCH


def match_bucket(norm_query: str, title: str) -> int:
    """Classify the strongest ladder rung between query and title (spec §4.6).

    Both inputs are expected non-empty; ``norm_query`` is already normalized.
    Returns one of the ``BUCKET_*`` constants or :data:`NO_MATCH`.
    """
    norm_title = search_norm(title)
    if not norm_query or not norm_title:
        return NO_MATCH
    if norm_title == norm_query:
        return BUCKET_EXACT
    if norm_title.startswith(norm_query):
        return BUCKET_PREFIX
    q_tokens = coarse_tokens(norm_query)
    if len(q_tokens) > 1:
        return _multi_token_bucket(q_tokens, title, norm_title)
    # Token prefix is over COARSE (separator-split) tokens; a match starting
    # at a camel edge INSIDE a token is the weaker word-boundary rung below.
    title_tokens = coarse_tokens(norm_title)
    if any(token.startswith(norm_query) for token in title_tokens):
        return BUCKET_TOKEN_PREFIX
    acronym = "".join(token[0] for token in title_tokens)
    if len(norm_query) >= 2 and acronym == norm_query:
        return BUCKET_ACRONYM
    # NFKD can shift offsets, so boundary detection runs on a case-keeping
    # accent-folded form (no lower()) whose code points stay closer to the
    # original than the fully normalized string.
    case_kept = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", title)
        if not unicodedata.combining(ch)
    )
    for position in _boundary_positions(case_kept):
        window = search_norm(case_kept[position : position + len(norm_query)])
        if window == norm_query:
            return BUCKET_WORD_BOUNDARY
    if norm_query in norm_title:
        return BUCKET_SUBSTRING
    if _is_subsequence(norm_query, norm_title):
        return BUCKET_SUBSEQUENCE
    return NO_MATCH


def score_candidate(norm_query: str, title: str, *, trigram_recalled: bool) -> int:
    """Bucket for a recalled candidate; trigram recalls keep a fuzzy floor."""
    bucket = match_bucket(norm_query, title)
    if bucket == NO_MATCH and trigram_recalled:
        return BUCKET_TRIGRAM_FLOOR
    return bucket


def _occurrence_ranges(norm_query: str, title: str) -> list[tuple[int, int]]:
    """Case-insensitive occurrences of the query on the original code points.

    Each query token is located in the normalized title and mapped back
    through :func:`norm_with_map`; returned ranges are half-open ``[start,
    end)`` code-point offsets into ``title``.
    """
    norm_title, mapping = norm_with_map(title)
    if not mapping:
        return []
    ranges: list[tuple[int, int]] = []
    for token in coarse_tokens(norm_query):
        if not token:
            continue
        start = norm_title.find(token)
        while start != -1:
            end = start + len(token)
            ranges.append((mapping[start], mapping[end - 1] + 1))
            start = norm_title.find(token, start + 1)
    return _merge_ranges(ranges)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def highlight_ranges(bucket: int, norm_query: str, title: str) -> list[list[int]] | None:
    """Code-point highlight ranges for a scored hit; ``None`` when fuzzy-only."""
    if bucket not in _LITERAL_CLASSES:
        return None
    ranges = _occurrence_ranges(norm_query, title)
    return ranges or None
