import os

from mesh_runtime import RUNTIME_TOKEN_PREFIX
from mesh_runtime.config import DaemonConfig
from mesh_runtime.doctor import run_checks
from mesh_runtime.inventory import Inventory
from mesh_runtime.providers.base import ProbeResult
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.token_store import FileTokenStore


def make_config(tmp_path, **overrides) -> DaemonConfig:
    state = tmp_path / "state"
    work = tmp_path / "work"
    state.mkdir(mode=0o700, exist_ok=True)
    work.mkdir(mode=0o700, exist_ok=True)
    base = dict(
        server_url="https://mesh.example.com",
        state_dir=state,
        work_dir=work,
        max_concurrent=1,
    )
    base.update(overrides)
    return DaemonConfig.from_dict(base)


async def healthy_inventory():
    return await Inventory.probe([FakeProvider(events=[])])


async def degraded_inventory():
    return await Inventory.probe(
        [
            FakeProvider(
                events=[],
                probe_result=ProbeResult(
                    available=False, name="claude-code", version=None,
                    binary_sha256=None, capabilities=(), reason="binary not found",
                ),
            )
        ]
    )


class TestDoctor:
    async def test_all_green(self, tmp_path):
        cfg = make_config(tmp_path)
        report = await run_checks(cfg, await healthy_inventory())
        assert report.all_ok()
        names = {c.name for c in report.checks}
        assert {"server_url", "state_dir", "work_dir", "providers"} <= names

    async def test_degraded_provider_reports_hint(self, tmp_path):
        cfg = make_config(tmp_path)
        report = await run_checks(cfg, await degraded_inventory())
        assert not report.all_ok()
        providers = next(c for c in report.checks if c.name == "providers")
        assert not providers.ok
        assert "binary not found" in providers.detail
        assert providers.hint  # actionable guidance present

    async def test_missing_state_dir_flagged(self, tmp_path):
        cfg = make_config(tmp_path)
        os.chmod(cfg.state_dir, 0o755)  # too permissive
        report = await run_checks(cfg, await healthy_inventory())
        state_check = next(c for c in report.checks if c.name == "state_dir")
        assert not state_check.ok
        assert "0700" in state_check.detail

    async def test_token_file_bad_mode_flagged(self, tmp_path):
        cfg = make_config(tmp_path)
        store = FileTokenStore(cfg.token_path, expected_uid=os.getuid())
        await store.save(RUNTIME_TOKEN_PREFIX + "abcdef")
        os.chmod(cfg.token_path, 0o644)
        report = await run_checks(cfg, await healthy_inventory())
        token_check = next(c for c in report.checks if c.name == "token_file")
        assert not token_check.ok

    async def test_token_file_absent_is_info_not_failure(self, tmp_path):
        cfg = make_config(tmp_path)
        report = await run_checks(cfg, await healthy_inventory())
        token_check = next(c for c in report.checks if c.name == "token_file")
        assert token_check.ok  # not yet activated is fine
        assert "not activated" in token_check.detail.lower()

    async def test_render_contains_status_lines(self, tmp_path):
        cfg = make_config(tmp_path)
        report = await run_checks(cfg, await healthy_inventory())
        text = report.render()
        assert "OK" in text
        assert "server_url" in text

    async def test_http_server_url_flagged(self, tmp_path):
        cfg = make_config(tmp_path, server_url="https://mesh.example.com")
        # forge an insecure config by bypassing validation
        object.__setattr__(cfg, "server_url", "ftp://mesh.example.com")
        report = await run_checks(cfg, await healthy_inventory())
        url_check = next(c for c in report.checks if c.name == "server_url")
        assert not url_check.ok
