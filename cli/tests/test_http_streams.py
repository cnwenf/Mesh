"""MeshClient streaming + remaining refresh/backoff branches (cli.md C12/C5):
``stream_request`` (Deprecation/Sunset warning, error envelopes), invalid
Retry-After, non-JSON error bodies, and the silent-refresh edge paths that
test_http.py does not exercise."""

from __future__ import annotations

import httpx
import pytest
import respx

from meshcli.config import CredentialEntry
from meshcli.errors import EXIT_AUTH, EXIT_GENERIC, EXIT_VALIDATION, CliError
from meshcli.http import ClientOptions, MeshClient

BASE = "https://mesh.test"


class Store:
    """In-memory credential store standing in for the YAML files."""

    def __init__(self, entry: CredentialEntry | None) -> None:
        self.entry = entry
        self.saves: list[CredentialEntry] = []

    def loader(self) -> CredentialEntry | None:
        return self.entry

    def saver(self, entry: CredentialEntry) -> None:
        self.saves.append(entry)
        self.entry = entry


class SeqStore:
    """Programmable loader: returns queued tokens, then a stable tail value."""

    def __init__(self, sequence: list[str | None], tail: str | None) -> None:
        self.sequence = list(sequence)
        self.tail = tail
        self.tokens: list[str | None] = []

    def loader(self) -> CredentialEntry | None:
        token = self.sequence.pop(0) if self.sequence else self.tail
        self.tokens.append(token)
        if token is None:
            return None
        return CredentialEntry(
            kind="device_session", token=token, refresh_token="mesh_rft_seq"
        )

    def saver(self, entry: CredentialEntry) -> None:
        # The rotation-edge tests drive token changes through the loader
        # sequence; any save is recorded but not asserted on.
        self.tail = entry.token


PAT = CredentialEntry(kind="pat", token="mesh_pat_abc123", prefix="mesh_pat_abc")
DEVICE = CredentialEntry(
    kind="device_session", token="old-access-jwt", refresh_token="mesh_rft_refresh1"
)


