#!/usr/bin/env python3
"""Fixed finite lifecycle dispatcher entry point; never a scheduler."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, resolve_runtime_authority
from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission
from src.ops.watchtower_historical_execution_binding import CohortStorageGuard
from src.ops.watchtower_lifecycle_dispatcher import FiniteLifecycleDispatcher
from src.ops.watchtower_lifecycle_probe import OneRequestLifecycleProbe
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding

AUTHORIZATION = ROOT / "docs/audits/watchtower_lifecycle_cohort_authorization_20261011.v1.json"
COHORT_ROOT = Path("/private/tmp/dev014-watchtower-lifecycle-cohort")
QUEUE = Path("/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/queue")
SUPERVISOR = Path("/Users/kevinkeaveney/Dev/claude/flex/config/supervisor/supervisord.conf")


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
        result = FiniteLifecycleDispatcher(authorization, root / "journals", health=gate.check, probe_factory=factory, storage_guard=guard).run()
    print(json.dumps({"status": result["status"], "completed": len(result.get("completed", [])), "sessions": len(result.get("sessions", []))}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
