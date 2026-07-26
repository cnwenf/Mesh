"""Markdown rendering, whitelist sanitization and mention extraction tests.

Security-critical surface (comment-inbox.md §5.1 XSS acceptance, §3.5
server-side mention authority): every vector asserts the sanitized output
contains no executable surface.
"""

from __future__ import annotations

import uuid
from html.parser import HTMLParser

import pytest

from mesh.comment_inbox.markdown import (
    extract_text,
    preview_of,
    render_body,
    sanitize_html,
)

pytestmark = pytest.mark.unit

MEMBER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def test_renders_full_markdown_feature_set():
    body = (
        "# H1\n\n**bold** *italic* ~~strike~~ `code`\n\n"
        "- item\n\n1. one\n\n> quote\n\n---\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\nprint(1)\n```\n\n"
        "- [ ] open task\n- [x] done task\n"
    )
    rendered = render_body(body)
    html = rendered.html
    assert "<h1>H1</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<s>strike</s>" in html
    assert "<code>code</code>" in html
    assert "<blockquote>" in html
    assert "<hr" in html or "<hr>" in html
    assert "<table>" in html and "<th>a</th>" in html and "<td>1</td>" in html
    assert '<code class="language-python">' in html
    assert '<li class="task"><input type="checkbox" disabled /> open task</li>' in html
    assert '<li class="task"><input type="checkbox" disabled checked /> done task</li>' in html
    assert "print(1)" in rendered.text


_SAFE_LIVE_TAGS = frozenset(
    {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
     "blockquote", "pre", "code", "em", "strong", "del", "s", "a", "img",
     "table", "thead", "tbody", "tr", "th", "td", "span", "input"}
)


class _LiveTagScanner(HTMLParser):
    """Parses the FINAL html; entity-escaped content is data, not tags, so
    only genuinely live elements are collected."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.elements.append((tag, {name: (value or "") for name, value in attrs}))

    handle_startendtag = handle_starttag


def _live_elements(rendered_html: str) -> list[tuple[str, dict[str, str | None]]]:
    scanner = _LiveTagScanner()
    scanner.feed(rendered_html)
    scanner.close()
    return scanner.elements


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '<svg onload="alert(1)"><circle r="1"/></svg>',
        '<iframe src="https://evil.example"></iframe>',
        '<a href="javascript:alert(1)">click</a>',
        '<a href="JAVASCRIPT:alert(1)">click</a>',
        '<a href="java\tscript:alert(1)">click</a>',
        '<a href="data:text/html,<script>alert(1)</script>">click</a>',
        '<a href="vbscript:msgbox(1)">click</a>',
        '<div onmouseover="alert(1)">hover</div>',
        "<form action=https://evil.example><button>x</button></form>",
        '<object data="https://evil.example/x.swf"></object>',
        "<math><mtext>x</mtext></math>",
    ],
)
def test_xss_vectors_neutralized(payload):
    rendered = render_body(payload)
    elements = _live_elements(rendered.html)
    for tag, attrs in elements:
        assert tag in _SAFE_LIVE_TAGS, f"live <{tag}> survived: {rendered.html!r}"
        for name, value in attrs.items():
            assert not name.startswith("on"), f"event handler survived: {name}"
            if name in ("href", "src"):
                lowered = (value or "").strip().lower()
                assert not lowered.startswith(
                    ("javascript:", "vbscript:", "data:", "file:")
                ), f"unsafe {name} survived: {value!r}"
    # The raw payload survives only as inert, entity-escaped text.
    assert "<script" not in rendered.html


def test_safe_links_and_images_survive():
    rendered = render_body(
        "[site](https://mesh.example/docs) and ![alt](https://img.example/a.png)"
    )
    assert '<a href="https://mesh.example/docs">site</a>' in rendered.html
    assert '<img src="https://img.example/a.png" alt="alt" />' in rendered.html


def test_sanitize_html_strips_unknown_tags_keeping_children():
    sanitized, mentions = sanitize_html("<blink>hello <marquee>world</marquee></blink>")
    assert sanitized == "hello world"
    assert mentions == ()


def test_sanitize_html_drops_dangerous_subtrees_wholesale():
    sanitized, _ = sanitize_html("before<script>var x = '<b>inner</b>';</script>after")
    assert "inner" not in sanitized
    assert "before" in sanitized and "after" in sanitized


def test_mention_link_becomes_span_and_is_extracted():
    rendered = render_body(f"hey [@张三](mention://member/{MEMBER_ID}) 看一下")
    assert f'<span class="mesh-mention" data-member-id="{MEMBER_ID}">' in rendered.html
    assert rendered.mention_ids == (MEMBER_ID,)
    assert "张三" in rendered.text


def test_mention_ids_deduplicated():
    rendered = render_body(
        f"[@a](mention://member/{MEMBER_ID}) and [@b](mention://member/{MEMBER_ID})"
    )
    assert rendered.mention_ids == (MEMBER_ID,)


def test_plain_at_names_extracted():
    rendered = render_body("@李四 确认一下，还有 @code-reviewer 跑回归")
    assert rendered.mention_names == ("李四", "code-reviewer")


def test_plain_mentions_excluded_from_code_and_links():
    body = (
        "`@InlineCode` 和 ```\n@BlockCode\n```\n"
        f"[@LinkLabel](mention://member/{MEMBER_ID}) 但 @RealName 要算"
    )
    rendered = render_body(body)
    assert "InlineCode" not in rendered.mention_names
    assert "BlockCode" not in rendered.mention_names
    assert "LinkLabel" not in rendered.mention_names
    assert rendered.mention_names == ("RealName",)


def test_mention_link_invalid_uuid_rejected_by_validator():
    # markdown-it drops links whose URL fails validateLink: the syntax
    # survives as literal text, never as an anchor/span.
    rendered = render_body("[x](mention://member/not-a-uuid)")
    assert "mesh-mention" not in rendered.html
    assert rendered.mention_ids == ()


def test_sanitizer_rejects_forged_mention_span():
    # Raw HTML is escaped by the renderer; feeding pre-rendered HTML
    # directly to the sanitizer must still drop unknown span classes.
    sanitized, _ = sanitize_html('<span class="evil" data-member-id="x">hi</span>')
    assert "<span" not in sanitized
    assert "hi" in sanitized


def test_extract_text_collapses_whitespace():
    assert extract_text("<p>a\n  b</p><p>c</p>") == "a b c"


def test_preview_of_truncates():
    long = "x" * 200
    preview = preview_of(long)
    assert len(preview) == 140
    assert preview.endswith("…")
    assert preview_of("short") == "short"


def test_nested_dangerous_tags_all_dropped():
    sanitized, _ = sanitize_html("<script><script>inner</script></script>tail")
    assert "inner" not in sanitized
    assert "tail" in sanitized