def _client(store, *, refresh_requester=None, with_saver: bool = True) -> MeshClient:
    return MeshClient(
        ClientOptions(base_url=BASE),
        credential_loader=store.loader,
        credential_saver=store.saver if with_saver else None,
        refresh_requester=refresh_requester,
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import meshcli.http as http_mod

    monkeypatch.setattr(http_mod.time, "sleep", lambda _seconds: None)


class TestStreamRequestDeprecationUnit:
    """Direct unit coverage of the shared Deprecation/Sunset warning emitter
    (also exercised end-to-end through stream_request in TestStreamRequest)."""

    def test_note_deprecation_warns_with_sunset(self, capsys):
        # Arrange
        response = httpx.Response(
            200, headers={"Deprecation": "true", "Sunset": "2026-12-31"}
        )
        client = _client(Store(PAT))
        # Act
        client._note_deprecation(response)
        client.close()
        # Assert — the upgrade warning carries the sunset date.
        err = capsys.readouterr().err
        assert "deprecated" in err
        assert "sunset: 2026-12-31" in err

    def test_note_deprecation_silent_without_headers(self, capsys):
        # Arrange
        client = _client(Store(PAT))
        # Act
        client._note_deprecation(httpx.Response(200))
        client.close()
        # Assert
        assert capsys.readouterr().err == ""


class TestStreamRequest:
    @respx.mock
    def test_stream_returns_iterable_response(self):
        # Arrange
        respx.get(f"{BASE}/stream").mock(
            return_value=httpx.Response(200, content="line1\nline2\n")
        )
        # Act
        with _client(Store(PAT)) as client:
            response = client.stream_request("GET", "/stream")
            lines = list(response.iter_lines())
            response.close()
        # Assert
        assert lines == ["line1", "line2"]

    @respx.mock
    def test_stream_deprecation_and_sunset_headers_warn(self, capsys):
        # Arrange
        respx.get(f"{BASE}/stream").mock(
            return_value=httpx.Response(
                200, content="x", headers={"Deprecation": "true", "Sunset": "2026-12-31"}
            )
        )
        # Act
        with _client(Store(PAT)) as client:
            response = client.stream_request("GET", "/stream")
            response.close()
        # Assert — the upgrade warning carries the sunset date.
        err = capsys.readouterr().err
        assert "deprecated" in err
        assert "sunset: 2026-12-31" in err

    @respx.mock
    def test_stream_404_json_error_raises_with_envelope(self):
        # Arrange
        envelope = {"error": {"code": "not_found", "message": "gone"}}
        respx.get(f"{BASE}/stream").mock(
            return_value=httpx.Response(404, json=envelope)
        )
        # Act / Assert
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.stream_request("GET", "/stream")
        assert exc.value.exit_code == EXIT_VALIDATION
        assert exc.value.envelope == envelope

    @respx.mock
    def test_stream_500_non_json_body_falls_back_to_generic_envelope(self):
        # Arrange
        respx.get(f"{BASE}/stream").mock(
            return_value=httpx.Response(500, content=b"upstream exploded")
        )
        # Act / Assert
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.stream_request("GET", "/stream")
        assert exc.value.exit_code == EXIT_GENERIC
        assert "HTTP 500" in exc.value.message

    @respx.mock
    def test_stream_transport_error_exits_1(self):
        # Arrange
        respx.get(f"{BASE}/stream").mock(side_effect=httpx.ConnectError("refused"))
        # Act / Assert
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.stream_request("GET", "/stream")
        assert exc.value.exit_code == EXIT_GENERIC
        assert "network error" in exc.value.message


class TestDeprecationWarning:
    """Direct coverage of the shared Deprecation/Sunset warning emitter —
    the request() path covers it end-to-end in test_commands_auth / test_http,
    and TestStreamRequest covers the streaming path."""

    def test_deprecation_header_warns_on_stderr(self, capsys):
        # Arrange
        response = httpx.Response(200, headers={"Deprecation": "true"})
        # Act
        _client(Store(PAT))._note_deprecation(response)
        # Assert
        err = capsys.readouterr().err
        assert "deprecated" in err
        assert "upgrade" in err

    def test_sunset_header_adds_the_date(self, capsys):
        # Arrange
        response = httpx.Response(
            200, headers={"Deprecation": "true", "Sunset": "2026-12-31"}
        )
        # Act
        _client(Store(PAT))._note_deprecation(response)
        # Assert
        assert "sunset: 2026-12-31" in capsys.readouterr().err

    def test_no_headers_no_warning(self, capsys):
        # Arrange / Act
        _client(Store(PAT))._note_deprecation(httpx.Response(200))
        # Assert
        assert capsys.readouterr().err == ""


class TestBackoffAndEnvelopes:
    @respx.mock
    def test_invalid_retry_after_falls_back_to_default_wait(self, capsys):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/me")
        route.side_effect = [
            httpx.Response(
                429,
                json={"error": {"code": "rate_limited", "message": "slow"}},
                headers={"Retry-After": "not-a-number"},
            ),
            httpx.Response(200, json={"data": {}}),
        ]
        # Act
        with _client(Store(PAT)) as client:
            response = client.request("GET", "/api/v1/me")
        # Assert — default 1s backoff, then success.
        assert response.status_code == 200
        assert route.call_count == 2
        assert "retrying in 1s" in capsys.readouterr().err

    @respx.mock
    def test_non_json_500_body_produces_generic_envelope_after_retries(self):
        # Arrange
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(500, content=b"<html>error</html>")
        )
        # Act / Assert
        with _client(Store(PAT)) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_GENERIC
        assert exc.value.message == "request failed with HTTP 500"
        assert exc.value.envelope == {
            "error": {"code": "http_error", "message": "request failed with HTTP 500"}
        }


