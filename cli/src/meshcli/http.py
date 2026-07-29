"""HTTP client for the Mesh API (cli.md C5/C21/C26, §3.5, §5.3).

Responsibilities:
- Bearer injection from the credential store (PAT, or the device session's
  short-lived access JWT);
- 401 on a device session → SILENT refresh (§3.8-aware) with single-flight:
  one in-flight refresh per credential store; losers re-read the file after
  the lock releases (another process may have written the winner credential)
  and retry — never a double rotation, never a mis-logout;
- 429 → back off per ``Retry-After``; 5xx/network → bounded retries; retries
  exhausted → exit 1 (never 2 — 2 is auth-exclusive);
- PAT 401 → the token is revoked/expired: clear the local credential, exit 2;
- transport fail-closed: plaintext ``http://`` refused unless the
  per-invocation ``--insecure`` flag is set (warned on stderr every time,
  never persisted); custom CA via ``--ca-cert`` > config ``tls.ca_cert`` >
  ``SSL_CERT_FILE``; proxies honored from HTTPS_PROXY/HTTP_PROXY + NO_PROXY
  (httpx native; authenticated proxies via env userinfo only);
- ``Deprecation``/``Sunset`` response headers → stderr upgrade hint;
- verbose mode prints ONLY method/path/status/elapsed (Authorization is
  always ``Bearer [REDACTED]`` — no bodies, no other headers).
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Self

import httpx

from meshcli.config import CredentialEntry
from meshcli.errors import EXIT_AUTH, EXIT_GENERIC, CliError, from_api_error

REDACTED = "Bearer [REDACTED]"

MAX_RATE_RETRIES = 3
MAX_SERVER_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.0

USER_AGENT = "mesh-cli/0.1.0"


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class ClientOptions:
    base_url: str
    verbose: bool = False
    insecure: bool = False
    ca_cert: str | None = None  # resolved: flag > config > SSL_CERT_FILE
    timeout: float = 30.0


class MeshClient:
    """A thin, contract-faithful client over ``/api/v1``."""

    def __init__(
        self,
        options: ClientOptions,
        *,
        credential_loader: Callable[[], CredentialEntry | None],
        credential_saver: Callable[[CredentialEntry], None] | None = None,
        credential_clearer: Callable[[], None] | None = None,
        refresh_requester: Callable[[str], httpx.Response] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._options = options
        self._load_credential = credential_loader
        self._save_credential = credential_saver
        self._clear_credential = credential_clearer
        self._refresh_requester = refresh_requester
        self._refresh_lock = threading.Lock()  # single-flight per store (C5)
        self._check_transport_safety()
        verify: str | bool = options.ca_cert if options.ca_cert else True
        if options.insecure:
            verify = False
        self._http = httpx.Client(
            base_url=options.base_url.rstrip("/"),
            timeout=options.timeout,
            verify=verify,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    # -- lifecycle ---------------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport safety (cli.md §5.3) -------------------------------------------

    def _check_transport_safety(self) -> None:
        url = self._options.base_url
        if url.startswith("http://") and not self._options.insecure:
            raise CliError(
                f"refusing plaintext API base {url}",
                exit_code=EXIT_GENERIC,
                hint=(
                    "Use an https:// endpoint. For a deliberate loopback/test "
                    "exception pass --insecure for this invocation only."
                ),
            )
        if self._options.insecure:
            _stderr(
                "warning: --insecure disables TLS certificate verification for "
                "this invocation only (never persisted)."
            )

    # -- the request pipeline ------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        if_match: str | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        """Send one API request with auth, retries, refresh, and diagnostics."""
        extra = dict(headers or {})
        if idempotency_key:
            extra["Idempotency-Key"] = idempotency_key
        if if_match:
            extra["If-Match"] = if_match

        retried_after_refresh = False
        rate_retries = 0
        server_retries = 0
        while True:
            used_token = self._current_token()
            response = self._send(method, path, json=json, params=params, headers=extra, stream=stream)
            self._note_deprecation(response)

            if response.status_code == 401 and not retried_after_refresh:
                retried_after_refresh = True
                if self._try_refresh(failed_token=used_token):
                    continue  # retry once with the fresh credential
                self._handle_auth_failure(response)

            if response.status_code == 429 and rate_retries < MAX_RATE_RETRIES:
                rate_retries += 1
                self._backoff(response, kind="rate limit")
                continue
            if response.status_code >= 500 and server_retries < MAX_SERVER_RETRIES:
                server_retries += 1
                time.sleep(RETRY_BACKOFF_SECONDS * server_retries)
                continue

            if response.status_code == 401:
                self._handle_auth_failure(response)
            if 400 <= response.status_code < 600:
                raise from_api_error(response.status_code, self._error_envelope(response))
            return response

    def stream_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Open a streaming response (SSE) — the caller iterates lines."""
        response = self._send(
            method, path, json=None, params=params, headers=headers, stream=True
        )
        self._note_deprecation(response)
        if 400 <= response.status_code < 600:
            body = response.read()
            response.close()
            raise from_api_error(response.status_code, self._envelope_from_bytes(body, response))
        return response

    # -- internals -----------------------------------------------------------------

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any | None,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        stream: bool,
    ) -> httpx.Response:
        final_headers = dict(headers or {})
        credential = self._load_credential()
        if credential is not None and credential.token:
            final_headers["Authorization"] = f"Bearer {credential.token}"
        started = time.monotonic()
        try:
            if stream:
                request = self._http.build_request(
                    method, path, json=json, params=params, headers=final_headers
                )
                response = self._http.send(request, stream=True)
            else:
                response = self._http.request(
                    method, path, json=json, params=params, headers=final_headers
                )
        except httpx.TransportError as exc:
            raise CliError(
                f"network error talking to {self._options.base_url}: {exc.__class__.__name__}",
                exit_code=EXIT_GENERIC,
                hint="Check connectivity, proxy settings (HTTPS_PROXY/NO_PROXY) and CA configuration.",
            ) from exc
        elapsed_ms = (time.monotonic() - started) * 1000
        if self._options.verbose:
            # Diagnostics surface: method/path/status/elapsed ONLY. The
            # Authorization header is NEVER printed in full (C21/§6.16).
            _stderr(f"* {method} {path} → {response.status_code} ({elapsed_ms:.0f}ms)")
        return response

    def _current_token(self) -> str | None:
        credential = self._load_credential()
        return credential.token if credential is not None else None

    def _try_refresh(self, *, failed_token: str | None) -> bool:
        """Single-flight silent refresh for device sessions (C5/§3.8).

        Returns True when a usable credential is now in effect (this process
        refreshed, or another process did and the re-read file has it). The
        comparison is against the token that RECEIVED the 401 — if the store
        already holds a different token, another thread/process won the
        rotation and we just retry with it (no second rotation, ever)."""
        credential = self._load_credential()
        if credential is None or credential.kind != "device_session":
            return False
        if not credential.refresh_token:
            return False

        refresh_requester = self._refresh_requester or self._default_refresh_request
        with self._refresh_lock:
            # Re-read under the lock: if the store no longer holds the token
            # that failed, the rotation already happened elsewhere.
            current = self._load_credential()
            if current is not None and current.token and current.token != failed_token:
                return True
            try:
                response = refresh_requester(credential.refresh_token)
            except httpx.TransportError:
                return False
            if response.status_code != 200:
                return False
            data = response.json().get("data", {})
            new_access = data.get("access_token")
            if not new_access:
                return False
            new_refresh = data.get("refresh_token")
            # Winner: the response carries the new refresh (persist it).
            # Grace loser: access only — the winner (possibly another process)
            # owns the refresh; re-read the file for the freshest credential.
            if new_refresh and self._save_credential is not None:
                self._save_credential(
                    CredentialEntry(
                        kind="device_session",
                        token=new_access,
                        refresh_token=new_refresh,
                        scopes=credential.scopes,
                        prefix=credential.prefix,
                        workspace=credential.workspace,
                    )
                )
                return True
            reread = self._load_credential()
            if reread is not None and reread.token and reread.token != credential.token:
                return True
            # Grace access from the server: adopt it (same session, fresh exp).
            if new_access and self._save_credential is not None:
                self._save_credential(
                    CredentialEntry(
                        kind="device_session",
                        token=new_access,
                        refresh_token=credential.refresh_token,
                        scopes=credential.scopes,
                        prefix=credential.prefix,
                        workspace=credential.workspace,
                    )
                )
                return True
            return False

    def _default_refresh_request(self, refresh_token: str) -> httpx.Response:
        return self._http.post(
            "/api/v1/auth/refresh", headers={"Authorization": f"Bearer {refresh_token}"}
        )

    def _handle_auth_failure(self, response: httpx.Response) -> None:
        """401/403 terminal handling (cli.md §4.3 / §5.3)."""
        envelope = self._error_envelope(response)
        credential = self._load_credential()
        if response.status_code == 401 and credential is not None and credential.kind == "pat":
            # Revoked/expired PAT: purge the dead credential locally.
            if self._clear_credential is not None:
                self._clear_credential()
            raise CliError(
                "your token is invalid, expired or revoked",
                exit_code=EXIT_AUTH,
                hint="Run `mesh auth login` to re-authenticate.",
                envelope=envelope,
            )
        raise from_api_error(response.status_code, envelope)

    def _backoff(self, response: httpx.Response, *, kind: str) -> None:
        retry_after = response.headers.get("Retry-After")
        try:
            wait = max(0.5, float(retry_after)) if retry_after else RETRY_BACKOFF_SECONDS
        except ValueError:
            wait = RETRY_BACKOFF_SECONDS
        _stderr(f"* {kind}: retrying in {wait:.0f}s")
        time.sleep(min(wait, 30.0))

    def _note_deprecation(self, response: httpx.Response) -> None:
        sunset = response.headers.get("Sunset")
        if response.headers.get("Deprecation") or sunset:
            detail = f" (sunset: {sunset})" if sunset else ""
            _stderr(
                f"warning: this API version is deprecated{detail} — upgrade "
                "your mesh CLI to the latest release."
            )

    @staticmethod
    def _error_envelope(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
            if isinstance(body, dict) and "error" in body:
                return body
        except ValueError:
            pass
        return {
            "error": {
                "code": "http_error",
                "message": f"request failed with HTTP {response.status_code}",
            }
        }

    @staticmethod
    def _envelope_from_bytes(body: bytes, response: httpx.Response) -> dict[str, Any]:
        try:
            import json as jsonlib

            parsed = jsonlib.loads(body.decode("utf-8"))
            if isinstance(parsed, dict) and "error" in parsed:
                return parsed
        except (ValueError, UnicodeDecodeError):
            pass
        return {
            "error": {
                "code": "http_error",
                "message": f"request failed with HTTP {response.status_code}",
            }
        }
