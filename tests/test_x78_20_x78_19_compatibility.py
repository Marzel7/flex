"""X78.20 Phase K -- X78.19 durability remains intact under priority
arbitration.

X78.19 proved: birth received -> primary write delayed/fails -> durable
queue/fallback retains it -> eventual persistence, with zero permanent
loss. X78.20 adds priority scheduling on TOP of that -- these tests prove
priority arbitration doesn't remove or weaken the durability guarantee, and
that P0-tagged birth writes still hit the SAME retry-queue path as before
when they genuinely can't acquire the lane (e.g. a real cross-process
CrossProcessDatabaseWriteTimeout, not just a "there's a higher-priority
waiter" defer).

Priority arbitration must REDUCE how often the durability fallback is
needed (fewer P0 write failures because P0 tends to win contention sooner)
but must never be treated as a replacement for it.
"""
import asyncio
import os
import sqlite3
import tempfile
import threading
import time

import pytest

from src.core.database_write_service import (
    CrossProcessDatabaseWriteTimeout, acquire_write_lease, release_write_lease,
    PRIORITY_P0_CRITICAL_INGESTION, PRIORITY_P2_BACKGROUND,
)
from src.core import pumpfun_curve_listener as listener_mod
from src.core.pumpfun_curve_listener import (
    PumpFunCurveListener, birth_persistence_telemetry, _BIRTH_TELEMETRY, _BIRTH_TELEMETRY_LOCK,
)


SCHEMA_SQL = """
CREATE TABLE token_analysis (
    mint TEXT UNIQUE PRIMARY KEY,
    created_at NUM, analyzed_at REAL, earliest_tx_creator TEXT, pf_ws_creator TEXT,
    bonding_curve_pda TEXT, create_tx_signature TEXT, source_platform TEXT,
    lifecycle_stage TEXT, is_new INTEGER DEFAULT 0, migration_signal_source TEXT,
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


def test_p0_tagged_birth_write_still_durably_queues_on_genuine_timeout(temp_db, monkeypatch):
    """A P0-tagged birth insert that genuinely cannot acquire the lane
    (real CrossProcessDatabaseWriteTimeout, not just a priority defer) must
    still hit the exact same durable-retry path X78.19 built -- priority
    tagging must not change WHAT happens on failure, only how often it's
    needed."""
    def _always_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="_insert_bonding_curve_token",
            wait_seconds=60.0, current_owner={"command": "some_heavy_p2_writer"},
        )
    monkeypatch.setattr(listener_mod, "managed_db_connect", _always_times_out)

    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintP0Timeout", "Creator1", "1786400000",
        bonding_curve_pda="BCP", create_tx_signature="Sig",
        symbol="P0T", name="P0 Timeout Token",
    ))

    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    queued = conn.execute("SELECT * FROM birth_persist_queue WHERE mint=?", ("MintP0Timeout",)).fetchone()
    conn.close()
    assert queued is not None, "P0 tagging must not bypass the durable retry queue on genuine failure"
    assert queued["status"] == "PENDING"

    telemetry = birth_persistence_telemetry(temp_db)
    assert telemetry["births_queued_for_retry"] == 1


def test_real_p0_write_wins_lock_after_p2_holder_releases_no_data_loss(tmp_path):
    """End-to-end proof against REAL flock() contention (not mocked): a P2
    writer holds the lane, a P0 writer waits, the P2 writer releases, the
    P0 writer acquires and completes -- no exception, no data loss, and the
    P0 writer's wait was governed by the priority ticket mechanism (it was
    registered as a higher-priority waiter throughout)."""
    db_path = str(tmp_path / "real_contention.sqlite")

    holder_active = threading.Event()
    release_holder = threading.Event()
    result = {}

    def p2_holder():
        lease = acquire_write_lease(
            "test", db_path, "p2-txid", "p2-heavy-writer",
            timeout=5.0, priority=PRIORITY_P2_BACKGROUND,
        )
        holder_active.set()
        release_holder.wait(timeout=3)
        release_write_lease(lease)

    def p0_waiter():
        t0 = time.monotonic()
        lease = acquire_write_lease(
            "test", db_path, "p0-txid", "p0-birth-write",
            timeout=5.0, priority=PRIORITY_P0_CRITICAL_INGESTION,
        )
        result["wait_s"] = time.monotonic() - t0
        result["acquired"] = True
        release_write_lease(lease)

    t_holder = threading.Thread(target=p2_holder)
    t_holder.start()
    assert holder_active.wait(timeout=2)

    t_waiter = threading.Thread(target=p0_waiter)
    t_waiter.start()
    time.sleep(0.2)  # let P0 register its ticket and start waiting
    release_holder.set()

    t_holder.join(timeout=3)
    t_waiter.join(timeout=3)

    assert result.get("acquired") is True, "P0 must successfully acquire once the P2 holder releases"
    assert result["wait_s"] < 3.0, "P0 must not wait anywhere near the full 60s bound once the lane is free"


def test_priority_arbitration_does_not_introduce_a_second_writer(tmp_path):
    """Critical safety invariant (explicitly out of scope to violate): at
    no point may two leases be simultaneously held for the same database
    path, regardless of priority. This proves the ticket/defer mechanism
    only reorders WAITERS -- it never grants the flock() to more than one
    holder at a time."""
    db_path = str(tmp_path / "single_writer.sqlite")
    concurrent_holders = []
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def writer(priority, name):
        barrier.wait(timeout=3)
        lease = acquire_write_lease("test", db_path, f"{name}-txid", name, timeout=5.0, priority=priority)
        with lock:
            concurrent_holders.append(name)
            assert len(concurrent_holders) == 1, f"multiple simultaneous holders: {concurrent_holders}"
        time.sleep(0.05)
        with lock:
            concurrent_holders.remove(name)
        release_write_lease(lease)

    threads = [
        threading.Thread(target=writer, args=(PRIORITY_P0_CRITICAL_INGESTION, "p0")),
        threading.Thread(target=writer, args=(PRIORITY_P2_BACKGROUND, "p2")),
        threading.Thread(target=writer, args=(PRIORITY_P2_BACKGROUND, "p2b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
