"""Bounded, atomic worker-liveness sidecars.

These sidecars complement database-backed worker progress heartbeats.  They
never connect to SQLite and therefore never bypass or contend for the
canonical write lane.  A stale or malformed sidecar is deliberately treated
as unavailable rather than as proof of liveness.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping


SCHEMA = "worker_liveness.v1"
MAX_LIVENESS_BYTES = 4096


def default_liveness_path(db_path: str, worker_name: str) -> str:
    """Return the one bounded sidecar path for a worker/database pair."""
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in worker_name)
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), f".{safe_name}.liveness.json")


def publish_liveness(
    path: str,
    *,
    worker_name: str,
    pid: int,
    progress_at: float,
    progress_phase: str,
    progress_cycle: int,
    observed_at: float | None = None,
) -> bool:
    """Atomically replace a compact liveness record without creating a DB writer."""
    observed = float(time.time() if observed_at is None else observed_at)
    payload = {
        "schema": SCHEMA,
        "worker_name": str(worker_name)[:120],
        "pid": int(pid),
        "observed_at": observed,
        "progress_at": float(progress_at),
        "progress_phase": str(progress_phase)[:120],
        "progress_cycle": max(0, int(progress_cycle)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_LIVENESS_BYTES:
        return False
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        return False
    temp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except OSError:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return False


def read_liveness(path: str, *, worker_name: str, now: float | None = None) -> dict[str, Any] | None:
    """Read one validated bounded sidecar; malformed data fails closed."""
    try:
        if os.path.getsize(path) > MAX_LIVENESS_BYTES:
            return None
        with open(path, "rb") as handle:
            raw = handle.read(MAX_LIVENESS_BYTES + 1)
        if len(raw) > MAX_LIVENESS_BYTES:
            return None
        row = json.loads(raw.decode("utf-8"))
        if not isinstance(row, Mapping) or row.get("schema") != SCHEMA:
            return None
        if row.get("worker_name") != worker_name:
            return None
        observed_at = float(row["observed_at"])
        progress_at = float(row["progress_at"])
        current = float(time.time() if now is None else now)
        if observed_at > current + 5 or progress_at > current + 5:
            return None
        return {
            "pid": int(row["pid"]),
            "observed_at": observed_at,
            "progress_at": progress_at,
            "progress_phase": str(row.get("progress_phase") or "")[:120],
            "progress_cycle": max(0, int(row.get("progress_cycle") or 0)),
            "liveness_age_s": max(0, int(current - observed_at)),
            "progress_age_s": max(0, int(current - progress_at)),
        }
    except (OSError, TypeError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def annotate_progress_health(row: dict[str, Any], liveness: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expose liveness separately without changing database-progress health."""
    row["progress_age_s"] = row.get("age_s")
    row["liveness"] = dict(liveness) if liveness is not None else {"state": "unavailable"}
    return row
