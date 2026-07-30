"""Backend-root resolution for subprocess fixtures (MES-121 regression guard).

The e2e PYTHONPATH pin must derive the backend root from the anchor file
alone — a ``pyproject.toml`` ascent, never a hand-counted ``dirname`` depth
and never the caller's cwd. A miscounted depth once pointed the pin at
``backend/tests`` (a nonexistent ``src``), letting spawned e2e servers
resolve ``mesh`` from a stale editable install of another checkout — the
queue-e2e false-negative root cause recorded in the MES-88 acceptance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import BACKEND_DIR, resolve_backend_dir

pytestmark = pytest.mark.unit


def _write_tree(root: Path, *, with_manifest: bool = True) -> Path:
    """Synthetic backend layout: root/backend/{pyproject.toml,src,tests/e2e}."""
    backend = root / "backend"
    (backend / "src" / "mesh").mkdir(parents=True)
    (backend / "tests" / "e2e").mkdir(parents=True)
    if with_manifest:
        (backend / "pyproject.toml").write_text(
            "[project]\nname = 'mesh-backend'\n", encoding="utf-8"
        )
    anchor = backend / "tests" / "e2e" / "conftest.py"
    anchor.write_text("", encoding="utf-8")
    return anchor


class TestResolveBackendDir:
    def test_real_backend_root_resolved(self):
        assert BACKEND_DIR.is_absolute()
        assert (BACKEND_DIR / "pyproject.toml").is_file()
        assert (BACKEND_DIR / "src" / "mesh").is_dir()
        # The e2e fixture anchor resolves to the same root the suite uses.
        e2e_anchor = BACKEND_DIR / "tests" / "e2e" / "conftest.py"
        assert resolve_backend_dir(e2e_anchor) == BACKEND_DIR

    def test_real_anchor_is_cwd_independent(self, tmp_path, monkeypatch):
        anchor = BACKEND_DIR / "tests" / "e2e" / "conftest.py"
        for cwd in (tmp_path, Path("/")):
            monkeypatch.chdir(cwd)
            assert resolve_backend_dir(anchor) == BACKEND_DIR

    def test_relative_anchor_resolves_against_cwd(self, monkeypatch):
        monkeypatch.chdir(BACKEND_DIR)
        assert resolve_backend_dir(Path("tests") / "e2e" / "conftest.py") == BACKEND_DIR

    def test_synthetic_tree_under_foreign_cwd(self, tmp_path, monkeypatch):
        """Heterogeneous layout: the checkout lives anywhere and the caller's
        cwd is somewhere else entirely — the anchor alone decides."""
        anchor = _write_tree(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert resolve_backend_dir(anchor) == tmp_path / "backend"

    def test_deeper_anchor_finds_same_root(self, tmp_path):
        """No hand-counted depth: a more deeply nested anchor still ascends to
        the manifest instead of landing one level short."""
        _write_tree(tmp_path)
        deep_dir = tmp_path / "backend" / "tests" / "e2e" / "sub" / "deeper"
        deep_dir.mkdir(parents=True)
        deep_anchor = deep_dir / "helper.py"
        deep_anchor.write_text("", encoding="utf-8")
        assert resolve_backend_dir(deep_anchor) == tmp_path / "backend"

    def test_missing_manifest_raises_loudly(self, tmp_path):
        """Silent mis-derivation is the bug being prevented: with no manifest
        anywhere above, resolution fails rather than guessing a directory."""
        anchor = _write_tree(tmp_path, with_manifest=False)
        with pytest.raises(RuntimeError, match="pyproject.toml"):
            resolve_backend_dir(anchor)


class TestE2ePythonpathPin:
    """The fixture pin built on resolve_backend_dir (tests/e2e/conftest.py)."""

    def test_pin_fronts_this_checkout_and_drops_stale_entries(self):
        import tests.e2e.conftest as e2e_conftest

        stale_flat = "/workspaces/ws1/workdir/Mesh/backend/src"
        stale_nested = "/root/agent_workspaces/ws-1/run-2/workdir/Mesh/backend/src"
        unrelated = "/opt/other/lib"
        env = {"PYTHONPATH": f"{stale_flat}{_sep()}{stale_nested}{_sep()}{unrelated}"}
        e2e_conftest.pin_code_under_test(env)
        entries = env["PYTHONPATH"].split(_sep())
        assert entries[0] == str(BACKEND_DIR / "src")
        assert entries[1] == str(BACKEND_DIR)
        assert entries[2:] == [unrelated]  # stale Mesh paths dropped, rest kept
        assert (Path(entries[0]) / "mesh").is_dir()  # pin actually exists on disk

    def test_pin_without_inherited_pythonpath(self):
        import tests.e2e.conftest as e2e_conftest

        env: dict[str, str] = {}
        e2e_conftest.pin_code_under_test(env)
        assert env["PYTHONPATH"].split(_sep())[:2] == [
            str(BACKEND_DIR / "src"),
            str(BACKEND_DIR),
        ]


def _sep() -> str:
    import os

    return os.pathsep
