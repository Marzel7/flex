import json
import sqlite3
from pathlib import Path

import pytest

from src.core.database_write_service import (
    CrossProcessDatabaseWriteTimeout,
    PRIORITY_P3_HOUSEKEEPING,
)
from src.ops.dev023_hot_rpc_cache_maintenance import (
    RuntimeRetentionConfig,
    run_retention_tick,
)
from src.ops.dev023_hot_rpc_cache_retention import RetentionLimits


def _db(tmp_path: Path, expired: int = 2) -> Path:
    path = tmp_path / "canonical.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE rpc_response_cache (cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL, "
        "method TEXT NOT NULL, cached_at REAL NOT NULL, ttl_seconds INTEGER NOT NULL, "
        "hit_count INTEGER NOT NULL DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO rpc_response_cache VALUES (?, '{}', 'getTransaction', 1, 1, 0)",
        [(f"expired-{i}",) for i in range(expired)],
    )
    conn.execute("INSERT INTO rpc_response_cache VALUES ('live', '{}', 'getTransaction', 100, 100, 0)")
    conn.commit()
    conn.close()
    return path


def _limits(**overrides):
    values = {"min_free_bytes": 0, "max_wal_bytes": 10**9, "batch_rows": 200, "max_rows_per_run": 200}
    values.update(overrides)
    return RetentionLimits(**values)


class _Service:
    def __init__(self, *, timeout=False):
        self.registered = []
        self.submissions = []
        self.timeout = timeout

    def register_database(self, selector, path):
        self.registered.append((selector, path))

    def submit(self, selector, command, transaction, *, priority):
        self.submissions.append((selector, command, priority))
        if self.timeout:
            raise CrossProcessDatabaseWriteTimeout(
                database="canonical", lock_path="fixture.lock", waiting_pid=1,
                waiting_thread="fixture", command=command, wait_seconds=0.0,
                current_owner=None,
            )
        conn = sqlite3.connect(self.registered[-1][1])
        try:
            conn.execute("BEGIN")
            result = transaction(conn)
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def _config(path, **overrides):
    values = dict(enabled=True, canonical_database_path=str(path), cutoff=10, stop_file="", limits=_limits())
    values.update(overrides)
    return RuntimeRetentionConfig(**values)


def _keys(path):
    conn = sqlite3.connect(path)
    rows = [row[0] for row in conn.execute("SELECT cache_key FROM rpc_response_cache ORDER BY cache_key")]
    conn.close()
    return rows


def test_disabled_schedule_never_registers_or_opens_database(tmp_path):
    service = _Service()
    result = run_retention_tick(_config(tmp_path / "missing.db", enabled=False), write_service=service)
    assert result == {"status": "DISABLED", "deleted": 0}
    assert service.registered == []


def test_success_is_one_p3_bounded_batch_and_preserves_live_rows(tmp_path):
    path, service = _db(tmp_path, expired=201), _Service()
    result = run_retention_tick(_config(path), write_service=service)
    assert result == {"status": "STOP_ROW_CAP", "deleted": 200}
    assert service.submissions[0][2] == PRIORITY_P3_HOUSEKEEPING
    assert len(service.registered) == 1 and service.registered[0][1] == str(path.resolve())
    assert _keys(path) == ["expired-200", "live"]


def test_timeout_skips_without_direct_sqlite_fallback(tmp_path):
    path, service = _db(tmp_path), _Service(timeout=True)
    assert run_retention_tick(_config(path), write_service=service) == {
        "status": "STOP_WRITE_LANE_TIMEOUT", "deleted": 0
    }
    assert set(_keys(path)) == {"expired-0", "expired-1", "live"}


@pytest.mark.parametrize("guard", ["stop", "disk", "wal"])
def test_stop_file_disk_and_wal_guards_stop_before_write(tmp_path, monkeypatch, guard):
    path, service = _db(tmp_path), _Service()
    config = _config(path)
    if guard == "stop":
        stop = tmp_path / "STOP"
        stop.write_text("stop")
        config = _config(path, stop_file=str(stop))
        expected = "STOP_FILE"
    elif guard == "disk":
        config = _config(path, limits=_limits(min_free_bytes=10**30))
        expected = "STOP_DISK_FLOOR"
    else:
        (tmp_path / "canonical.db-wal").write_bytes(b"wal")
        config = _config(path, limits=_limits(max_wal_bytes=1))
        expected = "STOP_WAL_CEILING"
    assert run_retention_tick(config, write_service=service) == {"status": expected, "deleted": 0}
    assert service.registered == [] and len(_keys(path)) == 3


def test_idempotent_rerun_and_unintended_target_rejection(tmp_path):
    path, service = _db(tmp_path), _Service()
    config = _config(path)
    assert run_retention_tick(config, write_service=service) == {"status": "COMPLETE", "deleted": 2}
    assert run_retention_tick(config, write_service=service) == {"status": "COMPLETE", "deleted": 0}
    missing = _config(tmp_path / "wrong.db")
    assert run_retention_tick(missing, write_service=_Service()) == {
        "status": "REJECTED_MISSING_DATABASE", "deleted": 0
    }
    assert _keys(path) == ["live"]


def test_runtime_rejects_any_batch_larger_than_qualified_ceiling(tmp_path):
    path = _db(tmp_path)
    assert run_retention_tick(_config(path, limits=_limits(batch_rows=201)), write_service=_Service()) == {
        "status": "REJECTED_INVALID_LIMITS", "deleted": 0
    }


def test_checked_in_schedule_is_explicitly_disabled_without_a_live_installer():
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config/maintenance/dev023_hot_rpc_cache_retention.json").read_text())
    assert payload["enabled"] is False
    assert payload["maximum_rows_per_tick"] == 200
    assert payload["activation"] == "EXPLICIT_SEPARATE_AUTHORIZATION_REQUIRED"
