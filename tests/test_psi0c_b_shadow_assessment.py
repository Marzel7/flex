import json
from pathlib import Path

import pytest

from src.evidence.contracts.production_shadow_assessment import (
    PSI0A_E_DIGEST,
    ProductionShadowAssessmentError,
    assess_fixture_shadow,
    build_shadow_assessment_contract,
    verify_fixture_shadow_assessment,
    verify_shadow_assessment_contract,
)


LINEAGE = {
    "psi0c_a_digest": "430dbeae5e87f2971e4e29927d2c6b86abc9c70be8fff022c5b724e1e35417b3",
    "psi0b_g_digest": "efadf9e061529af1fc690a253be4e8e29d7058d06d6a61be6105890b4e5e90cd",
    "psi0b_bundle_identity_digest": "370c815a4bacc640874d798c168ff812c2efd205d6de497fdcfb79b4005351b9",
}


def row(query, mint, rowid=1, creator="creator-a"):
    if query == "creator_selected_cohort":
        return {"rowid": rowid, "creator_address": creator, "mint": mint, "created_at": 1}
    if query == "evidence_launch_facts":
        return {"rowid": rowid, "fact_family": "LaunchFact", "payload_json": json.dumps({"mint": mint, "creator": creator}), "raw_artifact_digest": "a" * 64, "acquired_at": 1, "source_id": "fixture", "source_version": "1", "verification_state": "VERIFIED"}
    if query == "main_selected_cohort":
        return {"rowid": rowid, "mint": mint, "migrated_at": 1, "first_observed_mc": 1.0, "first_observed_price": 1.0, "first_observed_at": 1, "first_observed_source": "fixture", "first_observed_confidence": 1.0, "pf_ws_creator": creator, "creator_mismatch": 0}
    if query == "ops_selected_cohort":
        return {"rowid": rowid, "mint": mint, "creator_wallet": creator, "create_signature": "sig", "create_time": 1, "create_slot": 1, "creator_extraction_method": "fixture", "confidence": "HIGH", "recorded_at": 1}
    return {"rowid": rowid, "snapshot_id": rowid, "mint": mint, "price_usd": 1.0, "market_cap": 1.0, "source": "fixture", "captured_at": 1, "created_at": 1}


def complete():
    return {query: [row(query, "mint-a")] for query in (
        "creator_selected_cohort", "evidence_launch_facts", "main_selected_cohort",
        "ops_selected_cohort", "snapshot_selected_cohort",
    )}


def run(tmp_path, results=None, cohort=("mint-a",), name="assessment"):
    return assess_fixture_shadow(
        build_shadow_assessment_contract(), cohort_mints=cohort,
        synthetic_results=complete() if results is None else results,
        output_directory=tmp_path / name, input_lineage=LINEAGE,
    )


def test_contract_binds_required_inputs_and_false_authority():
    contract = build_shadow_assessment_contract()
    assert verify_shadow_assessment_contract(contract)
    assert contract.resource_ceiling_digest == PSI0A_E_DIGEST
    assert not any((contract.accepts_production_rows, contract.grants_policy_authority,
                    contract.grants_ranking_authority, contract.grants_integration_authority,
                    contract.grants_activation_authority, contract.conflict_resolution_allowed,
                    contract.negative_inference_from_absence_allowed))


def test_complete_coverage_and_exact_replay(tmp_path):
    bundle = run(tmp_path)
    assert verify_fixture_shadow_assessment(bundle.output_directory) == bundle
    data = json.loads((bundle.output_directory / "assessment.json").read_text())
    assert all(item["coverage_numerator"] == item["coverage_denominator"] == 1 for item in data["membership"].values())
    assert data["conflicts"] == [] and not any(data["authority"].values())


def test_empty_and_partial_coverage_never_infer_negative(tmp_path):
    empty = {query: [] for query in complete()}
    bundle = run(tmp_path, empty, cohort=("mint-a", "mint-b"), name="empty")
    data = json.loads((bundle.output_directory / "assessment.json").read_text())
    assert all(item["coverage_numerator"] == 0 and item["coverage_denominator"] == 2 for item in data["membership"].values())
    assert all(not item["negative_outcome_inferred"] for item in data["missingness"])
    partial = complete(); partial["snapshot_selected_cohort"] = []
    bundle = run(tmp_path, partial, cohort=("mint-a", "mint-b"), name="partial")
    data = json.loads((bundle.output_directory / "assessment.json").read_text())
    mint_a = next(item for item in data["missingness"] if item["mint"] == "mint-a")
    assert mint_a["surfaces"]["snapshot_selected_cohort"] == "ABSENT_NOT_NEGATIVE"


