"""
Standalone alert evaluator entry point — managed by supervisord.

Responsibilities:
  • Poll Detection Health DB state every ALERT_EVAL_INTERVAL_S seconds
  • Evaluate all four alert conditions (NO_TELEMETRY / PW_DEGRADED /
    UNMONITORED_TREASURY / RETRY_BACKLOG)
  • Persist RAISED → ACTIVE → RECOVERED transitions into wt_alerts.db
  • Write stdout logs readable via supervisorctl tail alert_evaluator

Does NOT:
  • Touch ws_cascade, ProgramWatcher, or any detection-pipeline code
  • Duplicate heartbeat / treasury / latency logic (all lives in alert_evaluator.py)
  • Open the Flask app or any HTTP connection

Usage (supervisor starts this automatically):
  python3 scripts/run_alert_evaluator.py

Environment variables:
  OPS_V2_DB_PATH          path to wt_ops_v2.db  (default: database/wt_ops_v2.db)
  ALERTS_DB_PATH          path to wt_alerts.db  (default: database/wt_alerts.db)
  ALERT_EVAL_INTERVAL_S   poll interval seconds  (default: 30)
"""

import os
import sys

# Ensure repo root is on the path when invoked directly
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.core.alert_evaluator import run_evaluator

if __name__ == "__main__":
    interval = int(os.environ.get("ALERT_EVAL_INTERVAL_S", "30"))
    run_evaluator(interval_s=interval)
