import os
import stat

import pytest

from mesh_runtime import RUNTIME_TOKEN_PREFIX
from mesh_runtime.token_store import FileTokenStore, TokenStoreError

TOKEN = RUNTIME_TOKEN_PREFIX + "abcdefghij-0123456789"
NOBODY_UID = 65534


@pytest.fixture
def store(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    return FileTokenStore(state / "runtime.token", expected_uid=os.getuid())


class TestSave:
    async def test_save_creates_0600_file_in_0700_dir(self, store):
        await store.save(TOKEN)
        st = store.path.lstat()
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
        assert store.path.read_text() == TOKEN

    async def test_save_rejects_non_runtime_token(self, store):
        with pytest.raises(TokenStoreError, match="prefix"):
            await store.save("api_pat_not_a_runtime_token")

    async def test_save_creates_parent_dir_0700(self, tmp_path):
        path = tmp_path / "deep" / "dir" / "token"
        s = FileTokenStore(path, expected_uid=os.getuid())
        await s.save(TOKEN)
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    async def test_save_overwrites_atomically(self, store):
        await store.save(TOKEN)
        new_token = RUNTIME_TOKEN_PREFIX + "second-token-value"
        await store.save(new_token)
        assert store.path.read_text() == new_token
        # no leftover temp files
        assert os.listdir(store.path.parent) == [store.path.name]


class TestLoad:
    async def test_load_returns_saved_token(self, store):
        await store.save(TOKEN)
        assert await store.load() == TOKEN

    async def test_load_missing_file_returns_none(self, store):
        assert await store.load() is None

    async def test_load_rejects_symlink(self, store, tmp_path):
        target = tmp_path / "elsewhere"
        target.write_text(TOKEN)
        os.chmod(target, 0o600)
        store.path.symlink_to(target)
        with pytest.raises(TokenStoreError, match="symlink"):
            await store.load()

    async def test_load_rejects_world_readable_mode(self, store):
        await store.save(TOKEN)
        os.chmod(store.path, 0o644)
        with pytest.raises(TokenStoreError, match="mode"):
            await store.load()

    async def test_load_rejects_group_readable_mode(self, store):
        await store.save(TOKEN)
        os.chmod(store.path, 0o640)
        with pytest.raises(TokenStoreError, match="mode"):
            await store.load()

    @pytest.mark.skipif(os.getuid() != 0, reason="needs root to chown")
    async def test_load_rejects_wrong_owner(self, store):
        await store.save(TOKEN)
        os.chown(store.path, NOBODY_UID, NOBODY_UID)
        with pytest.raises(TokenStoreError, match="owner"):
            await store.load()

    async def test_load_rejects_world_accessible_parent_dir(self, store):
        await store.save(TOKEN)
        os.chmod(store.path.parent, 0o755)
        with pytest.raises(TokenStoreError, match="parent"):
            await store.load()

    async def test_load_rejects_directory_in_place_of_file(self, store):
        store.path.mkdir()
        with pytest.raises(TokenStoreError, match="regular file"):
            await store.load()

    async def test_load_rejects_token_with_bad_prefix_on_disk(self, store):
        await store.save(TOKEN)
        # tamper directly (bypass save validation)
        store.path.write_text("not_mesh_rt_")
        os.chmod(store.path, 0o600)
        with pytest.raises(TokenStoreError, match="prefix"):
            await store.load()

    async def test_load_rejects_empty_token_on_disk(self, store):
        await store.save(TOKEN)
        store.path.write_text("   \n")
        os.chmod(store.path, 0o600)
        with pytest.raises(TokenStoreError, match="empty"):
            await store.load()


class TestClear:
    async def test_clear_removes_file(self, store):
        await store.save(TOKEN)
        await store.clear()
        assert await store.load() is None

    async def test_clear_missing_file_is_ok(self, store):
        await store.clear()  # no error


class TestLoadHardening:
    async def test_load_rejects_mode_swap_between_lstat_and_fstat(self, store, monkeypatch):
        """The fstat re-check must include the 0600 mode: a file swapped to a
        world-readable one between lstat and open fails closed."""
        import mesh_runtime.token_store as ts

        await store.save("mesh_rt_swap-target")

        class SwappedStat:
            st_mode = 0o100644  # regular file, but 0644 — swapped after lstat
            st_uid = ts.os.getuid()

        monkeypatch.setattr(ts.os, "fstat", lambda fd: SwappedStat())
        with pytest.raises(TokenStoreError):
            await store.load()

    async def test_load_rejects_oversized_token_file(self, store):
        """A token file filling the 4096-byte read budget is anomalous —
        refuse instead of silently truncating."""
        await store.save("mesh_rt_" + "a" * 5000)
        with pytest.raises(TokenStoreError):
            await store.load()
