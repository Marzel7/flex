"""Fresh-window safety predicate for the bounded Watchtower monitor soak.

The predicate deliberately observes only files; it neither opens SQLite nor
starts/stops a process.  A caller snapshots immediately before the one-shot
bridge and evaluates immediately afterwards, so retained historical errors
cannot veto a later healthy iteration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time
from typing import Callable


ERROR_MARKERS = (b"WAL_WATCHDOG", b"CROSS_PROCESS_LOCK", b"SQLITE_BUSY", b"database is locked")


@dataclass(frozen=True)
class FreshWindow:
    log_path: str
    wal_path: str
    lock_path: str
    log_device: int
    log_inode: int
    log_size: int
    log_mtime_ns: int
    wal_size: int
    lock_generation: str | None


@dataclass(frozen=True)
class FreshWindowResult:
    accepted: bool
    reasons: tuple[str, ...]
    log_mode: str
    fresh_error_count: int
    wal_size_before: int
    wal_size_after: int
    lock_generation_before: str | None
    lock_generation_after: str | None


def snapshot(log_path: Path, wal_path: Path, lock_path: Path) -> FreshWindow:
    """Capture the compact pre-iteration boundary without touching SQLite."""
    stat = log_path.stat()
    return FreshWindow(
        log_path=str(log_path), wal_path=str(wal_path), lock_path=str(lock_path),
        log_device=stat.st_dev, log_inode=stat.st_ino, log_size=stat.st_size,
        log_mtime_ns=stat.st_mtime_ns, wal_size=wal_path.stat().st_size if wal_path.exists() else 0,
        lock_generation=_lock(lock_path).get("lease_generation"),
    )


def write_snapshot(path: Path, window: FreshWindow) -> None:
    """Persist one compact, caller-selected boundary document."""
    path.write_text(json.dumps(asdict(window), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def read_snapshot(path: Path) -> FreshWindow:
    return FreshWindow(**json.loads(path.read_text(encoding="utf-8")))


def evaluate(window: FreshWindow, *, now: float | None = None,
             owner_alive: Callable[[int], bool] | None = None,
             persistent_lock_seconds: float = 60.0) -> FreshWindowResult:
    """Accept only a clean post-boundary log and non-persistent active lock."""
    log = Path(window.log_path)
    wal = Path(window.wal_path)
    lock = Path(window.lock_path)
    if not log.is_file():
        return _result(False, ("FRESH_WINDOW_LOG_UNAVAILABLE",), "unavailable", 0, window, wal, _lock(lock))
    stat = log.stat()
    # Rotation creates a new file, and truncation may retain the inode.  In
    # either case, the entire current file belongs to the fresh window; scan it
    # rather than relying on a stale byte offset.
    if (stat.st_dev, stat.st_ino) != (window.log_device, window.log_inode):
        mode, start = "rotated", 0
    elif stat.st_size < window.log_size:
        mode, start = "truncated", 0
    else:
        mode, start = "appended", window.log_size
    fresh = log.read_bytes()[start:]
    errors = sum(fresh.count(marker) for marker in ERROR_MARKERS)
    current = _lock(lock)
    reasons: list[str] = []
    if errors:
        reasons.append("FRESH_WINDOW_DATABASE_ERROR")
    timestamp = time.time() if now is None else now
    owner = current.get("process_pid")
    acquired = current.get("acquired_at")
    alive = owner_alive or _pid_alive
    if (current.get("state") == "ACTIVE" and isinstance(owner, int)
            and isinstance(acquired, (int, float))
            and timestamp - acquired > persistent_lock_seconds and alive(owner)):
        reasons.append("FRESH_WINDOW_PERSISTENT_ACTIVE_LOCK")
    return _result(not reasons, tuple(reasons), mode, errors, window, wal, current)


def _result(accepted: bool, reasons: tuple[str, ...], mode: str, errors: int,
            window: FreshWindow, wal: Path, current: dict) -> FreshWindowResult:
    return FreshWindowResult(
        accepted=accepted, reasons=reasons, log_mode=mode, fresh_error_count=errors,
        wal_size_before=window.wal_size, wal_size_after=wal.stat().st_size if wal.exists() else 0,
        lock_generation_before=window.lock_generation,
        lock_generation_after=current.get("lease_generation"),
    )


def _lock(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
