"""AppContext — workspace resolution, envelope handling, pagination, the
output discipline in ``emit``, confirmation gating, context building
(cli.md C16/C18/C20, §3.1, §3.5)."""

from __future__ import annotations

import json
import sys

import certifi
import httpx
import pytest
import respx

from meshcli.config import CredentialEntry
from meshcli.context import AppContext, build_context, get_context
from meshcli.errors import EXIT_VALIDATION, CliError
from meshcli.http import ClientOptions, MeshClient

BASE = "https://mesh.test"
WS_UUID = "11111111-1111-1111-1111-111111111111"
WS_SLUG = "acme"


class Store:
    """In-memory credential store standing in for the YAML files."""

    def __init__(self, entry: CredentialEntry | None = None) -> None:
        self.entry = entry

    def loader(self) -> CredentialEntry | None:
        return self.entry


def _app(store: Store | None = None, **overrides) -> AppContext:
    settings = {
        "workspace": None,
        "output": "json",
        "verbose": False,
        "quiet": False,
        "yes": False,
        "jq": None,
        "no_header": False,
    }
    settings.update(overrides)
    client = MeshClient(ClientOptions(base_url=BASE), credential_loader=(store or Store()).loader)
    return AppContext(client=client, api_url=BASE, **settings)


class TestWorkspaceResolution:
    def test_uuid_workspace_passes_through_without_http(self):
        # Arrange / Act — a UUID needs no resolution request.
        with respx.mock:
            resolved = _app(workspace=WS_UUID).require_workspace()
        # Assert
        assert resolved == WS_UUID

    def test_slug_workspace_resolves_via_by_slug_route(self):
        # Arrange
        with respx.mock:
            respx.get(f"{BASE}/api/v1/workspaces/by-slug/{WS_SLUG}").mock(
                return_value=httpx.Response(200, json={"data": {"id": WS_UUID}})
            )
            # Act
            resolved = _app(workspace=WS_SLUG).require_workspace()
        # Assert
        assert resolved == WS_UUID

    def test_unknown_slug_workspace_exits_3(self):
        # Arrange
        with respx.mock:
            respx.get(f"{BASE}/api/v1/workspaces/by-slug/nope").mock(
                return_value=httpx.Response(200, json={"data": {}})
            )
            # Act / Assert
            with pytest.raises(CliError) as exc:
                _app(workspace="nope").require_workspace()
        assert exc.value.exit_code == EXIT_VALIDATION
        assert "not found" in exc.value.message

    def test_credential_bound_workspace_used_when_no_flag(self):
        # Arrange — device login stored the workspace approved on the web side.
        entry = CredentialEntry(kind="device_session", token="t", workspace=WS_SLUG)
        with respx.mock:
            respx.get(f"{BASE}/api/v1/workspaces/by-slug/{WS_SLUG}").mock(
                return_value=httpx.Response(200, json={"data": {"id": WS_UUID}})
            )
            # Act
            resolved = _app(store=Store(entry)).require_workspace()
        # Assert
        assert resolved == WS_UUID

    def test_no_workspace_anywhere_lists_options_in_hint(self):
        # Arrange
        with respx.mock:
            respx.get(f"{BASE}/api/v1/workspaces").mock(
                return_value=httpx.Response(
                    200, json={"data": [{"slug": "acme"}, {"slug": "beta"}]}
                )
            )
            # Act / Assert
            with pytest.raises(CliError) as exc:
                _app().require_workspace()
        assert exc.value.exit_code == EXIT_VALIDATION
        assert "acme, beta" in (exc.value.hint or "")
        assert "--workspace" in (exc.value.hint or "")

    def test_no_workspace_and_auth_error_yields_plain_hint(self):
        # Arrange — the workspace listing itself fails auth: the hint degrades
        # gracefully (no list) but the failure stays a usage error (3, not 2).
        with respx.mock:
            respx.get(f"{BASE}/api/v1/workspaces").mock(
                return_value=httpx.Response(
                    401, json={"error": {"code": "unauthorized", "message": "x"}}
                )
            )
            # Act / Assert
            with pytest.raises(CliError) as exc:
                _app().require_workspace()
        assert exc.value.exit_code == EXIT_VALIDATION
        assert "Your workspaces" not in (exc.value.hint or "")