class TestRefreshEdges:
    @respx.mock
    def test_second_401_after_successful_refresh_exits_2(self):
        # Arrange — refresh succeeds but the retry STILL gets a 401.
        me = respx.get(f"{BASE}/api/v1/me")
        me.side_effect = [
            httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}}),
            httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}}),
        ]
        respx.post(f"{BASE}/api/v1/auth/refresh").mock(
            return_value=httpx.Response(
                200, json={"data": {"access_token": "new", "refresh_token": "mesh_rft_2"}}
            )
        )
        # Act / Assert — no infinite loop; terminal auth failure.
        with _client(Store(DEVICE)) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH
        assert me.call_count == 2

    @respx.mock
    def test_device_without_refresh_token_never_refreshes(self):
        # Arrange
        refresh_route = respx.post(f"{BASE}/api/v1/auth/refresh")
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )
        stranded = CredentialEntry(kind="device_session", token="access-only")
        # Act / Assert
        with _client(Store(stranded)) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH
        assert refresh_route.call_count == 0

    @respx.mock
    def test_refresh_transport_error_exits_2(self):
        # Arrange
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )

        def unreachable(_refresh_token):
            raise httpx.ConnectError("auth host down")

        # Act / Assert
        with (
            _client(Store(DEVICE), refresh_requester=unreachable) as client,
            pytest.raises(CliError) as exc,
        ):
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH

    @respx.mock
    def test_refresh_non_200_exits_2(self):
        # Arrange
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )
        respx.post(f"{BASE}/api/v1/auth/refresh").mock(
            return_value=httpx.Response(
                403, json={"error": {"code": "forbidden", "message": "rotated"}}
            )
        )
        # Act / Assert
        with _client(Store(DEVICE)) as client, pytest.raises(CliError) as exc:
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH

    @respx.mock
    def test_refresh_without_access_token_exits_2(self):
        # Arrange
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )

        def empty_body(_refresh_token):
            return httpx.Response(200, json={"data": {}})

        # Act / Assert
        with (
            _client(Store(DEVICE), refresh_requester=empty_body) as client,
            pytest.raises(CliError) as exc,
        ):
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH

    @respx.mock
    def test_rotation_elsewhere_retries_without_second_refresh(self):
        # Arrange — by the time the refresh lock is taken, the store already
        # holds a NEWER token (another process rotated); we just retry with it.
        me = respx.get(f"{BASE}/api/v1/me")
        me.side_effect = [
            httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}}),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
        refresh_route = respx.post(f"{BASE}/api/v1/auth/refresh")
        store = SeqStore(["old-a", "old-a", "new-b"], tail="new-b")
        # Act
        with _client(store) as client:
            response = client.request("GET", "/api/v1/me")
        # Assert
        assert response.status_code == 200
        assert refresh_route.call_count == 0
        assert me.calls[1].request.headers["Authorization"] == "Bearer new-b"

    @respx.mock
    def test_grace_access_adopted_via_reread(self):
        # Arrange — access-only refresh response (the winner elsewhere owns the
        # rotation); the re-read store carries the fresh access token.
        me = respx.get(f"{BASE}/api/v1/me")
        me.side_effect = [
            httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}}),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
        store = SeqStore(["old-a", "old-a", "old-a", "grace-b"], tail="grace-b")

        def access_only(_refresh_token):
            return httpx.Response(200, json={"data": {"access_token": "grace-access"}})

        # Act
        with _client(store, refresh_requester=access_only, with_saver=False) as client:
            response = client.request("GET", "/api/v1/me")
        # Assert
        assert response.status_code == 200
        assert me.calls[1].request.headers["Authorization"] == "Bearer grace-b"

    @respx.mock
    def test_access_only_refresh_without_saver_is_terminal(self):
        # Arrange — no way to adopt the grace access (no saver, re-read shows
        # the SAME dead token) → terminal auth failure.
        respx.get(f"{BASE}/api/v1/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "x"}}
            )
        )
        store = SeqStore(["old-a"], tail="old-a")

        def access_only(_refresh_token):
            return httpx.Response(200, json={"data": {"access_token": "grace-access"}})

        # Act / Assert
        with (
            _client(store, refresh_requester=access_only, with_saver=False) as client,
            pytest.raises(CliError) as exc,
        ):
            client.request("GET", "/api/v1/me")
        assert exc.value.exit_code == EXIT_AUTH
