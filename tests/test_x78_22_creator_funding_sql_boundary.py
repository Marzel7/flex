import sqlite3
import time
import json

from src.core.creator_funding_worker import (
    _ensure_creator_funding_rescore_trigger,
    _run_loop_async,
)
from src.utils import db_locking


def _fixture(path, rows=20_000):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE creator_funders (
            creator_address TEXT NOT NULL,
            funder_address TEXT NOT NULL,
            amount_sol REAL NOT NULL,
            PRIMARY KEY (creator_address, funder_address)
        );
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            earliest_tx_creator TEXT,
            pf_ws_creator TEXT,
            lifecycle_stage TEXT,
            migrated_at INTEGER
        );
        CREATE TABLE token_rescore_queue (
            mint TEXT PRIMARY KEY,
            reason TEXT,
            created_at INTEGER
        );
        CREATE INDEX idx_token_analysis_earliest_creator ON token_analysis(earliest_tx_creator);
        CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator);
        CREATE TRIGGER trg_token_prediction_funding_inserted AFTER INSERT ON creator_funders BEGIN
          INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
          SELECT mint, 'funding_extracted', strftime('%s','now') FROM token_analysis
          WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = NEW.creator_address
            AND COALESCE(lifecycle_stage, '') = 'migrated' AND migrated_at IS NOT NULL;
        END;
    """)
    data = [(f"mint-{i}", f"creator-{i}", None, "migrated", i + 1) for i in range(rows)]
    data.extend([
        ("match-direct", "target", "ignored", "migrated", 1),
        ("match-fallback", None, "target", "migrated", 1),
        ("no-fallback", "other", "target", "migrated", 1),
        ("not-migrated", "target", None, "birth", None),
    ])
    conn.executemany("INSERT INTO token_analysis VALUES (?,?,?,?,?)", data)
    conn.commit()
    conn.close()


def test_trigger_rewrite_preserves_coalesce_semantics_and_uses_creator_indexes(tmp_path):
    path = tmp_path / "funding.db"
    _fixture(path)
    assert _ensure_creator_funding_rescore_trigger(str(path)) == "optimized"
    assert _ensure_creator_funding_rescore_trigger(str(path)) == "unchanged"

    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO creator_funders VALUES ('target','funder',1.0)")
    conn.commit()
    assert conn.execute(
        "SELECT mint FROM token_rescore_queue ORDER BY mint"
    ).fetchall() == [("match-direct",), ("match-fallback",)]

    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        ("trg_token_prediction_funding_inserted",),
    ).fetchone()[0]
    assert "COALESCE(earliest_tx_creator, pf_ws_creator)" not in trigger_sql
    assert "INDEXED BY idx_token_analysis_earliest_creator" in trigger_sql
    assert "INDEXED BY idx_ta_pf_ws_creator" in trigger_sql
    conn.close()


def test_optimized_trigger_does_not_hold_unrelated_writer(tmp_path):
    path = tmp_path / "funding.db"
    _fixture(path, rows=50_000)
    _ensure_creator_funding_rescore_trigger(str(path))

    conn = sqlite3.connect(path)
    started = time.monotonic()
    conn.execute("INSERT INTO creator_funders VALUES ('target','funder',1.0)")
    conn.commit()
    elapsed = time.monotonic() - started
    conn.close()
    assert elapsed < 1.0


def test_statement_diagnostics_correlate_transaction_without_parameter_values(tmp_path, monkeypatch):
    path = tmp_path / "diagnostic.db"
    log = tmp_path / "sql.jsonl"
    monkeypatch.setattr(db_locking, "_CF_SQL_DIAGNOSTICS_PATH", str(log))
    monkeypatch.setattr(db_locking, "_cf_sql_diagnostics_enabled", lambda _conn: True)

    with db_locking.db_connect(str(path), timeout=5) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE sample (secret TEXT)")
        cursor.execute("INSERT INTO sample VALUES (?)", ("must-not-appear",))

    rows = [json.loads(line) for line in log.read_text().splitlines()]
    insert = [row for row in rows if row["operation"] == "INSERT"]
    assert [row["event"] for row in insert] == ["statement_start", "statement_end"]
    assert insert[0]["transaction_id"] == insert[1]["transaction_id"]
    assert insert[0]["statement_id"] == insert[1]["statement_id"]
    assert insert[1]["duration_ms"] >= 0
    assert "must-not-appear" not in log.read_text()


def test_trigger_migration_failure_is_fail_open(monkeypatch):
    import asyncio
    import src.core.creator_funding_worker as worker

    monkeypatch.setattr(worker, "_ensure_creator_funding_rescore_trigger", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("busy")))
    monkeypatch.setattr(worker, "_STOP", True)
    messages = []
    monkeypatch.setattr(worker, "_log", messages.append)
    asyncio.run(_run_loop_async(once=True))
    assert any("funding rescore trigger=deferred:OperationalError" in line for line in messages)
