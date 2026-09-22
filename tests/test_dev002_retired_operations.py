import sqlite3

from src.ops.operator_reader import OperatorReader


RETIRED = (
    "777211c3-211e-551b-9310-ff9301570627",
    "f560f4fa-770b-57aa-83be-954d11d1a3c1",
    "ccb7b1b0-56e1-4543-9e95-3f284bed3943",
)


def test_retired_operation_rows_are_not_active_reader_results(tmp_path):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE operators (operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT, updated_at INTEGER);
        CREATE TABLE operation_registry_dispositions (operator_id TEXT, disposition TEXT, source_candidate_id TEXT, updated_at INTEGER);
        CREATE TABLE operation_qualification_contracts (operator_id TEXT, qualification_category TEXT, automation_eligibility TEXT, detector_version TEXT, parent_mechanism TEXT, benchmark_json TEXT);
        CREATE TABLE operation_activity_snapshots (snapshot_id TEXT PRIMARY KEY, operator_id TEXT, observed_at INTEGER, timestamp_semantics TEXT, metrics_json TEXT, activity_state TEXT);
        CREATE TABLE operator_launch_membership (operator_id TEXT, mint TEXT);
        CREATE TABLE operation_behavioural_profiles (operator_id TEXT, profile_version INTEGER, member_mints_json TEXT, provenance_json TEXT);
        """
    )
    for index, operation_id in enumerate(RETIRED, start=1):
        conn.execute("INSERT INTO operators VALUES (?,?,?,?)", (operation_id, f"retired-{index}", "CONFIRMED", index))
        conn.execute("INSERT INTO operation_registry_dispositions VALUES (?,?,?,?)", (operation_id, "ACTIVE_MANUAL", None, index))
    conn.execute("INSERT INTO operators VALUES (?,?,?,?)", ("active", "WATCHTOWER", "CONFIRMED", 9))
    conn.execute("INSERT INTO operation_registry_dispositions VALUES (?,?,?,?)", ("active", "ACTIVE_MANUAL", None, 9))
    conn.commit()
    conn.close()

    reader = OperatorReader(str(path))
    assert [row["operator_id"] for row in reader.fetch_all_operators()] == ["active"]
    assert [row["operator_id"] for row in reader.fetch_active_manual_operators()] == ["active"]
    assert reader.fetch_summary()["total"] == 1
