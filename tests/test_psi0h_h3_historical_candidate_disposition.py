import json

import pytest

from src.evidence.contracts.psi0h_h3_historical_candidate_disposition import (
    Psi0hH3HistoricalCandidateDispositionError,
    qualify_historical_candidate_disposition,
    verify_historical_candidate_disposition,
)


def sample_h2():
    return {
        "schema_version": "psi0h-h2.historical-candidate-generation.v1",
        "status": "PASS",
        "artifact_digest": "a" * 64,
        "candidate_rows": [
            {
                "continuity_candidate_id": "abc123",
                "operation_ids": ["watchtower", "three_sw2"],
                "relationship": "insufficient_evidence",
                "identity_guarded": True,
            },
            {
                "continuity_candidate_id": "def456",
                "operation_ids": ["watchtower", "three_sw2"],
                "relationship": "shared_behaviour",
                "identity_guarded": True,
                "continuity_evidence": ["shared"],
            },
        ],
    }


def test_h3_disposition_for_h2_candidates():
    result = qualify_historical_candidate_disposition(h2_artifact=sample_h2())
    assert result["status"] == "PASS"
    assert result["reviewed_count"] == 2
    assert {row["human_disposition"]["provisional_disposition"] for row in result["reviewed_rows"]} == {
        "insufficient_evidence",
        "review_required",
    }
    assert result["disposition_counts"]["insufficient_evidence"] == 1
    assert result["disposition_counts"]["review_required"] == 1
    verify_historical_candidate_disposition(result)


def test_h3_rejects_invalid_binding():
    with pytest.raises(Psi0hH3HistoricalCandidateDispositionError):
        qualify_historical_candidate_disposition(h2_artifact={})


def test_h3_runner_output(tmp_path):
    h2 = sample_h2()
    path = tmp_path / "h2.json"
    path.write_text(json.dumps(h2), encoding="utf-8")
    out_path = tmp_path / "out.json"
    from scripts.run_psi0h_h3_historical_candidate_disposition import run

    out = run(h2_artifact=str(path), output=str(out_path))
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert out["artifact"] == str(out_path)
    assert loaded["artifact_digest"] == out["artifact_digest"]
    verify_historical_candidate_disposition(loaded)
