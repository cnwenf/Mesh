"""Search scoring / highlight / cursor unit tests (spec §4.6 / §3.2).

Pure-Python contract paths: the match-strength ladder ordering, integer
bucket quantization, code-point highlight mapping (multi-byte / CJK /
combining marks), identifier-shape detection and cursor sign/verify with
binding mismatch. Nothing mocked — these are deterministic functions.
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from mesh.errors import ValidationError
from mesh.search.cursor import (
    binding_fingerprint,
    canonical_sort_factors,
    decode_cursor,
    encode_cursor,
    factors_as_sort_key,
)
from mesh.search.norm import is_identifier_shape, norm_with_map, search_norm
from mesh.search.scoring import (
    BUCKET_ACRONYM,
    BUCKET_EXACT,
    BUCKET_IDENTIFIER_EXACT,
    BUCKET_PREFIX,
    BUCKET_SUBSEQUENCE,
    BUCKET_SUBSTRING,
    BUCKET_TOKEN_PREFIX,
    BUCKET_TRIGRAM_FLOOR,
    BUCKET_WORD_BOUNDARY,
    NO_MATCH,
    highlight_ranges,
    match_bucket,
    score_candidate,
)

SECRET = b"unit-test-cursor-secret"


# ---------------------------------------------------------------------------
# Normalization (Python mirror of public.mesh_search_norm)
# ---------------------------------------------------------------------------


def test_search_norm_folds_accents_and_case() -> None:
    assert search_norm("José Àncône") == "jose ancone"
    assert search_norm("ZHANG") == "zhang"


def test_search_norm_nfkd_compatibility_decomposition() -> None:
    # Full-width latin + ligatures decompose under NFKD.
    assert search_norm("ＷＥＢ") == "web"
    assert search_norm("ﬁx") == "fix"


def test_norm_with_map_records_source_codepoints() -> None:
    norm, mapping = norm_with_map("José")
    assert norm == "jose"
    assert mapping == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Identifier shape detection (fast-path routing)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("web-124", True),
        ("WEB-124", True),  # normalized before the check
        ("wéb-124", True),  # accent folds to the shape
        ("web-12x", False),
        ("web124", False),
        ("web-", False),
        ("", False),
    ],
)
def test_is_identifier_shape(raw: str, expected: bool) -> None:
    assert is_identifier_shape(search_norm(raw)) is expected


# ---------------------------------------------------------------------------
# Scoring ladder (spec §4.6 — strict strong→weak ordering)
# ---------------------------------------------------------------------------


def test_ladder_strict_ordering() -> None:
    buckets = {
        "exact": match_bucket("safari", "Safari"),
        "prefix": match_bucket("saf", "Safari crashes"),
        "token_prefix": match_bucket("crash", "Safari crashes"),
        "acronym": match_bucket("sc", "Safari Crashes"),
        "word_boundary": match_bucket("crash", "safariCrashReport"),
        "substring": match_bucket("fari", "Safari"),
        "subsequence": match_bucket("sfx", "Safari fix"),
    }
    assert buckets["exact"] == BUCKET_EXACT
    assert buckets["prefix"] == BUCKET_PREFIX
    assert buckets["token_prefix"] == BUCKET_TOKEN_PREFIX
    assert buckets["acronym"] == BUCKET_ACRONYM
    assert buckets["word_boundary"] == BUCKET_WORD_BOUNDARY
    assert buckets["substring"] == BUCKET_SUBSTRING
    assert buckets["subsequence"] == BUCKET_SUBSEQUENCE
    ordered = [
        buckets["exact"],
        buckets["prefix"],
        buckets["token_prefix"],
        buckets["acronym"],
        buckets["word_boundary"],
        buckets["substring"],
        buckets["subsequence"],
    ]
    assert ordered == sorted(ordered, reverse=True)
    assert len(set(ordered)) == len(ordered)  # quantized to distinct rungs


def test_multi_token_query_token_prefix() -> None:
    # spec §1.2 S5 shape: a multi-word query matches when every query token
    # prefixes some title token (「saf cra」→ Safari Crashes).
    assert match_bucket("saf cra", "Safari Crashes") == BUCKET_TOKEN_PREFIX
    # One token matching as a plain substring degrades to substring class.
    assert match_bucket("saf rash", "Safari Crashes") == BUCKET_SUBSTRING


def test_accent_insensitive_match() -> None:
    assert match_bucket(search_norm("JOSÉ"), "José García") == BUCKET_PREFIX


def test_no_match_is_zero() -> None:
    assert match_bucket("zzz", "Safari") == NO_MATCH


def test_buckets_are_integers() -> None:
    for bucket in (
        BUCKET_IDENTIFIER_EXACT,
        BUCKET_EXACT,
        BUCKET_PREFIX,
        BUCKET_TOKEN_PREFIX,
        BUCKET_ACRONYM,
        BUCKET_WORD_BOUNDARY,
        BUCKET_SUBSTRING,
        BUCKET_SUBSEQUENCE,
        BUCKET_TRIGRAM_FLOOR,
        NO_MATCH,
    ):
        assert isinstance(bucket, int)


def test_identifier_exact_bucket_is_max() -> None:
    assert BUCKET_IDENTIFIER_EXACT > BUCKET_EXACT
    assert BUCKET_IDENTIFIER_EXACT == max(
        BUCKET_EXACT,
        BUCKET_PREFIX,
        BUCKET_TOKEN_PREFIX,
        BUCKET_ACRONYM,
        BUCKET_WORD_BOUNDARY,
        BUCKET_SUBSTRING,
        BUCKET_SUBSEQUENCE,
        BUCKET_IDENTIFIER_EXACT,
    )


def test_trigram_floor_applies_only_to_recalled_candidates() -> None:
    assert score_candidate("qxz", "Safari", trigram_recalled=False) == NO_MATCH
    assert score_candidate("qxz", "Safari", trigram_recalled=True) == BUCKET_TRIGRAM_FLOOR
    # A real ladder match keeps its rung regardless of recall path.
    assert (
        score_candidate("saf", "Safari", trigram_recalled=True) == BUCKET_PREFIX
    )


@pytest.mark.parametrize(
    ("query", "title", "trigram_recalled"),
    [
        ("safari", "Safari", False),
        ("saf", "Safari crashes", False),
        ("crash", "Safari crashes", False),
        ("saf cra", "Safari crashes", False),
        ("sc", "Safari Crashes", False),
        ("crash", "safariCrashReport", False),
        ("fari", "Safari", False),
        ("sfx", "Safari fix", False),
        ("qxz", "Safari", True),
        ("登录", "代码助手 登录", False),
    ],
)
async def test_python_scoring_matches_database_function(
    db_session, query: str, title: str, trigram_recalled: bool
) -> None:
    """SQL keyset scoring and Python highlight classification cannot drift."""
    from sqlalchemy import text

    db_bucket = (
        await db_session.execute(
            text("SELECT public.mesh_search_text_score(:title, :query)"),
            {"title": title, "query": query},
        )
    ).scalar_one()
    assert db_bucket == score_candidate(
        search_norm(query), title, trigram_recalled=trigram_recalled
    )


# ---------------------------------------------------------------------------
# Highlight codepoint ranges (spec §3.2 — offsets on the ORIGINAL title)
# ---------------------------------------------------------------------------


def test_highlight_cjk_codepoint_offsets() -> None:
    ranges = highlight_ranges(BUCKET_SUBSTRING, "登录", "登录页在 Safari 崩溃")
    assert ranges == [[0, 2]]


def test_highlight_maps_through_combining_marks() -> None:
    # "José" — query "jos" matches normalized "jose"; range maps back to the
    # first three code points of the ORIGINAL title (J, o, s).
    bucket = match_bucket("jos", "José")
    assert highlight_ranges(bucket, "jos", "José") == [[0, 3]]


def test_highlight_multibyte_fullwidth() -> None:
    # ＡＢＣ normalizes to "abc"; "ab" maps back to codepoints [0, 2).
    bucket = match_bucket("ab", "ＡＢＣ")
    assert highlight_ranges(bucket, "ab", "ＡＢＣ") == [[0, 2]]


def test_highlight_collects_multiple_occurrences() -> None:
    bucket = match_bucket("in", "login in inbox")
    ranges = highlight_ranges(bucket, "in", "login in inbox")
    # "in" at login[3:5], the standalone word [6:8] and inbox[9:11].
    assert ranges == [[3, 5], [6, 8], [9, 11]]


def test_highlight_fuzzy_only_match_has_no_ranges() -> None:
    # Acronym and subsequence classes never emit ranges.
    assert highlight_ranges(BUCKET_ACRONYM, "sc", "Safari Crashes") is None
    assert highlight_ranges(BUCKET_SUBSEQUENCE, "sfx", "Safari fix") is None
    assert highlight_ranges(BUCKET_TRIGRAM_FLOOR, "qxz", "Safari") is None


def test_highlight_no_occurrence_returns_none() -> None:
    assert highlight_ranges(BUCKET_SUBSTRING, "zz", "Safari") is None


# ---------------------------------------------------------------------------
# Cursor sign / verify / binding (spec §3.2)
# ---------------------------------------------------------------------------


def _factors() -> list:
    return canonical_sort_factors(
        score_bucket=80,
        title_len=12,
        title_lex="login portal",
        result_type="project",
        result_id=str(uuid.uuid4()),
    )


def test_cursor_roundtrip() -> None:
    fp = binding_fingerprint("login", ("issue", "project"), uuid.uuid4())
    factors = _factors()
    raw = encode_cursor(SECRET, fp=fp, factors=factors)
    decoded_fp, decoded_factors = decode_cursor(SECRET, raw)
    assert decoded_fp == fp
    assert decoded_factors == factors


def test_cursor_sort_key_negates_bucket() -> None:
    factors = _factors()
    assert factors_as_sort_key(factors)[0] == -80


def test_tampered_factors_fail_signature() -> None:
    fp = binding_fingerprint("login", ("issue",), uuid.uuid4())
    raw = encode_cursor(SECRET, fp=fp, factors=_factors())
    envelope = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    envelope["t"][0] = 95  # forge a higher bucket
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(ValidationError):
        decode_cursor(SECRET, tampered)


def test_wrong_secret_fails_signature() -> None:
    fp = binding_fingerprint("login", ("issue",), uuid.uuid4())
    raw = encode_cursor(SECRET, fp=fp, factors=_factors())
    with pytest.raises(ValidationError):
        decode_cursor(b"another-secret", raw)


def test_garbage_cursor_fails_cleanly() -> None:
    for raw in ("", "not-base64!!!", "aGVsbG8=", "eyJmYWtlIjp0cnVlfQ"):
        with pytest.raises(ValidationError):
            decode_cursor(SECRET, raw)


def test_binding_fingerprint_changes_with_any_parameter() -> None:
    ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
    base = binding_fingerprint("login", ("issue", "project"), ws_a)
    assert binding_fingerprint("login2", ("issue", "project"), ws_a) != base
    # types order-insensitive when the caller pre-sorts.
    assert binding_fingerprint("login", ("project", "issue"), ws_a) != base
    assert binding_fingerprint("login", ("issue", "project"), ws_b) != base
    assert binding_fingerprint("login", ("issue", "project"), ws_a) == base
