"""X24.2 — restart durability: fairness state must survive a process restart
without resetting to a starvation-prone state.

Since fair_sweep_candidates() derives its ordering ENTIRELY from durable
columns (last_swept_at, sweep_count, first_swept_at) rather than any
in-memory structure, "restart" here is modelled correctly as: close the
connection, open a brand new one (simulating a fresh process), and confirm
the ordering is unchanged. There is no in-memory cache to lose.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.core import ws_cascade_store as store


SCHEMA = """
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
"""


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "restart_test.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return str(p)


def _insert(path, subprov, expires_at):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, state, detected_at, expires_at) "
        "VALUES (?, 'ACTIVE', 0, ?)", (subprov, expires_at))
    conn.commit()
    sid = conn.execute("SELECT id FROM wt_active_subprov_sessions WHERE subprov_wallet=?", (subprov,)).fetchone()[0]
    conn.close()
    return sid


def test_fairness_survives_process_restart(db_path):
    id_a = _insert(db_path, "SESSION_A", expires_at=1000)
    id_b = _insert(db_path, "SESSION_B", expires_at=2000)
    id_c = _insert(db_path, "SESSION_C", expires_at=3000)

    # "Process 1": sweep A and B, then simulate a full process restart (new
    # connection, nothing in memory carries over).
    conn1 = sqlite3.connect(db_path)
    store.mark_swept(conn1, id_a, swept_at=100)
    store.mark_swept(conn1, id_b, swept_at=200)
    conn1.close()  # process "dies"

    # "Process 2": brand new connection, no in-memory state whatsoever.
    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    rows = store.fair_sweep_candidates(conn2, limit=10)
    order = [r["subprov_wallet"] for r in rows]

    # C was never swept in "process 1" -- it must be prioritised first after
    # restart, not starved because a naive in-memory fairness tracker reset.
    assert order[0] == "SESSION_C"
    # A was swept longest ago (100 < 200) so comes before B among the swept tier.
    assert order[1:] == ["SESSION_A", "SESSION_B"]
    conn2.close()


def test_sweep_count_accumulates_correctly_across_restarts(db_path):
    sid = _insert(db_path, "MULTI_RESTART_SESSION", expires_at=9999)

    conn1 = sqlite3.connect(db_path)
    store.mark_swept(conn1, sid, swept_at=10)
    conn1.close()

    conn2 = sqlite3.connect(db_path)
    store.mark_swept(conn2, sid, swept_at=20)
    conn2.close()

    conn3 = sqlite3.connect(db_path)
    row = conn3.execute(
        "SELECT sweep_count, first_swept_at, last_swept_at FROM wt_active_subprov_sessions WHERE id=?",
        (sid,)).fetchone()
    assert row == (2, 10, 20)
    conn3.close()


def test_no_in_memory_only_fairness_state_exists():
    """Static guard: confirm fair_sweep_candidates and mark_swept take a
    connection and read/write only durable columns -- no module-level or
    instance-level dict/set is used to track sweep order, which would be lost
    on restart and reintroduce the exact starvation risk this sprint fixes."""
    import inspect
    from src.core import ws_cascade_store as store_mod

    src = inspect.getsource(store_mod.fair_sweep_candidates) + inspect.getsource(store_mod.mark_swept)
    # Neither function should reference any module-level mutable cache.
    assert "_sweep_cache" not in src
    assert "global " not in src
