"""Bounded, deterministic Birdeye 429 retry policy (no transport)."""
from __future__ import annotations

MAX_RATE_LIMIT_RETRIES_PER_LOGICAL_WINDOW = 8
MAX_RATE_LIMIT_WAIT_SECONDS = 60
_SCHEDULE = (2, 5, 10, 30, 60)


def wait_seconds(retry_after: object, retry_number: int) -> int:
    base = _SCHEDULE[min(retry_number - 1, len(_SCHEDULE) - 1)]
    try:
        supplied = float(retry_after or 0)
    except (TypeError, ValueError):
        supplied = 0
    return min(MAX_RATE_LIMIT_WAIT_SECONDS, max(base, int(supplied)))


def retry_allowed(retry_number: int) -> bool:
    return retry_number <= MAX_RATE_LIMIT_RETRIES_PER_LOGICAL_WINDOW


def run_retries(dispatch, sleep):
    """Run only same-window retries; dispatch owns accounting and retention."""
    status, retry_after = dispatch()
    retries = total_wait = 0
    while status == 'RATE_LIMITED':
        retries += 1
        if not retry_allowed(retries):
            return {'status': status, 'retries': retries - 1, 'total_wait_seconds': total_wait,
                    'last_retry_after': retry_after, 'exhausted': True}
        wait = wait_seconds(retry_after, retries)
        sleep(wait)
        total_wait += wait
        status, retry_after = dispatch()
    return {'status': status, 'retries': retries, 'total_wait_seconds': total_wait,
            'last_retry_after': retry_after, 'exhausted': False}
