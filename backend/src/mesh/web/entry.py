"""Personalized HTML entry — theme.md §2.3 ① (precise injection).

Serves the SPA shell for HTML document navigations. When the request carries
the HttpOnly ``mesh_session`` cookie (auth.md §5.5 web session form) or the
route itself identifies a workspace context (``/w/{slug}/…``, ``/invite/{token}``),
the entry resolves the requester's negotiation chain (mesh.web.appearance) and
inlines the non-sensitive binary ``window.__MESH_APPEARANCE__ = {"mode": …}``
before ``</head>`` so the first frame never flashes a wrong theme.

Security contract (theme.md §5.3):
- ``data-theme``/``__MESH_APPEARANCE__.mode`` converge to the binary
  ``light|dark`` — never user-controlled beyond the whitelist;
- every inline script (injected data + FOUC resolver) is released by a
  per-request CSP nonce — ``script-src`` never allows ``unsafe-inline``;
- personalized responses are ``Cache-Control: private, no-store`` so shared
  caches never store a session-derived shell; the anonymous shell is
  byte-stable (no nonce) and served with a sha256-hashed FOUC script so it
  remains public-cacheable;
- the workspace identity comes from the URL path segment only — never from
  query params or headers; the injection payload carries no workspace
  identifier/name (no enumeration surface).

Read-only against the session model; degrades to a static shell (or 404 when
the built frontend is absent) — never breaks the API.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from mesh.web.appearance import resolve_entry_appearance

SESSION_COOKIE_NAME = "mesh_session"

# Namespaces the entry must never shadow. The router is mounted last so
# registered API routes win; this guard is defence in depth (unknown
# /api/… paths must 404 as JSON-shaped misses, not fall into the shell).
_GUARDED_PREFIXES = ("/api", "/ws", "/assets", "/uploads", "/favicon", "/_debug")
_INVITE_PATH_TOKEN = re.compile(r"^/invite/([^/?#]+)")
_FIRST_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_FIRST_PLAIN_SCRIPT = re.compile(r"<script>")
_FIRST_SCRIPT_BODY = re.compile(r"<script>(.*?)</script>", re.DOTALL)

_CSP_BASE = (
    "default-src 'self'; "
    "script-src 'self' {script_allow}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "base-uri 'self'; object-src 'none'; frame-ancestors 'none'"
)

# Content cache keyed by (index path, mtime_ns) — the built shell is static;
# tests swap dist dirs, so key on the file identity, not process lifetime.
_shell_cache: dict[tuple[str, int], tuple[str, str | None]] = {}


def _load_shell(dist_dir: str) -> tuple[str, str | None] | None:
    """Return (index_html, fouc_sha256_b64) or None when the build is absent."""
    index_path = Path(dist_dir) / "index.html"
    try:
        stat = index_path.stat()
    except OSError:
        return None
    key = (str(index_path), stat.st_mtime_ns)
    cached = _shell_cache.get(key)
    if cached is None:
        html = index_path.read_text(encoding="utf-8")
        match = _FIRST_SCRIPT_BODY.search(html)
        script_hash = None
        if match is not None:
            digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
            script_hash = base64.standard_b64encode(digest).decode("ascii")
        cached = (html, script_hash)
        _shell_cache.clear()
        _shell_cache[key] = cached
    return cached


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return (
        "text/html" in accept
        or "*/*" in accept
        or request.headers.get("sec-fetch-dest") == "document"
    )


def build_html_entry_router() -> APIRouter:
    """Catch-all HTML entry. Mount AFTER every API router."""
    router = APIRouter(tags=["web-entry"], include_in_schema=False)

    @router.get("/{path:path}")
    async def html_entry(request: Request) -> Response:
        full_path = request.url.path
        if full_path.startswith(_GUARDED_PREFIXES):
            return PlainTextResponse("Not Found", status_code=404)
        if not _wants_html(request):
            return PlainTextResponse("Not Found", status_code=404)

        loaded = _load_shell(request.app.state.settings.frontend_dist_dir)
        if loaded is None:
            # Built frontend absent (dev backend-only runs, startup race):
            # the API keeps working; the shell is simply not served here.
            return PlainTextResponse("Not Found", status_code=404)
        template, script_hash = loaded

        # URL-derived invite token (path segment; query fallback for the
        # documented /invite?token= form). Never from headers.
        invite_token: str | None = None
        invite_match = _INVITE_PATH_TOKEN.match(full_path)
        if invite_match is not None:
            invite_token = invite_match.group(1)
        elif full_path.startswith("/invite"):
            invite_token = request.query_params.get("token")

        invitation_service = request.app.state.invitation_service

        async def invite_default_theme(token: str) -> str | None:
            preview = await invitation_service.preview_invitation(token=token)
            if not preview.get("valid"):
                return None
            return (preview.get("appearance") or {}).get("default_theme")

        resolution = await resolve_entry_appearance(
            request.app.state.session_factory,
            cookie_value=request.cookies.get(SESSION_COOKIE_NAME),
            path=full_path,
            invite_default_theme=invite_default_theme if invite_token else None,
            invite_token=invite_token,
        )

        headers = {"vary": "Accept, Cookie"}
        body = template
        if resolution.mode is not None:
            nonce = secrets.token_urlsafe(16)
            payload = json.dumps({"mode": resolution.mode}, separators=(",", ":"))
            injection = (
                f'<script nonce="{nonce}">'
                f"window.__MESH_APPEARANCE__ = {payload};"
                "</script>"
            )
            body = _FIRST_PLAIN_SCRIPT.sub(f'<script nonce="{nonce}">', body, count=1)
            body = _FIRST_HEAD_CLOSE.sub(injection + "\n</head>", body, count=1)
            headers["content-security-policy"] = _CSP_BASE.format(
                script_allow=f"'nonce-{nonce}'"
            )
        else:
            # Byte-stable anonymous shell: release the static FOUC script via
            # its sha256 hash so the document stays public-cacheable.
            script_allow = f"'sha256-{script_hash}'" if script_hash else ""
            headers["content-security-policy"] = _CSP_BASE.format(
                script_allow=script_allow
            )

        if resolution.personalized:
            # auth.md §3 cache boundary: a session-bearing request is never
            # share-cacheable, even when nothing was injected.
            headers["cache-control"] = "private, no-store"
        else:
            headers["cache-control"] = "public, max-age=300"

        return HTMLResponse(body, headers=headers)

    return router


__all__ = ["SESSION_COOKIE_NAME", "build_html_entry_router"]
