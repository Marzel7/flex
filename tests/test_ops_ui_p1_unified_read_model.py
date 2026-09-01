"""OPS-UI-P1: provider-free tests for the unified operations read-model
design. No provider calls. No production writes. All queries mode=ro.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"

READ_MODEL_ARTIFACT = ROOT / "docs/audits/ops_ui_p1_unified_operations_read_model.json"
INTAKE_ARTIFACT = ROOT / "docs/audits/ops_ui_p1_discovery_intake_contract.json"
RESULT_ARTIFACT = ROOT / "docs/audits/ops_ui_p1_implementation_result.json"


@pytest.fixture(scope="module")
def read_model_doc():
    return json.loads(READ_MODEL_ARTIFACT.read_text())


@pytest.fixture(scope="module")
def intake_doc():
    return json.loads(INTAKE_ARTIFACT.read_text())


def test_artifacts_exist():
    assert READ_MODEL_ARTIFACT.exists()
    assert INTAKE_ARTIFACT.exists()
    assert RESULT_ARTIFACT.exists()


def test_dv34_historical_population_documented_baseline_never_shown_as_23(read_model_doc):
    """The core Phase D/K requirement: historical population must be 123, not 23."""
    dv34 = read_model_doc["phase_k_dv34_reference_result"]
    assert dv34["historical_population"]["count"] == 123
    assert dv34["evidence_qualification"]["high_confidence"] == 23
    assert dv34["historical_population"]["count"] != dv34["evidence_qualification"]["high_confidence"]


def test_dv34_full_number_set_sums_to_historical(read_model_doc):
    dv34 = read_model_doc["phase_k_dv34_reference_result"]
    eq = dv34["evidence_qualification"]
    assert eq["valid_not_high"] == 82
    assert eq["historical_only_not_reproducible"] == 18
    assert eq["high_confidence"] + eq["valid_not_high"] + eq["historical_only_not_reproducible"] == 123


def test_dv34_raw_verification_6_of_6(read_model_doc):
    rv = read_model_doc["phase_k_dv34_reference_result"]["raw_verification"]
    assert rv["sample_size"] == 6
    assert rv["verified"] == 6


def test_dv34_role_is_provisioning_network_candidate_not_operation(read_model_doc):
    dv34 = read_model_doc["phase_k_dv34_reference_result"]
    assert "PROVISIONING_NETWORK_CANDIDATE" in dv34["role"]
    assert "NOT canonical" in dv34["operation_identity"] or "NOT canonical" in dv34.get("operation_identity", "")


def test_dv34_watchtower_relationship_is_shared_source_lineage_not_confirmed(read_model_doc):
    dv34 = read_model_doc["phase_k_dv34_reference_result"]
    assert "SHARED_SOURCE_LINEAGE" in dv34["watchtower_relationship"]
    assert "NOT confirmed" in dv34["watchtower_relationship"]


def test_dv34_promotion_not_eligible(read_model_doc):
    dv34 = read_model_doc["phase_k_dv34_reference_result"]
    assert "NOT ELIGIBLE" in dv34["promotion_eligibility"]


def test_watchtower_control_confirmed_and_unmutated(read_model_doc):
    control = read_model_doc["phase_l_watchtower_control"]
    assert control["mutation_performed"] is False
    assert "CONFIRMED" in control["result"]


def test_3sw2_control_confirmed_and_unmutated(read_model_doc):
    control = read_model_doc["phase_m_3sw2_control"]
    assert control["mutation_performed"] is False
    assert "CONFIRMED" in control["result"]


def test_watchtower_and_3sw2_are_only_confirmed_operators_live():
    """Structural verification against the real live DB, not just the artifact."""
    conn = sqlite3.connect(f"file:{ROOT}/database/wt_ops_v2.db?mode=ro", uri=True)
    rows = conn.execute("SELECT display_name, status FROM operators WHERE status='CONFIRMED'").fetchall()
    conn.close()
    names = {r[0] for r in rows}
    assert names == {"WATCHTOWER", "3SW2"}


def test_authority_state_principle_is_operators_status_only(read_model_doc):
    """Structural guard: exactly one field is canonical authority, everything
    else is workflow/UI overlay -- new evidence-qualification states must
    never become canonical."""
    model = read_model_doc["phase_b_authority_model"]
    assert model["principle"] == "operators.status is the ONLY canonical authority state. Everything else is workflow/UI/lifecycle overlay."
    safety_rule = model["safety_rule_for_this_milestone"]
    assert "never written to operators.status" in safety_rule
    assert "never treated as a canonical-authority-state" in safety_rule


def test_human_decided_states_never_auto_overwritten(read_model_doc):
    classification = read_model_doc["phase_b_authority_model"]["classification"]
    for state in ("CONFIRMED", "REJECTED", "MERGED", "SPLIT", "RETIRED"):
        assert state in classification
        assert "HUMAN_DECIDED" in classification[state] or "canonical-authority-state" in classification[state]


def test_investigation_states_distinct_from_canonical(read_model_doc):
    classification = read_model_doc["phase_b_authority_model"]["classification"]
    assert "investigation-workflow-state" in classification["CANDIDATE / REVIEW_CANDIDATE / PROVISIONAL"]
    assert "canonical-authority-state" in classification["CONFIRMED"]


def test_discovery_corpus_census_matches_live_query(intake_doc):
    """Re-derive the 385 total and classification breakdown live rather than
    trusting the artifact."""
    conn = sqlite3.connect(f"file:{ROOT}/database/local_operation_discovery_corpus.db?mode=ro", uri=True)
    total = conn.execute("SELECT COUNT(*) FROM candidate_families").fetchone()[0]
    strong = conn.execute(
        "SELECT COUNT(*) FROM candidate_families WHERE classification='STRONG_CANDIDATE_FAMILY'"
    ).fetchone()[0]
    conn.close()
    verification = intake_doc["discovery_corpus_verification"]
    assert total == verification["candidate_families_total"] == 385
    assert strong == verification["classification_breakdown"]["STRONG_CANDIDATE_FAMILY"] == 21


def test_intake_eligibility_deterministic_reuses_existing_boundary(intake_doc):
    criteria = intake_doc["phase_e_intake_eligibility_criteria"]["eligible_for_analyst_intake_queue"]
    assert "STRONG_CANDIDATE_FAMILY" in criteria["condition"]
    assert "PARTIAL_CANDIDATE_FAMILY" in criteria["condition"]


def test_no_automatic_promotion_anywhere_in_classification_path(intake_doc):
    policy = intake_doc["phase_e_deterministic_classification_rule"]["policy"]
    assert "No automatic promotion exists" in policy
    assert "never writes to operators.status" in policy


def test_conflict_presentation_rule_documented(read_model_doc):
    rule = read_model_doc["phase_c_unified_read_model_spec"]["conflict_presentation_rule"]
    assert "never silently picks a winner" in rule


def test_no_second_operations_registry_built(read_model_doc):
    """Structural guard against building a competing app: the trace must
    confirm operator_routes.py is THE live registry, not a second one."""
    trace = read_model_doc["phase_a_existing_ui_trace"]
    assert "operator_routes.py" in trace["route_layer"]
    assert "THE live Operations registry" in trace["route_layer"]


def test_performance_queries_are_index_backed():
    """Live re-verification that the proposed read paths use indexes, not
    full table scans."""
    conn = sqlite3.connect(f"file:{ROOT}/database/flex_complete_database.db?mode=ro", uri=True)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM creator_funders WHERE funder_address=?", (DV34,)
    ).fetchall()
    conn.close()
    plan_text = " ".join(str(row) for row in plan).upper()
    assert "SEARCH" in plan_text


def test_no_production_write_statements_in_this_test_module():
    src = Path(__file__).read_text()
    lines = [ln for ln in src.splitlines() if ".execute(" in ln and "test_no_production_write" not in ln]
    combined = "\n".join(lines).upper()
    for table in ("OPERATORS", "OPERATOR_ENTITIES", "CREATOR_FUNDERS", "CANDIDATE_FAMILIES"):
        for verb in ("INSERT INTO " + table, "UPDATE " + table, "DELETE FROM " + table):
            assert verb not in combined
