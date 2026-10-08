"""Disabled external-scheduler wrapper for the bounded DEV-023 tick.

This is deliberately a one-shot adapter: cron (or another existing external
scheduler) invokes it at most once per cadence.  It does not create a daemon,
change Supervisor, or enable itself.  State is bounded to 4 KiB (8 KiB while
atomically replacing) and contains metrics only--never cache payloads.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.ops.dev023_hot_rpc_cache_maintenance import RuntimeRetentionConfig, run_retention_tick
from src.ops.storage_lock_safety import CleanupLeaseHeldError, acquire_cleanup_lease


MAX_STATE_BYTES = 4096


@dataclass(frozen=True)
class ScheduledRetentionConfig:
    enabled: bool
    tick: RuntimeRetentionConfig
    lease_path: str
    state_path: str
    failure_alert_threshold: int = 3


def _read_state(path: Path) -> dict[str, int]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_STATE_BYTES:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


def _write_state(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("RETENTION_STATE_TOO_LARGE")
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


def run_scheduled_retention_tick(
    config: ScheduledRetentionConfig,
    *,
    tick_runner: Callable[..., dict[str, Any]] = run_retention_tick,
    write_service: Any = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run at most one existing P3 tick with external-scheduler safeguards."""
    if not config.enabled:
        return {"status": "DISABLED", "deleted": 0, "alert": False}
    try:
        with acquire_cleanup_lease(config.lease_path):
            result = tick_runner(config.tick, write_service=write_service)
    except CleanupLeaseHeldError:
        return {"status": "SKIP_OVERLAP", "deleted": 0, "alert": False}

    status = str(result.get("status", "STOP_UNKNOWN"))
    deleted = int(result.get("deleted", 0))
    healthy = status in {"COMPLETE", "STOP_ROW_CAP"}
    state = _read_state(Path(config.state_path))
    failures = 0 if healthy else int(state.get("consecutive_failures", 0)) + 1
    capped = int(state.get("consecutive_capacity_ticks", 0)) + 1 if status == "STOP_ROW_CAP" else 0
    _write_state(Path(config.state_path), {
        "consecutive_capacity_ticks": capped,
        "consecutive_failures": failures,
        "last_deleted": deleted,
        "last_run_at": int(now()),
    })
    return {
        "status": status,
        "deleted": deleted,
        "remaining_expired_rows": "UNKNOWN_NO_EXPIRY_INDEX",
        "maintenance_lag": "CAPACITY_PRESSURE" if capped else "CLEAR_OR_BELOW_CAP",
        "alert": failures >= config.failure_alert_threshold or capped >= config.failure_alert_threshold,
    }
