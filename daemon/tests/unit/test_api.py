import httpx
import pytest

from mesh_runtime.api import (
    ActivateResponse,
    RuntimeApiClient,
    _parse_retry_after,
)
from mesh_runtime.errors import (
    FatalAuthError,
    LeaseConflictError,
    RateLimitedError,
    ServerError,
)

TOKEN = "mesh_rt_test-token"
RUNTIME_ID = "11111111-1111-1111-1111-111111111111"
ATTEMPT_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def server(fake_server):
    return fake_server


def client(server, token=TOKEN):
    return RuntimeApiClient(
        "https://mesh.example.com",
        token,
        transport=server.transport(),
    )


class TestActivate:
    async def test_posts_code_and_metadata_without_bearer(self, server):
        server.enqueue(
            "POST /api/v1/daemon/runtimes:activate",
            200,
            {
                "data": {
                    "runtime_id": RUNTIME_ID,
                    "runtime_token": TOKEN,
                    "heartbeat_interval_seconds": 15,
                }
            },
        )
        api = client(server, token=None)
        resp = await api.activate("activate-code-123", {"hostname": "box"})
        assert isinstance(resp, ActivateResponse)
        assert resp.runtime_id == RUNTIME_ID
        assert resp.runtime_token == TOKEN
        assert resp.heartbeat_interval_seconds == 15.0
        call = server.calls_for("POST /api/v1/daemon/runtimes:activate")[0]
        assert call.body == {"activation_code": "activate-code-123", "metadata": {"hostname": "box"}}
        assert "authorization" not in call.headers

    async def test_activate_error_maps(self, server):
        server.enqueue(
            "POST /api/v1/daemon/runtimes:activate",
            410,
            {"error": {"code": "activation_expired", "message": "expired"}},
        )
        from mesh_runtime.errors import GoneError

        with pytest.raises(GoneError):
            await client(server, token=None).activate("bad-code", {})


