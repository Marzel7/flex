"""Bounded retained-evidence integration replay for Watchtower.

The 96-record ledger retains only compact Opening offsets.  This fixture uses
those records as-is: it never invents lifecycle candles, starts a worker, or
opens a provider connection.  It exercises the real membership outbox, bridge,
MonitorQueue, qualified-entry activation, retained Injector importer, and the
read-only audit projection against temporary SQLite databases only.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.ops.canonical_membership_outbox import ensure_schema as ensure_canonical_outbox
from src.ops.monitor_live_admission import (
    commit_membership_and_outbox,
    ensure_schema as ensure_admission_outbox,
)
from src.ops.operation_monitor_bridge_service import run_once
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker
from src.ops.operator_lifecycle_projection import ensure_schema as ensure_monitor_schema
from src.ops.retained_injector_opening import MINT as INJECTOR_MINT, import_retained_injector_opening
from src.ops.strict_migration_window import (
    BOUNDED_POST_MIGRATION_METHOD,
    MIGRATION_SECOND_FALLBACK_METHOD,
    PLUS1_ENTRY_METHOD,
    reduce_retained_offsets,
)
from src.ops.watchtower_offset_audit import product_projection


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/audits/watchtower-opening-offset-audit.v2.json"
T_PLUS_ONE_MINT = "42iYbRaVhj4dy6WXTWFMZ45rWVUxW3UdBPndaNRdpump"
T_FALLBACK_MINT = "HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump"
T_PLUS_TWO_MINTS = (
    "4Z9eH1BCuq2Y6FFRXkZft9LoxwMqQWo7bJsEbcd7pump",
    "59F95Wkn5LRpiKmG2FE3g87Lzkz4redvNJNKSi1Npump",
    "INJECTOR",
)
UNQUALIFIED_MINT = "3677Aqo8U5HEbAWL7B8gNQoDtRdjQCY2QU6nCD13pump"


def _records() -> dict[str, dict]:
    return {row["mint"]: row for row in json.loads(LEDGER.read_text())["records"]}


def _selection(record: dict) -> dict:
    return reduce_retained_offsets(
        migration_timestamp=record["migration_timestamp"],
        offsets=record["offsets"],
        duplicate_offsets=record["duplicate_offsets"],
    )


def _source_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE operator_launch_membership("
            "mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,"
            "assigned_at INTEGER,event_id TEXT)"
        )
        ensure_canonical_outbox(connection)
        ensure_admission_outbox(connection)


def _monitor_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        ensure_monitor_schema(connection)
        # The qualified runtime schema already contains this column.  The
        # compact test schema predates it, so mirror that compatibility shape.
        connection.execute("ALTER TABLE operation_monitor_facts ADD COLUMN entry_native_mc_sol TEXT")


def _temporary_persist(path: Path):
    def persist(item):
        with sqlite3.connect(path) as connection:
            for statement, values in item.statements:
                connection.execute(statement, values)
        return True
    return persist


def test_retained_opening_replay_delivers_real_bridge_queue_and_live_activation(tmp_path):
    records = _records()
    selected = {mint: _selection(records[mint]) for mint in (
        T_PLUS_ONE_MINT, T_FALLBACK_MINT, *T_PLUS_TWO_MINTS, UNQUALIFIED_MINT,
    )}
    assert selected[T_PLUS_ONE_MINT]["entry_method"] == PLUS1_ENTRY_METHOD
    assert selected[T_FALLBACK_MINT]["entry_method"] == MIGRATION_SECOND_FALLBACK_METHOD
    assert all(selected[mint]["entry_method"] == BOUNDED_POST_MIGRATION_METHOD for mint in T_PLUS_TWO_MINTS)
    assert selected[UNQUALIFIED_MINT]["state"] == "INSUFFICIENT_EVIDENCE"

    source, monitor, queue_root = tmp_path / "source.sqlite", tmp_path / "monitor.sqlite", tmp_path / "queue"
    _source_database(source)
    _monitor_database(monitor)
    replayed = (T_PLUS_ONE_MINT, T_FALLBACK_MINT, *T_PLUS_TWO_MINTS[:2])
    with sqlite3.connect(source) as connection:
        for ordinal, mint in enumerate(replayed):
            connection.execute("BEGIN")
            commit_membership_and_outbox(
                connection, operation_id="watchtower", mint=mint,
                source_population_id="retained-96", membership_id=f"membership-{ordinal}",
                committed_at=records[mint]["migration_timestamp"],
            )
            connection.commit()

    bridge_config = {
        "source_db": str(source), "monitor_db": str(monitor),
        "queue_path": str(queue_root), "health_path": str(tmp_path / "bridge-health.json"),
    }
    delivered = []
    while True:
        result = run_once(bridge_config, now=1_800_000_000)
        if result is None:
            break
        delivered.append(result)
    assert len(delivered) == len(replayed)
    assert run_once(bridge_config, now=1_800_000_001) is None

    queue = MonitorQueue(queue_root, enabled=True, claim_authority_db_path=source)
    pending = list((queue_root / "pending").glob("*.json"))
    assert len(pending) == len(replayed)
    worker = MonitorWorker(
        queue, db_path=str(monitor), persist=_temporary_persist(monitor),
        transport=lambda _request: (_ for _ in ()).throw(AssertionError("provider call forbidden")),
    )
    for path in pending:
        envelope = json.loads(path.read_text())["envelope"]
        retained = selected[envelope["mint"]]
        envelope.update(retained)
        envelope.update({"entry_provenance": "retained-96", "entry_reference_state": "ENTRY_REFERENCE_QUALIFIED"})
        worker._activate_from_qualified_opening(envelope)

    with sqlite3.connect(monitor) as connection:
        rows = connection.execute(
            "SELECT mint,entry_status,monitor_state,entry_method,entry_offset_seconds "
            "FROM operation_monitor_facts ORDER BY mint"
        ).fetchall()
    assert len(rows) == len(replayed)
    assert all(row[1:3] == ("QUALIFIED", "MONITORING_ACTIVE") for row in rows)
    assert {row[0] for row in rows} == set(replayed)
    assert {row[0] for row in rows if row[4] == 2} == set(T_PLUS_TWO_MINTS[:2])
    assert not list((queue_root / "retry").glob("*.json"))
    assert not list((queue_root / "dead_letter").glob("*.json"))

    # The retained ledger intentionally carries no lifecycle observations.
    # This proves the replay does not fabricate terminal/History data.
    assert all(set(record) <= {"mint", "migration_timestamp", "http_status", "normalization_state", "normalized_item_count", "normalized_timestamps", "offsets", "duplicate_offsets", "failure_state"} for record in records.values())


def test_injector_reuses_its_materialized_plus_two_opening_and_audit_projection_is_read_only(tmp_path):
    monitor = tmp_path / "monitor.sqlite"
    _monitor_database(monitor)
    with sqlite3.connect(monitor) as connection:
        connection.execute(
            "INSERT INTO operation_monitor_facts("
            "operation_id,mint,cohort_class,entry_method,entry_status,monitor_state,"
            "evidence_status,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("watchtower", INJECTOR_MINT, "PROSPECTIVE_MONITOR_COHORT",
             "FIRST_FULL_POST_MIGRATION_SECOND_MC", "WAITING_FOR_ENTRY_REFERENCE",
             "WAITING_FOR_ENTRY_REFERENCE", "RETAINED", "fixture", 1, 1),
        )
    imported = import_retained_injector_opening(str(monitor), now=2)
    assert imported["entry_offset_seconds"] == 2
    assert imported["route"] == "HISTORICAL_RECONSTRUCTION"
    with sqlite3.connect(monitor) as connection:
        row = connection.execute(
            "SELECT entry_status,entry_offset_seconds,monitor_state FROM operation_monitor_facts WHERE mint=?",
            (INJECTOR_MINT,),
        ).fetchone()
    assert row == ("QUALIFIED", 2, "HISTORICAL_RECOVERY_ACQUIRING")

    projection = product_projection(LEDGER)
    assert projection["INJECTOR"]["proposed_plus2_classification"] == "PLUS2"
    assert projection[UNQUALIFIED_MINT]["proposed_plus2_classification"] == "NO_VALID_OPENING_CANDLE_THROUGH_PLUS4"
