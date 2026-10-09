import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile_watchtower_price_forensics_batches_1_4.py"


def load_module():
    spec = importlib.util.spec_from_file_location("batch5_reconciliation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_reconciliation_preserves_cumulative_coverage_and_missing_rank_contract():
    artifact = load_module().artifact()
    reconciliation = artifact["reconciliation"]
    assert reconciliation["total_mints"] == 40
    assert reconciliation["newly_acquired_mints"] == 33
    assert reconciliation["missing_one_minute_evidence_ranks"] == [22, 23, 24, 26]
    assert reconciliation["duplicate_request_identities"] == 0
    assert reconciliation["duplicate_evidence_identities"] == 0
    assert reconciliation["missing_bucket_count"] == 664
    assert reconciliation["complete_windows"] == 48
    assert reconciliation["partial_windows"] == 56
    assert reconciliation["no_valid_candle_outcomes"] == 4


def test_catchup_and_batch5_are_bounded_deterministic_and_do_not_substitute():
    artifact = load_module().artifact()
    catchup = artifact["catchup_batch"]["records"]
    batch5 = artifact["batch_5"]["records"]
    assert [item["rank"] for item in catchup] == [22, 23, 24, 26]
    assert len(catchup) == 4
    assert [item["rank"] for item in batch5] == list(range(41, 51))
    assert len(batch5) == 10
    assert sum(item["proposed_request"] is not None for item in batch5) == 6
    assert [item["rank"] for item in batch5 if item["proposed_request"] is None] == [42, 44, 45, 49]
    identities = [item["proposed_request"]["request_identity"] for item in catchup + batch5 if item["proposed_request"]]
    assert len(identities) == len(set(identities))


def test_rendered_plan_is_compact_and_contains_no_provider_payloads(tmp_path):
    module = load_module()
    output = tmp_path / "plan.json"
    module.main.__globals__["OUTPUT"] = output
    # Exercise the pure artifact only; no provider, DB, queue, or budget object exists in this module.
    rendered = __import__("json").dumps(module.artifact(), sort_keys=True).encode()
    assert len(rendered) < 1_000_000
    assert b'"raw_provider_payload"' not in rendered
