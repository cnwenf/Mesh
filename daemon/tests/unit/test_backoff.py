import pytest

from mesh_runtime.backoff import (
    EMPTY_QUEUE,
    KEEPALIVE,
    NETWORK,
    RATE_LIMITED_FALLBACK_SECONDS,
    RETRY_AFTER_CAP_SECONDS,
    BackoffPolicy,
    capped_retry_after,
)
from tests.conftest import make_rand


class TestBackoffPolicy:
    def test_empty_queue_bounds_match_spec(self):
        # §3.1: 204 empty queue 1s start, 15s cap
        assert EMPTY_QUEUE.base == 1.0
        assert EMPTY_QUEUE.cap == 15.0

    def test_network_bounds_match_spec(self):
        # §3.1: network/5xx 2s start, 60s cap
        assert NETWORK.base == 2.0
        assert NETWORK.cap == 60.0

    def test_delay_is_zero_when_rand_zero(self):
        assert EMPTY_QUEUE.delay(5, make_rand([0.0])) == 0.0

    def test_delay_grows_then_caps(self):
        policy = BackoffPolicy(base=1.0, cap=15.0)
        ceilings = [policy.delay(a, make_rand([1.0])) for a in range(8)]
        assert ceilings == [
            pytest.approx(1.0),
            pytest.approx(2.0),
            pytest.approx(4.0),
            pytest.approx(8.0),
            pytest.approx(15.0),
            pytest.approx(15.0),
            pytest.approx(15.0),
            pytest.approx(15.0),
        ]

    def test_keepalive_caps_at_15s(self):
        assert KEEPALIVE.delay(20, make_rand([1.0])) == pytest.approx(15.0)

    def test_delay_never_negative(self):
        assert NETWORK.delay(0, make_rand([0.0])) >= 0.0

    def test_invalid_policy_rejected_by_full_jitter(self):
        with pytest.raises(ValueError):
            BackoffPolicy(base=0.0, cap=10.0).delay(0, make_rand([0.5]))


class TestCappedRetryAfter:
    def test_absent_uses_fallback(self):
        assert capped_retry_after(None) == RATE_LIMITED_FALLBACK_SECONDS

    def test_within_bounds_honoured(self):
        assert capped_retry_after(17.5) == 17.5

    def test_huge_capped_at_minute(self):
        assert capped_retry_after(3600.0) == RETRY_AFTER_CAP_SECONDS == 60.0

    def test_negative_clamped_to_zero(self):
        assert capped_retry_after(-5.0) == 0.0
