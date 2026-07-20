"""X24.2 Phase 2 — fair, bounded sweep scheduler tests.

Proves the replacement for the old unordered active_sessions()[:MAX_ACTIVE_SUBPROVS]
slice (which could let a session outside the arbitrary top-N expire without ever
being inspected — the proven AWiaGsus-class coverage defect):

  - every eligible session is eventually selected under sustained load (no starvation)
  - never-swept sessions are prioritised over already-swept ones
  - soon-to-expire never-swept sessions are prioritised within the never-swept tier
  - scheduling is deterministic (same DB state -> same order, every time)
  - the durable columns (not in-memory state) mean a process restart does not
    reset fairness to a starvation-prone state
  - mark_swept is idempotent bookkeeping (repeated calls don't corrupt state,
    just accumulate sweep_count)
  - a simulated AWiaGsus-class session (competing against hundreds of others)
    is inspected before its expiry under the fair scheduler, where the old
    unordered top-10 slice could miss it entirely
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.core import ws_cascade_store as store


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov_wallet TEXT NOT NULL,
            treasury_wallet TEXT,
            funding_signature TEXT,
            funding_amount REAL,
            funding_time INTEGER,
            state TEXT NOT NULL DEFAULT 'ACTIVE',
            detected_at INTEGER NOT NULL,
            expires_at INTEGER,
            closed_at INTEGER,
            subprov_known INTEGER DEFAULT 0,
            open_reason TEXT DEFAULT 'PROVISION_CANDIDATE',
            initial_funding_amount REAL,
            topup_count INTEGER DEFAULT 0,
            topup_amount_total REAL DEFAULT 0.0,
            last_topup_at INTEGER,
            monitoring_state TEXT DEFAULT 'LIVE_ARMED',
            funding_sequence_number INTEGER,
            treasury_rotated INTEGER DEFAULT 0,
            last_activity_at INTEGER,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            session_tag TEXT DEFAULT NULL,
            operation_state TEXT,
            last_swept_at INTEGER,
            sweep_count INTEGER NOT NULL DEFAULT 0,
            first_swept_at INTEGER,
            UNIQUE(subprov_wallet, funding_signature)
        );
    """)
    c.commit()
    yield c
    c.close()


def _insert_session(conn, subprov, expires_at, detected_at=0, funding_mechanism="WSOL_WRAP_CLOSE"):
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions "
        "(subprov_wallet, state, detected_at, expires_at, funding_mechanism) "
        "VALUES (?, 'ACTIVE', ?, ?, ?)",
        (subprov, detected_at, expires_at, funding_mechanism))
    conn.commit()
    return conn.execute("SELECT id FROM wt_active_subprov_sessions WHERE subprov_wallet=?", (subprov,)).fetchone()[0]


def test_never_swept_sessions_prioritised_over_swept(conn):
    id_a = _insert_session(conn, "SWEPT_ALREADY", expires_at=1000)
    id_b = _insert_session(conn, "NEVER_SWEPT", expires_at=2000)
    store.mark_swept(conn, id_a, swept_at=500)
    rows = store.fair_sweep_candidates(conn, limit=10)
    wallets_in_order = [r["subprov_wallet"] for r in rows]
    assert wallets_in_order[0] == "NEVER_SWEPT"
    assert wallets_in_order[1] == "SWEPT_ALREADY"


def test_never_swept_tier_orders_by_soonest_expiry(conn):
    _insert_session(conn, "EXPIRES_LATE", expires_at=9999)
    _insert_session(conn, "EXPIRES_SOON", expires_at=100)
    rows = store.fair_sweep_candidates(conn, limit=10)
    wallets_in_order = [r["subprov_wallet"] for r in rows]
    assert wallets_in_order == ["EXPIRES_SOON", "EXPIRES_LATE"]


def test_swept_tier_orders_by_least_recently_swept(conn):
    id_a = _insert_session(conn, "SWEPT_LONG_AGO", expires_at=5000)
    id_b = _insert_session(conn, "SWEPT_RECENTLY", expires_at=5000)
    store.mark_swept(conn, id_a, swept_at=100)
    store.mark_swept(conn, id_b, swept_at=900)
    rows = store.fair_sweep_candidates(conn, limit=10)
    wallets_in_order = [r["subprov_wallet"] for r in rows]
    assert wallets_in_order == ["SWEPT_LONG_AGO", "SWEPT_RECENTLY"]


