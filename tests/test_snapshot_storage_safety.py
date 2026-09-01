"""P0/P1 low-disk safety tests; all persistence is under pytest tmp_path."""
from __future__ import annotations

import errno
import os
import threading
import time
from types import SimpleNamespace

import pytest

import src.core.intelligence_snapshot_scheduler as scheduler
import src.ops.intelligence_snapshots as snapshots


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(scheduler, "LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(snapshots, "MIN_FREE_BYTES", 0)
    monkeypatch.setattr(scheduler, "_PERSISTENCE_CIRCUIT", None)


def test_low_disk_rejects_before_temp_file(monkeypatch):
    monkeypatch.setattr(snapshots, "MIN_FREE_BYTES", 100)
    monkeypatch.setattr(snapshots.shutil, "disk_usage", lambda _p: SimpleNamespace(free=99))
    with pytest.raises(snapshots.SnapshotDiskSpaceError) as raised:
        snapshots.write_snapshot("f", 1, {"x": 1}, build_duration_ms=1)
    assert raised.value.reason == "INSUFFICIENT_DISK_SPACE"
    assert not os.path.exists(snapshots.SNAPSHOT_DIR) or not os.listdir(snapshots.SNAPSHOT_DIR)


def test_serialization_failure_cleans_tmp_and_preserves_completed_snapshot(monkeypatch):
    snapshots.write_snapshot("f", 1, {"old": True}, build_duration_ms=1)
    original_dump = snapshots.json.dump
    monkeypatch.setattr(snapshots.json, "dump", lambda *_a, **_k: (_ for _ in ()).throw(TypeError("bad json")))
    with pytest.raises(TypeError, match="bad json"):
        snapshots.write_snapshot("f", 1, {"new": object()}, build_duration_ms=1)
    assert snapshots.read_snapshot("f", 1).payload == {"old": True}
    assert not [p for p in os.listdir(snapshots.SNAPSHOT_DIR) if ".tmp." in p]
    monkeypatch.setattr(snapshots.json, "dump", original_dump)


def test_enospc_and_cleanup_failure_preserve_original_error(monkeypatch):
    monkeypatch.setattr(snapshots.json, "dump", lambda *_a, **_k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    monkeypatch.setattr(snapshots.os, "unlink", lambda _p: (_ for _ in ()).throw(OSError("cleanup failed")))
    with pytest.raises(OSError) as raised:
        snapshots.write_snapshot("f", 1, {"x": 1}, build_duration_ms=1)
    assert raised.value.errno == errno.ENOSPC


def test_persistence_is_single_flight(monkeypatch):
    active = 0
    peak = 0
    guard = threading.Lock()
    original_replace = snapshots.os.replace

    def slow_replace(src, dst):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        original_replace(src, dst)
        with guard:
            active -= 1

    monkeypatch.setattr(snapshots.os, "replace", slow_replace)
    threads = [threading.Thread(target=snapshots.write_snapshot, args=(f"f{i}", 1, {"x": i}), kwargs={"build_duration_ms": 1}) for i in range(3)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert peak == 1


def test_scheduler_opens_circuit_and_skips_next_expensive_build(monkeypatch):
    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence", lambda _w: {"total_launches": 1})
    monkeypatch.setattr(scheduler, "write_snapshot", lambda *_a, **_k: (_ for _ in ()).throw(snapshots.SnapshotDiskSpaceError(path="x", free_bytes=1, minimum_bytes=2)))
    first = scheduler.refresh_one("operational_intelligence", 1)
    second = scheduler.refresh_one("operational_intelligence", 2)
    assert first["status"] == "INSUFFICIENT_DISK_SPACE"
    assert second["status"] == "SKIPPED_CIRCUIT_OPEN"
