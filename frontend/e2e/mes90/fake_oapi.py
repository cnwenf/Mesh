"""Controlled DingTalk credential-proof peer for the MES-90 browser stack.

The browser test generates ``dingapp<SUFFIX>`` and the matching
``MES90-<SUFFIX>-DingTalk-Secret!7``. Only that exact relation is accepted;
arbitrary synthetic pairs are rejected. The service is compose-internal and
never logs the presented secret.
"""

from __future__ import annotations

import hmac
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

_APP_KEY = re.compile(r"dingapp([a-z0-9]+)\Z")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: object) -> None:
        # Request targets contain appsecret; never let the stdlib access log
        # reflect them into compose logs.
        return

    def _reply(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._reply(200, {"ok": True})
            return
        if parsed.path != "/gettoken":
            self._reply(404, {"errcode": 404, "errmsg": "not found"})
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        app_key = (query.get("appkey") or [""])[0]
        app_secret = (query.get("appsecret") or [""])[0]
        match = _APP_KEY.fullmatch(app_key)
        expected = f"MES90-{match.group(1)}-DingTalk-Secret!7" if match else ""
        if expected and hmac.compare_digest(app_secret, expected):
            self._reply(200, {"errcode": 0, "access_token": "mes90-proof-token"})
            return
        self._reply(200, {"errcode": 40089, "errmsg": "invalid app credentials"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
