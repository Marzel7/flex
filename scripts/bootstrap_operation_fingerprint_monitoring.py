"""Bounded, membership-neutral bootstrap for existing d3de and Byzantine members."""
from __future__ import annotations
import argparse, json, os, signal, sqlite3, time, traceback
from pathlib import Path; import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.durable_execution_evidence import PhaseEvidenceStore
from src.ops.operation_fingerprint_drift import observe_completed_walkback

DEFAULT_NAMES = ("Byzantine", "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER")

def _write(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n")
    os.replace(temporary, path)

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--db", default="database/wt_ops_v2.db"); parser.add_argument("--output", required=True); args = parser.parse_args()
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    run_id = f"fingerprint-bootstrap-{int(time.time())}"
    phases = PhaseEvidenceStore(output.parent / "runs", run_id)
    terminal = {"run_id": run_id, "scope": "bounded existing d3de/Byzantine members", "status": "RUNNING", "pid": os.getpid(), "bootstrap_started_at": int(time.time()), "heartbeat_at": int(time.time()), "current_phase": "STARTING"}
    _write(output, terminal)
    phases.emit("STARTED", pid=os.getpid(), output=str(output))
    def interrupted(signum, _frame):
        terminal.update({"status": "BOOTSTRAP_FAILED", "bootstrap_ended_at": int(time.time()), "exit_status": 128 + signum, "error": f"signal {signum}"})
        _write(output, terminal)
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted)
    print(f"[fingerprint-bootstrap] start run_id={run_id} pid={os.getpid()}", flush=True)
    conn = None
    try:
      # The live worker may briefly hold the same SQLite writer lock. This
      # bounded observer must wait for that normal contention rather than turn
      # it into a partial monitoring run.
      conn = sqlite3.connect(args.db, timeout=30); conn.row_factory = sqlite3.Row
      conn.execute("PRAGMA busy_timeout=30000")
      # The interrupted pre-activation bootstrap used a superseded generic
      # one-hop WSOL comparison. Remove only those invalid monitoring rows;
      # membership, detector evidence and multi-step drift remain untouched.
      legacy_ids = ("100SOL-WSOL-CLOSE-v1", "1SOL-WSOL-PROVISION-CLOSE-15K-v1")
      conn.execute("DELETE FROM operation_fingerprint_drift_evidence WHERE fingerprint_id IN (?,?) AND classification LIKE 'NEAR_MATCH%'", legacy_ids)
      conn.execute("DELETE FROM operation_fingerprint_drift_clusters WHERE fingerprint_id IN (?,?)", legacy_ids)
      started_at = terminal["bootstrap_started_at"]
      before = conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0]
      before_counts = {
          "drift_evidence_rows": conn.execute("SELECT COUNT(*) FROM operation_fingerprint_drift_evidence").fetchone()[0],
          "health_snapshot_rows": conn.execute("SELECT COUNT(*) FROM operation_fingerprint_health_snapshots").fetchone()[0],
          "drift_cluster_rows": conn.execute("SELECT COUNT(*) FROM operation_fingerprint_drift_clusters").fetchone()[0],
      }
      rows = conn.execute("SELECT m.mint FROM operator_launch_membership m JOIN operators o ON o.operator_id=m.operator_id WHERE o.display_name IN (?,?) ORDER BY m.mint", DEFAULT_NAMES).fetchall()
      terminal.update({"current_phase": "OBSERVING", "membership_before": before, "monitoring_before": before_counts, "heartbeat_at": int(time.time()), "mints_planned": len(rows)})
      _write(output, terminal)
      phases.emit("BASELINE", membership_before=before, monitoring_before=before_counts, mints_planned=len(rows))
      outcomes = {}
      for index, row in enumerate(rows, start=1):
        outcomes[row["mint"]] = observe_completed_walkback(conn, row["mint"], now=int(time.time()))
        if index % 10 == 0 or index == len(rows):
          terminal.update({"current_phase": "OBSERVING", "observed": index, "heartbeat_at": int(time.time())})
          _write(output, terminal)
          phases.emit("PROGRESS", observed=index, planned=len(rows))
          print(f"[fingerprint-bootstrap] observed={index}/{len(rows)}", flush=True)
      ended_at = int(time.time())
      after = conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0]
      rows_during = [dict(row) for row in conn.execute("SELECT mint,operator_id,source_population_id,assigned_at FROM operator_launch_membership WHERE assigned_at BETWEEN ? AND ? ORDER BY assigned_at,mint", (started_at, ended_at))]
      for row in rows_during:
          source = row.get("source_population_id") or ""
          row["causal_classification"] = ("BOOTSTRAP_CAUSED" if "fingerprint" in source else "LEGITIMATE_EXISTING_PRODUCTION_CLASSIFIER" if source else "UNKNOWN_CAUSE")
      unknown = [row for row in rows_during if row["causal_classification"] == "UNKNOWN_CAUSE"]
      bootstrap_caused = [row for row in rows_during if row["causal_classification"] == "BOOTSTRAP_CAUSED"]
      conn.commit()
      result = {**terminal, "status": "BOOTSTRAP_BLOCKED_MEMBERSHIP_CAUSALITY" if unknown or bootstrap_caused else "BOOTSTRAP_COMPLETE", "current_phase": "TERMINAL", "heartbeat_at": ended_at, "bootstrap_ended_at": ended_at, "duration_seconds": ended_at-started_at, "exit_status": 1 if unknown or bootstrap_caused else 0, "mints_observed": len(rows), "membership_before": before, "membership_after": after, "membership_delta": after-before, "membership_rows_during_window": rows_during, "bootstrap_caused_membership_changes": len(bootstrap_caused), "unknown_membership_changes": len(unknown), "drift_evidence_rows_before": before_counts["drift_evidence_rows"], "health_snapshot_rows_before": before_counts["health_snapshot_rows"], "drift_cluster_rows_before": before_counts["drift_cluster_rows"], "drift_evidence_rows": conn.execute("SELECT COUNT(*) FROM operation_fingerprint_drift_evidence").fetchone()[0], "health_snapshot_rows": conn.execute("SELECT COUNT(*) FROM operation_fingerprint_health_snapshots").fetchone()[0], "drift_cluster_rows": conn.execute("SELECT COUNT(*) FROM operation_fingerprint_drift_clusters").fetchone()[0], "outcomes": outcomes}
      _write(output, result)
      phases.emit("COMPLETE", status=result["status"], membership_before=before, membership_after=after, bootstrap_caused_membership_changes=len(bootstrap_caused), unknown_membership_changes=len(unknown))
      print(f"[fingerprint-bootstrap] status={result['status']}", flush=True)
      if unknown: raise SystemExit(1)
    except BaseException as exc:
      if terminal.get("status") == "RUNNING":
          terminal.update({"status": "BOOTSTRAP_FAILED", "bootstrap_ended_at": int(time.time()), "exit_status": 1, "error": repr(exc), "traceback": traceback.format_exc()})
          _write(output, terminal)
      raise
    finally:
      if conn is not None: conn.close()

if __name__ == "__main__": main()
