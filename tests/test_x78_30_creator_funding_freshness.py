from __future__ import annotations

import sqlite3

import src.core.creator_funding_worker as cfw


def _db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE creator_funding_queue (
          creator_address TEXT, mint TEXT, migration_timestamp TEXT,
          create_tx_signature TEXT, status TEXT, source TEXT,
          job_priority INTEGER DEFAULT 0, priority_reason TEXT,
          next_attempt_at INTEGER DEFAULT 0, locked_until INTEGER DEFAULT 0,
          attempts INTEGER DEFAULT 0, last_error TEXT,
          funding_extracted_at INTEGER, created_at INTEGER, updated_at INTEGER,
          PRIMARY KEY (creator_address, mint));
        CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT);
    """)
    conn.close()


def _row(path: str, creator: str, now: int, age: int, *, status="pending", priority=0):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO creator_funding_queue "
        "(creator_address,mint,status,job_priority,priority_reason,next_attempt_at,locked_until,attempts,created_at,updated_at) "
        "VALUES (?,?,?,?,'test',0,0,0,?,?)",
        (creator, f"mint-{creator}", status, priority, now - age, now),
    )
    conn.commit()
    conn.close()


def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.db")
    _db(path)
    monkeypatch.setattr(cfw, "DB_PATH", path)
    monkeypatch.setattr(cfw, "HOT_MAX_AGE_SECONDS", 6 * 3600)
    return path


def test_fresh_beats_stale_and_stale_is_never_claimed(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    now = 2_000_000_000
    _row(path, "stale-high", now, 7 * 3600, priority=99)
    _row(path, "fresh-low", now, 60, priority=0)
    rows = cfw._select_ready_rows(now, 10)
    assert [r["creator_address"] for r in rows] == ["fresh-low"]


def test_stale_expiry_is_bounded_retained_and_idempotent(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    now = 2_000_000_000
    for i in range(3):
        _row(path, f"stale-{i}", now, 7 * 3600 + i)
    assert cfw._expire_stale_rows(now, limit=2) == 2
    assert cfw._expire_stale_rows(now, limit=2) == 1
    assert cfw._expire_stale_rows(now, limit=2) == 0
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT status,last_error FROM creator_funding_queue").fetchall()
    conn.close()
    assert rows == [("expired", cfw.STALE_EXPIRY_REASON)] * 3


def test_fresh_retry_eligible_stale_retry_expires(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    now = 2_000_000_000
    _row(path, "fresh-retry", now, 300, status="retry")
    _row(path, "stale-retry", now, 8 * 3600, status="retry")
    assert [r["creator_address"] for r in cfw._select_ready_rows(now, 10)] == ["fresh-retry"]
    assert cfw._expire_stale_rows(now) == 1


def test_priority_breaks_equal_recency_ties_inside_hot(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    now = 2_000_000_000
    _row(path, "low", now, 30, priority=0)
    _row(path, "high", now, 30, priority=1)
    assert [r["creator_address"] for r in cfw._select_ready_rows(now, 2)] == ["high", "low"]


def test_satisfied_stale_reconciles_instead_of_expiring(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    now = 2_000_000_000
    _row(path, "satisfied", now, 8 * 3600)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO creator_funders VALUES ('satisfied','funder')")
    conn.commit()
    conn.close()
    assert cfw._expire_stale_rows(now) == 0
    recovered, _ = cfw._recover_stale_rows(now)
    assert recovered == 1
    conn = sqlite3.connect(path)
    status = conn.execute("SELECT status FROM creator_funding_queue").fetchone()[0]
    conn.close()
    assert status == "complete"
