#!/usr/bin/env python3
"""Register only the qualified observed Deep historical route, never auto-detect.

Run from the qualified Walkback runtime checkout with PYTHONPATH set to that
checkout. The new historical classifier is loaded from the engineering checkout;
the shared writer lane and SQLite lock code come from the running runtime tree.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


ENGINEERING_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ENGINEERING_ROOT / ".runtime_recovery_c06d"
DB_PATH = ENGINEERING_ROOT / "database" / "wt_ops_v2.db"
CLASSIFIER_PATH = ENGINEERING_ROOT / "src" / "ops" / "watchtower_deep_historical.py"


def _classifier():
    spec = importlib.util.spec_from_file_location("watchtower_deep_historical_local", CLASSIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("historical classifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readonly():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _runtime_parity():
    from src.core import database_write_service as service_module
    from src.ops import operator_writer as writer_module
    from src.utils import db_locking as locking_module

    expected = {
        "database_write_service": RUNTIME_ROOT / "src/core/database_write_service.py",
        "operator_writer": RUNTIME_ROOT / "src/ops/operator_writer.py",
        "db_locking": RUNTIME_ROOT / "src/utils/db_locking.py",
    }
    actual = {
        "database_write_service": Path(service_module.__file__).resolve(),
        "operator_writer": Path(writer_module.__file__).resolve(),
        "db_locking": Path(locking_module.__file__).resolve(),
    }
    if actual != {name: path.resolve() for name, path in expected.items()}:
        raise RuntimeError(f"runtime module parity failed: {actual}")
    return writer_module.OperatorWriter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--check-runtime", action="store_true")
    args = parser.parse_args()
    if args.check_runtime or args.execute:
        _runtime_parity()
    classifier = _classifier()
    with _readonly() as conn:
        plan = classifier.historical_plan(conn)
        wt_before = conn.execute(
            "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
            ("04265d9f-6eb2-568c-a49e-9253091a4dbb",),
        ).fetchone()[0]
        existing = conn.execute(
            "SELECT COUNT(*) FROM operators WHERE display_name='WATCHTOWER_DEEP'"
        ).fetchone()[0]
    summary = {
        "candidate_count": plan["candidate_count"],
        "accepted_count": plan["accepted_count"],
        "accepted_digest": plan["accepted_digest"],
        "watchtower_members_before": wt_before,
        "deep_operators_before": existing,
        "automatic_detection": "OFF",
    }
    print(json.dumps({"preflight": summary}, sort_keys=True), flush=True)
    if plan["candidate_count"] != 71 or plan["accepted_count"] != 26:
        raise RuntimeError("historical cohort drift; abort")
    if not args.execute:
        return 0
    OperatorWriter = _runtime_parity()
    writer = OperatorWriter(str(DB_PATH))
    result = writer.transaction(
        "watchtower-deep-historical-registration",
        lambda conn: classifier.commit_historical_operation(
            conn, expected_mints=plan["accepted"],
            expected_digest=plan["accepted_digest"],
            expected_watchtower_count=wt_before,
        ),
    )
    with _readonly() as conn:
        deep_after = conn.execute(
            "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
            (classifier.OPERATOR_ID,),
        ).fetchone()[0]
        wt_after = conn.execute(
            "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
            ("04265d9f-6eb2-568c-a49e-9253091a4dbb",),
        ).fetchone()[0]
        contract = conn.execute(
            "SELECT automation_eligibility FROM operation_qualification_contracts "
            "WHERE operator_id=?", (classifier.OPERATOR_ID,),
        ).fetchone()
    if deep_after != 26 or wt_after != wt_before or contract != ("REVIEW_ONLY",):
        raise RuntimeError("post-write readback mismatch; investigate before any further action")
    print(json.dumps({"result": result, "deep_members_after": deep_after,
                      "watchtower_members_after": wt_after,
                      "contract": contract[0]}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
