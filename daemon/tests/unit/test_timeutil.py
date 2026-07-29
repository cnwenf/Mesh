import random

import pytest

from tests.conftest import make_rand

from mesh_runtime.timeutil import FakeClock, SystemClock, full_jitter


class TestFakeClock:
    async def test_sleep_records_and_advances(self):
        clock = FakeClock(start=100.0)
        start = clock.now()
        await clock.sleep(2.5)
        assert clock.sleeps == [2.5]
        assert clock.now() == start + 2.5

    async def test_sleep_zero_or_negative_does_not_advance(self):
        clock = FakeClock(start=100.0)
        await clock.sleep(0)
        await clock.sleep(-1)
        assert clock.now() == 100.0
        assert clock.sleeps == [0, -1]

    def test_advance_rejects_backwards(self):
        clock = FakeClock()
        with pytest.raises(ValueError, match="backwards"):
            clock.advance(-5)

    def test_total_sleep_ignores_nonpositive(self):
        clock = FakeClock()
        clock.sleeps = [1.0, 0, -2.0, 3.0]
        assert clock.total_sleep == 4.0

    def test_utcnow_is_tz_aware(self):
        clock = FakeClock(start=0.0)
        assert clock.utcnow().utcoffset() is not None


class TestSystemClock:
    def test_now_is_monotonic_nondecreasing(self):
        clock = SystemClock()
        assert clock.now() <= clock.now()

    def test_utcnow_is_tz_aware(self):
        assert SystemClock().utcnow().utcoffset() is not None

    async def test_sleep_zero_returns_immediately(self):
        await SystemClock().sleep(0)


class TestFullJitter:
    def test_zero_rand_gives_zero(self):
        assert full_jitter(1.0, 15.0, 0, make_rand([0.0])) == 0.0

    def test_attempt_zero_ceiling_is_base(self):
        # rand≈1 -> just under base*2^0 = base
        assert full_jitter(1.0, 15.0, 0, make_rand([0.999])) == pytest.approx(0.999)

    def test_exponential_growth_until_cap(self):
        # attempt 3: ceiling = min(15, 1*8) = 8 -> rand 1.0 ~ 8
        assert full_jitter(1.0, 15.0, 3, make_rand([1.0])) == pytest.approx(8.0)
        # attempt 10: 1*1024 capped at 15
        assert full_jitter(1.0, 15.0, 10, make_rand([1.0])) == pytest.approx(15.0)

    def test_never_exceeds_cap_with_real_random(self):
        for attempt in range(12):
            delay = full_jitter(2.0, 60.0, attempt, random.random)
            assert 0.0 <= delay <= 60.0

    def test_rejects_nonpositive_base_or_cap(self):
        with pytest.raises(ValueError, match="positive"):
            full_jitter(0, 15.0, 0, make_rand([0.5]))
        with pytest.raises(ValueError, match="positive"):
            full_jitter(1.0, 0, 0, make_rand([0.5]))

    def test_rejects_negative_attempt(self):
        with pytest.raises(ValueError, match="attempt"):
            full_jitter(1.0, 15.0, -1, make_rand([0.5]))
