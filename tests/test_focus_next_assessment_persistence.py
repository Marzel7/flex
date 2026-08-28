import json
from pathlib import Path

from src.ops.potential_operations import (
    FOCUS_NEXT_ASSESSMENT,
    _decorate,
    _persisted_assessment,
    assessment_digest,
    replay_focus_next_assessment,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_ID = "p3r-v2-dc4953db7adb853337c4"


def test_frozen_assessment_replays_to_the_persisted_semantic_digest():
    persisted = json.loads(FOCUS_NEXT_ASSESSMENT.read_text())
    replayed = replay_focus_next_assessment()
    assert persisted["candidate_id"] == CANDIDATE_ID
    assert persisted["assessment_digest"] == assessment_digest(persisted)
    assert replayed["assessment_digest"] == persisted["assessment_digest"]
    assert replayed["exact_cohort"] == {"members": 27, "observable": 27, "unobservable": 0, "distinct_creators": 27, "distinct_direct_funders": 27, "first_observed": 1783571264, "latest_observed": 1787651587}
    assert replayed["current_census"] == {"matched_routes_total": 33, "matched_routes_24h": 2, "matched_routes_7d": 14, "matched_routes_30d": 30, "selected_edge_observations_24h": 16, "selected_edge_observations_7d": 112, "selected_edge_observations_30d": 240, "selected_edge_observations_total": 264}


def test_assessment_loader_is_idempotent_and_read_side_only():
    before = FOCUS_NEXT_ASSESSMENT.stat().st_mtime_ns
    first = _persisted_assessment(CANDIDATE_ID)
    second = _persisted_assessment(CANDIDATE_ID)
    assert first == second
    assert first["assessment_digest"] == replay_focus_next_assessment()["assessment_digest"]
    assert FOCUS_NEXT_ASSESSMENT.stat().st_mtime_ns == before
    row = _decorate({"candidate_id": CANDIDATE_ID, "workflow_status": "QUEUED", "priority_rank": 1})
    assert row["workflow_status"] == "QUEUED"
    assert row["relationship_label"] == "Distinct Potential Operation"
    assert row["assessment_display"]["recommendation"] == "Deep review recommended"


def test_templates_obtain_assessment_values_from_the_read_projection():
    page = (ROOT / "templates/potential_operations.html").read_text()
    detail = (ROOT / "templates/potential_operation_detail.html").read_text()
    for literal in ("DISTINCT_POTENTIAL_OPERATION", "ADVANCE_TO_DEEP_REVIEW", "Strong coherence"):
        assert literal not in page
        assert literal not in detail
    assert "next.assessment_display.classification" in page
    assert "next.assessment_display.recommendation" in page
    assert "candidate.assessment_display.classification" in detail


def test_route_activity_contract_is_explicit_in_projection_and_detail():
    row = _decorate({"candidate_id": CANDIDATE_ID, "workflow_status": "QUEUED", "priority_rank": 1})
    assert row["assessment"]["activity_metric_contract"]["primary_unit"] == "MATCHED_ROUTES"
    assert row["assessment"]["current_census"]["matched_routes_24h"] == 2
    detail = (ROOT / "templates/potential_operation_detail.html").read_text()
    assert "Matched routes · 24h / 7d / 30d" in detail
    assert "selected-edge timestamp observations" in detail
