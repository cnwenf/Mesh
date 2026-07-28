"""Streaming parser + row-key allocator tests (import-export.md §2.5 / §5)."""

import json
import os

import pytest

from mesh.data_jobs.parser import (
    RowKeyAllocator,
    SourceParseError,
    canonical_row_hash,
    hash_file,
    iter_source_rows,
    read_headers,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "issues.csv"
    path.write_text(
        'Title,State,Key\n登录崩溃,Open,EXT-1\n修复按钮,Done,EXT-2\n"多行\n字段",Open,\n',
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def json_file(tmp_path):
    path = tmp_path / "issues.json"
    path.write_text(
        json.dumps([{"Title": "a", "Key": "K1"}, {"Title": "b", "Key": "K2"}]),
        encoding="utf-8",
    )
    return str(path)


class TestCsvParsing:
    def test_streams_rows_1_based(self, csv_file):
        rows = list(iter_source_rows(csv_file, "csv"))
        assert [n for n, _ in rows] == [1, 2, 3]
        assert rows[0][1]["Title"] == "登录崩溃"
        assert rows[2][1]["Title"] == "多行\n字段"  # quoted multi-line cell

    def test_empty_file_raises(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "csv"))

    def test_binary_garbage_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_bytes(b"\xff\xfe\x00garbage")
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "csv"))

    def test_read_headers_samples(self, csv_file):
        headers, samples = read_headers(csv_file, "csv", sample_rows=2)
        assert headers == ["Title", "State", "Key"]
        assert len(samples) == 2


class TestJsonParsing:
    def test_array_streaming(self, json_file):
        rows = list(iter_source_rows(json_file, "json"))
        assert [n for n, _ in rows] == [1, 2]
        assert rows[1][1] == {"Title": "b", "Key": "K2"}

    def test_pretty_printed_array(self, tmp_path):
        path = tmp_path / "pretty.json"
        path.write_text(json.dumps([{"a": 1}, {"b": 2}, {"c": 3}], indent=4), encoding="utf-8")
        rows = list(iter_source_rows(str(path), "json"))
        assert len(rows) == 3

    def test_non_object_rows_rejected(self, tmp_path):
        path = tmp_path / "scalars.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "json"))

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text('{"unterminated": ', encoding="utf-8")
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "json"))

    def test_empty_json_raises(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("   ", encoding="utf-8")
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "json"))

    def test_read_headers_from_first_object(self, json_file):
        headers, samples = read_headers(json_file, "json")
        assert headers == ["Title", "Key"]
        assert len(samples) == 2


class TestRowKeyAllocator:
    def test_first_ref_claims_ref_key(self):
        import hashlib

        allocator = RowKeyAllocator()
        key, dup = allocator.key_for(1, {"a": "1"}, "EXT-1")
        # L1: the ref is hashed into a fixed-length key (no btree overflow),
        # NOT embedded raw; still stable + distinct from content-addressed keys.
        assert dup is False
        assert key == "ref:" + hashlib.sha256(b"EXT-1").hexdigest()
        assert key != "ref:EXT-1"

    def test_duplicate_ref_falls_back_content_addressed(self):
        allocator = RowKeyAllocator()
        allocator.key_for(1, {"a": "1"}, "EXT-1")
        key, dup = allocator.key_for(2, {"a": "2"}, "EXT-1")
        assert dup is True
        assert key == f"row:2:{canonical_row_hash({'a': '2'})}"

    def test_unmapped_rows_content_addressed(self):
        allocator = RowKeyAllocator()
        key1, dup1 = allocator.key_for(1, {"x": "y"}, None)
        key2, dup2 = allocator.key_for(2, {"x": "y"}, None)
        assert dup1 is False and dup2 is False
        assert key1 != key2  # same content, different row numbers → distinct
        assert key1 == f"row:1:{canonical_row_hash({'x': 'y'})}"

    def test_deterministic_across_instances(self):
        first = RowKeyAllocator()
        second = RowKeyAllocator()
        keys_a = [first.key_for(n, {"v": n}, f"R{n % 2}") for n in (1, 2, 3, 4)]
        keys_b = [second.key_for(n, {"v": n}, f"R{n % 2}") for n in (1, 2, 3, 4)]
        assert keys_a == keys_b  # replay of the same source → identical key set


class TestHashFile:
    def test_hash_is_stable_sha256(self, tmp_path):
        path = tmp_path / "f.bin"
        path.write_bytes(b"mesh" * 1000)
        import hashlib

        assert hash_file(str(path)) == hashlib.sha256(b"mesh" * 1000).hexdigest()
        assert os.path.exists(str(path))
