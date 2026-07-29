"""Coverage-completion tests for config/doctor edge branches."""

import pytest

from mesh_runtime.config import ConfigError, DaemonConfig


def valid_raw(tmp_path, **overrides):
    raw = {
        "server_url": "https://mesh.example.com",
        "state_dir": str(tmp_path / "state"),
        "work_dir": str(tmp_path / "work"),
    }
    raw.update(overrides)
    return raw


class TestConfigEdges:
    def test_derived_paths(self, tmp_path):
        cfg = DaemonConfig.from_dict(valid_raw(tmp_path))
        assert cfg.token_path == cfg.state_dir / "runtime.token"
        assert cfg.journal_path == cfg.state_dir / "ledger.sqlite3"
        assert cfg.spool_dir == cfg.state_dir / "spool"
        assert cfg.labels == {}

    def test_heartbeat_nonpositive_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="heartbeat"):
            DaemonConfig.from_dict(valid_raw(tmp_path, heartbeat_interval_seconds=0))

    def test_negative_grace_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="shutdown_grace"):
            DaemonConfig.from_dict(valid_raw(tmp_path, shutdown_grace_seconds=-1))

    def test_provider_version_coerced_to_str(self, tmp_path):
        cfg = DaemonConfig.from_dict(valid_raw(tmp_path, provider_version=2.5))
        assert cfg.provider_version == "2.5"

    def test_missing_server_url_rejected(self, tmp_path):
        raw = valid_raw(tmp_path)
        del raw["server_url"]
        with pytest.raises(ConfigError, match="server_url"):
            DaemonConfig.from_dict(raw)

    def test_schemeless_url_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="https"):
            DaemonConfig.from_dict(valid_raw(tmp_path, server_url="mesh.example.com"))

    def test_url_with_query_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="origin"):
            DaemonConfig.from_dict(valid_raw(tmp_path, server_url="https://mesh.example.com?x=1"))

    def test_url_without_host_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="host"):
            DaemonConfig.from_dict(valid_raw(tmp_path, server_url="https://"))

    def test_trailing_slash_stripped(self, tmp_path):
        cfg = DaemonConfig.from_dict(valid_raw(tmp_path, server_url="https://mesh.example.com/"))
        assert cfg.server_url == "https://mesh.example.com"


class TestDoctorDirEdges:
    async def test_missing_dir_flagged(self, tmp_path):
        from mesh_runtime.doctor import _check_dir

        check = _check_dir("state_dir", tmp_path / "absent")
        assert not check.ok
        assert "does not exist" in check.detail
        assert "mkdir" in check.hint
