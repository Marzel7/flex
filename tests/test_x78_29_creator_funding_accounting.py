import sqlite3
from pathlib import Path

from src.core import creator_funding_worker as worker


def _queue_db(path: Path, *, now: int) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE creator_funding_queue (
            creator_address TEXT NOT NULL,
            mint TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            next_attempt_at INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            funding_extracted_at INTEGER,
            PRIMARY KEY (creator_address, mint)
        );
        CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT);
        """
    )
    for index in range(30):
        creator = f"creator-{index:02d}"
        conn.execute(
            "INSERT INTO creator_funding_queue VALUES (?,?, 'pending', ?,0,0,NULL,?,?,NULL)",
            (creator, f"mint-{index:02d}", now - 1000 + index, now - 1000 + index, now - 1000 + index),
        )
        conn.execute("INSERT INTO creator_funders VALUES (?,?)", (creator, "funder"))
    conn.execute(
        "INSERT INTO creator_funding_queue VALUES ('unsatisfied','mint-u','pending',?,0,0,NULL,?,?,NULL)",
        (now - 2000, now - 2000, now - 2000),
    )
    conn.commit()
    conn.close()


def test_satisfied_pending_reconciliation_is_bounded_and_idempotent(
    tmp_path: Path, monkeypatch,
) -> None:
    now = 2_000_000_000
    db = tmp_path / "funding.db"
    _queue_db(db, now=now)
    monkeypatch.setattr(worker, "DB_PATH", str(db))
    monkeypatch.setattr(worker, "SATISFIED_RECONCILE_LIMIT", 25)

    recovered, stale = worker._recover_stale_rows(now)
    assert (recovered, stale) == (25, 0)

    conn = sqlite3.connect(db)
    counts = dict(conn.execute("SELECT status,COUNT(*) FROM creator_funding_queue GROUP BY status"))
    assert counts == {"complete": 25, "pending": 6}
    assert conn.execute(
        "SELECT status FROM creator_funding_queue WHERE creator_address='unsatisfied'"
    ).fetchone()[0] == "pending"
    conn.close()

    recovered, stale = worker._recover_stale_rows(now + 1)
    assert (recovered, stale) == (5, 0)
    recovered, stale = worker._recover_stale_rows(now + 2)
    assert (recovered, stale) == (0, 0)


def test_completion_accounting_uses_explicit_job_outcome() -> None:
    assert worker._outcome_deltas("complete") == (1, 0, 0)
    assert worker._outcome_deltas("complete_fast") == (1, 0, 0)
    assert worker._outcome_deltas("retry") == (0, 1, 0)
    assert worker._outcome_deltas("failed") == (0, 0, 1)
    assert worker._outcome_deltas("unknown") == (0, 0, 0)
