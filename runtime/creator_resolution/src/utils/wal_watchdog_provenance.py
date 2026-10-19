"""Bounded, metadata-only provenance for a critical SQLite WAL pin.

The collector is diagnostic only.  It does not connect to SQLite, checkpoint,
signal a process, or inspect SQL values.  Cross-process connection age and
transaction age come from the opt-in connection lifecycle JSONL stream.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Iterable


MAX_HOLDERS = 20
MAX_FIELD = 240
MAX_LIFECYCLE_BYTES = 4 * 1024 * 1024


def _bounded(value: Any) -> str:
    return str(value or "")[:MAX_FIELD]


def _read_tail(path: str, max_bytes: int = MAX_LIFECYCLE_BYTES) -> Iterable[dict]:
    if not path or not os.path.isfile(path):
        return []
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        start = max(0, size - max_bytes)
        handle.seek(start)
        if start:
            handle.readline()
        lines = handle.readlines()
    records = []
    for raw in lines:
        try:
            event = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            records.append(event)
    return records


def _lifecycle_state(path: str, db_path: str, live_pids: set[int], now: float) -> dict[int, list[dict]]:
    connections: dict[tuple[int, str], dict] = {}
    for event in _read_tail(path):
        try:
            pid = int(event.get("pid"))
        except (TypeError, ValueError):
            continue
        if pid not in live_pids:
            continue
        event_path = event.get("path")
        if event_path and os.path.abspath(str(event_path)) != os.path.abspath(db_path):
            continue
        connection_id = str(event.get("connection_id") or "")
        if not connection_id:
            continue
        key = (pid, connection_id)
        kind = event.get("event")
        if kind == "open":
            connections[key] = {
                "connection_id": connection_id,
                "opened_at": event.get("timestamp") or event.get("opened_at"),
                "caller": _bounded(event.get("caller")),
                "purpose": _bounded(event.get("purpose")),
                "mode": _bounded(event.get("mode")),
                "transaction_started_at": None,
            }
        elif kind == "sqlite_tx_begin" and key in connections:
            connections[key]["transaction_started_at"] = event.get("timestamp")
        elif kind in {"commit_end", "rollback_end"} and key in connections:
            connections[key]["transaction_started_at"] = None
        elif kind == "close":
            connections.pop(key, None)

    by_pid: dict[int, list[dict]] = {}
    for (pid, _), state in connections.items():
        opened_at = state.pop("opened_at", None)
        tx_at = state.pop("transaction_started_at", None)
        state["connection_age_seconds"] = round(max(0.0, now - float(opened_at)), 3) if opened_at else None
        state["read_transaction_age_seconds"] = round(max(0.0, now - float(tx_at)), 3) if tx_at else None
        state["transaction_active"] = tx_at is not None
        by_pid.setdefault(pid, []).append(state)
    for rows in by_pid.values():
        rows.sort(key=lambda row: (not row["transaction_active"], -(row["connection_age_seconds"] or 0)))
    return by_pid


def _process_commands(pids: set[int]) -> dict[int, str]:
    commands: dict[int, str] = {}
    for pid in sorted(pids):
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=2, check=False,
            )
            commands[pid] = _bounded(result.stdout.strip())
        except Exception as exc:
            commands[pid] = f"UNAVAILABLE:{type(exc).__name__}"
    return commands


def collect_wal_pin_provenance(
    *, db_path: str, checkpoint: dict, holder_pids: Iterable[int],
    lifecycle_path: str | None = None, now: float | None = None,
) -> dict:
    """Return a bounded snapshot without touching SQLite or process state."""
    captured_at = float(now if now is not None else time.time())
    pids = {int(pid) for pid in holder_pids if int(pid) > 0}
    pids = set(sorted(pids)[:MAX_HOLDERS])
    lifecycle = _lifecycle_state(lifecycle_path or "", db_path, pids, captured_at)
    commands = _process_commands(pids)
    holders = []
    for pid in sorted(pids):
        rows = lifecycle.get(pid, [])[:10]
        holders.append({
            "pid": pid,
            "command": commands.get(pid, ""),
            "connections": rows,
            "connection_provenance_available": bool(rows),
        })
    return {
        "schema": "sqlite.wal_pin_provenance.v1",
        "captured_at": captured_at,
        "database_basename": os.path.basename(db_path),
        "checkpoint": {
            "busy": int(checkpoint.get("busy", -1)),
            "log_frames": int(checkpoint.get("log_frames", -1)),
            "checkpointed_frames": int(checkpoint.get("checkpointed_frames", -1)),
        },
        "holders": holders,
        "holder_count": len(holders),
        "lifecycle_path_configured": bool(lifecycle_path),
    }
