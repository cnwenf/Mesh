"""Server-side Markdown rendering, sanitization and mention extraction.

The server parse of ``body_markdown`` is AUTHORITATIVE (comment-inbox.md
§3.5 / README §6.9): client-submitted mention lists are ignored.

Pipeline:

1. ``#IDENTIFIER`` shorthand (C6, e.g. ``#MES-123``) is linkified to a
   same-workspace issue link (``mesh-issue://`` scheme) BEFORE markdown-it,
   skipping fenced/inline code via tokenisation.
2. ``markdown-it-py`` (default preset: tables + strikethrough; ``html=False``
   escapes any raw HTML) renders to HTML. The link validator accepts
   ``mention://member/<uuid>`` and ``mesh-issue://`` links.
3. A stdlib whitelist sanitizer re-parses the render output (defense in depth):
   only a fixed tag/attribute set survives, URLs are scheme-checked
   (protocol-relative ``//host`` rejected, L5), ``mesh-issue://`` links become
   same-workspace ``/issues/by-identifier/<id>`` anchors, and
   ``script``/``iframe``/``svg`` subtrees are dropped.
4. Plain-text extraction for search / notification previews / email.

Mention extraction yields two inputs for the service layer:

* ``mention_ids`` — UUIDs from structural ``[label](mention://member/<uuid>)``
  links (the composer chip serialization);
* ``mention_names`` — ``@Name`` tokens in prose (exact display-name match in
  the service; ambiguous names resolve to nothing).
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser

from markdown_it import MarkdownIt

MENTION_SCHEME = "mention://member/"
MENTION_UUID_RE = re.compile(r"^mention://member/([0-9a-fA-F-]{36})$")
# C6: internal scheme produced by the #IDENTIFIER pre-processor.
ISSUE_LINK_SCHEME = "mesh-issue://"
_ISSUE_LINK_RE = re.compile(r"^mesh-issue://([A-Za-z0-9._-]{1,40})$")

# @Name tokens: word chars + CJK + dot/dash, 1-64 chars, at a word boundary.
_PLAIN_MENTION_RE = re.compile(r"(?:^|(?<=\s))@([\w一-鿿][\w一-鿿.\-]{0,63})")
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_TASK_LIST_ITEM_RE = re.compile(r"(<li>)\[( |[xX])\]\s+")
# C6: `#IDENTIFIER` shorthand (e.g. #MES-123). Word boundary before `#` avoids
# matching prose like "C#9" or "issue#5".
_ISSUE_IDENTIFIER_RE = re.compile(r"(?<![\w/])#([A-Za-z][A-Za-z0-9]*-[0-9]+)\b")

PREVIEW_LIMIT = 140

# Sentinel pushed onto the anchor-close stack when an unsafe link's opening
# <a> is dropped, so its matching </a> is dropped too (L5: no orphan close).
_DROP_A_CLOSE = chr(0)


@dataclass(frozen=True)
class RenderedBody:
    """The render output the service persists and parses mentions from."""

    html: str
    text: str
    mention_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)
    mention_names: tuple[str, ...] = field(default_factory=tuple)


def _renderer() -> MarkdownIt:
    md = MarkdownIt("default", {"html": False, "linkify": False, "typographer": False})
    original_validate = md.validateLink

    def _validate_link(url: str) -> bool:
        """Library gate + the mention / issue-link schemes (http(s)/mailto pass
        the library default; javascript:, data:, vbscript: are rejected there
        and again by the sanitizer)."""
        if MENTION_UUID_RE.match(url) or url.startswith(ISSUE_LINK_SCHEME):
            return True
        return original_validate(url)

    md.validateLink = _validate_link  # type: ignore[method-assign]
    return md


_MD = _renderer()

# -- sanitizer whitelist ------------------------------------------------------

_ALLOWED_TAGS = frozenset(
    {
        "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "blockquote", "pre", "code",
        "em", "strong", "del", "s", "a", "img",
        "table", "thead", "tbody", "tr", "th", "td",
        "span", "input",
    }
)
# Subtrees removed wholesale (content included) — never trust their bodies.
_DANGEROUS_TAGS = frozenset(
    {"script", "style", "iframe", "object", "embed", "form", "svg", "math", "template"}
)
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "code": frozenset({"class"}),
    "th": frozenset({"align"}),
    "td": frozenset({"align"}),
    "span": frozenset({"class", "data-member-id"}),
    "li": frozenset({"class"}),
    "input": frozenset({"type", "checked", "disabled"}),
}
_SAFE_URL_SCHEMES = ("http://", "https://", "mailto:")


def _safe_url(value: str, *, allow_relative: bool) -> bool:
    normalized = value.strip().lower()
    # Control chars / whitespace inside URLs are a classic bypass vector.
    if re.search(r"[\x00-\x20\x7f]", normalized):
        return False
    # L5: protocol-relative URLs (//evil.com) must NOT pass the relative check.
    if normalized.startswith("//"):
        return False
    if normalized.startswith(_SAFE_URL_SCHEMES):
        return True
    if allow_relative and (
        normalized.startswith("/") or normalized.startswith("./") or normalized.startswith("#")
    ):
        return True
    return False


def _linkify_issue_identifiers(markdown: str) -> str:
    """Rewrite ``#IDENTIFIER`` to mesh-issue links, skipping code regions."""
    out: list[str] = []
    pos = 0
    length = len(markdown)
    while pos < length:
        # Fenced code block: ``` ... ``` (greedy, DOTALL-style manual scan).
        if markdown.startswith("```", pos):
            end = markdown.find("```", pos + 3)
            end = length if end == -1 else end + 3
            out.append(markdown[pos:end])
            pos = end
            continue
        # Inline code span: ` ... ` (no newline inside).
        if markdown[pos] == "`":
            end = markdown.find("`", pos + 1)
            if end != -1:
                out.append(markdown[pos:end + 1])
                pos = end + 1
                continue
        match = _ISSUE_IDENTIFIER_RE.match(markdown, pos)
        if match:
            ident = match.group(1)
            out.append("[" + match.group(0) + "](" + ISSUE_LINK_SCHEME + ident + ")")
            pos = match.end()
            continue
        out.append(markdown[pos])
        pos += 1
    return "".join(out)


