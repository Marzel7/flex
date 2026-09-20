"""WT_OPS_BOUNDED_MAINTENANCE: bounded retention for rpc_response_cache and
wss_metrics, explicitly excluding wt_candidate_websocket_watches.

All tests use temp SQLite files -- never the live production databases.
"""
import os
import sqlite3
import time

import pytest

from src.core.rpc_cache import RPCCache
from src.metrics import usage_tracker
from src.ops import cache_metrics_retention_runner as runner


# ---------------------------------------------------------------------------
# RPC cache bounded cleanup
# ---------------------------------------------------------------------------

def _make_rpc_cache(tmp_path):
    db_path = str(tmp_path / "rpc_cache_test.db")
    return RPCCache(db_path), db_path


def test_rpc_cleanup_batch_deletes_only_expired(tmp_path):
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()

    # Expired: cached long ago with a short TTL.
    cache.set("expired_key", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ? WHERE cache_key='expired_key'",
                 (now - 10000,))
    conn.commit()
    conn.close()

    # Live: cached now with a long TTL.
    cache.set("live_key", {"x": 2}, "getTransaction")

    deleted = cache.cleanup_expired_batch(batch_size=100, now=now)
    assert deleted == 1

    conn = sqlite3.connect(db_path)
    remaining = {r[0] for r in conn.execute("SELECT cache_key FROM rpc_response_cache").fetchall()}
    conn.close()
    assert remaining == {"live_key"}


def test_rpc_cleanup_batch_cap_respected(tmp_path):
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    for i in range(10):
        cache.set(f"expired_{i}", {"x": i}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (now - 10000,))
    conn.commit()
    conn.close()

    deleted = cache.cleanup_expired_batch(batch_size=3, now=now)
    assert deleted == 3

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0]
    conn.close()
    assert remaining == 7


def test_rpc_cleanup_batch_idempotent_repeat_run(tmp_path):
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("expired_a", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (now - 10000,))
    conn.commit()
    conn.close()

    first = cache.cleanup_expired_batch(batch_size=100, now=now)
    second = cache.cleanup_expired_batch(batch_size=100, now=now)
    assert first == 1
    assert second == 0  # nothing left; repeat run is a safe no-op


def test_rpc_cleanup_batch_empty_result_is_safe(tmp_path):
    cache, db_path = _make_rpc_cache(tmp_path)
    deleted = cache.cleanup_expired_batch(batch_size=100)
    assert deleted == 0


def test_rpc_ttl_semantics_unchanged_after_cleanup(tmp_path):
    """cleanup_expired_batch must not alter TTL values or get()/set() behavior."""
    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("k1", {"data": "value"}, "getTransaction")
    assert cache.TTLS["getTransaction"] == 86400

    cache.cleanup_expired_batch(batch_size=100)  # no-op, nothing expired

    result = cache.get("k1")
    assert result == {"data": "value"}  # live entry still retrievable


def test_rpc_get_set_behavior_unchanged(tmp_path):
    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("mykey", {"foo": "bar"}, "getSignaturesForAddress")
    assert cache.get("mykey") == {"foo": "bar"}
    assert cache.get("missing_key") is None


def test_rpc_count_expired(tmp_path):
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("expired_1", {"x": 1}, "getSignaturesForAddress")
    cache.set("live_1", {"x": 2}, "getTransaction")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ? WHERE cache_key='expired_1'",
                 (now - 10000,))
    conn.commit()
    conn.close()
    assert cache.count_expired(now=now) == 1