def test_deterministic_id_tiebreaker_when_all_else_equal(conn):
    id_a = _insert_session(conn, "A", expires_at=5000)
    id_b = _insert_session(conn, "B", expires_at=5000)
    rows1 = store.fair_sweep_candidates(conn, limit=10)
    rows2 = store.fair_sweep_candidates(conn, limit=10)
    assert [r["id"] for r in rows1] == [r["id"] for r in rows2]  # deterministic
    assert [r["id"] for r in rows1] == sorted([id_a, id_b])       # tie-break by id ASC


def test_no_starvation_under_sustained_load_exceeding_cap(conn):
    """Simulates many cycles with 500 eligible sessions and a cap of 10 per
    cycle — every session must eventually be swept at least once, proving no
    starvation, unlike the old unordered top-10 slice which had no such
    guarantee."""
    N = 500
    CAP = 10
    ids = [_insert_session(conn, f"WALLET_{i}", expires_at=10_000_000) for i in range(N)]

    swept_ids = set()
    now = 1000
    max_cycles = (N // CAP) + 5  # generous bound; should finish within N/CAP cycles
    for _cycle in range(max_cycles):
        rows = store.fair_sweep_candidates(conn, limit=CAP)
        if not rows:
            break
        for r in rows:
            store.mark_swept(conn, r["id"], swept_at=now)
            swept_ids.add(r["id"])
        now += 6
        if len(swept_ids) >= N:
            break

    assert swept_ids == set(ids), (
        f"starvation detected: {N - len(swept_ids)} session(s) never swept "
        f"after {max_cycles} cycles"
    )


def test_awiagsus_class_session_inspected_before_expiry_among_hundreds(conn):
    """Simulates the exact proven scenario: a PLAIN_TRANSFER session (like
    AWiaGsus's real 7fBzd... subprov) opened alongside hundreds of competing
    sessions, with a bounded TTL. Under the fair scheduler it must be swept
    before its expiry; under the old unordered top-10 slice this was never
    guaranteed."""
    # AWiaGsus-like session: opened at t=0, expires at t=2073 (real gap: 1784051480 -> 1784053553)
    target_id = _insert_session(conn, "AWIAGSUS_LIKE_SUBPROV", expires_at=2073,
                                  detected_at=0, funding_mechanism="PLAIN_TRANSFER")
    # 300 competing sessions opened in the same window, many with later expiries
    # that would previously have monopolised an unordered/insertion-order top-10 slice.
    for i in range(300):
        _insert_session(conn, f"COMPETITOR_{i}", expires_at=50_000 + i, detected_at=1)

    now = 6
    swept = set()
    cap = 10
    while now < 2073:
        rows = store.fair_sweep_candidates(conn, limit=cap)
        for r in rows:
            store.mark_swept(conn, r["id"], swept_at=now)
            swept.add(r["id"])
        if target_id in swept:
            break
        now += 6

    assert target_id in swept, (
        "AWiaGsus-class session was NOT swept before its expiry even under the "
        "fair scheduler — this would indicate the scheduler itself needs a "
        "wider cap or faster cycle, not just fair ordering"
    )


def test_mark_swept_is_idempotent_bookkeeping(conn):
    sid = _insert_session(conn, "IDEMPOTENT_TEST", expires_at=5000)
    store.mark_swept(conn, sid, swept_at=100)
    store.mark_swept(conn, sid, swept_at=200)
    store.mark_swept(conn, sid, swept_at=300)
    row = conn.execute(
        "SELECT last_swept_at, sweep_count, first_swept_at FROM wt_active_subprov_sessions WHERE id=?",
        (sid,)).fetchone()
    assert row["last_swept_at"] == 300     # always the latest
    assert row["sweep_count"] == 3          # accumulates correctly
    assert row["first_swept_at"] == 100     # never overwritten once set


def test_sweep_coverage_snapshot_reports_never_swept_and_expiring_soon(conn):
    now = int(time.time())
    _insert_session(conn, "NEVER_SWEPT_EXPIRING_SOON", expires_at=now + 30)
    id_swept = _insert_session(conn, "SWEPT_RECENTLY", expires_at=now + 5000)
    store.mark_swept(conn, id_swept, swept_at=now - 10)

    snap = store.sweep_coverage_snapshot(conn, cap=10)
    assert snap["eligible_sessions"] == 2
    assert snap["never_swept"] == 1
    assert snap["expiring_within_60s_never_swept"] == 1
    assert snap["swept_within_30s"] == 1


def test_bounded_rpc_calls_per_cycle_never_exceeds_cap(conn):
    for i in range(1000):
        _insert_session(conn, f"BULK_{i}", expires_at=10_000_000 + i)
    for cap in (1, 5, 10, 50):
        rows = store.fair_sweep_candidates(conn, limit=cap)
        assert len(rows) <= cap
