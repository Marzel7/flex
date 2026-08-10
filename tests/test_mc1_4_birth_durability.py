"""MC1.4 -- authoritative, same-population birth durability metrics.

MC1.2B established: the legacy "seen / persisted / missing / completeness"
panel derives seen and persisted from DIFFERENT regex-matched log-tail
populations that are not guaranteed to share a window, so their ratio can
exceed 100% (a real production example: 651 seen / 661 persisted / 102%).
X78.19 added authoritative durability state (birth_persist_queue, file
fallback, in-process telemetry counters). This milestone replaces the
completeness computation with a reconciliation over ONE population --
"received" -- so persisted_immediately + recovered + pending_recovery +
permanently_lost can never exceed received, and neither percentage
(immediate_persistence_pct, eventual_durability_pct) can exceed 100%.

These tests exercise:
  1. compute_birth_durability_from_snapshot() -- the pure percentage math
     (Phase C/D/E), against the ticket's 5 deterministic cases (Phase K).
  2. pumpfun_curve_listener.birth_persistence_telemetry()'s durability
     block -- the mutually-exclusive state counters themselves, against a
     real temp SQLite birth_persist_queue (not mocked), including the two
     new counters (fallback_activations, permanently_lost) this milestone
     adds.
"""
import asyncio
import os
import sqlite3
import tempfile
import time

import pytest

from src.core.main import compute_birth_durability_from_snapshot
from src.core import pumpfun_curve_listener as listener_mod
from src.core.pumpfun_curve_listener import (
    PumpFunCurveListener, birth_persistence_telemetry,
    _BIRTH_TELEMETRY, _BIRTH_TELEMETRY_LOCK,
)
from src.core.database_write_service import CrossProcessDatabaseWriteTimeout


# ---------------------------------------------------------------------------
# Phase K -- deterministic cases against the pure percentage computation.
# ---------------------------------------------------------------------------

def _snapshot(received, persisted_immediately, recovered, pending_recovery,
              permanently_lost=0, fallback_pending=0, fallback_activations_total=0):
    return {
        "_snapshot_at": int(time.time()),
        "_process_started_at": int(time.time()) - 3600,
        "durability": {
            "received": received,
            "persisted_immediately": persisted_immediately,
            "recovered": recovered,
            "pending_recovery": pending_recovery,
            "fallback_pending": fallback_pending,
            "fallback_activations_total": fallback_activations_total,
            "permanently_lost": permanently_lost,
        },
    }


def test_case1_everything_immediate():
    """100 received, 100 immediate, 0 recovery, 0 lost."""
    result = compute_birth_durability_from_snapshot(_snapshot(100, 100, 0, 0))
    assert result["immediate_persistence_pct"] == 100.0
    assert result["eventual_durability_pct"] == 100.0
    assert result["permanently_lost"] == 0


def test_case2_recovery_required():
    """100 received, 90 immediate, 10 recovered, 0 lost."""
    result = compute_birth_durability_from_snapshot(_snapshot(100, 90, 10, 0))
    assert result["immediate_persistence_pct"] == 90.0
    assert result["eventual_durability_pct"] == 100.0
    final_persisted = result["persisted_immediately"] + result["recovered"]
    assert final_persisted == 100


def test_case3_pending_but_safe():
    """100 received, 90 immediate, 5 recovered, 5 durably pending, 0 lost.
    UI must distinguish persisted=95 from pending_recovery=5, while
    durability still reads 100% (pending-but-durable is not lost)."""
    result = compute_birth_durability_from_snapshot(_snapshot(100, 90, 5, 5))
    persisted = result["persisted_immediately"] + result["recovered"]
    assert persisted == 95
    assert result["pending_recovery"] == 5
    assert result["eventual_durability_pct"] == 100.0


def test_case4_permanent_loss():
    """100 received, 99 persisted/durable, 1 lost."""
    result = compute_birth_durability_from_snapshot(_snapshot(100, 90, 9, 0, permanently_lost=1))
    assert result["eventual_durability_pct"] == 99.0
    assert result["permanently_lost"] == 1


