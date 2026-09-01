import json
import sqlite3

from src.ops.manual_registry import refresh_watchtower_activity_snapshot


def test_watchtower_projection_uses_canonical_ledger_and_replaces_same_second_snapshot():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE wt_watchtower_launches (mint TEXT, create_time INTEGER);
        CREATE TABLE operation_activity_snapshots (
            snapshot_id TEXT PRIMARY KEY, operator_id TEXT, observed_at INTEGER,
            timestamp_semantics TEXT, metrics_json TEXT, activity_state TEXT,
            UNIQUE(operator_id, observed_at)
        );
    """)
    conn.executemany("INSERT INTO wt_watchtower_launches VALUES (?,?)", [
        ("mint-a", 100), ("mint-b", 200), ("mint-b", 200), ("mint-c", None),
    ])
    first = refresh_watchtower_activity_snapshot(conn, "watchtower", now=1_000)
    second = refresh_watchtower_activity_snapshot(conn, "watchtower", now=1_000)
    row = conn.execute("SELECT metrics_json FROM operation_activity_snapshots").fetchone()
    assert first["total_observed_launches"] == 3
    assert second["timestamp_qualified_launches"] == 2
    assert json.loads(row[0])["last_observed_launch_timestamp"] == 200
    assert conn.execute("SELECT COUNT(*) FROM operation_activity_snapshots").fetchone()[0] == 1
