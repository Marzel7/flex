from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import src.core.creator_funding_worker as worker
from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor
from src.utils.db_locking import db_connect


def _make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE creator_funding_queue (
            creator_address TEXT NOT NULL,
            mint TEXT NOT NULL,
            migration_timestamp TEXT,
            create_tx_signature TEXT,
            status TEXT DEFAULT 'pending',
            job_priority INTEGER DEFAULT 0,
            priority_reason TEXT,
            next_attempt_at INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            funding_extracted_at INTEGER,
            created_at INTEGER,
            updated_at INTEGER,
            PRIMARY KEY (creator_address, mint)
        );
        CREATE TABLE creator_funders (
            creator_address TEXT,
            funder_address TEXT
        );
        CREATE TABLE probe (value INTEGER);
        INSERT INTO creator_funding_queue
            (creator_address, mint, status, next_attempt_at, locked_until,
             created_at, updated_at)
        VALUES ('creator', 'mint', 'pending', 0, 0, 1, 1);
        """
    )
    conn.commit()
    conn.close()


def test_recovery_write_is_committed_before_ready_scan(tmp_path, monkeypatch):
    db_path = str(tmp_path / "queue.db")
    _make_db(db_path)
    monkeypatch.setattr(worker, "DB_PATH", db_path)
    original = worker._select_ready_rows
    observed = {}

    def inspected_select(now, batch):
        observed["owner_exists"] = (tmp_path / "queue.db.write.lock.owner").exists()
        return original(now, batch)

    monkeypatch.setattr(worker, "_select_ready_rows", inspected_select)
    rows, recovered, stale = worker._recover_stale_and_claim(int(time.time()), 1)

    assert recovered == 0
    assert stale == 0
    assert rows[0]["mint"] == "mint"
    assert observed["owner_exists"] is False


def test_concurrent_writer_proceeds_while_ready_scan_is_held(tmp_path, monkeypatch):
    db_path = str(tmp_path / "queue.db")
    _make_db(db_path)
    monkeypatch.setattr(worker, "DB_PATH", db_path)
    scan_started = threading.Event()
    release_scan = threading.Event()
    original = worker._select_ready_rows

    def held_read_only_scan(now, batch):
        read_conn = worker._db_connect(readonly=True, timeout=3)
        try:
            read_conn.execute("SELECT COUNT(*) FROM creator_funding_queue").fetchone()
            scan_started.set()
            assert release_scan.wait(3)
        finally:
            read_conn.close()
        return original(now, batch)

    monkeypatch.setattr(worker, "_select_ready_rows", held_read_only_scan)
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "value", worker._recover_stale_and_claim(int(time.time()), 1)
        )
    )
    thread.start()
    assert scan_started.wait(3)

    started = time.monotonic()
    writer = db_connect(db_path, timeout=2)
    try:
        writer.execute("INSERT INTO probe(value) VALUES (1)")
        writer.commit()
    finally:
        writer.close()
    elapsed = time.monotonic() - started
    release_scan.set()
    thread.join(5)

    assert not thread.is_alive()
    assert elapsed < 1.0
    assert result["value"][0][0]["mint"] == "mint"


def test_read_failure_does_not_leave_write_lease(tmp_path, monkeypatch):
    db_path = str(tmp_path / "queue.db")
    _make_db(db_path)
    monkeypatch.setattr(worker, "DB_PATH", db_path)

    def fail_read(now, batch):
        raise RuntimeError("read failed")

    monkeypatch.setattr(worker, "_select_ready_rows", fail_read)
    with pytest.raises(RuntimeError, match="read failed"):
        worker._recover_stale_and_claim(int(time.time()), 1)

    writer = db_connect(db_path, timeout=2)
    try:
        writer.execute("INSERT INTO probe(value) VALUES (2)")
        writer.commit()
    finally:
        writer.close()

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT value FROM probe").fetchone()[0] == 2
    conn.close()


@pytest.mark.asyncio
async def test_page_flush_completes_classification_reads_before_first_write():
    operations = []

    class Cursor:
        def execute(self, sql, params=()):
            operations.append(("execute", " ".join(sql.split()).upper()))
            return self

        def executemany(self, sql, params):
            operations.append(("executemany", " ".join(sql.split()).upper()))
            return self

        def fetchone(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            operations.append(("commit", ""))

        def rollback(self):
            operations.append(("rollback", ""))

    extractor = object.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None
    await extractor._flush_page_batch(
        Connection(),
        "creator",
        {
            "funder-a": {"amount": 1.0},
            "funder-b": {"amount": 2.0},
        },
        {},
        set(),
        [],
        [],
    )

    first_write = next(
        i for i, (_, sql) in enumerate(operations)
        if sql.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP"))
    )
    assert all(
        sql.startswith("SELECT")
        for _, sql in operations[:first_write]
        if sql
    )
    assert sum(sql.startswith("SELECT") for _, sql in operations) == 2
    assert sum(kind == "executemany" for kind, _ in operations) == 1
