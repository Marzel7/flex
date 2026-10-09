import pytest

from src.ops.watchtower_price_forensics import (
    AUTHORITATIVE_EVIDENCE_CLASS, RESEARCH_EVIDENCE_CLASS, WATERMARK,
    admit_prospective_capture, cohort_statistics, historical_backfill_plan,
    historical_baseline_manifest, lifecycle_projection, prospective_capture_contract,
    v2_cohort_manifest,
    reconstruct_retained_entry,
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


def launch(mint, *, created_at=None, monitored=False, label="WATCHTOWER"):
    return {"mint": mint, "original_classification": label,
            "assignment": {"identity": "assignment-" + mint, "timestamp": 10, "provenance": "verified"},
            "creation": {"timestamp": created_at, "creator": "creator", "signature": "signature"},
            "lifecycle_status": "UNKNOWN", "evidence": {"opening": {"status": "QUALIFIED" if monitored else "MISSING"}}}


def test_v2_manifest_orders_only_qualified_creation_timestamps():
    manifest = v2_cohort_manifest(launches=[launch("old", created_at=1, monitored=True), launch("new", created_at=2), launch("unknown")], baseline_mints=["old"], as_of=9)
    assert manifest["summary"]["most_recent"]["10"] == ["new", "old"]
    assert manifest["summary"]["creation_timestamp_unavailable"] == 1
    assert manifest["summary"]["most_recent_70_match"] is False
    unknown = next(row for row in manifest["launches"] if row["mint"] == "unknown")
    assert unknown["creation"]["chronology_status"] == "CREATION_TIMESTAMP_UNAVAILABLE"


def test_v2_manifest_rejects_duplicate_or_incomplete_assignment():
    with pytest.raises(ValueError, match="DUPLICATE"):
        v2_cohort_manifest(launches=[launch("same"), launch("same")], baseline_mints=[], as_of=1)
    bad = launch("bad")
    bad["assignment"]["provenance"] = ""
    with pytest.raises(ValueError, match="INCOMPLETE"):
        v2_cohort_manifest(launches=[bad], baseline_mints=[], as_of=1)


def entry_inputs(**creation):
    return {"mint": "mint", "assignment": {"identity": "assignment", "provenance": "verified"},
            "creation": {"timestamp": 10, "signature": "create", "source": "canonical_create_ledger", **creation},
            "migration": {"timestamp": 20, "signature": "migration", "source": "canonical_migration_ledger"}}


def test_retained_entry_reconstruction_requires_canonical_birth_and_price_offsets():
    values = entry_inputs(creation_source="ignored")
    missing = reconstruct_retained_entry(**values)
    assert missing["qualification_status"] == "MISSING_PRICE_EVIDENCE"
    fixture = entry_inputs(); fixture["creation"]["source"] = "FIXTURE_BACKFILL"
    assert reconstruct_retained_entry(**fixture)["qualification_status"] == "MISSING_BIRTH_PROVENANCE"


def test_retained_entry_reconstruction_preserves_chronology_conflict_and_can_qualify_offsets():
    conflict = entry_inputs(); conflict["migration"]["timestamp"] = 9
    conflict_result = reconstruct_retained_entry(**conflict)
    assert conflict_result["qualification_status"] == "CONFLICTING_EVIDENCE"
    assert "MISSING_PRICE_EVIDENCE" in conflict_result["evidence_deficiencies"]
    qualified = reconstruct_retained_entry(**entry_inputs(), retained_offsets={"1": 123.0})
    assert qualified["qualification_status"] == "QUALIFIED_RETAINED_ENTRY"
    assert qualified["entry_method"] == "FIRST_FULL_POST_MIGRATION_SECOND_MC"
