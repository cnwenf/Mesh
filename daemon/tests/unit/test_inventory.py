import hashlib
import os

from mesh_runtime.inventory import BinaryProbe, Inventory, probe_binary
from mesh_runtime.providers.base import ProbeResult
from mesh_runtime.providers.fake import FakeProvider


def make_executable(tmp_path, name="tool", body="#!/bin/sh\necho 2.0.0\n", mode=0o755):
    path = tmp_path / name
    path.write_text(body)
    os.chmod(path, mode)
    return path


class TestProbeBinary:
    async def test_probes_real_script_sha_and_version(self, tmp_path):
        path = make_executable(tmp_path)
        probe = await probe_binary(str(path))
        assert probe.ok
        expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert probe.sha256 == expected_sha
        assert probe.version == "2.0.0"

    async def test_rejects_relative_path(self, tmp_path):
        probe = await probe_binary("relative/tool")
        assert not probe.ok
        assert "absolute" in probe.reason

    async def test_rejects_missing_file(self, tmp_path):
        probe = await probe_binary(str(tmp_path / "nope"))
        assert not probe.ok
        assert "not found" in probe.reason

    async def test_rejects_symlink(self, tmp_path):
        real = make_executable(tmp_path, name="real")
        link = tmp_path / "link"
        link.symlink_to(real)
        probe = await probe_binary(str(link))
        assert not probe.ok
        assert "symlink" in probe.reason

    async def test_rejects_non_regular_file(self, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        probe = await probe_binary(str(d))
        assert not probe.ok
        assert "regular file" in probe.reason

    async def test_rejects_world_writable(self, tmp_path):
        path = make_executable(tmp_path, mode=0o777)
        probe = await probe_binary(str(path))
        assert not probe.ok
        assert "world-writable" in probe.reason

    async def test_rejects_non_executable(self, tmp_path):
        path = make_executable(tmp_path, mode=0o644)
        probe = await probe_binary(str(path))
        assert not probe.ok
        assert "not executable" in probe.reason

    async def test_version_timeout_fails_closed(self, tmp_path):
        path = make_executable(tmp_path, body="#!/bin/sh\nsleep 5\n")
        probe = await probe_binary(str(path), timeout=0.3)
        assert not probe.ok
        assert "version" in probe.reason or "timeout" in probe.reason

    async def test_nonzero_version_exit_is_unavailable(self, tmp_path):
        path = make_executable(tmp_path, body="#!/bin/sh\nexit 3\n")
        probe = await probe_binary(str(path))
        assert not probe.ok

    def test_binary_probe_is_dataclass(self):
        probe = BinaryProbe(ok=False, path="/x", sha256=None, version=None, reason="r")
        assert probe.path == "/x"


class TestInventory:
    async def test_healthy_when_all_available(self):
        inv = await Inventory.probe([FakeProvider(events=[])])
        assert inv.healthy()
        assert "coding_cli.fake" in inv.capability_keys()

    async def test_degraded_when_any_unavailable(self):
        bad = FakeProvider(
            events=[],
            probe_result=ProbeResult(
                available=False, name="claude-code", version=None,
                binary_sha256=None, capabilities=(), reason="missing",
            ),
        )
        inv = await Inventory.probe([FakeProvider(events=[]), bad])
        assert not inv.healthy()
        assert "missing" in inv.degraded_reasons()

    async def test_degraded_diagnostics_lists_required_missing_capabilities(self):
        bad = FakeProvider(
            events=[],
            probe_result=ProbeResult(
                available=False,
                name="claude-code",
                version=None,
                binary_sha256=None,
                capabilities=(),
                reason="missing",
                required_capabilities=("coding_cli.claude", "usage.cost"),
            ),
        )

        inventory = await Inventory.probe([bad])

        assert inventory.capability_keys() == []
        assert inventory.operational_diagnostics() == [
            {
                "reason_code": "provider_unavailable",
                "missing_capabilities": ["coding_cli.claude", "usage.cost"],
                "affected_task_types": ["provider:claude-code"],
            }
        ]

    async def test_capability_keys_sorted_union(self):
        a = FakeProvider(
            events=[],
            probe_result=ProbeResult(True, "a", "1", None, ("tool.git", "tool.fs"), None),
        )
        b = FakeProvider(
            events=[],
            probe_result=ProbeResult(True, "b", "1", None, ("tool.fs", "usage.cost"), None),
        )
        inv = await Inventory.probe([a, b])
        assert inv.capability_keys() == ["tool.fs", "tool.git", "usage.cost"]

    async def test_inventory_hash_stable_and_sensitive(self):
        inv1 = await Inventory.probe([FakeProvider(events=[])])
        inv2 = await Inventory.probe([FakeProvider(events=[])])
        assert inv1.inventory_hash() == inv2.inventory_hash()
        assert inv1.inventory_hash().startswith("sha256:")

    async def test_empty_inventory_is_healthy_with_no_capabilities(self):
        inv = await Inventory.probe([])
        assert inv.healthy()
        assert inv.capability_keys() == []
