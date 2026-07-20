"""X29.6.1 — Discovery Window Correctness.

Covers the sprint's success criteria: a confirmed launch must never become
undiscoverable simply because it moved beyond a hardcoded browsing window.

Tests:
  1. parse_window_param normalizes exactly the 4 required values, defaults
     to 24h for anything unrecognized (never silently defaults to "all").
  2. window_seconds_for maps each value to the correct seconds.
  3. empty_state_message never reads as "Discovery has no data" and always
     suggests the other window options.
  4. SWRCache correctly keys 4 distinct window_seconds values as 4 distinct
     entries (no accidental cache-sharing across windows).
  5. The confirmed WATCHTOWER example from X29.6 (create_time ~4.66 days
     old) is excluded at 24h and included at 7d/30d/all, using the exact
     since-cutoff arithmetic the routes use.
"""
from __future__ import annotations

import time

import pytest

from src.ops.discovery_window import (
    parse_window_param, window_seconds_for, empty_state_message,
    WINDOW_24H, WINDOW_7D, WINDOW_30D, WINDOW_ALL, WINDOW_ORDER, WINDOW_LABELS,
)
from src.ops.swr_cache import SWRCache


# ─────────────────────── parse_window_param ───────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("24h", WINDOW_24H),
    ("7d", WINDOW_7D),
    ("30d", WINDOW_30D),
    ("all", WINDOW_ALL),
    ("24H", WINDOW_24H),  # case-insensitive
    (" 7d ", WINDOW_7D),  # whitespace-tolerant
])
def test_parse_window_param_recognizes_all_four_values(raw, expected):
    assert parse_window_param(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "bogus", "365d", "1h"])
def test_parse_window_param_defaults_to_24h_for_unrecognized(raw):
    """Never silently falls through to 'all' -- the previous bug's exact
    shape (only 24h vs. everything-else-is-all)."""
    assert parse_window_param(raw) == WINDOW_24H


def test_window_order_has_exactly_four_values():
    assert WINDOW_ORDER == (WINDOW_24H, WINDOW_7D, WINDOW_30D, WINDOW_ALL)


# ─────────────────────── window_seconds_for ───────────────────────

def test_window_seconds_for_24h_is_one_day():
    assert window_seconds_for(WINDOW_24H) == 86400


def test_window_seconds_for_7d_is_seven_days():
    assert window_seconds_for(WINDOW_7D) == 7 * 86400


def test_window_seconds_for_30d_is_thirty_days():
    assert window_seconds_for(WINDOW_30D) == 30 * 86400


def test_window_seconds_for_all_is_at_least_a_year():
    assert window_seconds_for(WINDOW_ALL) >= 365 * 86400


def test_window_seconds_strictly_increasing():
    seconds = [window_seconds_for(w) for w in WINDOW_ORDER]
    assert seconds == sorted(seconds)
    assert len(set(seconds)) == 4  # all four distinct


# ─────────────────────── empty_state_message ───────────────────────

def test_empty_state_message_never_says_no_data():
    msg = empty_state_message(WINDOW_24H)
    assert "no data" not in msg.lower()
    assert "24 Hours" in msg


def test_empty_state_message_suggests_other_windows():
    msg = empty_state_message(WINDOW_24H)
    for other in (WINDOW_LABELS[WINDOW_7D], WINDOW_LABELS[WINDOW_30D], WINDOW_LABELS[WINDOW_ALL]):
        assert other in msg


def test_empty_state_message_for_all_window_suggests_smaller_windows():
    msg = empty_state_message(WINDOW_ALL)
    assert WINDOW_LABELS[WINDOW_24H] in msg
    assert WINDOW_LABELS[WINDOW_ALL] not in msg.replace(f"selected {WINDOW_LABELS[WINDOW_ALL]}", "")


# ─────────────────────── SWRCache keys windows distinctly ───────────────────────

def test_swr_cache_keys_each_window_seconds_as_a_distinct_entry():
    cache = SWRCache(ttl_seconds=300)
    call_counts = {}

    def make_compute(window_seconds):
        def compute():
            call_counts[window_seconds] = call_counts.get(window_seconds, 0) + 1
            return {"window_seconds": window_seconds, "total_launches": window_seconds}
        return compute

    results = {}
    for w in WINDOW_ORDER:
        seconds = window_seconds_for(w)
        value, _meta = cache.get(seconds, make_compute(seconds))
        results[w] = value

    # each window produced its OWN distinct value, not a shared/cross-contaminated one
    assert results[WINDOW_24H]["total_launches"] == 86400
    assert results[WINDOW_7D]["total_launches"] == 7 * 86400
    assert results[WINDOW_30D]["total_launches"] == 30 * 86400
    assert results[WINDOW_ALL]["total_launches"] == window_seconds_for(WINDOW_ALL)
    # each key computed exactly once (cold start), never reused another key's cached value
    assert len(call_counts) == 4
    assert all(n == 1 for n in call_counts.values())


def test_swr_cache_repeated_get_same_window_does_not_recompute():
    cache = SWRCache(ttl_seconds=300)
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    v1, _ = cache.get(86400, compute)
    v2, _ = cache.get(86400, compute)
    assert v1 is v2  # FRESH hit, same object, no recompute
    assert len(calls) == 1


# ─────────────────────── Confirmed WATCHTOWER launch scenario (X29.6) ───────────────────────

# The exact traced example from the X29.6 audit: create_time is ~4.66 days
# before "now" -- excluded at 24h, included at every wider window.
_NOW = 1784451072
_CREATE_TIME = 1784048633  # 2026-07-14T17:03:53Z, ~4.66 days before _NOW


@pytest.mark.parametrize("window,should_be_visible", [
    (WINDOW_24H, False),
    (WINDOW_7D, True),
    (WINDOW_30D, True),
    (WINDOW_ALL, True),
])
def test_confirmed_watchtower_launch_visibility_by_window(window, should_be_visible):
    """Reproduces the exact since = now - window_seconds arithmetic every
    builder (investigation_pipeline.py, funding_topology.py, etc.) uses."""
    since = _NOW - window_seconds_for(window)
    visible = _CREATE_TIME >= since
    assert visible is should_be_visible


def test_confirmed_watchtower_launch_age_matches_audit_finding():
    age_days = (_NOW - _CREATE_TIME) / 86400
    assert 4.6 < age_days < 4.7  # matches X29.6's "~4.66 days old" finding
