import sqlite3
from types import SimpleNamespace

from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker, MonitorBirdeyeTransport
from src.ops.strict_migration_window import failure_diagnostic, plan


def test_strict_failure_keeps_only_compact_t_and_t_plus_one_diagnostic():
    record = failure_diagnostic(
        request=plan(mint="mint", migration_timestamp=100), credential_alias="BIRDEYE", job_identity="job",
        provider_diagnostic={"normalized_item_count": 2, "normalized_timestamps": [100, 101, 999], "migration_timestamp_present": True, "target_timestamp_present": True}, attempt_timestamp=102,
    )
    assert record["normalized_timestamps"] == [100, 101]
    assert record["normalized_item_count"] == 2
    assert record["migration_timestamp_present"] is True and record["target_timestamp_present"] is True


def test_provider_backoff_allows_only_retained_terminal_work(tmp_path):
    db = tmp_path / "monitor.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE operation_monitor_facts(operation_id TEXT,mint TEXT,cohort_class TEXT,entry_method TEXT,entry_timestamp INTEGER,entry_mc_usd REAL,monitor_state TEXT,next_observation_at INTEGER,monitor_completed_at INTEGER,provenance_digest TEXT,created_at INTEGER,updated_at INTEGER,PRIMARY KEY(operation_id,mint));
        CREATE TABLE operation_monitor_observations(operation_id TEXT,mint TEXT,observation_timestamp INTEGER,mc_usd REAL,resolution TEXT,source TEXT,request_identity TEXT,provenance_digest TEXT,created_at INTEGER,PRIMARY KEY(operation_id,mint,observation_timestamp,resolution));
        """)
        conn.execute("INSERT INTO operation_monitor_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("watchtower", "terminal", "PROSPECTIVE_MONITOR_COHORT", "FIRST_FULL_POST_MIGRATION_SECOND_MC", 1, 1.0, "PRICE_MONITOR_COMPLETE_COLLAPSED", None, 2, "p", 1, 1))
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    queue.queue.enqueue({"work_type": "ordinary", "mint": "ordinary"}, message_id="ordinary")
    queue.queue.enqueue({"work_type": "WATCHTOWER_TERMINAL_ATH_FINALIZATION", "mint": "terminal", "logical_identity": "id", "terminal_coverage_state": "COMPLETE"}, message_id="terminal")
    called = []
    class Finalizer:
        def __init__(self, *_a, **_k): pass
        def freeze(self, _mint): return {"mint": "terminal", "terminal_coverage_state": "COMPLETE"}
        def logical_job_identity(self, _fact): return "id"
        def finalize(self, *_a, **_k): called.append("finalized"); return {"state": "FINALIZED", "provider_calls": 0}
    worker = MonitorWorker(queue, db_path=str(db), transport=lambda _request: (_ for _ in ()).throw(AssertionError("provider must not run")), terminal_finalizer_factory=Finalizer)
    queue.provider_eligible = lambda: False
    assert worker.process_once() == 1
    assert called == ["finalized"]
    assert (tmp_path / "queue" / "pending" / "ordinary.json").exists()


def test_forward_uses_shared_recovery_normalizer_for_timestamp_alias_and_order():
    t = 100
    payload = {"data": {"items": [
        {"timestamp": t + 3, "o": 1, "h": 2, "l": 1, "c": 1.5},
        {"timestamp": t, "o": 1, "h": 2, "l": 1, "c": 1.25},
        {"timestamp": t + 2, "o": 1, "h": 2, "l": 1, "c": 1.4},
    ]}}
    transport = MonitorBirdeyeTransport(binding=lambda _request: SimpleNamespace(status_code=200, payload=payload, response_headers={}))
    entry, _manifest = transport.acquire_watchtower_entry({"mint": "mint"}, {"migration_timestamp": t, "time_from": t, "time_to": t + 4})
    assert entry == {"timestamp": t, "mc": 1.25, "plus_one_absent": True}
