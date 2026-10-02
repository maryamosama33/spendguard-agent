from spendguard.ratelimit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: FakeClock, max_calls=4, window=60.0) -> RateLimiter:
    return RateLimiter(max_calls, window, clock=clock.time, sleep=clock.sleep)


def test_calls_under_limit_do_not_wait():
    clock = FakeClock()
    limiter = _limiter(clock)
    assert [limiter.acquire() for _ in range(4)] == [0.0, 0.0, 0.0, 0.0]
    assert clock.now == 0.0


def test_call_over_limit_waits_for_oldest_to_expire():
    clock = FakeClock()
    limiter = _limiter(clock)
    for t in (0.0, 10.0, 20.0, 30.0):
        clock.now = t
        limiter.acquire()

    clock.now = 35.0
    waited = limiter.acquire()

    assert waited == 25.0  # oldest call (t=0) frees its slot at t=60
    assert clock.now == 60.0


def test_slots_free_up_after_window():
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(4):
        limiter.acquire()

    clock.now = 60.0
    assert limiter.acquire() == 0.0
