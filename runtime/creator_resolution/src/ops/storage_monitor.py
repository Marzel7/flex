"""STORAGE-LIFECYCLE-P1: lightweight storage monitor (Part 16).

Cheap, incremental measurements only -- never a full recursive scan of
the artifacts/ content-addressed store on every call. Named files get
os.stat() (O(1)); directories intended for periodic (not per-call)
measurement should be sampled by a separate, explicitly-scheduled census
pass (see the storage census artifact), not by this hot-path module.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field


@dataclass
class DbSizeSample:
    path: str
    bytes: int
    wal_bytes: int
    shm_present: bool
    measured_at: float


@dataclass
class StorageSnapshot:
    measured_at: float
    free_bytes: int
    total_bytes: int
    db_sizes: list[DbSizeSample] = field(default_factory=list)


def _safe_stat_bytes(path: str) -> int:
    try:
        return os.stat(path).st_size
    except (FileNotFoundError, OSError):
        return 0


def measure_db(db_path: str) -> DbSizeSample:
    """O(1) -- three stat() calls, no scan, no SQLite connection opened."""
    return DbSizeSample(
        path=db_path,
        bytes=_safe_stat_bytes(db_path),
        wal_bytes=_safe_stat_bytes(db_path + "-wal"),
        shm_present=os.path.exists(db_path + "-shm"),
        measured_at=time.time(),
    )


def measure_disk_free(path: str) -> tuple[int, int]:
    """Returns (free_bytes, total_bytes) for the filesystem containing
    path. O(1) -- a single statvfs-backed call, not a directory walk."""
    usage = shutil.disk_usage(path)
    return usage.free, usage.total


def take_snapshot(*, root_path: str, tracked_db_paths: list[str]) -> StorageSnapshot:
    free_bytes, total_bytes = measure_disk_free(root_path)
    return StorageSnapshot(
        measured_at=time.time(),
        free_bytes=free_bytes,
        total_bytes=total_bytes,
        db_sizes=[measure_db(p) for p in tracked_db_paths],
    )


def growth_delta(before: DbSizeSample, after: DbSizeSample) -> int:
    """Raw byte delta between two samples of the SAME db_path. Callers
    must not extrapolate a single short-window delta into a daily rate
    without a longer, clearly-labeled observation window (see Part 25
    growth-forecast discipline)."""
    if before.path != after.path:
        raise ValueError("samples must be for the same path")
    return after.bytes - before.bytes
