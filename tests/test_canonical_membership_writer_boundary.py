"""Regression coverage for canonical-promotion use of the managed ops boundary."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest
from flask import Flask

from src.ops import operator_routes
from src.ops.operation_attribution_manual_bridge import _validate_resolved_input, promote_validated_operation
from src.utils import db_locking


def _schema(conn):
    conn.execute("CREATE TABLE operators(operator_id TEXT PRIMARY KEY,status TEXT,confidence TEXT,summary TEXT,review_state TEXT,display_name TEXT,created_at INT,updated_at INT)")
    conn.execute("CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INT,event_id TEXT)")
    conn.commit()


def _resolved():
    return {
        "candidate_source_id": "fixture-positive", "candidate_source_type": "POTENTIAL_OPERATION",
        "candidate_snapshot_ref": "sha256:fixture-snapshot", "detector_contract": "FIXTURE",
        "detector_evidence_refs": ["fixture:detector"], "candidate_member_refs": ["mint-a", "mint-b"],
        "proposed_operation_id": "fixture-operation", "proposed_operation_label": "Fixture operation",
        "evidence_families": [
            {"family": "CONTROLLER_CONTINUITY", "state": "PROVEN_STRONG", "independence_from_detector": "PASS", "independence_from_other_families": "PASS", "dependency_group": "controller", "evidence_refs": ["fixture:controller"]},
            {"family": "EARLY_EXECUTION_FINGERPRINT", "state": "PROVEN_MODERATE", "independence_from_detector": "PASS", "independence_from_other_families": "PASS", "dependency_group": "execution", "evidence_refs": ["fixture:execution"]},
        ], "common_infrastructure_exclusions": [], "completeness_state": "COMPLETE",
    }


def _app(path):
    app = Flask(__name__)
    app.config["OPS_DB_PATH"] = str(path)
    return app


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_canonical_promotion_preserves_contract_commits_and_releases_lane(tmp_path, monkeypatch):
    db = tmp_path / "wt_ops_v2.db"
    output = tmp_path / "lifecycle.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    app = _app(db)
    with app.app_context():
        canonical = operator_routes._canonical_membership_connection()
        connection_id = canonical._db_connection_id
        try:
            assert canonical.row_factory is sqlite3.Row
            assert canonical.execute("PRAGMA busy_timeout").fetchone()[0] == 1000
            assert canonical.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL, sqlite default
            _schema(canonical)
            workflow = sqlite3.connect(":memory:")
            resolved = _resolved()
            proposal_id, _ = _validate_resolved_input(workflow, resolved)
            result = promote_validated_operation(workflow, canonical, proposal_id, resolver=lambda _: resolved)
            assert result["rows_expected"] == result["rows_written"] == 2
            assert canonical.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0] == 2
        finally:
            canonical.close()
    rows = _records(output)
    flock = next(row for row in rows if row["event"] == "flock_acquired" and row["connection_id"] == connection_id)
    writes = [row for row in rows if row["event"] == "statement_start" and row["connection_id"] == connection_id and row.get("operation_class") in {"BEGIN", "CREATE", "INSERT"}]
    assert writes
    assert flock["timestamp"] <= writes[0]["timestamp"]
    assert not any(row.get("connection_id") == connection_id for row in db_locking._sqlite_lifecycle_snapshot())


def test_canonical_promotion_failure_rolls_back_and_closes_cleanly(tmp_path):
    db = tmp_path / "wt_ops_v2.db"
    app = _app(db)
    with app.app_context():
        canonical = operator_routes._canonical_membership_connection()
        try:
            _schema(canonical)
            canonical.execute("INSERT INTO operator_launch_membership VALUES ('mint-a','other','seed',1,NULL)")
            canonical.commit()
            workflow = sqlite3.connect(":memory:")
            resolved = _resolved()
            proposal_id, _ = _validate_resolved_input(workflow, resolved)
            with pytest.raises(ValueError, match="canonical membership conflict"):
                promote_validated_operation(workflow, canonical, proposal_id, resolver=lambda _: resolved)
            assert canonical.execute("SELECT COUNT(*) FROM operators").fetchone()[0] == 0
            assert canonical.execute("SELECT COUNT(*) FROM canonical_manual_operation_commit_intents").fetchone()[0] == 0
            assert canonical.in_transaction is False
        finally:
            canonical.close()


def test_canonical_connection_serializes_with_a_normal_ops_writer(tmp_path):
    db = tmp_path / "wt_ops_v2.db"
    app = _app(db)
    with app.app_context():
        canonical = operator_routes._canonical_membership_connection()
        try:
            canonical.execute("CREATE TABLE serial (value TEXT)")
            canonical.commit()
            canonical.execute("BEGIN IMMEDIATE")
            canonical.execute("INSERT INTO serial VALUES ('canonical')")
            completed = []

            def writer():
                conn = db_locking.db_connect(str(db), timeout=1, _caller="normal_writer.py:test")
                try:
                    conn.execute("INSERT INTO serial VALUES ('normal')")
                    conn.commit()
                    completed.append(True)
                finally:
                    conn.close()

            thread = threading.Thread(target=writer)
            thread.start()
            time.sleep(0.03)
            assert completed == []
            canonical.commit()
            thread.join(timeout=2)
            assert completed == [True]
            assert canonical.execute("SELECT COUNT(*) FROM serial").fetchone()[0] == 2
        finally:
            canonical.close()