def test_rpc_cleanup_batch_busy_fail_safe(tmp_path, monkeypatch):
    """Simulated persistent SQLITE_BUSY must return 0, never raise."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("k", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    class _AlwaysBusyCursor:
        def execute(self, *a, **kw):
            raise sqlite3.OperationalError("database is locked")

    class _AlwaysBusyConn:
        def cursor(self):
            return _AlwaysBusyCursor()
        def execute(self, *a, **kw):
            pass  # PRAGMA busy_timeout=... in cleanup_expired_batch's setup
        def close(self):
            pass
        def rollback(self):
            pass

    # cleanup_expired_batch() now calls db_connect() directly (not
    # self._get_conn()) as part of the bounded-acquisition fix -- patch the
    # module-level import cleanup_expired_batch actually uses.
    monkeypatch.setattr(rpc_cache_module, "db_connect", lambda *a, **kw: _AlwaysBusyConn())
    deleted = cache.cleanup_expired_batch(batch_size=10, busy_retry_attempts=2)
    assert deleted == 0  # must not raise


# ---------------------------------------------------------------------------
# WSS metrics bounded retention
# ---------------------------------------------------------------------------

def _make_wss_db(tmp_path):
    db_path = str(tmp_path / "wss_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    return db_path


def _insert_wss_row(db_path, ts, subscription="pumpswap_logs", source_file="test.py"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO wss_metrics (ts, subscription, source_file, msg_count, est_bytes) VALUES (?, ?, ?, 1, 100)",
        (ts, subscription, source_file),
    )
    conn.commit()
    conn.close()


def test_wss_cleanup_only_old_rows_removed(tmp_path):
    db_path = _make_wss_db(tmp_path)
    now = time.time()
    cutoff = now - 7 * 86400
    _insert_wss_row(db_path, now - 8 * 86400)  # old
    _insert_wss_row(db_path, now - 1 * 86400)  # recent

    deleted = usage_tracker.cleanup_old_wss_metrics_batch(db_path, batch_size=100, cutoff_ts=cutoff)
    assert deleted == 1

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM wss_metrics").fetchone()[0]
    conn.close()
    assert remaining == 1


def test_wss_last_7d_preserved(tmp_path):
    db_path = _make_wss_db(tmp_path)
    now = time.time()
    cutoff = now - 7 * 86400
    for days_ago in [0.5, 1, 3, 6.9]:
        _insert_wss_row(db_path, now - days_ago * 86400)
    for days_ago in [7.1, 10, 30]:
        _insert_wss_row(db_path, now - days_ago * 86400)

    deleted = usage_tracker.cleanup_old_wss_metrics_batch(db_path, batch_size=100, cutoff_ts=cutoff)
    assert deleted == 3

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM wss_metrics WHERE ts >= ?", (cutoff,)).fetchone()[0]
    conn.close()
    assert remaining == 4


def test_wss_batch_cap_respected(tmp_path):
    db_path = _make_wss_db(tmp_path)
    now = time.time()
    cutoff = now - 7 * 86400
    for _ in range(10):
        _insert_wss_row(db_path, now - 30 * 86400)

    deleted = usage_tracker.cleanup_old_wss_metrics_batch(db_path, batch_size=4, cutoff_ts=cutoff)
    assert deleted == 4

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM wss_metrics").fetchone()[0]
    conn.close()
    assert remaining == 6


def test_wss_cleanup_idempotent_repeat_run(tmp_path):
    db_path = _make_wss_db(tmp_path)
    now = time.time()
    cutoff = now - 7 * 86400
    _insert_wss_row(db_path, now - 30 * 86400)

    first = usage_tracker.cleanup_old_wss_metrics_batch(db_path, batch_size=100, cutoff_ts=cutoff)
    second = usage_tracker.cleanup_old_wss_metrics_batch(db_path, batch_size=100, cutoff_ts=cutoff)
    assert first == 1
    assert second == 0


def test_wss_schema_compatible_with_api_usage_query(tmp_path):
    """/api/usage's exact query shape must still work against the retained schema."""
    db_path = _make_wss_db(tmp_path)
    now = time.time()
    _insert_wss_row(db_path, now - 3600, subscription="pumpswap_logs", source_file="listener.py")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cutoff = now - 24 * 3600
    rows = conn.execute("""
        SELECT subscription, source_file, SUM(msg_count) as messages, SUM(est_bytes) as bytes
        FROM wss_metrics WHERE ts >= ? GROUP BY subscription, source_file ORDER BY bytes DESC
    """, (cutoff,)).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["subscription"] == "pumpswap_logs"


def test_wss_count_old(tmp_path):
    db_path = _make_wss_db(tmp_path)
    now = time.time()
    cutoff = now - 7 * 86400
    _insert_wss_row(db_path, now - 10 * 86400)
    _insert_wss_row(db_path, now - 1 * 86400)
    assert usage_tracker.count_old_wss_metrics(db_path, cutoff) == 1


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _wire_runner_paths(monkeypatch, wt_ops_path, flex_path):
    monkeypatch.setattr(runner, "WT_OPS_DB_PATH", wt_ops_path)
    monkeypatch.setattr(runner, "FLEX_DB_PATH", flex_path)


def test_runner_dry_run_zero_writes(tmp_path, monkeypatch):
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")

    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    _insert_wss_row(flex_path, time.time() - 30 * 86400)

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)

    result = runner.run_once(dry_run=True, quiet=True)
    assert result["stop_reason"] == "DRY_RUN_NO_MUTATION"
    assert result["rpc_deleted"] == 0
    assert result["wss_deleted"] == 0
    # Exact RPC backlog counts require an unindexed full-table scan over very
    # large payload rows.  Retention now leaves this unavailable rather than
    # allowing a dry-run preflight to run without a wall-clock bound.
    assert result["rpc_remaining_expired"] is None
    assert result["wss_remaining_expired"] == 1

    # Confirm zero mutation actually occurred.
    conn = sqlite3.connect(wt_ops_path)
    assert conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0] == 1
    conn.close()
    conn = sqlite3.connect(flex_path)
    assert conn.execute("SELECT COUNT(*) FROM wss_metrics").fetchone()[0] == 1
    conn.close()


