import sqlite3

from src.ops.walkback_health import build_walkback_health


def test_combined_health_counts_preserve_boundaries_errors_and_nulls():
    now = 10_000
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wt_walkback_queue (status TEXT, completed_at INTEGER, updated_at INTEGER, last_error TEXT, started_at INTEGER, enqueued_at INTEGER)")
    conn.execute("CREATE TABLE wt_worker_heartbeat (worker_name TEXT, last_seen INTEGER)")
    conn.executemany("INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?)", [
        ("complete", now - 60, now - 10, None, now - 80, now - 90),
        ("complete", now - 3600, now - 10, None, now - 3700, now - 3800),
        ("complete", None, now - 10, "NestedDatabaseWriteError", None, now - 10),
        ("pending", None, now - 10, "database is locked", None, now - 10),
        ("running", None, None, None, now - 10, now - 20),
    ])
    conn.execute("INSERT INTO wt_worker_heartbeat VALUES ('walkback_worker', ?)", (now,))
    health = build_walkback_health(conn, now=now)
    assert health["completed_per_minute"] == 1
    assert health["completed_last_hour"] == 2
    assert health["write_failures_last_hour"] == 2
    assert health["nested_write_failures_last_hour"] == 1
