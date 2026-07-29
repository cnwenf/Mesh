"""Coverage-completion tests for api.py branches not exercised elsewhere."""

import pytest

from mesh_runtime.api import ClaimResponse, HeartbeatResponse, RuntimeApiClient
from mesh_runtime.errors import ProtocolError

TOKEN = "mesh_rt_test-token"
RUNTIME_ID = "11111111-1111-1111-1111-111111111111"
ATTEMPT_ID = "22222222-2222-2222-2222-222222222222"
EXECUTION_ID = "33333333-3333-3333-3333-333333333333"


def client(server):
    return RuntimeApiClient("https://mesh.example.com", TOKEN, transport=server.transport())


class TestRemainingEndpoints:
    async def test_report_checkout(self, fake_server):
        fake_server.enqueue(f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}/checkouts", 200, {"data": {}})
        await client(fake_server).report_checkout(
            ATTEMPT_ID,
            lease_seq=2,
            status="ready",
            repo_url="https://git.example.com/repo.git",
            base_ref="main",
            commit_sha="abc123",
        )
        call = fake_server.calls_for(f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}/checkouts")[0]
        assert call.body["status"] == "ready"
        assert call.body["commit_sha"] == "abc123"

    async def test_refetch_credentials(self, fake_server):
        fake_server.enqueue(
            f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}/credentials:refetch",
            200,
            {"data": {"credentials": [{"id": "c1", "env": "GH_TOKEN"}]}},
        )
        creds = await client(fake_server).refetch_credentials(ATTEMPT_ID, lease_seq=4)
        assert creds == [{"id": "c1", "env": "GH_TOKEN"}]

    async def test_request_approval(self, fake_server):
        fake_server.enqueue(
            f"POST /api/v1/daemon/executions/{EXECUTION_ID}/approvals", 200, {"data": {"id": "ap1"}}
        )
        data = await client(fake_server).request_approval(
            EXECUTION_ID,
            lease_seq=7,
            attempt_id=ATTEMPT_ID,
            action_summary={"tool": "git.push"},
        )
        assert data == {"id": "ap1"}
        call = fake_server.calls_for(f"POST /api/v1/daemon/executions/{EXECUTION_ID}/approvals")[0]
        assert call.body["resume_context"] == {}


class TestProtocolErrors:
    async def test_activate_without_data_envelope(self, fake_server):
        fake_server.enqueue("POST /api/v1/daemon/runtimes:activate", 200, {"unexpected": True})
        with pytest.raises(ProtocolError, match="activate"):
            await RuntimeApiClient(
                "https://x.example", None, transport=fake_server.transport()
            ).activate("code-12345", {})

    async def test_claim_with_malformed_data(self, fake_server):
        fake_server.enqueue(
            f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}/executions:claim",
            200,
            {"data": {"execution": "not-a-dict"}},
        )
        with pytest.raises(ProtocolError, match="execution/attempt"):
            await client(fake_server).claim(RUNTIME_ID)

    async def test_renew_without_data(self, fake_server):
        fake_server.enqueue(f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}:renew-lease", 200, {})
        with pytest.raises(ProtocolError, match="renew"):
            await client(fake_server).renew_lease(ATTEMPT_ID, lease_seq=1)

    async def test_non_json_200_body_treated_as_empty(self, fake_server):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"})

        api = RuntimeApiClient("https://x.example", TOKEN, transport=httpx.MockTransport(handler))
        ack = await api.append_logs(ATTEMPT_ID, lease_seq=1, stream="stdout", start_offset=0, lines=[])
        assert ack.accepted_end_offset == 0  # fallback to start_offset

    async def test_append_logs_no_data_defaults(self, fake_server):
        fake_server.enqueue(f"POST /api/v1/daemon/attempts/{ATTEMPT_ID}/logs", 200, {"data": {}})
        ack = await client(fake_server).append_logs(
            ATTEMPT_ID, lease_seq=1, stream="stderr", start_offset=77, lines=["x"]
        )
        assert ack.accepted_end_offset == 77


class TestResponseModels:
    def test_claim_response_helpers(self):
        resp = ClaimResponse(
            execution={"id": "e1"},
            attempt={"id": "a1", "lease_seq": 3, "lease_expires_at": "t"},
        )
        assert resp.execution_id == "e1"
        assert resp.attempt_id == "a1"
        assert resp.lease_seq == 3
        assert resp.config_snapshot == {}  # missing snapshot -> empty dict
        assert resp.credentials == []

    def test_claim_response_snapshot_and_credentials(self):
        resp = ClaimResponse(
            execution={"id": "e1", "config_snapshot": {"repo": "r"}},
            attempt={"id": "a1", "lease_seq": 1, "lease_expires_at": "t",
                     "credentials": [{"id": "c1"}, "junk"]},
        )
        assert resp.config_snapshot == {"repo": "r"}
        assert resp.credentials == [{"id": "c1"}]  # non-dict filtered

    def test_cancel_commands_skip_malformed(self):
        resp = HeartbeatResponse(
            server_time=None,
            commands=[
                {"type": "other"},
                {"type": "cancel_execution"},  # missing attempt_id
                {"type": "cancel_execution", "attempt_id": "a1", "grace_seconds": None},
            ],
        )
        cancels = resp.cancel_commands()
        assert len(cancels) == 1
        assert cancels[0].grace_seconds == 15.0  # default when falsy
        assert cancels[0].execution_id is None