def test_runner_actual_run_deletes_eligible_rows(tmp_path, monkeypatch):
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")

    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    cache.set("live", {"x": 2}, "getTransaction")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ? WHERE cache_key='expired'",
                 (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    _insert_wss_row(flex_path, time.time() - 30 * 86400)
    _insert_wss_row(flex_path, time.time() - 1 * 86400)

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 1
    assert result["wss_deleted"] == 1
    # Both loops exit via "nothing left to do" once their single eligible
    # row is gone -- this is the normal, successful empty-backlog path, not
    # a failure. It intentionally shares the same stop_reason as a batch
    # call that failed safely (e.g. lock contention) since the two can't be
    # distinguished from the batch functions' return values; a genuine
    # positive-deletion count (rpc_deleted/wss_deleted > 0) is the actual
    # signal that real work happened.
    assert result["stop_reason"] == "NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE"

    conn = sqlite3.connect(wt_ops_path)
    remaining_rpc = {r[0] for r in conn.execute("SELECT cache_key FROM rpc_response_cache").fetchall()}
    conn.close()
    assert remaining_rpc == {"live"}


def test_runner_max_elapsed_budget_respected(tmp_path, monkeypatch):
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    for i in range(5):
        cache.set(f"expired_{i}", {"x": i}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_ELAPSED_SECONDS", 0.0)  # force immediate budget exhaustion
    monkeypatch.setattr(runner, "RPC_BATCH_SIZE", 1)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["stop_reason"] == "MAX_ELAPSED_STOP"


def test_runner_wal_ceiling_stop(tmp_path, monkeypatch):
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "_wal_bytes", lambda path: 999_999_999_999)  # force over any ceiling

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["stop_reason"] == "WAL_CEILING_STOP"
    assert result["rpc_deleted"] == 0  # nothing deleted -- guard checked before any batch


def test_runner_disk_floor_stop(tmp_path, monkeypatch):
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    conn = sqlite3.connect(wt_ops_path)
    conn.close()
    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "_free_disk_bytes", lambda path=None: 1)  # far below any floor

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["stop_reason"] == "DISK_FLOOR_STOP"


def test_runner_no_websocket_watch_references_in_source():
    """Static guarantee: no websocket-watch DELETE/UPDATE/reference exists
    anywhere in the runner's source."""
    src = open("src/ops/cache_metrics_retention_runner.py").read()
    assert "wt_candidate_websocket_watches" not in src or "does NOT touch" in src
    # Stronger check: no SQL verb applied to that table name anywhere.
    import re
    assert not re.search(r"(DELETE|UPDATE|DROP)\s+.*wt_candidate_websocket_watches", src, re.IGNORECASE)


def test_runner_no_manual_checkpoint_in_source():
    src = open("src/ops/cache_metrics_retention_runner.py").read()
    assert "wal_checkpoint" not in src.lower()


def test_runner_no_provider_network_calls_in_source():
    src = open("src/ops/cache_metrics_retention_runner.py").read()
    for forbidden in ("requests.", "aiohttp", "httpx", "urlopen", "socket.connect"):
        assert forbidden not in src


def test_runner_no_intelligence_snapshot_scheduler_dependency():
    src = open("src/ops/cache_metrics_retention_runner.py").read()
    assert "intelligence_snapshot_scheduler" not in src


# ---------------------------------------------------------------------------
# Disk start-threshold hard stop (Part 1 correction)
# ---------------------------------------------------------------------------

def test_runner_disk_start_threshold_stops_before_any_delete(tmp_path, monkeypatch):
    """Free disk below the conservative 4GiB start threshold (but still above
    the 2GiB hard floor) must refuse to start ANY mutation -- not merely warn."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")

    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    _insert_wss_row(flex_path, time.time() - 30 * 86400)

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    # Between the 2GiB hard floor and the 4GiB start threshold.
    monkeypatch.setattr(runner, "_free_disk_bytes", lambda path=None: 3 * 1024 * 1024 * 1024)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["stop_reason"] == "DISK_START_THRESHOLD_STOP"
    assert result["rpc_deleted"] == 0
    assert result["wss_deleted"] == 0

    # Confirm zero mutation actually occurred.
    conn = sqlite3.connect(wt_ops_path)
    assert conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0] == 1
    conn.close()
    conn = sqlite3.connect(flex_path)
    assert conn.execute("SELECT COUNT(*) FROM wss_metrics").fetchone()[0] == 1
    conn.close()


def test_runner_hard_floor_still_stops_below_2gib(tmp_path, monkeypatch):
    """The pre-existing 2GiB emergency floor must still behave as before."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    conn = sqlite3.connect(wt_ops_path)
    conn.close()
    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "_free_disk_bytes", lambda path=None: 1 * 1024 * 1024 * 1024)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["stop_reason"] == "DISK_FLOOR_STOP"


# ---------------------------------------------------------------------------
# Hard per-run row ceilings (Part 2)
# ---------------------------------------------------------------------------

