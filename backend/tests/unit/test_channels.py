"""Channel name parsing."""

from __future__ import annotations

from mesh.realtime.channels import is_valid_channel, parse_channel


def test_parse_simple_entity_channel():
    info = parse_channel("issue:11111111-1111-1111-1111-111111111111")
    assert info is not None
    assert info.entity == "issue"
    assert info.key == "11111111-1111-1111-1111-111111111111"
    assert info.raw == "issue:11111111-1111-1111-1111-111111111111"


def test_parse_multi_segment_key():
    info = parse_channel("execution:abc123:logs")
    assert info is not None
    assert info.entity == "execution"
    assert info.key == "abc123:logs"


def test_parse_workspace_issues_channel():
    assert parse_channel("workspace:ws-1:issues") is not None


def test_invalid_channels_rejected():
    assert parse_channel("") is None
    assert parse_channel("issue") is None  # no colon
    assert parse_channel("Issue:1") is None  # entity must be lowercase
    assert parse_channel("issue:") is None  # empty key
    assert parse_channel("issue:a b") is None  # whitespace
    assert parse_channel("issue:a\nb") is None  # control char
    assert parse_channel("x" * 300) is None  # too long
    assert parse_channel(None) is None  # type: ignore[arg-type]


def test_is_valid_channel():
    assert is_valid_channel("issue:abc")
    assert not is_valid_channel("not a channel")
