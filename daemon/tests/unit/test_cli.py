import os

import pytest

from mesh_runtime import __version__
from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.cli import (
    build_adapters,
    build_parser,
    main,
    read_activation_code,
    read_runtime_id,
    write_runtime_id,
)
from mesh_runtime.config import DaemonConfig
from mesh_runtime.errors import DaemonError


def make_config(tmp_path, **overrides):
    state = tmp_path / "state"
    work = tmp_path / "work"
    state.mkdir(mode=0o700, exist_ok=True)
    work.mkdir(mode=0o700, exist_ok=True)
    raw = {
        "server_url": "https://mesh.example.com",
        "state_dir": str(state),
        "work_dir": str(work),
    }
    raw.update(overrides)
    return DaemonConfig.from_dict(raw)


def write_config_file(tmp_path, config_text=None):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        config_text
        or f'server_url = "https://mesh.example.com"\nstate_dir = "{tmp_path / "state"}"\n'
        f'work_dir = "{tmp_path / "work"}"\n',
        encoding="utf-8",
    )
    (tmp_path / "state").mkdir(mode=0o700, exist_ok=True)
    (tmp_path / "work").mkdir(mode=0o700, exist_ok=True)
    return cfg


class TestVersion:
    def test_version_prints(self, capsys):
        assert main(["version"]) == 0
        assert __version__ in capsys.readouterr().out

    def test_parser_requires_command(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


class TestActivationCode:
    def test_reads_0600_file(self, tmp_path):
        code_file = tmp_path / "code"
        code_file.write_text("abcd1234\n")
        os.chmod(code_file, 0o600)
        assert read_activation_code(code_file) == "abcd1234"

    def test_rejects_world_readable_code_file(self, tmp_path):
        code_file = tmp_path / "code"
        code_file.write_text("abcd1234")
        os.chmod(code_file, 0o644)
        with pytest.raises(DaemonError, match="0600"):
            read_activation_code(code_file)

    def test_rejects_symlink_code_file(self, tmp_path):
        real = tmp_path / "real"
        real.write_text("abcd1234")
        os.chmod(real, 0o600)
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(DaemonError, match="regular file"):
            read_activation_code(link)

    def test_rejects_empty_code_file(self, tmp_path):
        code_file = tmp_path / "code"
        code_file.write_text("   \n")
        os.chmod(code_file, 0o600)
        with pytest.raises(DaemonError, match="empty"):
            read_activation_code(code_file)


class TestRuntimeIdPersistence:
    def test_write_then_read(self, tmp_path):
        cfg = make_config(tmp_path)
        write_runtime_id(cfg, "rt-123")
        assert read_runtime_id(cfg) == "rt-123"

    def test_read_missing_returns_none(self, tmp_path):
        cfg = make_config(tmp_path)
        assert read_runtime_id(cfg) is None


class TestBuildAdapters:
    def test_a1_ships_fake_provider(self, tmp_path):
        cfg = make_config(tmp_path)
        adapters = build_adapters(cfg)
        assert len(adapters) == 1
        assert adapters[0].name == "fake"


class TestActivateCommand:
    async def test_activate_persists_token_and_runtime_id(self, tmp_path, fake_server):
        cfg = make_config(tmp_path)
        runtime_id = "99999999-9999-9999-9999-999999999999"
        token = "mesh_rt_persisted-token"
        fake_server.enqueue(
            "POST /api/v1/daemon/runtimes:activate",
            200,
            {"data": {"runtime_id": runtime_id, "runtime_token": token,
                      "heartbeat_interval_seconds": 15}},
        )
        api = RuntimeApiClient(cfg.server_url, None, transport=fake_server.transport())
        from mesh_runtime.cli import cmd_activate

        rc = await cmd_activate(cfg, "activation-code-xyz", api=api)
        assert rc == 0
        # token stored 0600
        from mesh_runtime.token_store import FileTokenStore

        store = FileTokenStore(cfg.token_path, expected_uid=os.getuid())
        assert await store.load() == token
        assert read_runtime_id(cfg) == runtime_id

    def test_activate_via_main_with_code_file(self, tmp_path, fake_server, monkeypatch):
        cfg_file = write_config_file(tmp_path)
        code_file = tmp_path / "code"
        code_file.write_text("activation-code-xyz")
        os.chmod(code_file, 0o600)

        runtime_id = "99999999-9999-9999-9999-999999999999"

        # Patch RuntimeApiClient used inside cmd_activate to use the fake transport.
        import mesh_runtime.cli as cli_mod

        real_client = cli_mod.RuntimeApiClient

        def fake_client(base_url, token, **kw):
            return real_client(base_url, token, transport=fake_server.transport())

        fake_server.enqueue(
            "POST /api/v1/daemon/runtimes:activate",
            200,
            {"data": {"runtime_id": runtime_id, "runtime_token": "mesh_rt_t",
                      "heartbeat_interval_seconds": 15}},
        )
        monkeypatch.setattr(cli_mod, "RuntimeApiClient", fake_client)
        rc = main(["activate", "--config", str(cfg_file), "--activation-code-file", str(code_file)])
        assert rc == 0


class TestDoctorCommand:
    def test_doctor_green(self, tmp_path, capsys):
        cfg_file = write_config_file(tmp_path)
        rc = main(["doctor", "--config", str(cfg_file)])
        assert rc == 0
        assert "server_url" in capsys.readouterr().out

    def test_doctor_with_provider_binary_ok(self, tmp_path, capsys):
        prov = tmp_path / "claude"
        prov.write_text("#!/bin/sh\necho 2.0.0\n")
        os.chmod(prov, 0o755)
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            f'server_url = "https://mesh.example.com"\nstate_dir = "{tmp_path / "state"}"\n'
            f'work_dir = "{tmp_path / "work"}"\nprovider_path = "{prov}"\n'
        )
        (tmp_path / "state").mkdir(mode=0o700, exist_ok=True)
        (tmp_path / "work").mkdir(mode=0o700, exist_ok=True)
        rc = main(["doctor", "--config", str(cfg_file)])
        out = capsys.readouterr().out
        assert "provider_binary" in out
        assert rc == 0

    def test_doctor_with_missing_provider_binary_fails(self, tmp_path, capsys):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            f'server_url = "https://mesh.example.com"\nstate_dir = "{tmp_path / "state"}"\n'
            f'work_dir = "{tmp_path / "work"}"\nprovider_path = "{tmp_path / "nope"}"\n'
        )
        (tmp_path / "state").mkdir(mode=0o700, exist_ok=True)
        (tmp_path / "work").mkdir(mode=0o700, exist_ok=True)
        rc = main(["doctor", "--config", str(cfg_file)])
        assert rc == 1
        assert "provider_binary" in capsys.readouterr().out

    def test_run_without_token_exits_2(self, tmp_path):
        cfg_file = write_config_file(tmp_path)
        rc = main(["run", "--config", str(cfg_file)])
        assert rc == 2


