#!/usr/bin/env python3
"""Explicit finite DEV-014 cohort launcher; never a daemon or scheduler."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, resolve_runtime_authority
from src.ops.watchtower_historical_backfill_controller import HistoricalBackfillController
from src.ops.watchtower_historical_cohort import cli_executor, execute_authorized_sessions
from src.ops.watchtower_historical_execution_binding import CohortStorageGuard


BOUND = "DEV014_BIRDEYE_ENV_BOUND"
AUTHORIZATION = ROOT / "docs/audits/dev014_creation_time_anchored_cohort_authorization_20261010.v1.json"
RECON = ROOT / "docs/audits/dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json"
POPULATION = ROOT / "docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json"
JOURNALS = (
    Path("/private/tmp/dev014-evidence/journal.json"),
    Path("/private/tmp/dev014-r51-70-evidence/journal.json"),
    Path("/private/tmp/dev014-r71-90-evidence/journal.json"),
    Path("/private/tmp/dev014-final-four-evidence/journal.json"),
)
SUPERVISOR = Path("/Users/kevinkeaveney/Dev/claude/flex/config/supervisor/supervisord.conf")
QUEUE = Path("/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/queue")
DEFAULT_ROOT = Path("/private/tmp/dev014-creation-time-cohort-20261010")


class LaunchDenied(RuntimeError):
    pass


@contextmanager
def _exclusive_lock(root: Path) -> Iterator[None]:
    lock = root / ".cohort.lock"
    if lock.is_symlink():
        raise LaunchDenied("COHORT_LOCK_SYMLINK_DENIED")
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LaunchDenied("COHORT_ALREADY_RUNNING") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _root(path: Path) -> Path:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise LaunchDenied("COHORT_ROOT_UNSAFE")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise LaunchDenied("COHORT_ROOT_UNSAFE")
    return path.resolve()


def _run(root: Path) -> dict[str, object]:
    if os.environ.get(BOUND) != "1" or not os.environ.get("BIRDEYE", "").strip():
        raise LaunchDenied("BOUND_BIRDEYE_ENV_REQUIRED")
    root = _root(root)
    evidence, manifests = root / "evidence", root / "manifests"
    evidence.mkdir(mode=0o700, exist_ok=True); manifests.mkdir(mode=0o700, exist_ok=True)
    if evidence.is_symlink() or manifests.is_symlink():
        raise LaunchDenied("COHORT_ROOT_UNSAFE")
    guard = CohortStorageGuard(root)
    guard.check()
    authorization = json.loads(AUTHORIZATION.read_text())
    controller = HistoricalBackfillController(root / "controller.json", json.loads(RECON.read_text()), json.loads(POPULATION.read_text()))
    authority = resolve_runtime_authority(SUPERVISOR)
    gate = Batch4RuntimeGate(authority)
    adapter = cli_executor(ROOT / "scripts/run_watchtower_historical_backfill_session.py", [
        "--mode", "execute", "--live-opt-in", "--state-dir", str(root), "--evidence-dir", str(evidence),
        "--max-requests", "50", "--max-runtime-seconds", "3600", "--max-evidence-bytes", "5242880",
        "--max-consecutive-failures", "1", "--max-health-failures", "1", "--supervisor-config", str(SUPERVISOR),
        "--queue-root", str(QUEUE),
    ])
    with _exclusive_lock(root):
        result = execute_authorized_sessions(controller, authorization, authoritative_journals=list(JOURNALS),
            session_journal=evidence / "journal.json", manifest_root=manifests, execute_session=adapter,
            health=gate.check, storage_ok=guard.check,
            cancelled=lambda: bool(controller._state().get("cancelled")))
    return {"status": result["status"], "completed": len(result.get("completed", [])), "sessions": len(result.get("sessions", []))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--bound", action="store_true")
    parser.add_argument("--cohort-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("EXPLICIT_COHORT_EXECUTION_REQUIRED")
    if not args.bound:
        # Only the established preflight wrapper is allowed to source BIRDEYE.
        command = [sys.executable, str(ROOT / "scripts/run_dev014_birdeye_preflight.py"), "--mode", "cohort", "--cohort-root", str(args.cohort_root)]
        return os.spawnv(os.P_WAIT, sys.executable, command)
    try:
        print(json.dumps(_run(args.cohort_root), sort_keys=True))
        return 0
    except LaunchDenied as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