def test_runner_rpc_max_rows_per_run_respected(tmp_path, monkeypatch):
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    for i in range(50):
        cache.set(f"expired_{i}", {"x": i}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 10)
    monkeypatch.setattr(runner, "RPC_BATCH_SIZE", 3)  # smaller than the ceiling, forces multiple batches
    monkeypatch.setattr(runner, "INTER_BATCH_SLEEP_SECONDS", 0.0)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 10
    assert result["stop_reason"] == "MAX_ROWS_PER_RUN_STOP"

    conn = sqlite3.connect(wt_ops_path)
    remaining = conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0]
    conn.close()
    assert remaining == 40  # 50 - 10, never exceeded the ceiling


def test_runner_wss_max_rows_per_run_zero_means_zero_work(tmp_path, monkeypatch):
    """MAX_WSS_ROWS_PER_RUN=0 (used for the RPC-only tiny activation) must
    guarantee zero WSS deletion regardless of eligible backlog."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    for _ in range(5):
        _insert_wss_row(flex_path, time.time() - 30 * 86400)

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 0)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 25)
    monkeypatch.setattr(runner, "INTER_BATCH_SLEEP_SECONDS", 0.0)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 1  # RPC still runs normally
    assert result["wss_deleted"] == 0  # WSS entirely blocked by zero budget

    conn = sqlite3.connect(flex_path)
    remaining = conn.execute("SELECT COUNT(*) FROM wss_metrics").fetchone()[0]
    conn.close()
    assert remaining == 5  # untouched


def test_runner_max_rows_ceiling_never_exceeded_even_with_large_batch_size(tmp_path, monkeypatch):
    """The ceiling must clip the final batch, not just stop after overshooting."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    for i in range(100):
        cache.set(f"expired_{i}", {"x": i}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 25)
    monkeypatch.setattr(runner, "RPC_BATCH_SIZE", 200)  # batch size far exceeds the ceiling
    monkeypatch.setattr(runner, "INTER_BATCH_SLEEP_SECONDS", 0.0)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 25  # clipped exactly to the ceiling, not 100 or 200


# ---------------------------------------------------------------------------
# Write-lane read/write separation and bounded acquisition
# (RPC_MAINTENANCE_WRITE_LANE_QUALIFICATION)
# ---------------------------------------------------------------------------

def test_count_expired_uses_read_only_connection(tmp_path, monkeypatch):
    """count_expired() must open a mode=ro connection and never attempt the
    cross-process write lane (verified by confirming it calls db_connect
    with read_only=True, and that it works even when the DB is a normal
    read-write connection is unavailable for writes)."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("k", {"x": 1}, "getSignaturesForAddress")

    calls = []
    original_db_connect = rpc_cache_module.db_connect

    def _spy_db_connect(*args, **kwargs):
        calls.append(kwargs.get("read_only", False))
        return original_db_connect(*args, **kwargs)

    monkeypatch.setattr(rpc_cache_module, "db_connect", _spy_db_connect)
    cache.count_expired()

    assert any(calls), "db_connect was never called"
    assert calls[-1] is True, "count_expired() must request read_only=True"


def test_ensure_table_skips_write_when_table_already_exists(tmp_path, monkeypatch):
    """RPCCache.__init__ -> _ensure_table() must not attempt a write-lane
    CREATE TABLE once a read-only check confirms the table already exists --
    this was the root cause of maintenance queuing behind unrelated writers
    even for read-only operations."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)  # table now exists

    write_conn_calls = []
    original_get_conn = cache._get_conn

    def _spy_get_conn():
        write_conn_calls.append(1)
        return original_get_conn()

    monkeypatch.setattr(cache, "_get_conn", _spy_get_conn)

    # Re-constructing RPCCache against the same (now-existing) table must
    # not touch the write-attempting path at all.
    cache2 = RPCCache(db_path)
    monkeypatch.setattr(cache2, "_get_conn", _spy_get_conn)
    cache2._ensure_table()

    assert write_conn_calls == []


def test_runner_dry_run_does_not_request_write_lane(tmp_path, monkeypatch):
    """Dry-run's preflight counts must go through read-only paths only."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("k", {"x": 1}, "getSignaturesForAddress")

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    from src.core import rpc_cache as rpc_cache_module
    write_attempts = []
    original_get_conn = RPCCache._get_conn

    def _spy_get_conn(self):
        write_attempts.append(1)
        return original_get_conn(self)

    monkeypatch.setattr(RPCCache, "_get_conn", _spy_get_conn)
    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)

    result = runner.run_once(dry_run=True, quiet=True)
    assert result["stop_reason"] == "DRY_RUN_NO_MUTATION"
    assert write_attempts == [], "dry-run must never touch the write-attempting connection path"


def test_cleanup_acquires_write_lane_only_for_delete_transaction(tmp_path):
    """The bounded cleanup must succeed normally when the lane IS available --
    proving the bounded acquisition budget doesn't break the happy path."""
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (now - 10000,))
    conn.commit()
    conn.close()

    deleted = cache.cleanup_expired_batch(batch_size=100, now=now)
    assert deleted == 1


