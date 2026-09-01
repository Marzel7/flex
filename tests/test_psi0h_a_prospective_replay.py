import copy
import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_prospective_replay import (
    Psi0hProspectiveReplayError,
    replay_prospective_observations,
)


ROOT = Path(__file__).resolve().parents[1]
SURFACE = json.loads((ROOT / "docs/audits/psi0g_runs/psi0g-d8-first-real-provenance-surface-20260817-01/surface.json").read_text())


def baseline():
    return {"evidence_cutoff": 100, "observation_ids": ["training"],
            "evidence_ids": ["e-training"], "primitive_ids": ["p-training"]}


def observation(*, label="POSITIVE"):
    nomination = SURFACE["operational_roles"]["FUNDING_AND_LAUNCH_OPERATION"]["nominations"][0]
    return {
        "observation_id": "held-out", "observation_window": {"start": 101, "end": 110},
        "captured_at": 112, "evidence_ids": ["e-held-out"], "primitive_ids": ["p-held-out"],
        "edge_features": list(nomination["shared_edge_features"]),
        "mechanism_features": list(nomination["shared_mechanism_features"]),
        "temporal_features": list(nomination["shared_temporal_features"]), "reviewed_label": label,
    }


def test_complete_disjoint_holdout_surfaces_non_authoritative_candidate():
    result = replay_prospective_observations(SURFACE, baseline(), [observation()])
    assert result["status"] == "PASS" and len(result["continuity_candidates"]) == 1
    candidate = result["continuity_candidates"][0]
    assert candidate["candidate_disposition"] is None
    assert not candidate["same_operation_claim"] and not candidate["same_human_or_operator_claim"]
    assert result["metrics"]["max_logical_latency_seconds"] == 2
    assert not any(result["authority"].values()) and result["shadow_only"]


def test_incomplete_feature_proof_does_not_quietly_match():
    value = observation()
    value["mechanism_features"].pop()
    result = replay_prospective_observations(SURFACE, baseline(), [value])
    assert result["status"] == "PASS" and result["continuity_candidates"] == []


def test_temporal_and_identity_leakage_hold_before_comparison():
    value = observation()
    value["observation_id"] = "training"
    value["observation_window"]["start"] = 100
    value["evidence_ids"] = ["e-training"]
    value["primitive_ids"] = ["p-training"]
    result = replay_prospective_observations(SURFACE, baseline(), [value])
    assert result["status"] == "HOLD" and result["continuity_candidates"] == []
    assert len(result["blockers"]) == 4


def test_reviewed_negative_reports_false_positive_without_disposition():
    result = replay_prospective_observations(SURFACE, baseline(), [observation(label="NEGATIVE")])
    assert result["metrics"]["false_positive_count"] == 1
    assert result["metrics"]["false_positive_rate"] == 1.0
    assert result["continuity_candidates"][0]["candidate_disposition"] is None


def test_replay_is_order_independent_and_deterministic():
    first = observation()
    second = copy.deepcopy(first)
    second["observation_id"] = "held-out-2"
    second["evidence_ids"] = ["e-held-out-2"]
    second["primitive_ids"] = ["p-held-out-2"]
    a = replay_prospective_observations(SURFACE, baseline(), [first, second])
    b = replay_prospective_observations(SURFACE, baseline(), [second, first])
    assert a["replay_digest"] == b["replay_digest"]


def test_surface_authority_drift_is_rejected():
    surface = copy.deepcopy(SURFACE)
    surface["consumer_enabled"] = True
    with pytest.raises(Psi0hProspectiveReplayError, match="SURFACE_AUTHORITY"):
        replay_prospective_observations(surface, baseline(), [observation()])


def test_replay_from_e5_cohort_artifact_structure(tmp_path):
    import scripts.run_psi0h_a_historical_replay as runner

    artifact = tmp_path / "psi0h_e5_cohort.json"
    artifact.write_text(json.dumps({
        "execution_status": "COMPLETED",
        "run_id": "psi0h-e5-real-prospective",
        "source_id": "pumpportal-migration-census",
        "source_kind": "migration-census-live-observation",
        "execution": {
            "qualification": {
                "cutoff": 100,
                "selected": [{
                    "primitive_id": "e5-primitive-1",
                    "primitive_type": "LAUNCH_SIGNER",
                    "window_start": 150,
                    "window_end": 150,
                    "generated_at": 151,
                    "evidence_ids": ["evidence-e5"],
                    "missing_inputs": [],
                }],
            },
        },
        "source_identity": {},
    }))

    result = runner.run(cohort_artifact=artifact)
    assert result["status"] in ("PASS", "HOLD")
    assert result["source"]["source"]["source_mode"] == "e5-real-cohort"
    assert result["source"]["e5_source"] == {}
