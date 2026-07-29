"""S-01 / S-10 — provider isolation: frozen argv, reserved-env scrubbing,
attempt-private read-only configs, and hostile repo-file enumeration
(ISO-09 negative basis: repo config files are plain files, never loaded)."""

import json
import os
import stat

import pytest

from mesh_runtime.errors import DaemonError
from mesh_runtime.provider_env import (
    FORBIDDEN_ESCALATION_ARGS,
    ProviderLaunchSpec,
    ReservedEnvError,
    build_provider_argv,
    build_sandbox_env,
    scan_repo_for_hostile_files,
    scrub_env,
    validate_env_name,
    validate_no_escalation_args,
    write_provider_configs,
)


def spec(**overrides) -> ProviderLaunchSpec:
    kwargs = dict(
        provider_path="/opt/mesh/providers/fake/0.0.0/fake-provider",
        version="0.0.0-fake",
        model="fake-model",
        effort=None,
        budget_usd="1.50",
        tools_allow=("Read", "Write"),
        tools_deny=("Bash",),
        mcp_config_path="/run/mesh/mcp.json",
        settings_path="/run/mesh/settings.json",
        system_prompt_path="/run/mesh/system.md",
    )
    kwargs.update(overrides)
    return ProviderLaunchSpec(**kwargs)


class TestProviderArgv:
    def test_argv_matches_frozen_section_1_4_contract(self):
        argv = build_provider_argv(spec())
        assert argv[0] == "/opt/mesh/providers/fake/0.0.0/fake-provider"
        # Every mandatory isolation flag is present, in daemon-owned order.
        assert "--print" in argv
        assert argv[argv.index("--input-format") + 1] == "stream-json"
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--bare" in argv
        assert "--disable-slash-commands" in argv
        assert "--no-session-persistence" in argv
        assert argv[argv.index("--setting-sources") + 1] == ""  # load NO sources
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--mcp-config") + 1] == "/run/mesh/mcp.json"
        assert argv[argv.index("--settings") + 1] == "/run/mesh/settings.json"
        assert argv[argv.index("--system-prompt-file") + 1] == "/run/mesh/system.md"
        assert argv[argv.index("--tools") + 1] == "Read,Write"
        assert argv[argv.index("--disallowed-tools") + 1] == "Bash"
        # bypassPermissions ONLY closes the provider's own prompts — kernel
        # isolation still applies (§1.2). It is the sole permitted mode.
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
        assert argv[argv.index("--max-budget-usd") + 1] == "1.50"

    def test_no_escalation_flags_can_appear(self):
        argv = build_provider_argv(spec())
        for forbidden in FORBIDDEN_ESCALATION_ARGS:
            assert forbidden not in argv

    def test_empty_allowlist_becomes_none_token(self):
        argv = build_provider_argv(spec(tools_allow=(), tools_deny=()))
        assert argv[argv.index("--tools") + 1] == "none"
        assert argv[argv.index("--disallowed-tools") + 1] == "none"

    def test_validate_no_escalation_args_rejects_load_expansion(self):
        for bad in (
            ["--add-dir", "/etc"],
            ["--plugin-dir", "/x"],
            ["--plugin-url", "https://x.example"],
            ["--agent", "rogue"],
            ["--dangerously-skip-permissions"],
        ):
            with pytest.raises(ReservedEnvError):
                validate_no_escalation_args(bad)
        validate_no_escalation_args(["Read", "Edit", "mcp__mesh__issue_read"])  # tool names OK


class TestEnvNameValidation:
    @pytest.mark.parametrize("name", ["A", "BUILD_ID_2", "LOCALE_NAME", "ATTEMPT_PHASE"])
    def test_accepts_plain_uppercase_names(self, name):
        validate_env_name(name)  # no raise

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "lower_case",
            "2FAST",
            "X" * 65,  # over 64 chars
            "PATH",  # exact reserved
            "NODE_OPTIONS",  # exact reserved
            "HOME",  # daemon-reserved (§3.8)
            "XDG_CONFIG_HOME",
            "LD_PRELOAD",  # dynamic loading
            "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES",
            "PYTHONPATH",  # interpreter injection
            "PYTHONSTARTUP",
            "MESH_DAEMON_TOKEN",  # platform reserved prefixes
            "MESH_INTERNAL_X",
            "AWS_SECRET_ACCESS_KEY",  # cloud credentials
            "AZURE_CLIENT_SECRET",
            "GOOGLE_APPLICATION_CREDENTIALS",
            # §3.8 generic sensitive suffixes — vendor/CI-agnostic
            "CI_API_KEY",
            "REPO_TOKEN",
            "REGISTRY_PASSWORD",
            "DEPLOY_PASSWD",
            "APP_CREDENTIALS",
            "SERVICE_APIKEY",
            "SIGNING_KEYS",
            # §3.8 proxy family — only the daemon-assembled egress pointer
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "NPM_TOKEN",  # package-registry credential
        ],
    )
    def test_rejects_reserved_or_malformed_names(self, name):
        with pytest.raises(ReservedEnvError):
            validate_env_name(name)

    def test_error_is_daemon_error(self):
        with pytest.raises(DaemonError):
            validate_env_name("LD_PRELOAD")


