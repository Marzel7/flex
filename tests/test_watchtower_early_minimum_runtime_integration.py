import json
import sqlite3
import time

from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker
from src.ops.watchtower_early_minimum_tracking import admit_after_qualified_entry
from src.ops.watchtower_observed_minimum_store import ObservedMinimumEvidenceStore


def _fact(entry):
    return {"operation_id":"watchtower", "mint":"mint", "entry_status":"QUALIFIED",
            "entry_timestamp":entry, "entry_mc_usd":100.0, "entry_provenance":"entry-proof",
            "assignment_provenance":"assignment-proof", "birth_provenance":"birth-proof",
            "durably_committed":True, "newly_committed":True}


def test_early_minimum_durable_dispatch_persists_once_and_defers_priority(monkeypatch, tmp_path):
    entry = ((int(time.time()) - 3700) // 60) * 60
    admission = admit_after_qualified_entry(fact=_fact(entry), now=entry)
    assert admission["status"] == "ADMITTED"
    assert admit_after_qualified_entry(fact={**_fact(entry), "newly_committed":False}, now=entry)["status"] == "NOT_ADMITTED_HISTORICAL"
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    queue.queue.initialize()
    queue.queue.enqueue(admission["envelope"], message_id=admission["job_id"])
    # A non-early message is the durable higher-priority signal.
    queue.queue.enqueue({"mint":"other", "operation_id":"watchtower"}, message_id="higher")
    worker = MonitorWorker(queue, transport=lambda _p: (_ for _ in ()).throw(AssertionError("must defer")), db_path=str(tmp_path / "unused.db"))
    claimed = queue.claim(1, eligible=lambda p: p.get("work_type") == admission["work_type"])
    worker._process_early_minimum(claimed[0])
    assert (queue.queue.root / "pending" / f"{admission['job_id']}.json").exists()
    (queue.queue.root / "pending" / "higher.json").unlink()
    calls=[]
    def provider(request):
        calls.append(request["request_manifest"])
        return {"candles":[{"timestamp":ts,"low":float(100-(ts-entry)//60),"high":float(101-(ts-entry)//120),"mc":100.0} for ts in range(entry, entry+3600, 60)]}
    monkeypatch.setenv("WATCHTOWER_EARLY_MINIMUM_EVIDENCE_DB_PATH", str(tmp_path / "early.sqlite"))
    worker.transport=provider
    worker.process_once()
    assert len(calls) == 1 and calls[0]["request_parameters"]["type"] == "1m"
    rows=ObservedMinimumEvidenceStore(tmp_path / "early.sqlite").read(mint="mint")
    assert {row["window_seconds"] for row in rows} == {300,900,1800,3600}
    assert all("chronological_recovery" in row for row in rows)
    from src.ops.operator_routes import _early_minimum_projection
    projection = _early_minimum_projection("mint")
    assert projection["evidence_status"] == "OBSERVED_LOWER_BOUND"
    assert set(projection["windows"]) == {"300", "900", "1800", "3600"}
    assert not list((queue.queue.root / "pending").glob("*.json"))
    # Sidecar work cannot mutate an unrelated canonical fact database.
    monitor=tmp_path / "monitor.sqlite"; sqlite3.connect(monitor).close()
    assert monitor.stat().st_size == 0
