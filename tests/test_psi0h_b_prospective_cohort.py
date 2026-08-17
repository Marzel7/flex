import copy

import pytest

from src.evidence.contracts.psi0h_prospective_cohort import (
    Psi0hProspectiveCohortError,
    freeze_prospective_observation_cohort,
)


def row(identifier="p-new", *, start=101, observed=101):
    return {
        "primitive_id": identifier, "primitive_type": "LAUNCH_SIGNER",
        "observation_window": {"start": start, "end": start + 1},
        "generated_at": start + 2,
        "evidence": [{"evidence_id": f"e-{identifier}", "observed_at": observed}],
    }


def freeze(rows, **overrides):
    values = {
        "cutoff": 100, "baseline_observation_ids": ["o-old"],
        "baseline_evidence_ids": ["e-old"], "baseline_primitive_ids": ["p-old"],
        "primitive_rows": rows,
    }
    values.update(overrides)
    return freeze_prospective_observation_cohort(**values)


def test_strictly_post_cutoff_disjoint_unit_is_frozen_without_comparison():
    result = freeze([row()])
    assert result["status"] == "PASS" and result["selected_count"] == 1
    assert not result["comparison_performed"] and not any(result["authority"].values())


def test_late_acquisition_of_old_window_is_not_fresh_observation():
    result = freeze([row(start=90, observed=101)])
    assert result["status"] == "HOLD" and result["selected"] == []
    assert result["rejection_counts"]["WINDOW_NOT_STRICTLY_POST_CUTOFF"] == 1


def test_old_evidence_or_primitive_identity_is_rejected():
    value = row(identifier="p-old")
    value["evidence"] = [{"evidence_id": "e-old", "observed_at": 101}]
    result = freeze([value])
    assert result["status"] == "HOLD"
    assert result["rejection_counts"]["PRIMITIVE_IN_BASELINE"] == 1
    assert result["rejection_counts"]["EVIDENCE_IN_BASELINE"] == 1


def test_selection_is_bounded_and_order_independent():
    rows = [row(f"p-{number}", start=101 + number, observed=101 + number) for number in range(3)]
    first = freeze(rows, maximum=2)
    second = freeze(list(reversed(rows)), maximum=2)
    assert first["cohort_digest"] == second["cohort_digest"]
    assert first["replay_digest"] == second["replay_digest"]
    assert first["selected_count"] == 2 and first["eligible_count"] == 3


def test_invalid_or_duplicate_evidence_fails_closed():
    value = row()
    value["evidence"] *= 2
    with pytest.raises(Psi0hProspectiveCohortError, match="EVIDENCE_INVALID"):
        freeze([value])
