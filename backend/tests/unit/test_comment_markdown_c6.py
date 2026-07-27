"""C6 smart links + L5 sanitizer hardening (round-2 fixes)."""

from __future__ import annotations

import pytest

from mesh.comment_inbox.markdown import render_body, sanitize_html

pytestmark = pytest.mark.unit


def test_issue_identifier_shorthand_linkified():
    r = render_body("see #MES-123 for context")
    assert 'class="mesh-issue-link"' in r.html
    assert 'data-issue-identifier="MES-123"' in r.html
    assert 'href="/issues/by-identifier/MES-123"' in r.html
    assert "#MES-123</a>" in r.html


def test_identifier_not_linkified_inside_code():
    r = render_body("inline `#CODE-9` and\n```\n#FENCE-1\n```\nbut #WEB-7 yes")
    # code regions keep the literal #, no link
    assert "<code>#CODE-9</code>" in r.html
    assert "#FENCE-1" in r.html and 'data-issue-identifier="FENCE-1"' not in r.html
    assert 'data-issue-identifier="WEB-7"' in r.html


def test_identifier_word_boundary_no_false_match():
    r = render_body("C#9 and issue#5 and x#AB-1 nope but #OK-2 yes")
    assert 'data-issue-identifier="OK-2"' in r.html
    assert 'data-issue-identifier="C#9"' not in r.html  # not a valid ident anyway
    assert 'data-issue-identifier="9"' not in r.html


def test_l5_protocol_relative_url_rejected():
    h, _ = sanitize_html('<a href="//evil.com">click</a>')
    assert "<a" not in h
    assert "click" in h  # label kept, link dropped


def test_l5_unsafe_link_no_orphan_close():
    h, _ = sanitize_html('before <a href="javascript:alert(1)">x</a> after')
    assert "<a" not in h
    assert h.count("</a>") == 0  # no orphan closing tag
    assert "before" in h and "x" in h and "after" in h


def test_safe_relative_and_https_links_kept():
    h, _ = sanitize_html('<a href="/issues/1">a</a> <a href="https://x.com">b</a>')
    assert 'href="/issues/1"' in h
    assert 'href="https://x.com"' in h


def test_mention_still_works_alongside_linkify():
    r = render_body("#WEB-1 hi [@bob](mention://member/11111111-1111-4111-8111-111111111111)")
    assert 'data-issue-identifier="WEB-1"' in r.html
    assert 'data-member-id="11111111-1111-4111-8111-111111111111"' in r.html
    assert r.mention_ids[0].hex == "11111111111141118111111111111111"
