"""Scoring ladder (§4.6) and code-point highlight mapping (§3.2)."""

from __future__ import annotations

import pytest

from mesh.search.scoring import (
    SCORE_EXACT,
    SCORE_FUZZY,
    SCORE_PREFIX,
    SCORE_SUBSTRING,
    SCORE_TOKEN_PREFIX,
    highlight_ranges,
    normalize_search_text,
    score_match,
)

pytestmark = pytest.mark.unit


def test_normalize_mirrors_sql_function():
    assert normalize_search_text("José Àncône") == "jose ancone"
    assert normalize_search_text("ZHANG Wei") == "zhang wei"
    assert normalize_search_text("代码助手") == "代码助手"
    assert normalize_search_text("") == ""


def test_score_ladder_ordering():
    assert score_match("safari crash", "safari crash") == SCORE_EXACT
    assert score_match("safari crash", "saf") == SCORE_PREFIX
    assert score_match("safari crash", "crash") == SCORE_TOKEN_PREFIX
    assert score_match("wsafari crash", "saf") == SCORE_SUBSTRING
    assert score_match("unrelated", "zzz") == SCORE_FUZZY
    # The ladder is strictly decreasing — the total order depends on it.
    assert SCORE_EXACT > SCORE_PREFIX > SCORE_TOKEN_PREFIX > SCORE_SUBSTRING > SCORE_FUZZY


def test_score_accent_insensitive():
    assert score_match(normalize_search_text("José"), normalize_search_text("JOSE")) == SCORE_EXACT


def test_highlight_ascii_prefix():
    assert highlight_ranges("Login page", "log") == [(0, 3)]


def test_highlight_cjk_codepoints():
    # Code-point units: each CJK character is one unit.
    assert highlight_ranges("登录页在 Safari 崩溃", "登录") == [(0, 2)]
    assert highlight_ranges("登录页在 Safari 崩溃", "safari") == [(5, 11)]


def test_highlight_precomposed_accented():
    # Source title uses precomposed é (1 code point); normalized matching
    # must map back to the ORIGINAL offset, not the decomposed form.
    ranges = highlight_ranges("Café menu", "cafe")
    assert ranges == [(0, 4)]


def test_highlight_multi_token():
    ranges = highlight_ranges("Safari 崩溃", "saf 崩")
    assert ranges == [(0, 3), (7, 8)]


def test_highlight_no_match_empty():
    assert highlight_ranges("anything", "zzz") == []
    assert highlight_ranges("anything", "") == []
    assert highlight_ranges("", "q") == []


def test_highlight_ranges_merged_when_adjacent():
    # "abc" → [0,3), "cd" → [2,4): overlapping spans merge into one.
    ranges = highlight_ranges("abcdef", "abc cd")
    assert ranges == [(0, 4)]
