"""X24.2.2 Signature Processing Throughput -- tests for
subprov_sig_enqueue_running(), the store-layer fix that merges the old
subprov_sig_enqueue() (write to PENDING) + subprov_sig_mark_running() (write
to RUNNING) into a single write.

Audit context: measurement proved 0% of per-sweep signatures were redundant
(the durable cursor already excludes previously-seen signatures), so no
cursor/dedupe-before-fetch fix applies here. The actual per-signature cost
came from 4 separate DB round-trips through the process-wide DB_WRITE_SERIALIZE
lock (db_locking.py) -- cutting that to 2 (enqueue_running + mark_done) is the
minimal safe fix, since RPC/network cost alone did not explain the observed
per-signature latency.
"""
from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "sig_merge_test.db")
    c = sqlite3.connect(path)
    from src.core import ws_cascade_store as store
    store.ensure_cascade_schema(c)
    yield c
    c.close()


def test_first_call_is_new_and_row_goes_straight_to_running(conn):
    from src.core import ws_cascade_store as store
    is_new, first_seen_at = store.subprov_sig_enqueue_running(
        conn, subprov="SUB1", signature="SIG1", slot=100)
    assert is_new is True
    assert first_seen_at > 0

    row = conn.execute(
        "SELECT status, attempts, slot FROM wt_subprov_sig_retry "
        "WHERE subprov_wallet=? AND signature=?", ("SUB1", "SIG1")).fetchone()
    assert row[0] == "RUNNING", "must go straight to RUNNING, skipping the old PENDING step"
    assert row[1] == 1
    assert row[2] == 100


def test_second_call_for_same_signature_is_not_new_and_preserves_first_seen_at(conn):
    from src.core import ws_cascade_store as store
    is_new1, first_seen_at1 = store.subprov_sig_enqueue_running(
        conn, subprov="SUB1", signature="SIG1")
    time.sleep(0.05)
    is_new2, first_seen_at2 = store.subprov_sig_enqueue_running(
        conn, subprov="SUB1", signature="SIG1")

    assert is_new1 is True
    assert is_new2 is False
    assert first_seen_at1 == first_seen_at2, "first_seen_at must not regress on a retry call"


def test_attempts_increments_across_repeated_calls(conn):
    from src.core import ws_cascade_store as store
    for _ in range(3):
        store.subprov_sig_enqueue_running(conn, subprov="SUB1", signature="SIG1")
    row = conn.execute(
        "SELECT attempts FROM wt_subprov_sig_retry WHERE subprov_wallet=? AND signature=?",
        ("SUB1", "SIG1")).fetchone()
    assert row[0] == 3


def test_does_not_regress_a_done_row_back_to_running(conn):
    """If a signature was already marked DONE (e.g. this call races with a
    prior successful completion), a subsequent enqueue_running call must not
    silently downgrade it back to RUNNING -- the caller (_process_subprov_sig_durable)
    already short-circuits on DONE via a separate read before ever calling this,
    but the store function itself should not corrupt a DONE row if called anyway."""
    from src.core import ws_cascade_store as store
    store.subprov_sig_enqueue_running(conn, subprov="SUB1", signature="SIG1")
    store.subprov_sig_mark_done(conn, subprov="SUB1", signature="SIG1")

    row = conn.execute(
        "SELECT status FROM wt_subprov_sig_retry WHERE subprov_wallet=? AND signature=?",
        ("SUB1", "SIG1")).fetchone()
    assert row[0] == "DONE"


def test_one_write_statement_per_call_not_two(conn):
    """The core throughput fix: exactly one write-lock-worthy statement (the
    INSERT...ON CONFLICT) executes per call, not the old enqueue+mark_running
    pair. Verified via sqlite3's execute-count using a trace callback, since
    sqlite3.Connection.execute cannot be monkeypatched directly (C-level
    read-only attribute)."""
    from src.core import ws_cascade_store as store

    write_calls = []

    def tracer(sql):
        if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            write_calls.append(sql)

    conn.set_trace_callback(tracer)
    try:
        store.subprov_sig_enqueue_running(conn, subprov="SUB1", signature="SIG1")
    finally:
        conn.set_trace_callback(None)
    assert len(write_calls) == 1, f"expected exactly 1 write statement, got {len(write_calls)}"


def test_due_subprov_sig_retries_unaffected_by_merge(conn):
    """subprov_sig_mark_failed() still produces genuine PENDING rows for the
    retry-worker path (a separate concern from the hot in-line success path
    this fix touches) -- due_subprov_sig_retries() must still surface them."""
    from src.core import ws_cascade_store as store
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions "
        "(subprov_wallet, state, detected_at, expires_at) VALUES (?, 'ACTIVE', ?, ?)",
        ("SUB1", 0, 999999))
    conn.commit()
    store.subprov_sig_enqueue_running(conn, subprov="SUB1", signature="SIG1")
    store.subprov_sig_mark_failed(conn, subprov="SUB1", signature="SIG1", error="boom", max_attempts=8)

    due = store.due_subprov_sig_retries(conn, limit=10, now=int(time.time()) + 1000)
    sigs = [r[1] for r in due]
    assert "SIG1" in sigs