class TestAuthHeader:
    async def test_bearer_sent_on_token_endpoints(self, server):
        server.enqueue(f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat", 200, {"data": {}})
        api = client(server)
        await api.heartbeat(RUNTIME_ID, current_load=0, health="healthy", metrics={}, inflight=[])
        call = server.calls_for(f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat")[0]
        assert call.headers["authorization"] == f"Bearer {TOKEN}"

    async def test_missing_token_raises_before_request(self, server):
        api = client(server, token=None)
        with pytest.raises(FatalAuthError, match="no runtime token"):
            await api.heartbeat(RUNTIME_ID, current_load=0, health="healthy", metrics={}, inflight=[])
        assert server.calls == []  # nothing hit the wire


class TestHeartbeat:
    async def test_parses_commands(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat",
            200,
            {
                "data": {
                    "server_time": "2026-07-29T10:00:00Z",
                    "commands": [
                        {"type": "cancel_execution", "attempt_id": ATTEMPT_ID, "grace_seconds": 15}
                    ],
                }
            },
        )
        resp = await client(server).heartbeat(
            RUNTIME_ID, current_load=1, health="healthy", metrics={"a": 1}, inflight=[ATTEMPT_ID]
        )
        assert resp.server_time == "2026-07-29T10:00:00Z"
        assert len(resp.commands) == 1
        assert resp.cancel_commands()[0].attempt_id == ATTEMPT_ID
        assert resp.cancel_commands()[0].grace_seconds == 15

    async def test_body_shape(self, server):
        server.enqueue(f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat", 200, {"data": {}})
        await client(server).heartbeat(
            RUNTIME_ID, current_load=2, health="degraded", metrics={}, inflight=[ATTEMPT_ID]
        )
        call = server.calls_for(f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat")[0]
        assert call.body == {
            "current_load": 2,
            "health": "degraded",
            "metrics": {},
            "inflight": [ATTEMPT_ID],
        }


class TestClaim:
    async def test_204_returns_none(self, server):
        server.default_status = 204
        assert await client(server).claim(RUNTIME_ID) is None

    async def test_200_returns_execution_and_attempt(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}/executions:claim",
            200,
            {
                "data": {
                    "execution": {"id": "exec-1", "config_snapshot": {}},
                    "attempt": {"id": ATTEMPT_ID, "lease_seq": 1},
                }
            },
        )
        resp = await client(server).claim(RUNTIME_ID)
        assert resp is not None
        assert resp.attempt_id == ATTEMPT_ID
        assert resp.lease_seq == 1
        assert resp.execution["id"] == "exec-1"


class TestLeaseAndLogs:
    async def test_renew_returns_new_lease(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}:renew-lease",
            200,
            {"data": {"lease_seq": 6, "lease_expires_at": "2026-07-29T10:02:00Z"}},
        )
        info = await client(server).renew_lease(ATTEMPT_ID, lease_seq=5)
        assert info.lease_seq == 6
        assert info.lease_expires_at == "2026-07-29T10:02:00Z"
        call = server.calls_for(f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}:renew-lease")[0]
        assert call.body == {"lease_seq": 5}

    async def test_renew_409_lease_mismatch(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}:renew-lease",
            409,
            {"error": {"code": "lease_seq_mismatch", "message": "stale"}},
        )
        with pytest.raises(LeaseConflictError) as exc:
            await client(server).renew_lease(ATTEMPT_ID, lease_seq=1)
        assert exc.value.code == "lease_seq_mismatch"

    async def test_append_logs_ack(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}/logs",
            200,
            {"data": {"accepted_end_offset": 512, "redacted_hits": 1}},
        )
        ack = await client(server).append_logs(
            ATTEMPT_ID, lease_seq=3, stream="stdout", start_offset=0, lines=["a", "b"], sealed=False
        )
        assert ack.accepted_end_offset == 512
        assert ack.redacted_hits == 1
        call = server.calls_for(f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}/logs")[0]
        assert call.body["start_offset"] == 0
        assert call.body["lines"] == ["a", "b"]
        assert call.body["sealed"] is False

    async def test_transition_body(self, server):
        server.enqueue(f"PATCH /api/v1/daemon/attempts/{ATTEMPT_ID}", 200, {"data": {}})
        await client(server).transition(
            ATTEMPT_ID, lease_seq=9, status="completed", result={"schema_version": 1}
        )
        call = server.calls_for(f"PATCH /api/v1/daemon/attempts/{ATTEMPT_ID}")[0]
        assert call.method == "PATCH"
        assert call.body == {
            "lease_seq": 9,
            "status": "completed",
            "result": {"schema_version": 1},
            "failure_reason": None,
        }


class TestErrorMapping:
    async def test_401_is_fatal(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat",
            401,
            {"error": {"code": "invalid_token", "message": "no"}},
        )
        with pytest.raises(FatalAuthError):
            await client(server).heartbeat(
                RUNTIME_ID, current_load=0, health="healthy", metrics={}, inflight=[]
            )

    async def test_500_is_server_error(self, server):
        server.enqueue(f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat", 500, None)
        with pytest.raises(ServerError):
            await client(server).heartbeat(
                RUNTIME_ID, current_load=0, health="healthy", metrics={}, inflight=[]
            )

    async def test_429_carries_retry_after(self, server):
        server.enqueue(
            f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}/executions:claim",
            429,
            None,
            headers={"Retry-After": "30"},
        )
        with pytest.raises(RateLimitedError) as exc:
            await client(server).claim(RUNTIME_ID)
        assert exc.value.retry_after == 30.0

    async def test_transport_error_is_server_error(self, server):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        api = RuntimeApiClient("https://mesh.example.com", TOKEN, transport=httpx.MockTransport(boom))
        with pytest.raises(ServerError):
            await api.claim(RUNTIME_ID)


class TestRetryAfterParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [("30", 30.0), ("0", 0.0), (" 12 ", 12.0), (None, None), ("soon", None), ("-5", None)],
    )
    def test_parse(self, raw, expected):
        assert _parse_retry_after(raw) == expected


class TestNoTokenLeak:
    def test_repr_does_not_contain_token(self):
        api = RuntimeApiClient("https://mesh.example.com", TOKEN)
        assert TOKEN not in repr(api)
        assert "mesh_rt" not in repr(api)