class _Sanitizer(HTMLParser):
    """Whitelist HTML sanitizer with mention- and issue-link rewriting.

    Unknown tags are dropped (children kept); dangerous tags drop their whole
    subtree; attribute values are re-escaped on output.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: dict[str, int] = {}
        # Maps each opened <a> (or rewritten span) to its closing tag; the
        # _DROP_A_CLOSE sentinel marks an <a> whose open tag was dropped.
        self._open_a_closes: list[str] = []
        self.mention_ids: list[uuid.UUID] = []

    def _skipping(self) -> bool:
        return bool(self._skip_depth)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping():
            if tag in _DANGEROUS_TAGS:
                self._skip_depth[tag] = self._skip_depth.get(tag, 0) + 1
            return
        if tag in _DANGEROUS_TAGS:
            self._skip_depth[tag] = 1
            return
        if tag not in _ALLOWED_TAGS:
            return  # drop the tag, keep children
        rendered = self._render_tag(tag, attrs)
        if rendered is not None:
            self._parts.append(rendered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping() or tag not in _ALLOWED_TAGS or tag in _DANGEROUS_TAGS:
            return
        rendered = self._render_tag(tag, attrs, self_closing=True)
        if rendered is not None:
            self._parts.append(rendered)

    def handle_endtag(self, tag: str) -> None:
        if tag in _DANGEROUS_TAGS and tag in self._skip_depth:
            depth = self._skip_depth[tag] - 1
            if depth <= 0:
                del self._skip_depth[tag]
            else:
                self._skip_depth[tag] = depth
            return
        if self._skipping() or tag not in _ALLOWED_TAGS:
            return
        if tag == "a":
            close = self._open_a_closes.pop() if self._open_a_closes else "</a>"
            if close != _DROP_A_CLOSE:
                self._parts.append(close)
            return
        self._parts.append("</" + tag + ">")

    def handle_data(self, data: str) -> None:
        if self._skipping():
            return
        self._parts.append(html.escape(data, quote=False))

    def _render_tag(
        self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool = False
    ) -> str | None:
        attr_map = {name: (value or "") for name, value in attrs}
        if tag == "a":
            href = attr_map.get("href", "")
            mention_match = MENTION_UUID_RE.match(href)
            if mention_match:
                member_id = uuid.UUID(mention_match.group(1))
                self.mention_ids.append(member_id)
                self._open_a_closes.append("</span>")
                return '<span class="mesh-mention" data-member-id="' + str(member_id) + '">'
            issue_match = _ISSUE_LINK_RE.match(href)
            if issue_match:
                # C6: render as a same-workspace issue link the front-end can
                # hydrate into a title+status card (data-issue-identifier).
                ident = html.escape(issue_match.group(1), quote=True)
                self._open_a_closes.append("</a>")
                return (
                    '<a class="mesh-issue-link" data-issue-identifier="' + ident
                    + '" href="/issues/by-identifier/' + ident + '">'
                )
            if not _safe_url(href, allow_relative=True):
                self._open_a_closes.append(_DROP_A_CLOSE)  # L5: drop orphan </a>
                return None
            self._open_a_closes.append("</a>")
            out = '<a href="' + html.escape(href, quote=True) + '"'
            title = attr_map.get("title")
            if title:
                out += ' title="' + html.escape(title, quote=True) + '"'
            return out + (" />" if self_closing else ">")
        if tag == "img":
            src = attr_map.get("src", "")
            if not _safe_url(src, allow_relative=True):
                return None
            out = '<img src="' + html.escape(src, quote=True) + '"'
            for name in ("alt", "title"):
                value = attr_map.get(name)
                if value:
                    out += " " + name + '="' + html.escape(value, quote=True) + '"'
            return out + " />"
        if tag == "input":
            # Task-list checkboxes only: disabled, never user-submittable.
            if attr_map.get("type") != "checkbox":
                return None
            checked = " checked" if "checked" in attr_map else ""
            return '<input type="checkbox" disabled' + checked + " />"
        if tag == "code":
            cls = attr_map.get("class", "")
            if cls.startswith("language-") and re.fullmatch(r"language-[\w+-]{1,32}", cls):
                return '<code class="' + html.escape(cls, quote=True) + '">'
            return "<code>"
        if tag == "span":
            if attr_map.get("class") == "mesh-mention":
                member_raw = attr_map.get("data-member-id", "")
                if MENTION_UUID_RE.match("mention://member/" + member_raw):
                    escaped = html.escape(member_raw, quote=True)
                    return '<span class="mesh-mention" data-member-id="' + escaped + '">'
            return None
        if tag == "li":
            if attr_map.get("class") == "task":
                return '<li class="task">'
            return "<li>"
        if tag in ("th", "td"):
            align = attr_map.get("align")
            if align in ("left", "center", "right"):
                return "<" + tag + ' align="' + align + '">'
            return "<" + tag + ">"
        return "<" + tag + ">"

    def output(self) -> str:
        return "".join(self._parts)


def sanitize_html(rendered: str) -> tuple[str, tuple[uuid.UUID, ...]]:
    """Whitelist-sanitize rendered HTML; returns (html, mention ids)."""
    sanitizer = _Sanitizer()
    sanitizer.feed(rendered)
    sanitizer.close()
    return sanitizer.output(), tuple(sanitizer.mention_ids)


_BLOCK_TAGS = frozenset(
    {"p", "li", "tr", "blockquote", "pre", "table", "thead", "tbody", "h1",
     "h2", "h3", "h4", "h5", "h6", "hr", "br"}
)


class _TextExtractor(HTMLParser):
    """Collect visible text from sanitized HTML (no script — already gone)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def extract_text(sanitized_html: str) -> str:
    """Plain-text projection of sanitized HTML (search / previews / email)."""
    extractor = _TextExtractor()
    extractor.feed(sanitized_html)
    extractor.close()
    return extractor.text()


