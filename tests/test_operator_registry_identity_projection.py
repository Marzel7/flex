import json
import sqlite3

from src.ops.operator_reader import OperatorReader


def test_active_registry_projects_identity_family_and_persisted_24h_activity(tmp_path):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE operators (
            operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT,
            updated_at INTEGER
        );
        CREATE TABLE operation_registry_dispositions (
            operator_id TEXT, disposition TEXT, updated_at INTEGER
        );
        CREATE TABLE operation_qualification_contracts (
            operator_id TEXT, qualification_category TEXT,
            automation_eligibility TEXT, detector_version TEXT,
            parent_mechanism TEXT, benchmark_json TEXT
        );
        CREATE TABLE operation_activity_snapshots (
            snapshot_id TEXT PRIMARY KEY, operator_id TEXT, observed_at INTEGER,
            timestamp_semantics TEXT, metrics_json TEXT, activity_state TEXT
        );
        CREATE TABLE operator_launch_membership (operator_id TEXT, mint TEXT);
        """
    )
    conn.execute(
        "INSERT INTO operators VALUES (?, ?, ?, ?)",
        ("ladder", "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "CONFIRMED", 1),
    )
    conn.execute(
        "INSERT INTO operation_registry_dispositions VALUES (?, ?, ?)",
        ("ladder", "ACTIVE_MANUAL", 1),
    )
    conn.execute(
        "INSERT INTO operation_qualification_contracts VALUES (?, ?, ?, ?, ?, ?)",
        ("ladder", "CONFIRMED", "NO", "d0", "WSOL", "{}"),
    )
    conn.execute(
        "INSERT INTO operation_activity_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        ("snapshot", "ladder", 2, "test", json.dumps({
            "total_observed_launches": 9,
            "launches_last_1d": 2,
            "last_observed_launch_timestamp": 2,
        }), "ACTIVE"),
    )
    conn.commit()
    conn.close()

    row = OperatorReader(str(path)).fetch_active_manual_operators()[0]

    assert row["human_display_name"] == "30 SOL 14.479K Ladder"
    assert row["operation_family"] == "30 SOL WSOL Ladder"
    assert row["launches_last_1d"] == 2
    assert row["total_launches"] == 9
