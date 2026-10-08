import sqlite3
from contextlib import contextmanager

from src.ops.dev023_hot_rpc_cache_retention import RetentionLimits, retain_expired_rpc_cache


def _db(tmp_path):
    path = tmp_path / "canonical.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE rpc_response_cache (cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL, method TEXT NOT NULL, cached_at REAL NOT NULL, ttl_seconds INTEGER NOT NULL, hit_count INTEGER NOT NULL DEFAULT 0)")
    conn.executemany("INSERT INTO rpc_response_cache VALUES (?, '{}', 'getTransaction', ?, ?, 0)", [("expired-a", 1, 1), ("expired-b", 2, 1), ("live", 100, 100)])
    conn.commit(); conn.close()
    return path


def _limits(**values):
    defaults = {"min_free_bytes": 0, "max_wal_bytes": 10**9}
    defaults.update(values)
    return RetentionLimits(**defaults)


def _keys(path):
    conn = sqlite3.connect(path); rows = [row[0] for row in conn.execute("SELECT cache_key FROM rpc_response_cache ORDER BY cache_key")]; conn.close(); return rows


def test_expiry_and_nonexpired_preservation_and_idempotence(tmp_path):
    path = _db(tmp_path)
    first = retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=_limits(batch_rows=1))
    assert first == {"status": "COMPLETE", "deleted": 2}
    assert _keys(path) == ["live"]
    assert retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=_limits()) == {"status": "COMPLETE", "deleted": 0}


def test_max_rows_makes_restart_safe_progress(tmp_path):
    path = _db(tmp_path)
    result = retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=_limits(max_rows_per_run=1))
    assert result == {"status": "STOP_ROW_CAP", "deleted": 1}
    assert _keys(path) in (["expired-a", "live"], ["expired-b", "live"])


def test_selection_budget_interrupts_full_scan_before_any_delete(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO rpc_response_cache VALUES (?, '{}', 'm', 100, 100, 0)", [(f"live-{i}",) for i in range(4000)])
    conn.commit(); conn.close()
    result = retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10,
                                      limits=_limits(selection_progress_steps=10))
    assert result == {"status": "STOP_SELECTION_BUDGET", "deleted": 0}
    assert {"expired-a", "expired-b", "live"}.issubset(_keys(path))


def test_rejects_noncanonical_and_stops_for_disk_or_wal(tmp_path):
    path = _db(tmp_path)
    assert retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(tmp_path / "other.db"), cutoff=10, limits=_limits())["status"] == "REJECTED_NONCANONICAL_DATABASE"
    assert retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=RetentionLimits(min_free_bytes=1, max_wal_bytes=10**9), free_bytes=lambda _: 0)["status"] == "STOP_DISK_FLOOR"
    (tmp_path / "canonical.db-wal").write_bytes(b"x" * 4)
    assert retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=_limits(max_wal_bytes=1))["status"] == "STOP_WAL_CEILING"


def test_write_lane_is_bounded_and_nonexpired_rows_are_never_deleted(tmp_path):
    path = _db(tmp_path); calls = []
    @contextmanager
    def lane():
        calls.append("entered"); yield object(); calls.append("exited")
    result = retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=_limits(), write_lane=lane)
    assert result["deleted"] == 2 and calls == ["entered", "exited"] and _keys(path) == ["live"]


def test_busy_database_fails_closed_without_deleting(tmp_path):
    path = _db(tmp_path)
    lock = sqlite3.connect(path, timeout=0)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        result = retain_expired_rpc_cache(database_path=str(path), canonical_database_path=str(path), cutoff=10, limits=_limits())
    finally:
        lock.rollback(); lock.close()
    assert result == {"status": "STOP_WRITE_LANE_ERROR", "deleted": 0}
    assert set(_keys(path)) == {"expired-a", "expired-b", "live"}


def test_stop_file_blocks_contract_before_any_delete(tmp_path):
    path = _db(tmp_path)
    stop = tmp_path / "DEV023_STOP"
    stop.write_text("stop")
    assert retain_expired_rpc_cache(
        database_path=str(path), canonical_database_path=str(path), cutoff=10,
        limits=_limits(), stop_file=str(stop),
    ) == {"status": "STOP_FILE", "deleted": 0}
    assert set(_keys(path)) == {"expired-a", "expired-b", "live"}
