import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h7_bounded_historical_backfill_preflight import (
    VERDICT_HOLD_NOT_READY,
    VERDICT_READY_BOUNDED_BACKFILL,
    Psi0hH7BoundedHistoricalBackfillPreflightError,
    qualify_historical_backfill_preflight,
    verify_historical_backfill_preflight,
)


def _write_h6_artifact(tmp_path: Path, *, include_candidates: bool = False) -> Path:
    artifact = {
        "schema_version": "psi0h-h6.historical-source-retention-availability.v1",
        "status": "PASS",
        "verdict": "READY_BOUNDED_BACKFILL",
        "artifact_digest": "abcd" * 16,
        "source_inventory_rows": [],
    }
    if include_candidates:
        artifact["source_inventory_rows"] = [
            {
                "source_path": str(tmp_path / "hist1.db"),
                "source_identity": {
                    "device": 1,
                    "inode": 2,
                    "size_bytes": 3,
                    "mtime_ns": 4,
                },
                "evidence_rows": 12,
                "primitive_rows": 4,
                "provenance_links": 1,
                "has_temporal_windows": True,
                "has_topology_role_fields": False,
                "blocking_reasons": ["ADDRESS_LEVEL_MOTIFS_ONLY"],
            },
            {
                "source_path": str(tmp_path / "hist2.db"),
                "source_identity": {
                    "device": 5,
                    "inode": 6,
                    "size_bytes": 7,
                    "mtime_ns": 8,
                },
                "evidence_rows": 20,
                "primitive_rows": 2,
                "provenance_links": 3,
                "has_temporal_windows": True,
                "has_topology_role_fields": True,
                "blocking_reasons": [],
            },
        ]

    path = tmp_path / "h6.json"
    path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_h7_ready_bounded_backfill_plan(tmp_path):
    h6 = json.loads(_write_h6_artifact(tmp_path, include_candidates=True).read_text(encoding="utf-8"))
    result = qualify_historical_backfill_preflight(
        h6_artifact=h6,
        maximum_sources=10,
        cohort_max_rows=500,
        source_max_bytes=128,
        max_event_gap_seconds=3600,
    )
    assert result["status"] == "PASS"
    assert result["verdict"] == VERDICT_READY_BOUNDED_BACKFILL
    assert result["boundaries"]["max_rows_per_source"] > 0
    assert result["source_plan"]["candidate_count"] == 2
    assert result["source_plan"]["reconstructable_source_count"] == 1
    assert result["source_plan"]["legacy_source_count"] == 1
    assert len(result["source_plan"]["candidate_sources"]) == 1
    assert isinstance(result["source_plan"]["candidate_sources"], list)
    for row in result["source_plan"]["candidate_sources"]:
        assert row["row_reconstruction_ceiling"] <= 500
        assert row["row_reconstruction_ceiling"] >= 0
        assert row["reconstructable"]
    legacy_rows = result["source_plan"]["legacy_candidate_sources"]
    assert len(legacy_rows) == 1
    assert not legacy_rows[0]["reconstructable"]
    verify_historical_backfill_preflight(result)


def test_h7_hold_when_no_candidates(tmp_path):
    h6 = json.loads(_write_h6_artifact(tmp_path).read_text(encoding="utf-8"))
    result = qualify_historical_backfill_preflight(h6_artifact=h6)
    assert result["verdict"] == VERDICT_HOLD_NOT_READY
    assert result["source_plan"]["candidate_count"] == 0
    assert "NO_H6_BACKFILL_BOUNDARY" in result["blockers"]
    verify_historical_backfill_preflight(result)


def test_h7_hold_when_candidates_legacy_only(tmp_path):
    h6 = json.loads(_write_h6_artifact(tmp_path, include_candidates=True).read_text(encoding="utf-8"))
    # make all candidates legacy-only by removing reconstructable conditions
    for row in h6["source_inventory_rows"]:
        row["blocking_reasons"] = ["ADDRESS_LEVEL_MOTIFS_ONLY"]
        row["has_topology_role_fields"] = False
        row["provenance_links"] = 0
    result = qualify_historical_backfill_preflight(h6_artifact=h6)
    assert result["verdict"] == VERDICT_HOLD_NOT_READY
    assert result["source_plan"]["reconstructable_source_count"] == 0
    assert result["source_plan"]["legacy_source_count"] == 2
    assert result["source_plan"]["candidate_sources"] == []
    assert len(result["source_plan"]["legacy_candidate_sources"]) == 2
    verify_historical_backfill_preflight(result)


def test_h7_requires_ready_bounded_backfill_verdict():
    with pytest.raises(Psi0hH7BoundedHistoricalBackfillPreflightError, match="PSI0H_H7_H6_VERDICT_NOT_READY_BOUNDED_BACKFILL"):
        qualify_historical_backfill_preflight(
            h6_artifact={
                "schema_version": "psi0h-h6.historical-source-retention-availability.v1",
                "verdict": "BLOCKED_SOURCE_ABSENT",
            }
        )


def test_h7_runner_writes_artifact(tmp_path):
    from scripts.run_psi0h_h7_bounded_historical_backfill_preflight import run

    h6_path = _write_h6_artifact(tmp_path, include_candidates=True)
    out = run(
        h6_artifact=str(h6_path),
        output=str(tmp_path / "h7.json"),
        maximum_sources=10,
        cohort_max_rows=250,
    )
    artifact = json.loads((tmp_path / "h7.json").read_text(encoding="utf-8"))
    assert out["artifact"] == str(tmp_path / "h7.json")
    assert artifact["artifact_digest"] == out["artifact_digest"]
    assert artifact["schema_version"] == "psi0h-h7.bounded-historical-backfill-preflight.v1"
    verify_historical_backfill_preflight(artifact)