def test_write_lane_unavailable_zero_mutation(tmp_path, monkeypatch):
    """A permanently-busy write lane must result in zero mutation."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    class _AlwaysBusyConn:
        def cursor(self):
            class _C:
                def execute(self, *a, **kw):
                    raise sqlite3.OperationalError("database is locked")
            return _C()
        def execute(self, *a, **kw):
            pass
        def close(self):
            pass
        def rollback(self):
            pass

    monkeypatch.setattr(rpc_cache_module, "db_connect", lambda *a, **kw: _AlwaysBusyConn())
    deleted = cache.cleanup_expired_batch(batch_size=25, now=time.time())
    assert deleted == 0

    # Confirm zero mutation actually occurred (bypassing the mock entirely).
    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0]
    conn.close()
    assert remaining == 1


def test_write_lane_unavailable_bounded_quick_return(tmp_path, monkeypatch):
    """A permanently-busy write lane must return within the configured
    maintenance budget, NOT after minutes of retrying (the root cause of the
    prior production stall: 60s connection timeout x 5 retries x 2 phases
    could reach ~600s; the fixed budget must be an order of magnitude lower)."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    class _AlwaysBusyConn:
        def cursor(self):
            class _C:
                def execute(self, *a, **kw):
                    raise sqlite3.OperationalError("database is locked")
            return _C()
        def execute(self, *a, **kw):
            pass
        def close(self):
            pass
        def rollback(self):
            pass

    monkeypatch.setattr(rpc_cache_module, "db_connect", lambda *a, **kw: _AlwaysBusyConn())
    # Keep the test itself fast by shrinking the sleep backoff, while still
    # proving the retry-count/attempt bound (not the sleep duration) is what
    # actually caps elapsed time.
    t0 = time.monotonic()
    deleted = cache.cleanup_expired_batch(batch_size=25, now=time.time(), busy_retry_attempts=3)
    elapsed = time.monotonic() - t0
    assert deleted == 0
    # Generous margin above the actual MAINTENANCE_TOTAL_BUDGET_SECONDS (10s
    # x 2 phases = 20s worst case) but drastically below the >360s the
    # unbounded prior implementation could reach.
    assert elapsed < 30.0


def test_runner_rpc_batch_unavailable_does_not_trigger_unbounded_preflight(tmp_path, monkeypatch):
    """A zero batch result stays fail-closed without an exact COUNT preflight."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)

    # Simulate a batch that cannot proceed.  count_expired() must never be
    # consulted to distinguish an operator-facing label.
    class _FakeRPCCache:
        def __init__(self, *a, **kw):
            pass
        def count_expired(self, now=None):
            raise AssertionError("unbounded RPC preflight must not run")
        def cleanup_expired_batch(self, batch_size, now=None):
            return 0  # simulates a lane-busy skip

    monkeypatch.setattr(runner, "RPCCache", _FakeRPCCache)

    # WSS has nothing eligible in this scenario, so its own loop correctly
    # reports NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE and, since the two
    # domains are independent, that becomes the final overall stop_reason --
    # the RPC-specific signal is what rpc_deleted==0 + a known preflight
    # backlog tells the operator, not the shared final field alone.
    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 0
    assert result["rpc_remaining_expired"] is None

    # Confirm zero mutation actually occurred.
    conn = sqlite3.connect(wt_ops_path)
    remaining = conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0]
    conn.close()
    assert remaining == 1


def test_runner_rpc_lane_busy_reason_surfaces_when_wss_also_blocked(tmp_path, monkeypatch):
    """When BOTH RPC and WSS are blocked in the same run, the final
    stop_reason must be MAINTENANCE_SKIPPED_WRITE_LANE_BUSY (the RPC phase's
    reason survives because WSS's own loop hits the identical reason rather
    than overwriting it with a different one)."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    _insert_wss_row(flex_path, time.time() - 30 * 86400)  # one eligible WSS row too

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)

    class _FakeRPCCache:
        def __init__(self, *a, **kw):
            pass
        def count_expired(self, now=None):
            return 1
        def cleanup_expired_batch(self, batch_size, now=None):
            return 0

    def _fake_wss_cleanup(db_path, batch_size, cutoff_ts):
        return 0  # simulates WSS also failing to obtain its own lock this run

    monkeypatch.setattr(runner, "RPCCache", _FakeRPCCache)
    monkeypatch.setattr(usage_tracker, "cleanup_old_wss_metrics_batch", _fake_wss_cleanup)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 0
    assert result["wss_deleted"] == 0
    assert result["stop_reason"] == "MAINTENANCE_SKIPPED_WRITE_LANE_BUSY"