def preview_of(text: str, limit: int = PREVIEW_LIMIT) -> str:
    """A bounded single-line preview for notification payloads."""
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _plain_mention_names(markdown: str) -> tuple[str, ...]:
    """@Name candidates from prose, excluding code and link syntax."""
    stripped = _FENCED_CODE_RE.sub(" ", markdown)
    stripped = _INLINE_CODE_RE.sub(" ", stripped)
    stripped = _MARKDOWN_LINK_RE.sub(" ", stripped)
    names: list[str] = []
    for match in _PLAIN_MENTION_RE.finditer(stripped):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return tuple(names)


def render_body(body_markdown: str) -> RenderedBody:
    """Render markdown → (sanitized html, plain text, mention ids, names)."""
    linkified = _linkify_issue_identifiers(body_markdown)
    rendered = _MD.render(linkified)
    rendered = _TASK_LIST_ITEM_RE.sub(
        lambda m: '<li class="task"><input type="checkbox" disabled'
        + (" checked" if m.group(2) in ("x", "X") else "")
        + "> ",
        rendered,
    )
    sanitized, mention_ids = sanitize_html(rendered)
    text = extract_text(sanitized)
    deduped_ids: list[uuid.UUID] = []
    for member_id in mention_ids:
        if member_id not in deduped_ids:
            deduped_ids.append(member_id)
    return RenderedBody(
        html=sanitized,
        text=text,
        mention_ids=tuple(deduped_ids),
        mention_names=_plain_mention_names(body_markdown),
    )