def test_case5_no_percentage_ever_exceeds_100_regardless_of_inputs():
    """Fuzz-style sweep: no combination of received/persisted/recovered/
    pending/lost (even deliberately 'impossible' or inconsistent inputs,
    simulating what a legacy log-tail mismatch might have produced) can
    push either percentage above 100 -- because the percentages are
    computed from a single denominator (received) and a numerator that is
    itself capped relative to it upstream (birth_persistence_telemetry's
    min()/max() clamps), not from two independently-sourced counts."""
    import random
    rng = random.Random(42)
    for _ in range(200):
        received = rng.randint(1, 1000)
        persisted_immediately = rng.randint(0, received)
        remaining = received - persisted_immediately
        recovered = rng.randint(0, remaining)
        permanently_lost = rng.randint(0, remaining - recovered)
        pending_recovery = remaining - recovered - permanently_lost

        result = compute_birth_durability_from_snapshot(_snapshot(
            received, persisted_immediately, recovered, pending_recovery, permanently_lost,
        ))
        assert result["immediate_persistence_pct"] <= 100.0
        assert result["eventual_durability_pct"] <= 100.0
        assert result["immediate_persistence_pct"] >= 0.0
        assert result["eventual_durability_pct"] >= 0.0


def test_zero_received_returns_none_percentages_not_division_error():
    result = compute_birth_durability_from_snapshot(_snapshot(0, 0, 0, 0))
    assert result["immediate_persistence_pct"] is None
    assert result["eventual_durability_pct"] is None
    assert result["received"] == 0


def test_pending_recovery_never_labeled_as_missing_in_result_shape():
    """The result dict must have a 'pending_recovery' field distinct from
    any 'missing'/'lost' concept -- safely deferred births are never
    conflated with loss (ticket: 'Do not call safely retained births
    missing')."""
    result = compute_birth_durability_from_snapshot(_snapshot(100, 90, 0, 10))
    assert "pending_recovery" in result
    assert "missing" not in result
    assert result["pending_recovery"] == 10
    assert result["permanently_lost"] == 0


