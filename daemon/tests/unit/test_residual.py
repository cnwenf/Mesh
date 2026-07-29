"""Unit tests for crash-residual reaping (residual.py)."""


import pytest

import mesh_runtime.residual as residual_mod
from mesh_runtime.residual import (
    ResidualPaths,
    _parse_link_name,
    _segment,
    purge_attempt_residuals,
    purge_sandbox_wide,
)


@pytest.fixture
def paths(tmp_path):
    work_root = tmp_path / "work"
    spool_root = tmp_path / "spool"
    cgroup_base = tmp_path / "cgroups"
    work_root.mkdir()
    spool_root.mkdir()
    cgroup_base.mkdir()
    return ResidualPaths(work_root=work_root, spool_root=spool_root, cgroup_base=cgroup_base)


class TestPurgeAttempt:
    def test_removes_contained_dirs(self, paths):
        work = paths.work_root / "exec" / "att"
        work.mkdir(parents=True)
        (work / "f").write_text("x")
        spool = paths.spool_root / "att"
        spool.mkdir()
        (spool / "b.json").write_text("{}")
        errors = purge_attempt_residuals("att", paths, work_dir=str(work))
        assert errors == []
        assert not work.exists()
        assert not spool.exists()

    def test_refuses_escape_via_relative_components(self, paths, tmp_path):
        outside = tmp_path / "victim"
        outside.mkdir()
        (outside / "data").write_text("keep")
        evil = str(paths.work_root / ".." / "victim")
        errors = purge_attempt_residuals("att", paths, work_dir=evil)
        assert errors and "refused" in errors[0]
        assert (outside / "data").exists()

    def test_segment_never_traverses(self):
        for raw in ("../../etc", "a/b", "..\\..\\x", ".."):
            seg = _segment(raw)
            assert "/" not in seg and "\\" not in seg and ".." not in seg

    def test_missing_dirs_are_not_errors(self, paths):
        assert purge_attempt_residuals("ghost", paths, work_dir="") == []


class TestKillCgroup:
    def test_kill_cgroup_tolerates_dead_pids_and_missing_files(self, tmp_path):
        cg = tmp_path / "mesh-x"
        cg.mkdir()
        (cg / "cgroup.procs").write_text("999999999\nnotapid\n")
        residual_mod._kill_cgroup(cg)  # must not raise

    def test_kill_cgroup_uses_cgroup_kill_first(self, tmp_path):
        cg = tmp_path / "mesh-y"
        cg.mkdir()
        (cg / "cgroup.kill").write_text("")
        (cg / "cgroup.procs").write_text("")
        residual_mod._kill_cgroup(cg)
        assert (cg / "cgroup.kill").read_text() == "1"


class TestVethSweep:
    def test_parse_link_name(self):
        assert _parse_link_name("2: mvhabcd1234: <BROADCAST> mtu 1500") == "mvhabcd1234"
        assert _parse_link_name("3: eth0@if4: <BROADCAST>") == "eth0"
        assert _parse_link_name("garbage") is None

    def test_sweep_deletes_only_mesh_veth_hosts(self, paths, monkeypatch):
        deleted = []

        def fake_run(argv, **kw):
            class R:
                returncode = 0
                stdout = ""

            if argv[:3] == ["ip", "-o", "link"]:
                R.stdout = (
                    "1: lo: <LOOPBACK> mtu 65536\n"
                    "2: mvh12345678: <BROADCAST> mtu 1500\n"
                    "3: eth0@if2: <BROADCAST> mtu 1500\n"
                )
                return R()
            if argv[:3] == ["ip", "link", "del"]:
                deleted.append(argv[3])
                return R()
            raise AssertionError(f"unexpected argv {argv}")

        monkeypatch.setattr(residual_mod.subprocess, "run", fake_run)
        cgroups, links, errors = purge_sandbox_wide(paths)
        assert links == 1
        assert deleted == ["mvh12345678"]  # never eth0/lo
        assert cgroups == 0
        assert errors == []

    def test_sweep_survives_missing_ip_tool(self, paths, monkeypatch):
        def boom(argv, **kw):
            raise FileNotFoundError("ip")

        monkeypatch.setattr(residual_mod.subprocess, "run", boom)
        cgroups, links, errors = purge_sandbox_wide(paths)
        assert links == 0
        assert errors and "FileNotFoundError" in errors[0]

    def test_sweep_cgroup_rmdir_failure_reported_not_fatal(self, paths, monkeypatch):
        busy = paths.cgroup_base / "mesh-busy"
        busy.mkdir()
        (busy / "leftover").write_text("kernel file")  # makes rmdir fail
        cgroups, links, errors = purge_sandbox_wide(paths)
        assert cgroups == 0
        assert any("mesh-busy" in e for e in errors)
        assert busy.exists()
