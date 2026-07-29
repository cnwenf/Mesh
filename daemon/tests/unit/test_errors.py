import pytest

from mesh_runtime.errors import (
    DaemonError,
    FatalAuthError,
    GoneError,
    LeaseConflictError,
    ProtocolError,
    RateLimitedError,
    ServerError,
    classify_response,
)


class TestClassifyResponse:
    @pytest.mark.parametrize("status", [200, 201, 204, 299])
    def test_2xx_passes(self, status):
        assert classify_response(status, None) is None

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures_are_fatal(self, status):
        with pytest.raises(FatalAuthError):
            classify_response(status, {"error": {"code": "invalid_token"}})

    def test_409_with_lease_code(self):
        with pytest.raises(LeaseConflictError) as exc_info:
            classify_response(409, {"error": {"code": "lease_seq_mismatch"}})
        assert exc_info.value.code == "lease_seq_mismatch"

    def test_409_without_body(self):
        with pytest.raises(LeaseConflictError) as exc_info:
            classify_response(409, None)
        assert exc_info.value.code is None

    def test_409_carries_error_details(self):
        with pytest.raises(LeaseConflictError) as exc_info:
            classify_response(
                409, {"error": {"code": "offset_mismatch", "details": {"expected": 7}}}
            )
        assert exc_info.value.code == "offset_mismatch"
        assert exc_info.value.details == {"expected": 7}

    def test_409_details_default_to_empty_when_absent(self):
        with pytest.raises(LeaseConflictError) as exc_info:
            classify_response(409, {"error": {"code": "lease_seq_mismatch"}})
        assert exc_info.value.details == {}

    def test_409_details_ignores_non_dict(self):
        with pytest.raises(LeaseConflictError) as exc_info:
            classify_response(409, {"error": {"code": "x", "details": "not-a-dict"}})
        assert exc_info.value.details == {}

    def test_410_gone(self):
        with pytest.raises(GoneError):
            classify_response(410, {"error": {"code": "activation_expired"}})

    def test_429_carries_retry_after(self):
        with pytest.raises(RateLimitedError) as exc_info:
            classify_response(429, None, retry_after=12.5)
        assert exc_info.value.retry_after == 12.5

    def test_429_without_retry_after(self):
        with pytest.raises(RateLimitedError) as exc_info:
            classify_response(429, None)
        assert exc_info.value.retry_after is None

    @pytest.mark.parametrize("status", [500, 502, 503, 599])
    def test_5xx_is_retryable_server_error(self, status):
        with pytest.raises(ServerError):
            classify_response(status, None)

    @pytest.mark.parametrize("status", [400, 404, 418, 422])
    def test_unenumerated_4xx_fails_closed(self, status):
        with pytest.raises(ProtocolError):
            classify_response(status, None)

    def test_error_code_extraction_fallback_to_top_level(self):
        with pytest.raises(LeaseConflictError) as exc_info:
            classify_response(409, {"code": "attempt_terminal"})
        assert exc_info.value.code == "attempt_terminal"

    def test_error_code_ignores_malformed_body(self):
        with pytest.raises(ServerError):
            classify_response(500, {"error": "not-a-dict"})

    def test_message_does_not_echo_body_contents(self):
        with pytest.raises(DaemonError) as exc_info:
            classify_response(500, {"error": {"code": "boom", "detail": "secret=path=/root/x"}})
        assert "secret" not in str(exc_info.value)
        assert "/root/x" not in str(exc_info.value)
        assert "boom" in str(exc_info.value)  # code only