def test_duplicates_conflicts_and_unmatched_are_preserved(tmp_path):
    results = complete()
    results["creator_selected_cohort"].append(row("creator_selected_cohort", "mint-a", 2, "creator-b"))
    results["ops_selected_cohort"].append(row("ops_selected_cohort", "orphan", 2, "creator-c"))
    bundle = run(tmp_path, results)
    data = json.loads((bundle.output_directory / "assessment.json").read_text())
    assert data["membership"]["creator_selected_cohort"]["duplicate_row_count"] == 1
    assert data["conflicts"][0]["resolved_value"] is None
    assert {item["value"] for item in data["conflicts"][0]["assertions"]} == {"creator-a", "creator-b"}
    assert data["orphan_unmatched_accounting"] == {"unmatched_mints": ["orphan"], "unmatched_rows": 1}


def test_input_order_independence(tmp_path):
    first = complete()
    first["creator_selected_cohort"].append(row("creator_selected_cohort", "mint-a", 2, "creator-b"))
    second = {key: list(reversed(value)) for key, value in first.items()}
    a = run(tmp_path, first, name="a")
    b = run(tmp_path, second, name="b")
    adoc = json.loads((a.output_directory / "assessment.json").read_text())
    bdoc = json.loads((b.output_directory / "assessment.json").read_text())
    assert adoc == bdoc


@pytest.mark.parametrize("kind", ("missing", "extra", "altered"))
def test_replay_rejects_missing_extra_and_altered_files(tmp_path, kind):
    bundle = run(tmp_path)
    if kind == "missing":
        (bundle.output_directory / "contract.json").unlink()
    elif kind == "extra":
        (bundle.output_directory / "extra.json").write_text("{}")
    else:
        (bundle.output_directory / "assessment.json").write_text("{}\n")
    with pytest.raises(ProductionShadowAssessmentError, match="FILE_SET|DIGEST"):
        verify_fixture_shadow_assessment(bundle.output_directory)


def test_stale_lineage_unknown_schema_query_drift_and_production_marker_fail(tmp_path):
    contract = build_shadow_assessment_contract()
    with pytest.raises(ProductionShadowAssessmentError, match="STALE_OR_ALTERED_LINEAGE"):
        assess_fixture_shadow(contract, cohort_mints=("mint-a",), synthetic_results=complete(), output_directory=tmp_path / "stale", input_lineage={**LINEAGE, "psi0b_g_digest": "0" * 64})
    unknown = complete(); unknown["creator_selected_cohort"][0]["extra"] = True
    with pytest.raises(ProductionShadowAssessmentError, match="UNKNOWN_RESULT_SCHEMA"):
        assess_fixture_shadow(contract, cohort_mints=("mint-a",), synthetic_results=unknown, output_directory=tmp_path / "schema", input_lineage=LINEAGE)
    reordered = dict(reversed(list(complete().items())))
    with pytest.raises(ProductionShadowAssessmentError, match="QUERY_SET_OR_ORDER_DRIFT"):
        assess_fixture_shadow(contract, cohort_mints=("mint-a",), synthetic_results=reordered, output_directory=tmp_path / "order", input_lineage=LINEAGE)
    with pytest.raises(ProductionShadowAssessmentError, match="PRODUCTION_ROWS_PROHIBITED"):
        assess_fixture_shadow(contract, cohort_mints=("mint-a",), synthetic_results=complete(), output_directory=tmp_path / "prod", input_lineage=LINEAGE, fixture_only=False)


def test_malformed_evidence_and_ceiling_breach_fail_without_publication(tmp_path):
    malformed = complete(); malformed["evidence_launch_facts"][0]["payload_json"] = "not-json"
    with pytest.raises(ProductionShadowAssessmentError, match="MALFORMED_EVIDENCE"):
        run(tmp_path, malformed, name="malformed")
    assert not (tmp_path / "malformed").exists()
    oversized = complete()
    oversized["creator_selected_cohort"] = [row("creator_selected_cohort", "mint-a", index + 1) for index in range(5001)]
    with pytest.raises(ProductionShadowAssessmentError, match="RESOURCE_CEILING"):
        run(tmp_path, oversized, name="oversized")
    assert not (tmp_path / "oversized").exists()


def test_existing_output_and_contract_tamper_fail_closed(tmp_path):
    target = tmp_path / "existing"; target.mkdir()
    with pytest.raises(ProductionShadowAssessmentError, match="OUTPUT_NOT_NEW"):
        run(tmp_path, name="existing")
    contract = build_shadow_assessment_contract()
    tampered = type(contract)(**{**contract.__dict__, "grants_integration_authority": True})
    with pytest.raises(ProductionShadowAssessmentError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_shadow_assessment_contract(tampered)
