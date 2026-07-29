"""MeshClient — auth, single-flight refresh, retries, transport safety (C5/C21/C26)."""

from __future__ import annotations

import threading

import httpx
import pytest
import respx

from meshcli.config import CredentialEntry
from meshcli.errors import EXIT_AUTH, EXIT_GENERIC, CliError
from meshcli.http import ClientOptions, MeshClient

BASE = "https://mesh.test"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """No real backoff waits in tests."""
    import meshcli.http as http_mod

    monkeypatch.setattr(http_mod.time, "sleep", lambda _s: None)


class Store:
    """In-memory credential store standing in for the YAML files."""

    def __init__(self, entry: CredentialEntry | None) -> None:
        self.entry = entry
        self.saves: list[CredentialEntry] = []
        self.clears = 0

    def loader(self) -> CredentialEntry | None:
        return self.entry

    def saver(self, entry: CredentialEntry) -> None:
        self.saves.append(entry)
        self.entry = entry

    def clearer(self) -> None:
        self.clears += 1
        self.entry = None


def _client(store: Store, **opts) -> MeshClient:
    options = ClientOptions(base_url=BASE, **opts)
    return MeshClient(
        options,
        credential_loader=store.loader,
        credential_saver=store.saver,
        credential_clearer=store.clearer,
    )


PAT = CredentialEntry(kind="pat", token="mesh_pat_abc123", prefix="mesh_pat_abc")
DEVICE = CredentialEntry(
    kind="device_session",
    token="old-access-jwt",
    refresh_token="mesh_rft_refresh1",
    scopes=["issue:read"],
)


class TestBearerInjection:
    @respx.mock
    def test_pat_sent_as_bearer(self):
        route = respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "u"}})
        )
        store = Store(PAT)
        with _client(store) as client:
            response = client.request("GET", "/api/v1/me")
        assert response.status_code == 200
        assert route.calls[0].request.headers["Authorization"] == "Bearer mesh_pat_abc123"

    @respx.mock
    def test_idempotency_and_if_match_forwarded(self):
        route = respx.post(f"{BASE}/api/v1/x").mock(
            return_value=httpx.Response(201, json={"data": {}})
        )
        with _client(Store(PAT)) as client:
            client.request(
                "POST", "/api/v1/x", json={}, idempotency_key="k1", if_match="v7"
            )
        headers = route.calls[0].request.headers
        assert headers["Idempotency-Key"] == "k1"
        assert headers["If-Match"] == "v7"


class TestAuthFailures:
    @respx.mock
    def test_pat_401_clears_local_and_exits_2(self):
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "revoked"}}
            )
        )
        store = Store(PAT)
        with _client(store) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH
        assert store.clears == 1  # dead PAT purged locally (§5.3)
        assert "mesh auth login" in (exc.value.hint or "")

    @respx.mock
    def test_403_scope_hint(self):
        respx.post(f"{BASE}/api/v1/x").mock(
            return_value=httpx.Response(
                403,
                json={
                    "error": {
                        "code": "forbidden",
                        "message": "no",
                        "details": {"required_scope": "issue:write"},
                    }
                },
            )
        )
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.request("POST", "/api/v1/x", json={})
        assert exc.value.exit_code == EXIT_AUTH
        assert "issue:write" in (exc.value.hint or "")