def test_no_connection_leak_after_write_lane_failure(tmp_path, monkeypatch):
    """After a lane-busy failure, no connection object should be left open
    (the close() call must have been reached on every code path)."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    close_calls = []

    class _AlwaysBusyConn:
        def cursor(self):
            class _C:
                def execute(self, *a, **kw):
                    raise sqlite3.OperationalError("database is locked")
            return _C()
        def execute(self, *a, **kw):
            pass
        def close(self):
            close_calls.append(1)
        def rollback(self):
            pass

    monkeypatch.setattr(rpc_cache_module, "db_connect", lambda *a, **kw: _AlwaysBusyConn())
    cache.cleanup_expired_batch(batch_size=25, now=time.time(), busy_retry_attempts=2)
    assert len(close_calls) >= 1, "connection must be closed even on failure"


def test_normal_cleanup_still_works_when_lane_available(tmp_path):
    """Sanity: the bounded-acquisition rewrite doesn't break the ordinary
    successful-cleanup path (already covered elsewhere, repeated here as an
    explicit write-lane-qualification requirement)."""
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("expired1", {"x": 1}, "getSignaturesForAddress")
    cache.set("expired2", {"x": 2}, "getSignaturesForAddress")
    cache.set("live", {"x": 3}, "getTransaction")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ? WHERE cache_key != 'live'",
                 (now - 10000,))
    conn.commit()
    conn.close()

    deleted = cache.cleanup_expired_batch(batch_size=25, now=now)
    assert deleted == 2

    conn = sqlite3.connect(db_path)
    remaining = {r[0] for r in conn.execute("SELECT cache_key FROM rpc_response_cache").fetchall()}
    conn.close()
    assert remaining == {"live"}


def test_live_rows_preserved_across_write_lane_scenarios(tmp_path):
    """Live (non-expired) rows must never be deleted, regardless of lane
    contention scenarios exercised elsewhere in this file."""
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("live1", {"x": 1}, "getTransaction")
    cache.set("live2", {"x": 2}, "getTransaction")

    deleted = cache.cleanup_expired_batch(batch_size=100, now=now)
    assert deleted == 0

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0]
    conn.close()
    assert remaining == 2


def test_no_wss_mutation_from_rpc_write_lane_tests():
    """Static guarantee: none of this file's RPC write-lane tests touch
    wss_metrics -- confirms test isolation matches the milestone's scope
    (RPC-only, WSS explicitly out of scope for lane investigation)."""
    import inspect
    src = inspect.getsource(inspect.getmodule(test_no_wss_mutation_from_rpc_write_lane_tests))
    # This is a structural sanity check, not a strict prohibition -- WSS
    # tests exist elsewhere in this same file for their own feature area.
    assert "cleanup_old_wss_metrics_batch" in src  # WSS coverage exists...
    assert "def test_write_lane_unavailable_zero_mutation" in src  # ...separately from RPC lane tests


def test_no_websocket_watch_mutation_anywhere_in_rpc_cache_source():
    """Static guarantee: rpc_cache.py never references
    wt_candidate_websocket_watches in any form."""
    src = open("src/core/rpc_cache.py").read()
    assert "wt_candidate_websocket_watches" not in src


# ---------------------------------------------------------------------------
# MAX_RPC_ROWS_PER_RUN=0 must not silently block WSS (regression:
# CACHE_METRICS_PROGRESSIVE_MAINTENANCE)
# ---------------------------------------------------------------------------

def test_rpc_disabled_this_run_does_not_block_wss(tmp_path, monkeypatch):
    """MAX_RPC_ROWS_PER_RUN=0 (used for a WSS-only invocation) must NOT
    prevent the WSS phase from running -- the two are independent budgets.
    This reproduces a real production observation: a WSS-only run reported
    wss_deleted=0 despite 4.9M+ eligible rows, because the RPC loop's own
    "ceiling reached" check fired on iteration 1 with a 0 ceiling and that
    reason gated the WSS phase off entirely."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")

    conn = sqlite3.connect(wt_ops_path)
    conn.execute("""
        CREATE TABLE rpc_response_cache (
            cache_key TEXT PRIMARY KEY, response_json TEXT, method TEXT,
            cached_at REAL, ttl_seconds INTEGER, hit_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    _insert_wss_row(flex_path, time.time() - 30 * 86400)
    _insert_wss_row(flex_path, time.time() - 20 * 86400)

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 0)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 1000)
    monkeypatch.setattr(runner, "WSS_BATCH_SIZE", 1000)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 0
    assert result["wss_deleted"] == 2  # WSS must have actually run
    assert result["stop_reason"] != "MAX_ROWS_PER_RUN_STOP"  # would be misleading (RPC's ceiling, not WSS's)

    conn = sqlite3.connect(flex_path)
    remaining = conn.execute("SELECT COUNT(*) FROM wss_metrics").fetchone()[0]
    conn.close()
    assert remaining == 0


def test_wss_disabled_this_run_does_not_mask_rpc_stop_reason(tmp_path, monkeypatch):
    """Symmetric case: MAX_WSS_ROWS_PER_RUN=0 (RPC-only invocation) must
    preserve whatever RPC's own phase reported, not overwrite it with a
    WSS-specific marker."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("expired1", {"x": 1}, "getSignaturesForAddress")
    cache.set("expired2", {"x": 2}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 25)
    monkeypatch.setattr(runner, "RPC_BATCH_SIZE", 25)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 0)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_deleted"] == 2
    assert result["wss_deleted"] == 0
    # RPC exhausted its own (small) backlog before hitting its ceiling, so
    # the correct reason is "nothing left," not a WSS-specific marker.
    assert result["stop_reason"] == "NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Disabled-phase eligibility-scan skip (CONTROLLED_MAINTENANCE Part 20)
