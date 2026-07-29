"""SSE follow-loop + one-shot history fetch (cli.md C12, runtime.md §3.3) —
the socket-level behavior beyond the frame parser (resume de-duplication,
reconnect on a dropped stream, ``end`` handling, history shaping)."""

from __future__ import annotations

import json

from meshcli.sse import fetch_history, follow_logs


def _sse_lines(*events) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return lines


class FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def iter_lines(self):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class FakeStreamClient:
    """Hands out prepared stream responses in order; records the requests."""

    def __init__(self, responses: list[FakeStreamResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def stream_request(self, method, path, *, params=None, headers=None):
        self.calls.append((method, path, dict(params or {})))
        return self.responses.pop(0)


def _log(offset: int, line: str, *, stream: str = "stdout", ts: str | None = "T") -> dict:
    return {"type": "log", "stream": stream, "offset": offset, "line": line, "ts": ts}


class TestFollowLogs:
    def test_follows_until_end_and_returns_final_offset(self):
        # Arrange
        response = FakeStreamResponse(_sse_lines(
            _log(0, "hello"),
            {"type": "heartbeat"},
            {"type": "status", "status": "running"},
            _log(1, "boom", stream="stderr"),
            {"type": "end", "final_offset": 1},
        ))
        client = FakeStreamClient([response])
        rendered: list[str] = []
        # Act
        final = follow_logs(
            client, workspace_id="ws1", execution_id="ex1", on_frame=rendered.append
        )
        # Assert — heartbeat/status skipped, lines rendered with timestamps.
        assert final == 1
        assert rendered == ["T hello", "T boom"]
        assert response.closed is True
        method, path, params = client.calls[0]
        assert method == "GET"
        assert path == "/api/v1/workspaces/ws1/executions/ex1/logs/stream"
        assert params == {"offset": 0}

    def test_timestamps_disabled_emits_raw_lines(self):
        # Arrange
        response = FakeStreamResponse(_sse_lines(
            _log(0, "hello", ts="T0"),
            {"type": "end", "final_offset": 0},
        ))
        rendered: list[str] = []
        # Act
        follow_logs(
            FakeStreamClient([response]),
            workspace_id="ws1",
            execution_id="ex1",
            timestamps=False,
            on_frame=rendered.append,
        )
        # Assert
        assert rendered == ["hello"]

    def test_duplicate_offsets_are_dropped_on_resume(self):
        # Arrange — the server replays offset 0 after a reconnect.
        first = FakeStreamResponse(_sse_lines(_log(0, "a"), _log(1, "b")))  # dropped stream
        second = FakeStreamResponse(_sse_lines(
            _log(1, "b"),  # replayed — must be de-duplicated
            _log(2, "c"),
            {"type": "end", "final_offset": 2},
        ))
        client = FakeStreamClient([first, second])
        rendered: list[str] = []
        # Act
        final = follow_logs(
            client, workspace_id="ws1", execution_id="ex1", on_frame=rendered.append
        )
        # Assert — no loss, no duplicates, monotonic.
        assert final == 2
        assert rendered == ["T a", "T b", "T c"]
        assert client.calls[1][2] == {"offset": 2}  # resumed AFTER the last emitted

    def test_reconnects_when_stream_closes_without_end(self):
        # Arrange — first response carries logs but no ``end`` frame.
        first = FakeStreamResponse(_sse_lines(_log(0, "a")))
        second = FakeStreamResponse(_sse_lines({"type": "end", "final_offset": 0}))
        client = FakeStreamClient([first, second])
        # Act
        final = follow_logs(
            client, workspace_id="ws1", execution_id="ex1", on_frame=lambda _line: None
        )
        # Assert — exactly one reconnect, from the next offset, both closed.
        assert final == 0
        assert len(client.calls) == 2
        assert client.calls[1][2] == {"offset": 1}
        assert first.closed is True and second.closed is True

    def test_end_without_final_offset_returns_last_emitted(self):
        # Arrange
        response = FakeStreamResponse(_sse_lines(
            _log(0, "a"), _log(1, "b"), {"type": "end"}
        ))
        # Act
        final = follow_logs(
            FakeStreamClient([response]),
            workspace_id="ws1",
            execution_id="ex1",
            on_frame=lambda _line: None,
        )
        # Assert
        assert final == 1

    def test_default_on_frame_prints_to_stdout(self, capsys):
        # Arrange
        response = FakeStreamResponse(_sse_lines(
            _log(0, "printed"), {"type": "end", "final_offset": 0}
        ))
        # Act
        follow_logs(FakeStreamClient([response]), workspace_id="w", execution_id="e")
        # Assert
        assert capsys.readouterr().out.splitlines() == ["T printed"]

    def test_start_offset_resumes_mid_stream(self):
        # Arrange
        response = FakeStreamResponse(_sse_lines(
            _log(5, "resumed"), {"type": "end", "final_offset": 5}
        ))
        client = FakeStreamClient([response])
        # Act
        final = follow_logs(
            client, workspace_id="w", execution_id="e", start_offset=5,
            on_frame=lambda _line: None,
        )
        # Assert
        assert final == 5
        assert client.calls[0][2] == {"offset": 5}


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeRequestClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, *, params=None):
        self.calls.append((method, path, dict(params or {})))
        return FakeResponse(self.payload)


class TestFetchHistory:
    def test_dict_data_with_lines_key(self):
        # Arrange
        client = FakeRequestClient({"data": {"lines": [
            {"stream": "stdout", "offset": 0, "line": "hello", "ts": "T0"},
        ]}})
        # Act
        lines = fetch_history(client, workspace_id="ws1", execution_id="ex1")
        # Assert
        assert lines == ["T0 hello"]
        _method, path, params = client.calls[0]
        assert path == "/api/v1/workspaces/ws1/executions/ex1/logs"
        assert params == {"offset": 0}

    def test_list_data_with_text_key_and_stream_param(self):
        # Arrange
        client = FakeRequestClient({"data": [
            {"stream": "stderr", "offset": 3, "text": "legacy", "ts": None},
        ]})
        # Act
        lines = fetch_history(
            client, workspace_id="w", execution_id="e", offset=3, stream="stderr"
        )
        # Assert
        assert lines == ["legacy"]  # no ts → raw line
        assert client.calls[0][2] == {"offset": 3, "stream": "stderr"}

    def test_entries_key_and_timestamps_disabled(self):
        # Arrange
        client = FakeRequestClient({"data": {"entries": [
            {"stream": "stdout", "offset": 1, "line": "raw", "ts": "T1"},
        ]}})
        # Act
        lines = fetch_history(
            client, workspace_id="w", execution_id="e", timestamps=False
        )
        # Assert
        assert lines == ["raw"]

    def test_scalar_entries_stringified(self):
        # Arrange
        client = FakeRequestClient({"data": ["plain line", 7]})
        # Act
        lines = fetch_history(client, workspace_id="w", execution_id="e")
        # Assert
        assert lines == ["plain line", "7"]

    def test_empty_history(self):
        # Arrange
        client = FakeRequestClient({"data": {}})
        # Act / Assert
        assert fetch_history(client, workspace_id="w", execution_id="e") == []