class TestSilentRefresh:
    @respx.mock
    def test_device_401_refreshes_and_retries(self):
        me = respx.get(f"{BASE}/api/v1/me")
        me.side_effect = [
            httpx.Response(401, json={"error": {"code": "unauthorized", "message": "expired"}}),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
        respx.post(f"{BASE}/api/v1/auth/refresh").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"access_token": "new-access", "refresh_token": "mesh_rft_new"}},
            )
        )
        store = Store(DEVICE)
        with _client(store) as client:
            response = client.request("GET", "/api/v1/me")
        assert response.status_code == 200
        # Winner refresh persisted.
        assert store.entry is not None
        assert store.entry.token == "new-access"
        assert store.entry.refresh_token == "mesh_rft_new"
        # The retry carried the NEW access token.
        assert me.calls[1].request.headers["Authorization"] == "Bearer new-access"

    @respx.mock
    def test_grace_loser_adopts_access_only(self):
        """Refresh response with access ONLY (grace path) — no refresh field;
        the client adopts the fresh access, keeps the existing refresh."""
        me = respx.get(f"{BASE}/api/v1/me")
        me.side_effect = [
            httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}}),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
        respx.post(f"{BASE}/api/v1/auth/refresh").mock(
            return_value=httpx.Response(200, json={"data": {"access_token": "grace-access"}})
        )
        store = Store(DEVICE)
        with _client(store) as client:
            response = client.request("GET", "/api/v1/me")
        assert response.status_code == 200
        assert store.entry.token == "grace-access"
        assert store.entry.refresh_token == "mesh_rft_refresh1"  # unchanged

    @respx.mock
    def test_pat_never_refreshes(self):
        """A PAT 401 goes straight to exit 2 — no refresh attempt."""
        refresh_route = respx.post(f"{BASE}/api/v1/auth/refresh")
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}})
        )
        store = Store(PAT)
        with _client(store) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH
        assert refresh_route.call_count == 0

    @respx.mock
    def test_single_flight_one_refresh_for_concurrent_401s(self):
        """Two threads hit 401 concurrently on the SAME stored credential →
        exactly ONE refresh request (the second thread re-reads the rotated
        store under the single-flight lock and retries with the new token)."""
        call_count = {"n": 0}
        lock = threading.Lock()

        def refresh_side_effect(request):
            with lock:
                call_count["n"] += 1
            return httpx.Response(
                200, json={"data": {"access_token": "fresh", "refresh_token": "mesh_rft_2"}}
            )

        def me_side_effect(request):
            # The OLD access is dead; anything newer (post-rotation) succeeds —
            # deterministic regardless of thread interleaving.
            if request.headers.get("Authorization") == "Bearer old-access-jwt":
                return httpx.Response(
                    401, json={"error": {"code": "unauthorized", "message": "expired"}}
                )
            return httpx.Response(200, json={"data": {"ok": True}})

        respx.post(f"{BASE}/api/v1/auth/refresh").mock(side_effect=refresh_side_effect)
        respx.get(f"{BASE}/api/v1/me").mock(side_effect=me_side_effect)
        store = Store(DEVICE)
        client = _client(store)
        results: list[int] = []
        results_lock = threading.Lock()

        def worker():
            response = client.request("GET", "/api/v1/me")
            with results_lock:
                results.append(response.status_code)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        client.close()
        assert results == [200, 200]  # both requests converge, no mis-logout
        assert call_count["n"] == 1  # single-flight held — one rotation
        assert store.entry is not None and store.entry.token == "fresh"


class TestRetries:
    @respx.mock
    def test_429_retried_then_success(self):
        route = respx.get(f"{BASE}/api/v1/me")
        route.side_effect = [
            httpx.Response(429, json={"error": {"code": "rate_limited", "message": "slow"}}, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"data": {}}),
        ]
        with _client(Store(PAT)) as client:
            response = client.request("GET", "/api/v1/me")
        assert response.status_code == 200
        assert route.call_count == 2

    @respx.mock
    def test_429_exhaustion_exits_1_not_2(self):
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                429, json={"error": {"code": "rate_limited", "message": "slow"}}, headers={"Retry-After": "1"}
            )
        )
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_GENERIC  # retries exhausted → 1

    @respx.mock
    def test_5xx_retried_then_success(self):
        route = respx.get(f"{BASE}/api/v1/me")
        route.side_effect = [
            httpx.Response(503, json={"error": {"code": "internal_error", "message": "x"}}),
            httpx.Response(200, json={"data": {}}),
        ]
        with _client(Store(PAT)) as client:
            response = client.request("GET", "/api/v1/me")
        assert response.status_code == 200

    @respx.mock
    def test_5xx_exhaustion_exits_1(self):
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(500, json={"error": {"code": "internal_error", "message": "x"}})
        )
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_GENERIC


class TestTransportSafety:
    def test_plaintext_http_refused_without_insecure(self):
        with pytest.raises(CliError) as exc:
            MeshClient(
                ClientOptions(base_url="http://127.0.0.1:8000"),
                credential_loader=lambda: None,
            )
        assert exc.value.exit_code == EXIT_GENERIC
        assert "--insecure" in (exc.value.hint or "")

    def test_plaintext_http_allowed_with_insecure_warns(self, capsys):
        client = MeshClient(
            ClientOptions(base_url="http://127.0.0.1:8000", insecure=True),
            credential_loader=lambda: None,
        )
        client.close()
        assert "--insecure" in capsys.readouterr().err

    @respx.mock
    def test_deprecation_header_warns(self, capsys):
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {}}, headers={"Deprecation": "true"})
        )
        with _client(Store(PAT)) as client:
            client.request("GET", "/api/v1/me")
        assert "deprecated" in capsys.readouterr().err


class TestVerboseRedaction:
    @respx.mock
    def test_verbose_prints_only_method_path_status(self, capsys):
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(200, json={"data": {"secret": "s"}})
        )
        with _client(Store(PAT), verbose=True) as client:
            client.request("GET", "/api/v1/me")
        err = capsys.readouterr().err
        assert "GET /api/v1/me" in err and "200" in err
        # The token and response body NEVER appear in diagnostics (C21/§6.16).
        assert "mesh_pat_abc123" not in err
        assert "secret" not in err
