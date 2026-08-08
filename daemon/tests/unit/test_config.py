import pytest

from mesh_runtime.config import ConfigError, DaemonConfig


def write_toml(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = """
server_url = "https://mesh.example.com"
state_dir = "{state}"
work_dir = "{work}"
max_concurrent = 2
provider_path = "/opt/mesh/providers/claude/2.0.0/claude"
"""


class TestDaemonConfigValidation:
    def test_valid_config_loads(self, tmp_path):
        path = write_toml(
            tmp_path, VALID.format(state=tmp_path / "state", work=tmp_path / "work")
        )
        cfg = DaemonConfig.load(path)
        assert cfg.server_url == "https://mesh.example.com"
        assert cfg.max_concurrent == 2
        assert cfg.heartbeat_interval_seconds == 15.0  # server-free default
        assert cfg.shutdown_grace_seconds == 20.0

    def test_http_rejected_by_default(self, tmp_path):
        path = write_toml(
            tmp_path, VALID.format(state=tmp_path / "s", work=tmp_path / "w").replace(
                "https://mesh.example.com", "http://mesh.example.com"
            )
        )
        with pytest.raises(ConfigError, match="https"):
            DaemonConfig.load(path)

    def test_http_loopback_allowed_with_explicit_flag(self, tmp_path):
        text = VALID.format(state=tmp_path / "s", work=tmp_path / "w").replace(
            "https://mesh.example.com", "http://127.0.0.1:8000"
        ) + "\nallow_insecure_http = true\n"
        cfg = DaemonConfig.load(write_toml(tmp_path, text))
        assert cfg.server_url == "http://127.0.0.1:8000"

    def test_http_flag_does_not_allow_remote_host(self, tmp_path):
        text = VALID.format(state=tmp_path / "s", work=tmp_path / "w").replace(
            "https://mesh.example.com", "http://10.0.0.5:8000"
        ) + "\nallow_insecure_http = true\n"
        with pytest.raises(ConfigError, match="loopback"):
            DaemonConfig.load(write_toml(tmp_path, text))

    def test_relative_state_dir_rejected(self, tmp_path):
        text = VALID.format(state="relative/state", work=tmp_path / "w")
        with pytest.raises(ConfigError, match="absolute"):
            DaemonConfig.load(write_toml(tmp_path, text))

    def test_zero_max_concurrent_rejected(self, tmp_path):
        text = VALID.format(state=tmp_path / "s", work=tmp_path / "w").replace(
            "max_concurrent = 2", "max_concurrent = 0"
        )
        with pytest.raises(ConfigError, match="max_concurrent"):
            DaemonConfig.load(write_toml(tmp_path, text))

    def test_relative_provider_path_rejected(self, tmp_path):
        text = VALID.format(state=tmp_path / "s", work=tmp_path / "w").replace(
            "/opt/mesh/providers/claude/2.0.0/claude", "bin/claude"
        )
        with pytest.raises(ConfigError, match="provider_path"):
            DaemonConfig.load(write_toml(tmp_path, text))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            DaemonConfig.load(tmp_path / "nope.toml")

    def test_malformed_toml_raises(self, tmp_path):
        path = write_toml(tmp_path, "this is [ not toml")
        with pytest.raises(ConfigError, match="parse"):
            DaemonConfig.load(path)

    def test_unknown_key_rejected(self, tmp_path):
        text = VALID.format(state=tmp_path / "s", work=tmp_path / "w") + "\nbogus = 1\n"
        with pytest.raises(ConfigError, match="bogus"):
            DaemonConfig.load(write_toml(tmp_path, text))

    def test_server_url_with_path_rejected(self, tmp_path):
        text = VALID.format(state=tmp_path / "s", work=tmp_path / "w").replace(
            "https://mesh.example.com", "https://mesh.example.com/api"
        )
        with pytest.raises(ConfigError, match="origin"):
            DaemonConfig.load(write_toml(tmp_path, text))


class TestProviderManifestConfig:
    BASE = {
        "server_url": "https://mesh.example.com",
        "state_dir": "/var/lib/mesh",
        "work_dir": "/var/work/mesh",
    }

    def test_manifest_requires_provider_path(self):
        raw = {**self.BASE, "provider_manifest": "/opt/mesh/manifest.toml"}
        with pytest.raises(ConfigError, match="provider_path"):
            DaemonConfig.from_dict(raw)

    def test_manifest_and_env_file_absolute(self):
        raw = {
            **self.BASE,
            "provider_path": "/opt/mesh/providers/claude/2.1.218/claude",
            "provider_manifest": "/opt/mesh/manifest.toml",
            "provider_env_file": "/etc/mesh/provider.env",
        }
        config = DaemonConfig.from_dict(raw)
        assert str(config.provider_manifest) == "/opt/mesh/manifest.toml"
        assert str(config.provider_env_file) == "/etc/mesh/provider.env"

    def test_relative_manifest_rejected(self):
        raw = {
            **self.BASE,
            "provider_path": "/opt/mesh/claude",
            "provider_manifest": "relative/manifest.toml",
        }
        with pytest.raises(ConfigError, match="absolute"):
            DaemonConfig.from_dict(raw)

    def test_sandbox_ceiling_defaults_and_overrides(self):
        config = DaemonConfig.from_dict(self.BASE)
        assert config.sandbox_memory_bytes == 512 * 1024 * 1024
        assert config.sandbox_pids_max == 256
        raw = {**self.BASE, "sandbox_memory_bytes": 2147483648, "sandbox_pids_max": 512}
        config = DaemonConfig.from_dict(raw)
        assert config.sandbox_memory_bytes == 2147483648
        assert config.sandbox_pids_max == 512

    def test_sandbox_ceiling_must_be_positive(self):
        raw = {**self.BASE, "sandbox_memory_bytes": 0}
        with pytest.raises(ConfigError, match="sandbox_memory_bytes"):
            DaemonConfig.from_dict(raw)


class TestEgressGatewayMode:
    """TD-E (§3.4): the egress gateway is DEFAULT-ON (``strict``). Only an
    explicit, correctly-spelled opt-in reaches ``off``; every other value is a
    load error so a misconfiguration can never silently weaken enforcement."""

    BASE = {
        "server_url": "https://mesh.example.com",
        "state_dir": "/var/lib/mesh",
        "work_dir": "/var/work/mesh",
    }
    ENV_VAR = "MESH_EGRESS_GATEWAY_MODE"

    def test_default_is_strict(self, monkeypatch):
        monkeypatch.delenv(self.ENV_VAR, raising=False)
        config = DaemonConfig.from_dict(self.BASE)
        assert config.egress_gateway_mode == "strict"

    def test_explicit_strict_and_off_accepted(self, monkeypatch):
        monkeypatch.delenv(self.ENV_VAR, raising=False)
        for mode in ("strict", "off"):
            config = DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": mode})
            assert config.egress_gateway_mode == mode

    def test_value_is_normalized_case_and_whitespace(self, monkeypatch):
        monkeypatch.delenv(self.ENV_VAR, raising=False)
        config = DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": " OFF "})
        assert config.egress_gateway_mode == "off"

    @pytest.mark.parametrize(
        "bad",
        ["permissive", "disabled", "yes", "no", "0", "1", "", "of", "strictt"],
    )
    def test_unknown_values_rejected(self, monkeypatch, bad):
        # Near-misses ("of", "strictt") guard the normalization boundaries:
        # strip+lower must not accept anything but the exact frozen values.
        monkeypatch.delenv(self.ENV_VAR, raising=False)
        with pytest.raises(ConfigError, match="egress_gateway_mode"):
            DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": bad})

    def test_non_string_value_rejected(self, monkeypatch):
        monkeypatch.delenv(self.ENV_VAR, raising=False)
        with pytest.raises(ConfigError, match="egress_gateway_mode"):
            DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": True})

    def test_env_var_overrides_toml(self, monkeypatch):
        monkeypatch.setenv(self.ENV_VAR, "off")
        config = DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": "strict"})
        assert config.egress_gateway_mode == "off"

    def test_env_var_invalid_rejected_even_with_valid_toml(self, monkeypatch):
        monkeypatch.setenv(self.ENV_VAR, "permissive")
        with pytest.raises(ConfigError, match="MESH_EGRESS_GATEWAY_MODE"):
            DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": "strict"})

    def test_env_var_empty_falls_back_to_toml(self, monkeypatch):
        monkeypatch.setenv(self.ENV_VAR, "   ")
        config = DaemonConfig.from_dict({**self.BASE, "egress_gateway_mode": "off"})
        assert config.egress_gateway_mode == "off"

    def test_env_var_empty_falls_back_to_strict_default(self, monkeypatch):
        monkeypatch.setenv(self.ENV_VAR, "")
        config = DaemonConfig.from_dict(self.BASE)
        assert config.egress_gateway_mode == "strict"
