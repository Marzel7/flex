"""Run-isolated provenance wrapper for the unchanged S2B source snapshot runner.

This wrapper does not alter source-snapshot semantics.  It only creates a
unique local namespace and records lifecycle provenance around a future run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


RUNNER = Path(__file__).with_name("freeze_ops_discovery_p3r_s2b_source_boundary.py")
LEGACY = {
    "terminal_audit": {
        "path": "docs/audits/ops_discovery_p3r_s2b_source_boundary_snapshot.json",
        "sha256": "d585d353af6d4e4a64c026ef7c1fbe77472abfd77e687b5e0983b02659abfa58",
    },
    "temporary_snapshot": {
        "path": "docs/audits/ops_discovery_p3r_s2b_source_snapshot.sqlite.tmp",
        "sha256": "8812ad675a1de997fa5dd7bb506e1446ff72c43a353308f2a8d98d69a49f942e",
    },
}
SOURCE_CONTRACT = {
    "surfaces": [
        {"table": "token_analysis", "columns": ["mint", "pf_ws_creator"], "order": "mint ASC"},
        {"table": "pumpfun_migration_verification", "columns": ["mint"], "order": "mint ASC"},
    ],
    "read_semantics": "sqlite_uri_mode_ro_query_only_single_transaction",
    "canonicalization": "unchanged_runner_canonical_json_array_newline_sha256",
    "wall_seconds": 300,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".write")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def generated_run_id() -> str:
    return "s2b-source-snapshot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(16)


def validate_run_id(value: str) -> str:
    if not re.fullmatch(r"s2b-source-snapshot-[A-Za-z0-9_-]{8,160}", value):
        raise ValueError("run_id must begin s2b-source-snapshot- and contain only ASCII letters, digits, _ or -")
    return value


def fixture_command(tmp: Path, audit: Path, outcome: str, run_id: str) -> list[str]:
    code = (
        "import json,os,sys; tmp,audit,outcome,run_id=sys.argv[1:]; "
        "open(tmp,'wb').write(b'fixture-only-source-snapshot\\n'); "
        "status='COMPLETE' if outcome=='pass' else 'HOLD'; "
        "json.dump({'status':status,'fixture_only':True,'run_id':run_id,'replay_identical':outcome=='pass',"
        "'failure_reason':None if outcome=='pass' else 'INTERRUPTED_FIXTURE'},open(audit,'w')); "
        "print('fixture-run-id='+run_id); sys.exit(0 if outcome=='pass' else 3)"
    )
    return [sys.executable, "-c", code, str(tmp), str(audit), outcome, run_id]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", default="docs/audits/ops_discovery_p3r_s2b_runs")
    parser.add_argument("--source-db", default="database/flex_complete_database.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-outcome", choices=("pass", "interrupted"), default="pass")
    args = parser.parse_args()
    run_id = validate_run_id(args.run_id) if args.run_id else generated_run_id()
    root = Path(args.output_root).resolve()
    namespace = root / run_id
    if namespace.exists():
        print(json.dumps({"run_id": run_id, "status": "HOLD_PROVENANCE_ERROR", "reason": "RUN_NAMESPACE_COLLISION"}), file=sys.stderr)
        return 4
    namespace.parent.mkdir(parents=True, exist_ok=True)
    try:
        namespace.mkdir()  # Atomic namespace claim; never reuse or delete a collision.
    except FileExistsError:
        print(json.dumps({"run_id": run_id, "status": "HOLD_PROVENANCE_ERROR", "reason": "RUN_NAMESPACE_COLLISION"}), file=sys.stderr)
        return 4

    lifecycle = namespace / "lifecycle.json"
    stdout_path, stderr_path = namespace / "stdout.log", namespace / "stderr.log"
    runner_candidate = namespace / "runner_candidate.sqlite"
    tmp_snapshot, capture_snapshot, final_snapshot = namespace / "runner_candidate.sqlite.tmp", namespace / "runner_candidate.sqlite.capture.sqlite", namespace / "snapshot.sqlite"
    runner_audit = namespace / "source_boundary_audit.json"
    runner_hash = sha256(RUNNER)
    wrapper_start = time.monotonic()
    wrapper_execution_deadline = wrapper_start + 270.0
    record = {
        "run_id": run_id,
        "status": "STARTED",
        "launcher": {"pid": os.getpid(), "ppid": os.getppid(), "started_at_utc": utc_now(), "cwd": str(Path.cwd())},
        "runner": {"path": str(RUNNER.resolve()), "sha256": runner_hash, "pid": None, "ppid": None, "started_at_utc": None},
        "bindings": {"source_db": str(Path(args.source_db).resolve()), "source_contract": SOURCE_CONTRACT, "dry_run": args.dry_run, "wrapper_hard_ceiling_seconds": 300, "wrapper_cleanup_reserve_seconds": 30, "wrapper_execution_deadline_seconds": 270},
        "paths": {"namespace": str(namespace), "lifecycle": str(lifecycle), "stdout": str(stdout_path), "stderr": str(stderr_path), "temporary_snapshot": str(tmp_snapshot), "capture_snapshot": str(capture_snapshot), "runner_candidate_snapshot": str(runner_candidate), "final_snapshot": str(final_snapshot), "runner_audit": str(runner_audit)},
        "legacy_quarantine_excluded": LEGACY,
        "ownership": {"wrapper": ["namespace", "lifecycle", "stdout", "stderr", "final_snapshot"], "snapshot_runner": ["temporary_snapshot", "capture_snapshot", "runner_candidate_snapshot", "runner_audit"], "promotion": "wrapper only after runner COMPLETE, replay_identical true, and exit code zero"},
    }
    atomic_json(lifecycle, record)
    command = fixture_command(tmp_snapshot, runner_audit, args.dry_run_outcome, run_id) if args.dry_run else [
        sys.executable, str(RUNNER.resolve()), "--source-db", args.source_db, "--snapshot-db", str(runner_candidate),
        "--audit-path", str(runner_audit), "--wall-seconds", "300", "--max-progress-calls", "500000",
    ]
    record["runner"]["command"] = command
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            child = subprocess.Popen(command, cwd=Path.cwd(), stdout=stdout, stderr=stderr)
            record["runner"].update({"pid": child.pid, "ppid": os.getpid(), "started_at_utc": utc_now()})
            atomic_json(lifecycle, record)
            wrapper_timed_out = False
            while child.poll() is None:
                if time.monotonic() >= wrapper_execution_deadline:
                    wrapper_timed_out = True
                    child.terminate()
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    break
                time.sleep(0.1)
            exit_code = child.returncode
    except BaseException as exc:
        record.update({"status": "HOLD_INTERRUPTED", "terminal_at_utc": utc_now(), "exception": repr(exc)})
        atomic_json(lifecycle, record)
        return 3

    runner_result = json.loads(runner_audit.read_text()) if runner_audit.exists() else None
    complete = bool(runner_result and runner_result.get("status") == "COMPLETE" and runner_result.get("replay_identical") is True and exit_code == 0)
    if complete:
        os.replace(tmp_snapshot if args.dry_run else runner_candidate, final_snapshot)
        record["status"] = "PASS"
        record["authoritative_snapshot"] = str(final_snapshot)
    else:
        failure = (runner_result or {}).get("failure_reason")
        record["status"] = "HOLD_BOUND_EXCEEDED" if wrapper_timed_out or failure == "BOUND_EXCEEDED" else ("HOLD_INTERRUPTED" if failure == "INTERRUPTED_FIXTURE" else "HOLD_RUNNER_ERROR")
        record["authoritative_snapshot"] = None
    record.update({"terminal_at_utc": utc_now(), "exit_code": exit_code, "runner_terminal_audit": runner_result, "temporary_snapshot_non_authoritative": not complete})
    atomic_json(lifecycle, record)
    print(json.dumps({"run_id": run_id, "status": record["status"], "lifecycle": str(lifecycle)}, sort_keys=True))
    return 0 if complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