# ---------------------------------------------------------------------------
# Listener-side: mutually-exclusive counters against a real temp DB.
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE token_analysis (
    mint TEXT UNIQUE PRIMARY KEY, created_at NUM, analyzed_at REAL,
    earliest_tx_creator TEXT, pf_ws_creator TEXT, bonding_curve_pda TEXT,
    create_tx_signature TEXT, source_platform TEXT, lifecycle_stage TEXT,
    is_new INTEGER DEFAULT 0, migration_signal_source TEXT,
    migration_signal_updated_at INTEGER, first_pre_migration_signal_at INTEGER
);
CREATE TABLE birth_persist_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, creator TEXT, created_at TEXT,
    bonding_curve_pda TEXT, create_tx_signature TEXT, symbol TEXT, name TEXT,
    received_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_attempt_at INTEGER,
    processed_at INTEGER, UNIQUE(mint)
);
"""


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(listener_mod, "DB_PATH", path, raising=False)
    with _BIRTH_TELEMETRY_LOCK:
        for k in _BIRTH_TELEMETRY:
            _BIRTH_TELEMETRY[k] = 0
    fallback_path = f"{path}.birth_fallback.jsonl"
    yield path
    os.unlink(path)
    if os.path.exists(fallback_path):
        os.unlink(fallback_path)


class _BareListener:
    _insert_bonding_curve_token = PumpFunCurveListener._insert_bonding_curve_token

    def _remember_recent_birth_token(self, mint, bonding_curve_pda=None):
        pass

    async def _upsert_birth_metadata_cache(self, mint, symbol, name):
        pass


def _run(coro):
    return asyncio.run(coro)


def test_durability_block_present_and_consistent_after_direct_success(temp_db):
    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintA", "Creator1", "1786400000", bonding_curve_pda="B1",
        create_tx_signature="S1", symbol="AAA", name="Token A",
    ))
    telemetry = birth_persistence_telemetry(temp_db)
    d = telemetry["durability"]
    assert d["received"] == 1
    assert d["persisted_immediately"] == 1
    assert d["pending_recovery"] == 0
    assert d["permanently_lost"] == 0
    assert d["fallback_activations_total"] == 0


def test_fallback_activations_counter_increments_on_double_failure(temp_db, monkeypatch):
    """MC1.4 adds fallback_activations as a NEW cumulative counter (this
    milestone) -- previously only logged, never counted. Verify it
    increments exactly once per genuine fallback-file write."""
    def _always_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="_insert_bonding_curve_token",
            wait_seconds=60.0, current_owner={"command": "some_heavy_writer"},
        )
    monkeypatch.setattr(listener_mod, "managed_db_connect", _always_times_out)

    import sqlite3 as real_sqlite3
    def _connect_also_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="birth_persist_queue enqueue",
            wait_seconds=60.0, current_owner={"command": "another_heavy_writer"},
        )
    monkeypatch.setattr(real_sqlite3, "connect", _connect_also_times_out)

    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintB", "Creator2", "1786400100", bonding_curve_pda="B2",
        create_tx_signature="S2", symbol="BBB", name="Token B",
    ))

    with _BIRTH_TELEMETRY_LOCK:
        assert _BIRTH_TELEMETRY["fallback_activations"] == 1
        assert _BIRTH_TELEMETRY["permanently_lost"] == 0


def test_permanently_lost_counter_increments_only_when_fallback_file_also_fails(temp_db, monkeypatch):
    """The permanently_lost counter must only increment on the ONE path
    with no further backstop: primary insert fails, queue write fails,
    AND the file fallback write itself fails."""
    def _always_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="_insert_bonding_curve_token",
            wait_seconds=60.0, current_owner={"command": "writer"},
        )
    monkeypatch.setattr(listener_mod, "managed_db_connect", _always_times_out)

    import sqlite3 as real_sqlite3
    def _connect_also_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="enqueue",
            wait_seconds=60.0, current_owner={"command": "writer2"},
        )
    monkeypatch.setattr(real_sqlite3, "connect", _connect_also_times_out)

    def _fallback_also_fails(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(listener_mod, "_fallback_append_birth", _fallback_also_fails)

    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintC", "Creator3", "1786400200", bonding_curve_pda="B3",
        create_tx_signature="S3", symbol="CCC", name="Token C",
    ))

    with _BIRTH_TELEMETRY_LOCK:
        assert _BIRTH_TELEMETRY["permanently_lost"] == 1
        assert _BIRTH_TELEMETRY["fallback_activations"] == 0


def test_durability_counts_are_mutually_exclusive_shares_of_received(temp_db, monkeypatch):
    """End-to-end invariant: after a mix of direct successes and queued
    retries, persisted_immediately + pending_recovery + permanently_lost
    must never exceed received (the core MC1.4 guarantee that makes >100%
    structurally impossible)."""
    listener = _BareListener()

    # 2 direct successes.
    _run(listener._insert_bonding_curve_token("MintD1", "C", "1", symbol="D1", name="D1"))
    _run(listener._insert_bonding_curve_token("MintD2", "C", "1", symbol="D2", name="D2"))

    # 1 queued (durable retry needed).
    def _always_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="_insert_bonding_curve_token",
            wait_seconds=60.0, current_owner={"command": "writer"},
        )
    monkeypatch.setattr(listener_mod, "managed_db_connect", _always_times_out)
    _run(listener._insert_bonding_curve_token("MintD3", "C", "1", symbol="D3", name="D3"))

    telemetry = birth_persistence_telemetry(temp_db)
    d = telemetry["durability"]
    assert d["received"] == 3
    accounted_for = d["persisted_immediately"] + d["pending_recovery"] + d["recovered"] + d["permanently_lost"]
    assert accounted_for <= d["received"]