class TestEnvScrubbing:
    def test_scrub_drops_reserved_from_merged_dict(self):
        merged = {
            "CI_API_KEY": "v1",       # generic _KEY suffix — dropped (§3.8)
            "LD_PRELOAD": "/evil.so",
            "PYTHONPATH": "/evil",
            "HOME": "/root",
            "MESH_DAEMON_SECRET": "x",
            "REPO_TOKEN": "rot-x",    # generic _TOKEN suffix — dropped (§3.8)
            "HTTPS_PROXY": "http://attacker:3128",  # proxy family — dropped
            "NPM_TOKEN": "npm_x",     # registry credential — dropped
            "BUILD_ID": "42",         # harmless — kept
        }
        scrubbed = scrub_env(merged)
        assert scrubbed == {"BUILD_ID": "42"}

    def test_scrub_drops_non_allowlisted_shapes(self):
        assert scrub_env({"bad name": "1", "OK_NAME": "2", "": "3"}) == {"OK_NAME": "2"}

    def test_sandbox_env_is_built_from_empty_not_inherited(self):
        env = build_sandbox_env(
            attempt_id="att-1",
            execution_id="exec-1",
            home="/attempt/home",
            xdg_root="/attempt/xdg",
            proxy_url="http://169.254.7.1:3128",
        )
        assert env["HOME"] == "/attempt/home"
        assert env["XDG_CONFIG_HOME"] == "/attempt/xdg/config"
        assert env["XDG_DATA_HOME"] == "/attempt/xdg/data"
        assert env["XDG_CACHE_HOME"] == "/attempt/xdg/cache"
        assert env["HTTP_PROXY"] == "http://169.254.7.1:3128"
        assert env["HTTPS_PROXY"] == "http://169.254.7.1:3128"
        assert env["LC_ALL"] == "C.UTF-8"
        assert env["MESH_ATTEMPT_ID"] == "att-1"
        # Built from empty: nothing from the daemon's own environment leaks.
        assert "PATH" not in env
        for leaked in os.environ:
            if leaked.startswith(("MESH_", "PYTHON", "LD_", "SSH", "AWS_")):
                assert leaked not in env

    def test_sandbox_env_without_proxy_sets_no_proxy_vars(self):
        env = build_sandbox_env(attempt_id="a", execution_id="e", home="/h", xdg_root="/x")
        assert "HTTP_PROXY" not in env
        assert "HTTPS_PROXY" not in env


class TestProviderConfigFiles:
    async def test_writes_broker_only_mcp_and_readonly_files(self, tmp_path):
        paths = await write_provider_configs(
            tmp_path,
            system_prompt="trusted platform policy",
            broker_socket_path="/run/mesh/broker.sock",
            settings={"effort": "high"},
        )
        mcp = json.loads(paths.mcp_json.read_text())
        servers = mcp["mcpServers"]
        assert list(servers) == ["mesh-task-broker"]  # platform broker ONLY
        assert servers["mesh-task-broker"]["path"] == "/run/mesh/broker.sock"
        settings = json.loads(paths.settings_json.read_text())
        assert settings["effort"] == "high"
        assert paths.system_md.read_text() == "trusted platform policy"
        for path in (paths.mcp_json, paths.settings_json, paths.system_md):
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o444  # ro-owned by daemon; ro-mounted into sandbox
            if os.getuid() == 0:
                assert path.stat().st_uid == 0  # daemon-owned, task cannot rewrite

    async def test_settings_default_is_empty_object(self, tmp_path):
        paths = await write_provider_configs(
            tmp_path, system_prompt="s", broker_socket_path="/s.sock"
        )
        assert json.loads(paths.settings_json.read_text()) == {}


class TestHostileRepoScan:
    def _plant(self, worktree, *rel_paths):
        for rel in rel_paths:
            path = worktree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("malicious content")

    def test_finds_all_hostile_file_kinds(self, tmp_path):
        self._plant(
            tmp_path,
            ".mcp.json",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".claude/hooks/beacon.sh",
            "CLAUDE.md",
        )
        findings = scan_repo_for_hostile_files(tmp_path)
        kinds = {f.kind for f in findings}
        assert kinds == {"mcp_config", "project_settings", "local_settings", "hooks", "project_instructions"}
        # Enumeration NEVER executes: plant a hook that would leave a marker.
        hook = tmp_path / ".claude" / "hooks" / "beacon.sh"
        hook.write_text("#!/bin/sh\ntouch /tmp/hostile-beacon-marker\n")
        hook.chmod(0o755)
        scan_repo_for_hostile_files(tmp_path)
        assert not (tmp_path / "hostile-beacon-marker").exists()
        assert not os.path.exists("/tmp/hostile-beacon-marker")

    def test_clean_repo_has_no_findings(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('ok')")
        (tmp_path / "README.md").write_text("docs")
        assert scan_repo_for_hostile_files(tmp_path) == []

    def test_nested_and_case_exact_only(self, tmp_path):
        # Only exact spec paths count; similarly-named innocent files don't.
        self._plant(tmp_path, "docs/claude.md.backup", "sub/.mcp.json")
        findings = scan_repo_for_hostile_files(tmp_path)
        assert {f.kind for f in findings} == {"mcp_config"}  # nested .mcp.json still hostile