class TestCallEnvelope:
    @respx.mock
    def test_call_returns_the_envelope_dict(self):
        # Arrange
        respx.get(f"{BASE}/api/v1/x").mock(
            return_value=httpx.Response(200, json={"data": {"id": "x"}})
        )
        # Act
        envelope = _app().call("GET", "/api/v1/x")
        # Assert
        assert envelope == {"data": {"id": "x"}}

    @respx.mock
    def test_call_wraps_non_dict_bodies(self):
        # Arrange — a bare list body still comes back as an envelope.
        respx.get(f"{BASE}/api/v1/x").mock(
            return_value=httpx.Response(200, json=[{"id": 1}])
        )
        # Act
        envelope = _app().call("GET", "/api/v1/x")
        # Assert
        assert envelope == {"data": [{"id": 1}]}

    @respx.mock
    def test_call_forwards_headers_and_params(self):
        # Arrange
        route = respx.patch(f"{BASE}/api/v1/x").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        # Act
        _app().call(
            "PATCH", "/api/v1/x", json={"a": 1}, params={"q": "1"},
            idempotency_key="k1", if_match="7",
        )
        # Assert
        request = route.calls[0].request
        assert request.headers["Idempotency-Key"] == "k1"
        assert request.headers["If-Match"] == "7"
        assert request.url.params["q"] == "1"
        assert json.loads(request.content) == {"a": 1}


class TestEmit:
    def test_json_mode_writes_exactly_one_document(self, capsys):
        # Arrange / Act
        _app().emit({"data": {"id": "x"}})
        # Assert
        assert json.loads(capsys.readouterr().out) == {"data": {"id": "x"}}

    def test_jq_requires_json_output(self):
        # Arrange
        app = _app(output="table", jq=".data.id")
        # Act / Assert
        with pytest.raises(CliError) as exc:
            app.emit({"data": {"id": "x"}})
        assert exc.value.exit_code == EXIT_VALIDATION
        assert "--output json" in (exc.value.hint or "")

    def test_jq_emits_json_lines(self, capsys):
        # Arrange
        app = _app(jq=".[] | .id")
        # Act
        app.emit({"data": [{"id": "a"}, {"id": "b"}]})
        # Assert
        assert capsys.readouterr().out.splitlines() == ['"a"', '"b"']

    def test_table_rows_with_explicit_columns(self, capsys):
        # Arrange
        app = _app(output="table")
        # Act
        app.emit({"data": [{"identifier": "M-1", "title": "t"}]},
                 columns=["identifier", "title"])
        # Assert
        out = capsys.readouterr().out
        assert "IDENTIFIER" in out and "M-1" in out

    def test_table_dict_data_with_row_of_projection(self, capsys):
        # Arrange
        app = _app(output="table")
        # Act
        app.emit(
            {"data": {"identifier": "M-9", "extra": 1}},
            columns=["identifier"],
            row_of=lambda row: {"identifier": row["identifier"]},
        )
        # Assert
        out = capsys.readouterr().out
        assert "M-9" in out and "extra" not in out

    def test_table_default_columns_from_first_row(self, capsys):
        # Arrange / Act
        _app(output="table").emit({"data": [{"a": 1, "b": 2}]})
        # Assert
        out = capsys.readouterr().out
        assert "A" in out and "B" in out

    def test_scalar_data_falls_back_to_json(self, capsys):
        # Arrange / Act
        _app(output="table").emit({"data": 42})
        # Assert
        assert json.loads(capsys.readouterr().out) == {"data": 42}

    def test_empty_list_prints_nothing(self, capsys):
        # Arrange / Act
        _app(output="table").emit({"data": []}, columns=["x"])
        # Assert
        assert capsys.readouterr().out == ""

    def test_no_header_suppresses_header_row(self, capsys):
        # Arrange / Act
        _app(output="table", no_header=True).emit(
            {"data": [{"identifier": "M-2"}]}, columns=["identifier"]
        )
        # Assert
        out = capsys.readouterr().out
        assert "IDENTIFIER" not in out and "M-2" in out


class TestCallAll:
    @respx.mock
    def test_follows_cursor_and_merges_pages(self):
        # Arrange
        route = respx.get(f"{BASE}/api/v1/things")
        route.side_effect = [
            httpx.Response(200, json={"data": [{"id": 1}], "next_cursor": "c1"}),
            httpx.Response(200, json={"data": [{"id": 2}]}),
        ]
        # Act
        envelope = _app().call_all("GET", "/api/v1/things", params={"limit": 1})
        # Assert
        assert envelope["data"] == [{"id": 1}, {"id": 2}]
        assert "next_cursor" not in envelope
        assert route.call_count == 2
        first_params = route.calls[0].request.url.params
        assert first_params["limit"] == "1" and "cursor" not in first_params
        assert route.calls[1].request.url.params["cursor"] == "c1"

    @respx.mock
    def test_single_page_without_cursor(self):
        # Arrange
        respx.get(f"{BASE}/api/v1/things").mock(
            return_value=httpx.Response(200, json={"data": [{"id": 1}]})
        )
        # Act
        envelope = _app().call_all("GET", "/api/v1/things")
        # Assert
        assert envelope == {"data": [{"id": 1}]}


