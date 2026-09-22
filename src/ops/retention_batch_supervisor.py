"""Opt-in process boundary for one optional RPC-cache retention batch.

The parent never enters the SQLite writer lane. A timeout ends this run with
an unknown row count: a child may have committed immediately before it was
terminated. The next hourly invocation can safely reselect expired rows.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def run_rpc_batch(
    db_path: str, batch_size: int, cutoff: float, *, timeout_seconds: float,
    child_command: list[str] | None = None,
) -> dict[str, object]:
    """Return exact success or an indeterminate, fail-closed failure result."""
    if batch_size <= 0 or timeout_seconds <= 0:
        return {"status": "invalid", "deleted": None, "child_reaped": True}
    database = str(Path(db_path).resolve())
    command = child_command or [
        sys.executable, "-m", "src.ops.retention_batch_supervisor", "--child",
        database, str(int(batch_size)), repr(float(cutoff)),
    ]
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            command, cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=None, start_new_session=True,
        )
    except OSError:
        return {"status": "spawn_error", "deleted": None, "child_reaped": True}

    try:
        stdout, _ = child.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            child.kill()
            try:
                child.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                return {"status": "unreaped", "deleted": None,
                        "child_reaped": False, "pid": child.pid}
        return {"status": "timeout", "deleted": None,
                "child_reaped": True, "elapsed_seconds": round(time.monotonic() - started, 3)}

    if child.returncode != 0:
        return {"status": "child_error", "deleted": None, "child_reaped": True}
    try:
        payload = json.loads(stdout.decode("utf-8"))
        deleted = payload["deleted"]
        if not isinstance(deleted, int) or isinstance(deleted, bool) or not 0 <= deleted <= batch_size:
            raise ValueError("invalid deleted count")
    except (UnicodeError, ValueError, KeyError, TypeError):
        return {"status": "invalid_result", "deleted": None, "child_reaped": True}
    return {"status": "complete", "deleted": deleted, "child_reaped": True,
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def _child_main(args: list[str]) -> int:
    from src.core.rpc_cache import RPCCache

    if len(args) != 3:
        return 2
    db_path, raw_size, raw_cutoff = args
    if not Path(db_path).is_absolute():
        return 2
    batch_size = int(raw_size)
    if batch_size <= 0:
        return 2
    # The parent terminates an overdue child. Capture only its Python stack
    # at that boundary so a natural timeout identifies the blocking phase.
    # Chaining preserves SIGTERM's normal process-exit behavior.
    faulthandler.register(signal.SIGTERM, file=sys.stderr, all_threads=True, chain=True)
    print("[RETENTION_RPC_CHILD] phase=cache_open", file=sys.stderr, flush=True)
    cache = RPCCache(db_path)
    print("[RETENTION_RPC_CHILD] phase=cleanup", file=sys.stderr, flush=True)
    deleted = cache.cleanup_expired_batch(batch_size, now=float(raw_cutoff))
    print("[RETENTION_RPC_CHILD] phase=complete", file=sys.stderr, flush=True)
    print(json.dumps({"deleted": deleted}), flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("args", nargs="*")
    parsed = parser.parse_args()
    sys.exit(_child_main(parsed.args) if parsed.child else 2)
