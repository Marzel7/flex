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
    assert replayed["current_census"] == {"matches": 33, "activity": [16, 112, 240], "total_observations": 264}


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
