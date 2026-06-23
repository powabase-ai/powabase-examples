"""Per-user rate limiting for expensive AI operations.

A simple in-process fixed-window counter keyed by (scope, user). This is correct
for the single-instance deployment RankForge targets; a horizontally-scaled
deployment would move this to Redis (the cap would otherwise be per-replica).

Used as a FastAPI dependency on the expensive POST routes (generate / refine /
research / optimize / score / scout-run / opportunity-draft) so one user can't
exhaust shared LLM/scrape budget or the background-task pool for everyone else.
"""

import threading
import time

from fastapi import Depends, HTTPException, status

from .auth import get_current_user
from .config import get_settings
from .models.profile import CurrentUser

# key -> [count, window_reset_monotonic]
_buckets: dict[tuple[str, str], list[float]] = {}
_lock = threading.Lock()


def _allow(key: tuple[str, str], limit: int, window: float) -> bool:
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None or now >= bucket[1]:
            _buckets[key] = [1, now + window]
            return True
        if bucket[0] >= limit:
            return False
        bucket[0] += 1
        return True


def rate_limit(scope: str):
    """Build a dependency that rate-limits the caller for `scope` using the
    configured per-window limit. 429 with Retry-After when exceeded."""

    def _dep(user: CurrentUser = Depends(get_current_user)) -> None:
        settings = get_settings()
        limit = settings.rate_limit_expensive
        window = settings.rate_limit_window_seconds
        if not _allow((scope, str(user.id)), limit, window):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate limit exceeded; slow down",
                headers={"Retry-After": str(int(window))},
            )

    return _dep


def reset() -> None:
    """Clear all buckets (test helper)."""
    with _lock:
        _buckets.clear()