class _FakeTTYStdin:
    def isatty(self) -> bool:
        return True


class TestConfirm:
    def test_yes_flag_skips_prompt(self):
        # Act / Assert — no error, no stdin access.
        _app(yes=True).confirm("delete?")

    def test_non_tty_without_yes_refuses_exit_3(self):
        # Arrange — pytest's captured stdin is not a TTY.
        # Act / Assert
        with pytest.raises(CliError) as exc:
            _app().confirm("delete?")
        assert exc.value.exit_code == EXIT_VALIDATION
        assert "--yes" in (exc.value.hint or "")

    def test_tty_affirmative_proceeds(self, monkeypatch):
        # Arrange
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        # Act / Assert — returns without raising.
        _app().confirm("delete?")

    def test_tty_negative_aborts(self, monkeypatch):
        # Arrange
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        # Act / Assert
        with pytest.raises(CliError) as exc:
            _app().confirm("delete?")
        assert exc.value.exit_code == EXIT_VALIDATION
        assert "aborted" in exc.value.message


class TestProgress:
    def test_progress_writes_stderr_unless_quiet(self, capsys):
        # Arrange / Act
        _app().progress("working…")
        # Assert
        assert "working…" in capsys.readouterr().err
        # Act — quiet silences it.
        _app(quiet=True).progress("working…")
        assert capsys.readouterr().err == ""


class _FakeClickCtx:
    def __init__(self, flags: dict) -> None:
        self.obj = {"flags": flags}
        self.params: dict = {}  # real click contexts always carry params


def _flags(**overrides) -> dict:
    base = {
        "api_url": BASE,
        "workspace": None,
        "output": "json",
        "verbose": False,
        "quiet": False,
        "yes": False,
        "insecure": False,
        "ca_cert": None,
        "jq": None,
        "no_header": False,
    }
    base.update(overrides)
    return base


def _build(**overrides) -> AppContext:
    settings = {
        "api_url": BASE,
        "workspace": None,
        "output": None,
        "verbose": False,
        "quiet": False,
        "yes": False,
        "insecure": False,
        "ca_cert": None,
        "jq": None,
        "no_header": False,
    }
    settings.update(overrides)
    return build_context(**settings)


class TestBuildContext:
    def test_get_context_builds_once_and_caches(self, mesh_env):
        # Arrange
        ctx = _FakeClickCtx(_flags())
        # Act
        first = get_context(ctx)
        second = get_context(ctx)
        # Assert
        assert second is first
        first.client.close()

    def test_flag_wins_over_env(self, mesh_env, monkeypatch):
        # Arrange
        monkeypatch.setenv("MESH_API_URL", "https://env.example.com")
        # Act
        app = _build(api_url=BASE)
        # Assert
        assert app.api_url == BASE

    def test_env_used_when_no_flag(self, mesh_env, monkeypatch):
        # Arrange
        monkeypatch.setenv("MESH_API_URL", "https://env.example.com")
        # Act
        app = _build(api_url=None)
        # Assert
        assert app.api_url == "https://env.example.com"

    def test_config_ca_cert_used_when_no_flag(self, mesh_env):
        # Arrange — httpx eagerly loads the CA bundle, so point at a real one.
        ca_bundle = certifi.where()
        config = {"hosts": {BASE: {"tls": {"ca_cert": ca_bundle}}}}
        # Act
        app = _build(ca_cert=None, config=config)
        try:
            # Assert
            assert app.client._options.ca_cert == ca_bundle
        finally:
            app.client.close()

    def test_flag_ca_cert_overrides_config(self, mesh_env):
        # Arrange — the --ca-cert flag wins over the per-host config value.
        ca_bundle = certifi.where()
        config = {"hosts": {BASE: {"tls": {"ca_cert": "/should/not/be/used.pem"}}}}
        # Act
        app = _build(ca_cert=ca_bundle, config=config)
        try:
            # Assert
            assert app.client._options.ca_cert == ca_bundle
        finally:
            app.client.close()

    def test_credential_closures_target_the_host_store(self, mesh_env):
        # Arrange
        app = _build()
        entry = CredentialEntry(kind="pat", token="mesh_pat_zzz", prefix="mesh_pat_zz")
        # Act / Assert — the loader/saver/clearer wired into the client hit
        # the per-host credential file under the isolated MESH_CONFIG.
        assert app.client._load_credential() is None
        app.client._save_credential(entry)
        assert app.client._load_credential().token == "mesh_pat_zzz"
        app.client._clear_credential()
        assert app.client._load_credential() is None
