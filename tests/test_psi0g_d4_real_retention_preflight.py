import copy
import json
from pathlib import Path

from src.evidence.contracts.psi0g_real_retention_preflight import (
    assess_real_retention_preflight,
    projection_digest,
)
from tests.test_psi0g_d1_operation_projection import material
from src.evidence.contracts.psi0g_operation_projection import project_psi0g_operation_candidate


def inputs():
    projection = json.loads(project_psi0g_operation_candidate(**material(incomplete=True, absent=True)).payload)
    manifest = {
        "schema_version": "psi0g-d3.publication.v1", "status": "PASS",
        "candidate_id": projection["candidate"]["candidate_id"],
        "projection_sha256": projection_digest(projection),
    }
    candidate = projection["candidate"]
    # Model the real D3 completeness invariant in the small fixture.
    candidate["missing_evidence"] = [f"gap-{number}" for number in range(14)]
    manifest["projection_sha256"] = projection_digest(projection)
    disposition = {
        "schema_version": "psi0g-d4.human-disposition.v1", "status": "PASS",
        "candidate_id": candidate["candidate_id"],
        "candidate_input_digest": candidate["input_digest"],
        "reviewer_class": "HUMAN_REVIEW", "nomination_state": "PROPOSED",
        "operation_ids": ["watchtower", "three_sw2"],
        "unresolved_missing_evidence": list(candidate["missing_evidence"]),
        "contradictory_evidence": [],
        "authority": {"proposed": False, "supported": False, "publication": False},
        "semantic_guards": {
            "operations_remain_separate": True, "same_operation_claim": False,
            "same_person_or_operator_claim": False,
            "missing_evidence_is_negative_evidence": False,
        },
    }
    return projection, manifest, disposition


def test_exact_review_is_ready_but_does_not_write_or_authorize():
    result = assess_real_retention_preflight(*inputs())
    assert result["status"] == "READY" and result["blockers"] == []
    assert not result["fixture_f13_invoked"] and not result["store_written"]
    assert not result["real_retention_write_authorized"]
    assert not any(result["downstream_authority"].values())


def test_absent_review_holds_the_real_d3_boundary():
    projection, manifest, _ = inputs()
    result = assess_real_retention_preflight(projection, manifest, None)
    assert result["status"] == "HOLD"
    assert result["blockers"] == ["D4_EXACT_HUMAN_DISPOSITION_ABSENT"]


def test_old_candidate_disposition_cannot_transfer_to_new_identity():
    projection, manifest, disposition = inputs()
    disposition["candidate_id"] = "old-candidate"
    result = assess_real_retention_preflight(projection, manifest, disposition)
    assert "D4_DISPOSITION_CANDIDATE_MISMATCH" in result["blockers"]


def test_projection_tamper_and_authority_drift_fail_closed():
    projection, manifest, disposition = inputs()
    projection = copy.deepcopy(projection)
    projection["authority"]["monitoring"] = True
    result = assess_real_retention_preflight(projection, manifest, disposition)
    assert "D4_PROJECTION_DIGEST_MISMATCH" in result["blockers"]
    assert "D4_PROJECTION_AUTHORITY_DRIFT" in result["blockers"]


def test_missing_evidence_and_identity_guards_are_exact():
    projection, manifest, disposition = inputs()
    disposition["unresolved_missing_evidence"].pop()
    disposition["semantic_guards"]["same_operation_claim"] = True
    result = assess_real_retention_preflight(projection, manifest, disposition)
    assert "D4_DISPOSITION_MISSING_EVIDENCE_DRIFT" in result["blockers"]
    assert "D4_SEMANTIC_GUARD_DRIFT" in result["blockers"]


def test_current_real_d3_is_hold_only_for_missing_exact_review():
    root = Path(__file__).resolve().parents[1]
    projection = json.loads((root / "docs/audits/psi0g_runs/psi0g-d3-operation-candidate-20260817-01/projection.json").read_text())
    manifest = json.loads((root / "docs/audits/psi0g_runs/psi0g-d3-operation-candidate-20260817-01/manifest.json").read_text())
    result = assess_real_retention_preflight(projection, manifest, None)
    assert result["status"] == "HOLD"
    assert result["blockers"] == ["D4_EXACT_HUMAN_DISPOSITION_ABSENT"]