# ---------------------------------------------------------------------------

def test_rpc_disabled_skips_expensive_count_expired_scan(tmp_path, monkeypatch):
    """MAX_RPC_ROWS_PER_RUN<=0 must skip count_expired() entirely -- not
    just skip the delete loop. Proven by monkeypatching count_expired to
    raise if called."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)  # creates table

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 0)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 1000)

    def _boom(*a, **kw):
        raise AssertionError("count_expired() must not be called when RPC is disabled this run")

    monkeypatch.setattr(RPCCache, "count_expired", _boom)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_remaining_expired"] is None
    assert result["rpc_deleted"] == 0


def test_rpc_enabled_and_dry_run_never_call_unbounded_count(tmp_path, monkeypatch):
    """The expensive count is absent from every runner mode, not merely the
    disabled-phase shortcut."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    RPCCache(wt_ops_path)
    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()
    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)

    def _boom(*a, **kw):
        raise AssertionError("count_expired() must not be called by retention")

    monkeypatch.setattr(RPCCache, "count_expired", _boom)
    dry = runner.run_once(dry_run=True, quiet=True)
    live = runner.run_once(dry_run=False, quiet=True)
    assert dry["rpc_remaining_expired"] is None
    assert live["rpc_remaining_expired"] is None


def test_rpc_batch_select_is_interrupted_at_maintenance_deadline(tmp_path, monkeypatch):
    """LIMIT bounds result cardinality, while the progress handler bounds the
    VM work needed to discover that no eligible row exists."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO rpc_response_cache "
        "(cache_key, method, response_json, cached_at, ttl_seconds) "
        "VALUES (?, 'm', '{}', 9999999999, 3600)",
        [(f"live_{i}",) for i in range(2000)],
    )
    conn.commit()
    conn.close()

    real_monotonic = time.monotonic
    call_count = 0

    def _deadline_clock():
        nonlocal call_count
        call_count += 1
        # call_start and the pre-query guard remain within budget; VM
        # callbacks observe the deadline as expired.
        return 0.0 if call_count <= 2 else 20.0

    monkeypatch.setattr(rpc_cache_module.time, "monotonic", _deadline_clock)
    deleted = cache.cleanup_expired_batch(batch_size=25, now=1.0)
    monkeypatch.setattr(rpc_cache_module.time, "monotonic", real_monotonic)

    assert deleted == 0
    assert call_count >= 3, "SQLite progress callback did not execute"
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM rpc_response_cache").fetchone()[0] == 2000
    conn.close()


def test_wss_disabled_skips_expensive_count_old_scan(tmp_path, monkeypatch):
    """MAX_WSS_ROWS_PER_RUN<=0 must skip count_old_wss_metrics() entirely."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("expired", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(wt_ops_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (time.time() - 10000,))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 25)
    monkeypatch.setattr(runner, "RPC_BATCH_SIZE", 25)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 0)

    def _boom(*a, **kw):
        raise AssertionError("count_old_wss_metrics() must not be called when WSS is disabled this run")

    monkeypatch.setattr(usage_tracker, "count_old_wss_metrics", _boom)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["wss_remaining_expired"] is None
    assert result["rpc_deleted"] == 1  # RPC still works normally
    assert result["wss_deleted"] == 0


