"""Provider-free compatibility coverage for legacy Monitor fact schemas."""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker, _h


def _create_facts(path, *, with_offset: bool) -> None:
    offset = ",entry_offset_seconds INTEGER" if with_offset else ""
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE operation_monitor_facts ("
            "operation_id TEXT NOT NULL,mint TEXT NOT NULL,cohort_class TEXT NOT NULL,"
            "assignment_timestamp INTEGER,assignment_provenance TEXT,entry_method TEXT NOT NULL,"
            "entry_timestamp INTEGER,entry_mc_usd REAL,entry_native_mc_sol TEXT,entry_status TEXT,"
            f"entry_exactness TEXT{offset},monitor_state TEXT NOT NULL,monitor_started_at INTEGER,"
            "next_observation_at INTEGER,running_peak_mc_usd REAL,running_peak_timestamp INTEGER,"
            "running_peak_multiple REAL,evidence_status TEXT,provenance_digest TEXT NOT NULL,"
            "created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,"
            "PRIMARY KEY(operation_id,mint))"
        )


def _worker(path, tmp_path):
    def persist(item):
        with sqlite3.connect(path) as con:
            for sql, values in item.statements:
                con.execute(sql, values)
        return True

    return MonitorWorker(
        MonitorQueue(tmp_path / "queue", enabled=True),
        transport=lambda _payload: (_ for _ in ()).throw(AssertionError("provider call")),
        persist=persist,
        db_path=str(path),
    )


def _opening(mint="mint", offset=2):
    return {
        "operation_id": "watchtower", "mint": mint, "cohort": "PROSPECTIVE_MONITOR_COHORT",
        "assignment": {"event_id": "assignment", "assigned_at": 100},
        "entry_method": "BOUNDED_POST_MIGRATION_MC_FALLBACK", "entry_timestamp": 102,
        "entry_mc_usd": 125378.29249620523, "entry_exactness": "POST_MIGRATION_OFFSET_2S_OBSERVED_MC",
        "entry_offset_seconds": offset, "entry_provenance": "opening-evidence",
    }


def test_legacy_schema_persists_qualified_entry_without_discarding_offset_provenance(tmp_path):
    db = tmp_path / "legacy.db"; _create_facts(db, with_offset=False)
    worker = _worker(db, tmp_path)
    opening = _opening()

    worker._activate_from_qualified_opening(opening)
    worker._activate_from_qualified_opening(opening)  # retry is idempotent

    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT entry_timestamp,entry_mc_usd,entry_status,monitor_state,provenance_digest FROM operation_monitor_facts").fetchall()
    assert len(rows) == 1
    assert rows[0][:4] == (102, 125378.29249620523, "QUALIFIED", "MONITORING_ACTIVE")
    assert rows[0][4] == _h({"activation": "QUALIFIED_ENTRY_REFERENCE", "entry_provenance": "opening-evidence", "mint": "mint", "native": None, "entry_offset_seconds": 2})


def test_current_schema_materializes_entry_offset_seconds(tmp_path):
    db = tmp_path / "current.db"; _create_facts(db, with_offset=True)
    worker = _worker(db, tmp_path)

    worker._activate_from_qualified_opening(_opening(offset=3))

    with sqlite3.connect(db) as con:
        assert con.execute("SELECT entry_offset_seconds FROM operation_monitor_facts").fetchone() == (3,)


def test_missing_monitor_fact_table_fails_closed(tmp_path):
    db = tmp_path / "missing.db"
    sqlite3.connect(db).close()
    worker = _worker(db, tmp_path)

    with pytest.raises(RuntimeError, match="MONITOR_FACT_SCHEMA_UNAVAILABLE"):
        worker._activate_from_qualified_opening(_opening())
