from src.ops.watchtower_early_minimum_tracking import (WORK_TYPE, admit_after_qualified_entry, eligible_for_acquisition, chronological_recovery, normalize_and_build_records, persist_records, read_projection)
from src.ops.watchtower_observed_minimum_store import ObservedMinimumEvidenceStore

def _fact(**extra):
    return {"operation_id":"watchtower","mint":"mint","entry_status":"QUALIFIED","entry_timestamp":120,"entry_mc_usd":100.,"entry_provenance":"entry","assignment_provenance":"assignment","birth_provenance":"birth","durably_committed":True,"newly_committed":True,**extra}

def test_admission_is_post_commit_watchtower_only_and_not_historical():
    job=admit_after_qualified_entry(fact=_fact(),now=121)
    assert job["status"]=="ADMITTED" and job["work_type"]==WORK_TYPE and job["next_eligible_at"]==3720
    assert admit_after_qualified_entry(fact=_fact(durably_committed=False),now=121)["status"]=="NOT_ADMITTED_UNQUALIFIED"
    assert admit_after_qualified_entry(fact=_fact(newly_committed=False),now=121)["status"]=="NOT_ADMITTED_HISTORICAL"

def test_priority_and_completed_window_guards():
    job=admit_after_qualified_entry(fact=_fact(),now=121)
    assert eligible_for_acquisition(job["envelope"],now=3719,higher_priority_pending=False)=="NOT_DUE"
    assert eligible_for_acquisition(job["envelope"],now=3720,higher_priority_pending=True)=="DEFERRED_HIGHER_PRIORITY"
    assert eligible_for_acquisition(job["envelope"],now=3720,higher_priority_pending=False)=="ELIGIBLE"

def test_one_response_builds_four_records_without_historical_mutation():
    job=admit_after_qualified_entry(fact=_fact(),now=121)["envelope"]
    records=normalize_and_build_records(job=job,candles=[{"timestamp":x,"low_mc_usd":100-x} for x in range(180,3720,60)],provenance="BIRDEYE")
    assert [r["window_seconds"] for r in records]==[300,900,1800,3600]
    assert all("candles" not in r for r in records)

def test_recovery_is_strictly_after_minimum_and_never_uses_entry():
    assert chronological_recovery(minimum_timestamp=100,candles=[{"timestamp":99,"high_mc_usd":999}])["status"]=="INSUFFICIENT_EVIDENCE"
    value=chronological_recovery(minimum_timestamp=100,candles=[{"timestamp":101,"high_mc_usd":5},{"timestamp":105,"high_mc_usd":4}])
    assert value=={"status":"OBSERVED_RECOVERY","subsequent_peak_mc_usd":5.0,"subsequent_peak_timestamp":101,"seconds_from_minimum":1}

def test_store_projection_is_read_only_and_labels_lower_bound(tmp_path):
    job=admit_after_qualified_entry(fact=_fact(),now=121)["envelope"]
    records=normalize_and_build_records(job=job,candles=[{"timestamp":x,"low_mc_usd":100-x} for x in range(180,3720,60)],provenance="BIRDEYE")
    store=ObservedMinimumEvidenceStore(tmp_path/"evidence.db")
    assert persist_records(store=store,records=records)==(True,True,True,True)
    view=read_projection(store=store,mint="mint")
    assert set(view)=={300,900,1800,3600}
    assert {row["metric_label"] for row in view.values()}=={"OBSERVED_MINIMUM_LOWER_BOUND"}
