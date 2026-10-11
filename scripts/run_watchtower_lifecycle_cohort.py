#!/usr/bin/env python3
"""Fixed finite lifecycle dispatcher entry point; never a scheduler."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, RuntimeGateDenied, resolve_runtime_authority
from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission
from src.ops.watchtower_historical_execution_binding import BindingDenied, CohortStorageGuard
from src.ops.watchtower_lifecycle_dispatcher import FiniteLifecycleDispatcher, journal_outcome
from src.ops.watchtower_lifecycle_probe import OneRequestLifecycleProbe
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding

AUTHORIZATION = ROOT / "docs/audits/watchtower_lifecycle_cohort_authorization_20261011.v1.json"
COHORT_ROOT = Path("/private/tmp/dev014-watchtower-lifecycle-cohort")
QUEUE = Path("/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/queue")
SUPERVISOR = Path("/Users/kevinkeaveney/Dev/claude/flex/config/supervisor/supervisord.conf")
STATUS_NAME = "run_status.json"
STATUS_MAX_BYTES = 16 * 1024


def _source_sha() -> str:
    result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=False, text=True)
    value = result.stdout.strip()
    if result.returncode or len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError("LIFECYCLE_SOURCE_SHA_UNAVAILABLE")
    return value


def _write_status(root: Path, value: dict[str, object]) -> None:
    path = root / STATUS_NAME
    if path.is_symlink():
        raise RuntimeError("LIFECYCLE_STATUS_SYMLINK_DENIED")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > STATUS_MAX_BYTES:
        raise RuntimeError("LIFECYCLE_STATUS_TOO_LARGE")
    descriptor, temporary = tempfile.mkstemp(prefix=".lifecycle-status-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(root, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def _safe_reason(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:512] or exc.__class__.__name__


def _is_controlled_hold(exc: BaseException) -> bool:
    return isinstance(exc, (RuntimeGateDenied, BindingDenied)) or _safe_reason(exc).startswith(("PROBE_DISK_", "PROBE_STORAGE_"))


def _durable_completion_count(authorization: dict[str, object], journal_root: Path) -> int:
    return sum(journal_outcome(journal_root / record["request_identity"] / "journal.json", record) != "NEW" for record in authorization["records"])


def _status(authorization: dict[str, object], source_sha: str, run_id: str, started_at: int, state: str, *, completed: int, completed_sessions: int, current_session: int, reason: str | None = None, exception: BaseException | None = None, exit_code: int | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "authorization_hash": authorization["content_hash"], "engineering_source_sha": source_sha,
        "run_id": run_id, "pid": os.getpid(), "started_at": started_at, "state": state,
        "last_durable_completion_count": completed, "completed_sessions": completed_sessions,
        "current_session": current_session, "updated_at": int(time.time()), "exit_code": exit_code,
    }
    if reason: value["reason"] = reason[:512]
    if exception: value.update({"exception_class": exception.__class__.__name__, "exception_reason": _safe_reason(exception)})
    return value


@contextmanager
def _lock(root: Path):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = root / ".cohort.lock"
    if lock.is_symlink(): raise RuntimeError("LIFECYCLE_COHORT_LOCK_DENIED")
    with lock.open("a+") as handle:
        try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc: raise RuntimeError("LIFECYCLE_COHORT_ALREADY_RUNNING") from exc
        yield


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--test-fixture", action="store_true"); args = parser.parse_args()
    if not args.test_fixture and os.environ.get("DEV014_BIRDEYE_ENV_BOUND") != "1": raise SystemExit("BOUND_PROCESS_REQUIRED")
    root = COHORT_ROOT; guard = CohortStorageGuard(root); authorization = json.loads(AUTHORIZATION.read_text())
    source_sha = _source_sha(); run_id = uuid.uuid4().hex; started_at = int(time.time())
    if args.test_fixture:
        class Gate:
            def check(self): return None
        from src.ops.token_data_provider_bindings import ProviderTransportOutcome
        transport = lambda _request: ProviderTransportOutcome(200, {"success": True, "data": {"items": []}}, {})
        gate = Gate(); admission = HistoricalForensicsBudgetAdmission(root / "fixture-queue")
    else:
        gate = Batch4RuntimeGate(resolve_runtime_authority(SUPERVISOR)); admission = HistoricalForensicsBudgetAdmission(QUEUE); transport = BirdeyeProductionBinding()
    def factory(record, journal):
        auth = {key: record[key] for key in ("rank", "mint", "creation_timestamp", "time_from", "time_to", "interval", "content_hash")}
        return OneRequestLifecycleProbe(auth, journal, runtime_gate=gate, admission=admission, transport=transport,
            disk_free=lambda: shutil.disk_usage(root).free, storage_guard=guard)
    with _lock(root):
        prior_completed = _durable_completion_count(authorization, root / "journals")
        _write_status(root, _status(authorization, source_sha, run_id, started_at, "STARTED", completed=prior_completed, completed_sessions=0, current_session=1))
        def progress(value):
            value = dict(value); value["completed"] += prior_completed
            _write_status(root, _status(authorization, source_sha, run_id, started_at, "RUNNING", **value))
        try:
            result = FiniteLifecycleDispatcher(authorization, root / "journals", health=gate.check, probe_factory=factory, storage_guard=guard, progress=progress).run()
        except Exception as exc:
            state = "HOLD" if _is_controlled_hold(exc) else "FAILED"
            try: durable_completed = _durable_completion_count(authorization, root / "journals")
            except Exception: durable_completed = prior_completed
            _write_status(root, _status(authorization, source_sha, run_id, started_at, state, completed=durable_completed, completed_sessions=0, current_session=1, reason=_safe_reason(exc), exception=exc, exit_code=1))
            raise
        terminal = "COMPLETED" if result["status"] == "EXHAUSTED" else "HOLD"
        _write_status(root, _status(authorization, source_sha, run_id, started_at, terminal, completed=prior_completed + len(result.get("completed", [])), completed_sessions=len(result.get("sessions", [])), current_session=len(result.get("sessions", [])) + 1, reason=None if terminal == "COMPLETED" else str(result.get("reason") or result["status"]), exit_code=0))
    print(json.dumps({"status": result["status"], "completed": len(result.get("completed", [])), "sessions": len(result.get("sessions", []))}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
