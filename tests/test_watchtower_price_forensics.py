import pytest

from src.ops.watchtower_price_forensics import (
    AUTHORITATIVE_EVIDENCE_CLASS, RESEARCH_EVIDENCE_CLASS, WATERMARK,
    admit_prospective_capture, cohort_statistics, historical_backfill_plan,
    historical_baseline_manifest, lifecycle_projection, prospective_capture_contract,
)


def row(**overrides):
    base = {"operation_id": "watchtower", "mint": "mint", "assignment_timestamp": WATERMARK + 1,
            "assignment_provenance": "assignment", "entry_timestamp": WATERMARK + 2,
            "entry_mc_usd": 100_000, "entry_status": "QUALIFIED", "entry_method": "SECOND_MC",
            "entry_provenance": "entry", "birth_provenance": "birth"}
    return {**base, **overrides}


def test_manifest_is_deterministic_and_marks_missing_birth_unavailable():
    manifest = historical_baseline_manifest([row(mint="b"), row(mint="a", birth_provenance=None)], captured_at=1)
    assert [item["mint"] for item in manifest["records"]] == ["a", "b"]
    assert manifest["records"][0]["birth_provenance"] == "UNAVAILABLE"
    assert manifest == historical_baseline_manifest([row(mint="a", birth_provenance=None), row(mint="b")], captured_at=1)


@pytest.mark.parametrize("invalid", [row(entry_status="PENDING"), row(entry_mc_usd=0), row(operation_id="other")])
def test_manifest_excludes_invalid_rows(invalid):
    assert historical_baseline_manifest([invalid], captured_at=1)["records"] == []


def test_duplicate_historical_mint_fails_closed():
    with pytest.raises(ValueError, match="DUPLICATE"):
        historical_baseline_manifest([row(), row()], captured_at=1)


def test_prospective_admission_is_post_commit_watermarked_and_provider_free():
    admission = admit_prospective_capture(fact={**row(), "durably_committed": True, "newly_committed": True}, birth={"birth_evidence_id": "birth"}, now=WATERMARK + 3)
    assert admission["status"] == "ADMITTED"
    assert admission["admission"]["provider_acquisition"] == "DEFERRED_TO_MONITOR_WORKER"
    assert admit_prospective_capture(fact={**row(entry_timestamp=WATERMARK), "durably_committed": True, "newly_committed": True}, birth={"birth_evidence_id": "birth"}, now=1)["status"] == "NOT_ADMITTED_INELIGIBLE"


def test_replayed_or_unproven_entry_is_not_admitted():
    assert admit_prospective_capture(fact={**row(), "durably_committed": True}, birth={"birth_evidence_id": "birth"}, now=1)["status"] == "NOT_ADMITTED_HISTORICAL_OR_REPLAY"
    assert admit_prospective_capture(fact={**row(), "newly_committed": True}, birth={"birth_evidence_id": "birth"}, now=1)["status"] == "NOT_ADMITTED_UNQUALIFIED"


def test_lifecycle_keeps_research_and_authoritative_evidence_separate():
    projected = lifecycle_projection(opening={"mint": "mint", "entry_identity": "entry"}, authoritative=[{"evidence_class": AUTHORITATIVE_EVIDENCE_CLASS}], research=[{"evidence_class": RESEARCH_EVIDENCE_CLASS}])
    assert len(projected["authoritative_observed_minimum"]) == 1
    assert len(projected["research_pilot_observations"]) == 1
    assert projected["aggregation_rule"] == "EVIDENCE_CLASSES_NEVER_COALESCED"


def test_lifecycle_rejects_mislabelled_evidence():
    with pytest.raises(ValueError, match="INVALID_EVIDENCE_CLASS"):
        lifecycle_projection(opening={"mint": "mint", "entry_identity": "entry"}, authoritative=[{"evidence_class": "research"}])


def test_historical_backfill_is_bounded_and_only_a_plan():
    plan = historical_backfill_plan(allowlist=[{"mint": "mint", "request_identity": "request"}])
    assert plan["concurrency"] == 1 and plan["runtime_action"] == "NOT_EXECUTED"
    with pytest.raises(ValueError, match="ALLOWLIST"):
        historical_backfill_plan(allowlist=[{"mint": str(index), "request_identity": str(index)} for index in range(11)])


def test_prospective_contract_preserves_shared_budget_and_no_raw_retention():
    contract = prospective_capture_contract()
    assert contract["provider_budget"] == {"global": 20, "per_mint": 4}
    assert contract["raw_payload_retention"] is False


def test_statistics_are_evidence_class_local_and_coverage_stratified():
    stats = cohort_statistics([
        {"evidence_class": AUTHORITATIVE_EVIDENCE_CLASS, "observed_minimum_mc_usd": 10, "observed_drop_percent": 90, "coverage_status": "COMPLETE"},
        {"evidence_class": RESEARCH_EVIDENCE_CLASS, "observed_minimum_mc_usd": 10, "observed_drop_percent": 50, "coverage_status": "PARTIAL"},
    ], evidence_class=AUTHORITATIVE_EVIDENCE_CLASS)
    assert stats["numerator_denominator"] == {"measured": 1, "cohort": 1}
    assert stats["population_inference"] == "PROHIBITED"
