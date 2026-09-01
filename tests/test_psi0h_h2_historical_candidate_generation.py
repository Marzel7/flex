import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h2_historical_candidate_generation import (
    Psi0hH2HistoricalCandidateGenerationError,
    qualify_historical_candidate_generation,
    verify_historical_candidate_generation,
)


def test_h2_generates_continuity_candidate_classification():
    h1 = {
        "artifact_digest": "a" * 64,
        "manifest_source_path": "docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01/manifest.json",
        "manifest_digest": "b" * 64,
        "eligible_operations": [
            {
                "operation_id": "watchtower",
                "evidence_count": 107941,
                "primitive_count": 85989,
                "supporting_candidate_count": 14203,
                "source_path": "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db",
                "source_access": "sqlite_uri_mode_ro_and_query_only",
                "source_identity": {"device": 16777234, "inode": 1, "size_bytes": 1, "mtime_ns": 2},
            },
            {
                "operation_id": "three_sw2",
                "evidence_count": 1000,
                "primitive_count": 858,
                "supporting_candidate_count": 94,
                "source_path": "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db",
                "source_access": "sqlite_uri_mode_ro_and_query_only",
                "source_identity": {"device": 16777234, "inode": 2, "size_bytes": 1, "mtime_ns": 3},
            },
        ],
    }
    result = qualify_historical_candidate_generation(h1_artifact=h1, manifest_path=Path(__file__).resolve().parents[1] / "docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01/manifest.json")
    assert result["status"] == "PASS"
    assert result["pair_count"] == 1
    row = result["candidate_rows"][0]
    assert row["relationship"] == "insufficient_evidence"
    assert row["shared_behaviour_observation_count"] == 0
    assert row["identity_guarded"] is True
    assert row["same_operation_claim"] is False
    assert result["authority"]["candidate_disposition"] is False
    verify_historical_candidate_generation(result)


def test_h2_holds_when_no_eligible_operations():
    with pytest.raises(Psi0hH2HistoricalCandidateGenerationError, match="NO_ELIGIBLE_OPERATIONS"):
        qualify_historical_candidate_generation(
            h1_artifact={
                "artifact_digest": "a" * 64,
                "eligible_operations": [],
            },
            manifest_path="docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01/manifest.json",
        )


def test_h2_rejects_bad_manifest_binding():
    h1 = {
        "artifact_digest": "a" * 64,
        "manifest_source_path": "nonexistent.json",
        "manifest_digest": "b" * 64,
        "eligible_operations": [
            {
                "operation_id": "watchtower",
                "evidence_count": 1,
                "primitive_count": 1,
                "supporting_candidate_count": 1,
                "source_path": "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db",
                "source_access": "sqlite_uri_mode_ro_and_query_only",
                "source_identity": {"device": 1, "inode": 2, "size_bytes": 3, "mtime_ns": 4},
            },
        ],
    }
    with pytest.raises(Psi0hH2HistoricalCandidateGenerationError, match="MANIFEST_MISSING"):
        qualify_historical_candidate_generation(h1_artifact=h1)


def test_h2_script_output_path_and_digest_consistency(tmp_path):
    fixture_h1 = {
        "artifact_digest": "a" * 64,
        "manifest_source_path": "docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01/manifest.json",
        "manifest_digest": "b" * 64,
        "eligible_operations": [
            {
                "operation_id": "watchtower",
                "evidence_count": 1,
                "primitive_count": 1,
                "supporting_candidate_count": 1,
                "source_path": "x",
                "source_access": "sqlite_uri_mode_ro_and_query_only",
                "source_identity": {"device": 1, "inode": 2, "size_bytes": 3, "mtime_ns": 4},
            },
            {
                "operation_id": "three_sw2",
                "evidence_count": 1,
                "primitive_count": 1,
                "supporting_candidate_count": 1,
                "source_path": "y",
                "source_access": "sqlite_uri_mode_ro_and_query_only",
                "source_identity": {"device": 1, "inode": 3, "size_bytes": 3, "mtime_ns": 5},
            },
        ],
    }
    h1_path = tmp_path / "h1.json"
    h1_path.write_text(json.dumps(fixture_h1), encoding="utf-8")
    output = tmp_path / "out.json"

    from scripts.run_psi0h_h2_historical_candidate_generation import run

    out = run(
        h1_artifact=str(h1_path),
        manifest="docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01/manifest.json",
        output=str(output),
        maximum_candidates=100,
    )
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert out["artifact"] == str(output)
    assert out["status"] in {"PASS", "HOLD"}
    assert loaded["artifact_digest"] == out["artifact_digest"]
    verify_historical_candidate_generation(loaded)
