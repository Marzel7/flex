import json
import os

from src.ops.watchtower_soak_safety import evaluate, snapshot


def _paths(tmp_path):
    log, wal, lock = (tmp_path / "bridge.err", tmp_path / "ops.db-wal", tmp_path / "ops.lock")
    log.write_text("historical [CROSS_PROCESS_LOCK] timeout\n")
    wal.write_bytes(b"wal")
    lock.write_text("{}")
    return log, wal, lock


def test_historical_errors_before_snapshot_are_ignored(tmp_path):
    log, wal, lock = _paths(tmp_path)
    result = evaluate(snapshot(log, wal, lock))
    assert result.accepted and result.fresh_error_count == 0 and result.log_mode == "appended"


def test_new_error_after_snapshot_fails_closed(tmp_path):
    log, wal, lock = _paths(tmp_path); window = snapshot(log, wal, lock)
    log.write_text(log.read_text() + "[WAL_WATCHDOG] checkpoint failed\n")
    assert "FRESH_WINDOW_DATABASE_ERROR" in evaluate(window).reasons


def test_rotation_and_truncation_scan_only_fresh_current_file(tmp_path):
    log, wal, lock = _paths(tmp_path); window = snapshot(log, wal, lock)
    old = tmp_path / "old.log"; os.rename(log, old); log.write_text("clean\n")
    assert evaluate(window).accepted and evaluate(window).log_mode == "rotated"
    log.write_text("clean baseline long enough for truncation\n")
    window = snapshot(log, wal, lock); log.write_text("[SQLITE_BUSY]\n")
    result = evaluate(window)
    assert result.log_mode == "truncated" and not result.accepted


def test_persistent_current_active_lock_fails_closed(tmp_path):
    log, wal, lock = _paths(tmp_path); window = snapshot(log, wal, lock)
    lock.write_text(json.dumps({"lease_generation": "new", "state": "ACTIVE", "process_pid": 42, "acquired_at": 10.0}))
    assert "FRESH_WINDOW_PERSISTENT_ACTIVE_LOCK" in evaluate(window, now=80.1, owner_alive=lambda pid: pid == 42).reasons


def test_healthy_bounded_bridge_completion_is_accepted(tmp_path):
    log, wal, lock = _paths(tmp_path); window = snapshot(log, wal, lock)
    log.write_text(log.read_text() + "bridge delivered=0\n")
    lock.write_text(json.dumps({"lease_generation": "release", "state": "RELEASE_PENDING", "process_pid": 42, "acquired_at": 10.0}))
    assert evaluate(window, now=80.1, owner_alive=lambda _: True).accepted