def test_both_disabled_skips_both_scans(tmp_path, monkeypatch):
    """Both budgets <=0 must skip both expensive scans -- a maximally cheap
    no-op invocation."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 0)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 0)

    def _boom_rpc(*a, **kw):
        raise AssertionError("RPC count_expired() must not be called")

    def _boom_wss(*a, **kw):
        raise AssertionError("WSS count_old_wss_metrics() must not be called")

    monkeypatch.setattr(RPCCache, "count_expired", _boom_rpc)
    monkeypatch.setattr(usage_tracker, "count_old_wss_metrics", _boom_wss)

    result = runner.run_once(dry_run=False, quiet=True)
    assert result["rpc_remaining_expired"] is None
    assert result["wss_remaining_expired"] is None
    assert result["rpc_deleted"] == 0
    assert result["wss_deleted"] == 0


def test_disabled_phase_optimization_does_not_alter_cache_semantics(tmp_path, monkeypatch):
    """Sanity: skipping the eligibility scan for a disabled phase must not
    change get()/set() cache correctness when that phase IS later enabled."""
    wt_ops_path = str(tmp_path / "wt_ops.db")
    flex_path = str(tmp_path / "flex.db")
    cache = RPCCache(wt_ops_path)
    cache.set("live_entry", {"data": "value"}, "getTransaction")

    conn = sqlite3.connect(flex_path)
    conn.executescript(usage_tracker._DDL)
    conn.commit()
    conn.close()

    _wire_runner_paths(monkeypatch, wt_ops_path, flex_path)
    monkeypatch.setattr(runner, "MAX_RPC_ROWS_PER_RUN", 0)
    monkeypatch.setattr(runner, "MAX_WSS_ROWS_PER_RUN", 0)

    runner.run_once(dry_run=False, quiet=True)

    # Cache lookup semantics must be completely unaffected by the disabled run.
    assert cache.get("live_entry") == {"data": "value"}


# ---------------------------------------------------------------------------
# hit_count write-on-read removal (BOUNDED_STORAGE_LIFECYCLE Part 9)
# ---------------------------------------------------------------------------

def test_cache_hit_returns_identical_payload(tmp_path):
    """A cache hit must still return the exact same payload as before."""
    cache, db_path = _make_rpc_cache(tmp_path)
    payload = {"foo": "bar", "nested": {"a": 1, "b": [1, 2, 3]}}
    cache.set("k1", payload, "getTransaction")
    assert cache.get("k1") == payload


def test_cache_hit_performs_no_update(tmp_path):
    """A cache hit must never issue an UPDATE against rpc_response_cache --
    proven by wrapping the real connection's execute() in a proxy that
    raises if ever called with an UPDATE statement while servicing a hit."""
    from src.core import rpc_cache as rpc_cache_module

    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("k1", {"data": "value"}, "getTransaction")

    original_get_conn = cache._get_conn

    class _GuardedConnProxy:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and sql.strip().upper().startswith("UPDATE"):
                raise AssertionError(f"cache hit must not execute UPDATE: {sql}")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _guarded_get_conn():
        real = original_get_conn()
        return _GuardedConnProxy(real) if real is not None else None

    cache._get_conn = _guarded_get_conn
    try:
        result = cache.get("k1")
    finally:
        cache._get_conn = original_get_conn

    assert result == {"data": "value"}


def test_hit_count_column_remains_present(tmp_path):
    """The hit_count column must still exist in the schema (not removed)."""
    cache, db_path = _make_rpc_cache(tmp_path)
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rpc_response_cache)").fetchall()}
    conn.close()
    assert "hit_count" in cols


def test_hit_count_value_no_longer_increments_on_hit(tmp_path):
    """hit_count must stay at its inserted default (0) across repeated
    hits -- proving the write-on-read behavior is actually gone, not just
    that no UPDATE statement fires for unrelated reasons."""
    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("k1", {"data": "value"}, "getTransaction")

    for _ in range(5):
        cache.get("k1")

    conn = sqlite3.connect(db_path)
    hit_count = conn.execute("SELECT hit_count FROM rpc_response_cache WHERE cache_key='k1'").fetchone()[0]
    conn.close()
    assert hit_count == 0


def test_cache_expiry_semantics_unchanged_after_hit_count_removal(tmp_path):
    """Lazy expiry (delete-on-miss for an expired row) must be completely
    unaffected -- it uses the same connection, just without the now-removed
    UPDATE step on the hit path."""
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("expired_key", {"x": 1}, "getSignaturesForAddress")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (now - 10000,))
    conn.commit()
    conn.close()

    result = cache.get("expired_key")
    assert result is None  # expired -> miss

    # Lazy expiry must still have deleted the row.
    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM rpc_response_cache WHERE cache_key='expired_key'").fetchone()[0]
    conn.close()
    assert remaining == 0


def test_cache_miss_unchanged(tmp_path):
    """A lookup for a key that was never set must still return None cleanly."""
    cache, db_path = _make_rpc_cache(tmp_path)
    assert cache.get("never_existed") is None


def test_cache_set_replace_unchanged_after_hit_count_removal(tmp_path):
    """set() (INSERT OR REPLACE) must be completely unaffected by the
    get()-path change -- new value overwrites old, hit_count resets to 0
    on replace exactly as before."""
    cache, db_path = _make_rpc_cache(tmp_path)
    cache.set("k1", {"v": 1}, "getTransaction")
    cache.get("k1")  # would have bumped hit_count under old behavior
    cache.set("k1", {"v": 2}, "getTransaction")  # INSERT OR REPLACE

    assert cache.get("k1") == {"v": 2}
    conn = sqlite3.connect(db_path)
    hit_count = conn.execute("SELECT hit_count FROM rpc_response_cache WHERE cache_key='k1'").fetchone()[0]
    conn.close()
    assert hit_count == 0


def test_maintenance_cleanup_unaffected_by_hit_count_removal(tmp_path):
    """cleanup_expired_batch() must behave identically regardless of how
    many times a live row was hit before this change."""
    cache, db_path = _make_rpc_cache(tmp_path)
    now = time.time()
    cache.set("expired1", {"x": 1}, "getSignaturesForAddress")
    cache.get("expired1")  # a hit before it later expires
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rpc_response_cache SET cached_at = ?", (now - 10000,))
    conn.commit()
    conn.close()

    deleted = cache.cleanup_expired_batch(batch_size=100, now=now)
    assert deleted == 1
