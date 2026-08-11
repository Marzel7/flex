import sqlite3

import src.core.creator_funding_lifecycle as lifecycle
from src.core.creator_funding_lifecycle import obligation_id, record_event_fail_open, work_class


def test_work_class_and_stable_identity():
    assert work_class("pf_ws_creator_migration") == "LIVE"
    assert work_class("creator_resolution_queue") == "RECOVERY"
    assert work_class("funding_coverage_sweep") == "BACKFILL"
    assert obligation_id("creator", "mint") == obligation_id("creator", "mint")


def test_retry_uses_same_obligation_and_duplicate_event_is_idempotent(tmp_path):
    path = str(tmp_path / "ledger.db")
    kwargs = dict(creator="creator", mint="mint", source="pf_ws_creator_migration", occurred_at=100, attempt=1)
    assert record_event_fail_open(path, event="CREATED", previous_status=None, new_status="pending", **kwargs)
    assert record_event_fail_open(path, event="RETRY", previous_status="running", new_status="retry", **kwargs)
    assert record_event_fail_open(path, event="RETRY", previous_status="running", new_status="retry", **kwargs)
    conn = sqlite3.connect(path)
    rows = conn.execute("select lifecycle_event, obligation_id from creator_funding_lifecycle_events order by lifecycle_event").fetchall()
    assert rows == [("CREATED", obligation_id("creator", "mint")), ("RETRY", obligation_id("creator", "mint"))]


def test_telemetry_failure_is_fail_open(tmp_path):
    assert not record_event_fail_open(str(tmp_path), creator="c", mint="m", source=None, event="CREATED")


def test_telemetry_failure_records_a_durable_gap_when_the_followup_write_is_available(tmp_path, monkeypatch):
    path = str(tmp_path / "ledger.db")
    original = lifecycle.ensure_schema
    calls = {"count": 0}

    def fail_once(conn):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("simulated event insert failure")
        return original(conn)

    monkeypatch.setattr(lifecycle, "ensure_schema", fail_once)
    assert not record_event_fail_open(
        path, creator="creator", mint="mint", source="creator_resolution_queue",
        event="CREATED", occurred_at=123,
    )
    conn = sqlite3.connect(path)
    gaps = conn.execute(
        "select lifecycle_event, error_class from creator_funding_lifecycle_gaps"
    ).fetchall()
    assert gaps == [("CREATED", "OperationalError")]


def test_qualification_snapshot_has_a_consistent_event_high_water(tmp_path):
    path = str(tmp_path / "snapshot.db")
    conn = sqlite3.connect(path)
    conn.execute("create table creator_funding_queue (creator_address text, mint text, source text, status text)")
    conn.execute("insert into creator_funding_queue values ('creator', 'mint', 'creator_resolution_queue', 'pending')")
    lifecycle.ensure_schema(conn)
    conn.commit()
    conn.close()
    assert record_event_fail_open(path, creator="creator", mint="mint", source="creator_resolution_queue", event="CREATED", occurred_at=100)
    snapshot = lifecycle.capture_qualification_snapshot(
        path, label="start", configuration={"slots": 2, "rpc_ceiling": 8}, captured_at=101,
    )
    assert snapshot["event_high_water"] == 1
    assert snapshot["actionable_counts_by_class"] == {"RECOVERY": 1}
    conn = sqlite3.connect(path)
    persisted = conn.execute("select event_high_water, label from creator_funding_qualification_snapshots").fetchone()
    assert persisted == (1, "start")


def test_recovery_reconciliation_completion_emits_terminal_event(tmp_path, monkeypatch):
    from src.core import creator_funding_worker as worker

    path = str(tmp_path / "recovery.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table creator_funding_queue (
            creator_address text, mint text, source text, status text,
            locked_until integer, attempts integer, last_error text,
            funding_extracted_at integer, updated_at integer, created_at integer
        );
        create table creator_funders (creator_address text);
        insert into creator_funding_queue values
            ('creator', 'mint', 'creator_resolution_queue', 'pending', 0, 0, null, null, 1, 1);
        insert into creator_funders values ('creator');
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(worker, "DB_PATH", path)
    recovered, stale = worker._recover_stale_rows(200)
    assert (recovered, stale) == (1, 0)
    conn = sqlite3.connect(path)
    status = conn.execute("select status from creator_funding_queue").fetchone()[0]
    events = conn.execute("select lifecycle_event, work_class, previous_status, new_status from creator_funding_lifecycle_events").fetchall()
    assert status == "complete"
    assert events == [("COMPLETED", "RECOVERY", "pending", "complete")]