class StubApp:
    def __init__(self, *args, **kwargs):
        self.runtime_id = None

    def set_runtime_id(self, rid):
        self.runtime_id = rid

    def request_shutdown(self):
        pass

    async def run(self):
        return None


class TestRunCommand:
    def test_run_happy_path_with_stub_app(self, tmp_path, monkeypatch):
        import asyncio

        from mesh_runtime.token_store import FileTokenStore

        cfg_file = write_config_file(tmp_path)
        cfg = DaemonConfig.load(cfg_file)
        store = FileTokenStore(cfg.token_path, expected_uid=os.getuid())
        asyncio.run(store.save("mesh_rt_abcdef"))
        write_runtime_id(cfg, "rt-xyz")

        import mesh_runtime.cli as cli_mod

        monkeypatch.setattr(cli_mod, "RuntimeApp", StubApp)
        rc = main(["run", "--config", str(cfg_file)])
        assert rc == 0

    def test_activate_via_stdin(self, tmp_path, fake_server, monkeypatch):
        import io

        import mesh_runtime.cli as cli_mod

        cfg_file = write_config_file(tmp_path)
        real = cli_mod.RuntimeApiClient

        def fake_client(base_url, token, **kw):
            return real(base_url, token, transport=fake_server.transport())

        monkeypatch.setattr(cli_mod, "RuntimeApiClient", fake_client)
        fake_server.enqueue(
            "POST /api/v1/daemon/runtimes:activate",
            200,
            {"data": {"runtime_id": "rt-1", "runtime_token": "mesh_rt_t",
                      "heartbeat_interval_seconds": 15}},
        )
        monkeypatch.setattr("sys.stdin", io.StringIO("code-stdin-xyz\n"))
        rc = main(["activate", "--config", str(cfg_file), "--activation-code-stdin"])
        assert rc == 0

    def test_activate_via_stdin_empty_exits_2(self, tmp_path, monkeypatch):
        import io

        cfg_file = write_config_file(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
        rc = main(["activate", "--config", str(cfg_file), "--activation-code-stdin"])
        assert rc == 2
