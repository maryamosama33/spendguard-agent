import pytest

from spendguard import extraction
from spendguard.ratelimit import RateLimiter


@pytest.fixture(autouse=True)
def no_gemini_rate_limit(monkeypatch):
    """Tests mock Gemini, so the real 4-per-minute limiter would only make them sleep."""
    monkeypatch.setattr(extraction, "_gemini_limiter", RateLimiter(max_calls=10**6, window=60.0))
