import sqlite3
from pathlib import Path

from src.core.creator_resolution_queue import (
    P0_PRIORITY,
    ensure_schema,
    promote_recent_missing_creators,
)
from src.ops.mission_control_capabilities import _compute_operational_intelligence


def _queue_db(path: Path, *, now: int) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            lifecycle_stage TEXT,
            migrated_at INTEGER,
            earliest_tx_creator TEXT,
            pf_ws_creator TEXT
        )"""
    )
    ensure_schema(conn)
    rows = [
        ("recent", "migrated", now - 60, None, None),
        ("old", "migrated", now - 7200, None, None),
        ("resolved", "migrated", now - 60, "creator", None),
    ]
    conn.executemany("INSERT INTO token_analysis VALUES (?,?,?,?,?)", rows)
    for mint in ("recent", "old", "resolved"):
        conn.execute(
            """INSERT INTO creator_resolution_queue
               (mint,status,priority,next_attempt_at,locked_until,attempts,created_at,updated_at)
               VALUES (?, 'pending', 100, ?, 0, 0, ?, ?)""",
            (mint, now, now, now),
        )
    conn.commit()
    conn.close()


def test_recent_missing_creator_promotion_is_bounded_and_idempotent(tmp_path: Path) -> None:
    now = 2_000_000_000
    db = tmp_path / "queue.db"
    _queue_db(db, now=now)

    assert P0_PRIORITY == 200
    assert promote_recent_missing_creators(str(db), now=now) == 1
    assert promote_recent_missing_creators(str(db), now=now) == 0

    conn = sqlite3.connect(db)
    priorities = dict(conn.execute("SELECT mint,priority FROM creator_resolution_queue"))
    conn.close()
    assert priorities == {"recent": 200, "old": 100, "resolved": 100}


def test_retired_legacy_watch_pipeline_does_not_degrade_fresh_snapshots() -> None:
    cap = _compute_operational_intelligence({
        "intelligence": {
            "watch_pipeline_lifecycle": "RETIRED",
            "watch_pipeline_age_secs": None,
            "watch_pipeline_interval_secs": 900,
            "operational_snapshot_health": "FRESH",
            "operational_snapshot_age_secs": 30,
            "crq_worker_age_secs": 180,
            "crq_heartbeat_threshold_secs": 320,
            "creator_queue_failed": 0,
            "missing_creators_1h": 3,
        }
    })
    assert cap["status"] == "HEALTHY"
    signals = {item["name"]: item for item in cap["signals"]}
    assert signals["watch_pipeline_freshness"]["abnormal"] is False
    assert "RETIRED" in signals["watch_pipeline_freshness"]["detail"]
    assert signals["operational_snapshot_freshness"]["abnormal"] is False
    assert signals["creator_resolution_freshness"]["abnormal"] is False


def test_failed_current_snapshot_degrades_even_when_legacy_watch_is_retired() -> None:
    cap = _compute_operational_intelligence({
        "intelligence": {
            "watch_pipeline_lifecycle": "RETIRED",
            "operational_snapshot_health": "STALE_FAILED",
            "operational_snapshot_age_secs": 9999,
            "crq_worker_age_secs": 10,
            "crq_heartbeat_threshold_secs": 120,
            "creator_queue_failed": 0,
            "missing_creators_1h": 0,
        }
    })
    assert cap["status"] == "WARNING"
    signals = {item["name"]: item for item in cap["signals"]}
    assert signals["operational_snapshot_freshness"]["abnormal"] is True
