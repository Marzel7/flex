#!/usr/bin/env python3
"""External-scheduler entrypoint; checked-in schedule remains disabled."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.dev023_hot_rpc_cache_maintenance import config_from_environment
from src.ops.dev023_hot_rpc_cache_schedule import ScheduledRetentionConfig, run_scheduled_retention_tick


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one disabled-by-default DEV-023 scheduled retention tick")
    parser.add_argument("--canonical-db-path", required=True)
    parser.add_argument("--config", default=str(ROOT / "config/maintenance/dev023_hot_rpc_cache_retention.json"))
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.config).read_text())
    if payload.get("schedule_enabled") is not True:
        print(json.dumps({"status": "DISABLED", "deleted": 0, "alert": False}, sort_keys=True))
        return 0
    tick = config_from_environment(canonical_database_path=args.canonical_db_path, cutoff=time.time())
    result = run_scheduled_retention_tick(ScheduledRetentionConfig(
        enabled=True, tick=tick,
        lease_path=str(ROOT / payload["single_flight_lease"]),
        state_path=str(ROOT / payload["bounded_metrics_state"]),
        failure_alert_threshold=int(payload["failure_alert_threshold"]),
    ))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "DISABLED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
