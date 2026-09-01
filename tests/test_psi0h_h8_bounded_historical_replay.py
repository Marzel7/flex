import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h8_bounded_historical_replay import (
    Psi0hH8BoundedReplayBoundaryError,
    qualify_h8_bounded_replay_boundary,
    verify_h8_bounded_replay_boundary,
)


ROOT = Path(__file__).resolve().parents[1]
D5 = ROOT / "docs/audits/psi0g_runs/psi0g-d5-real-provenance-retention-20260817-02/projection.json"
D8 = ROOT / "docs/audits/psi0g_runs/psi0g-d8-first-real-provenance-surface-20260817-01/surface.json"


def _load_artifact(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.loads(handle.read())
    if not isinstance(payload, dict):
        raise ValueError("artifact malformed")
    return payload


def test_replay_boundary_empty_primitive_pool_holds():
    h8 = {
        "schema_version": "psi0h-h8.bounded-historical-backfill-execution.v1",
        "status": "PASS",
        "milestone": "PSI0H-H8",
        "execution_status": "COMPLETED",
        "execution": {"primitive_rows": []},
        "artifact_digest": "0" * 64,
        "output_digest_bindings": {"artifact_path": "docs/audits/x.json"},
    }
    result = qualify_h8_bounded_replay_boundary(
        h8_artifact=h8,
        d8_surface=_load_artifact(D8),
        d5_projection=_load_artifact(D5),
    )
    assert result["status"] == "HOLD"
    assert result["verdict"] == "H8_REPLAY_BOUNDARY_EMPTY_PRIMITIVE_POOL"
    verify_h8_bounded_replay_boundary(result)


def test_replay_boundary_requires_compatible_d5_projection():
    with pytest.raises(Psi0hH8BoundedReplayBoundaryError, match="PSI0H_H8_REPLAY_BOUNDARY_D5_PROJECTION_INVALID"):
        qualify_h8_bounded_replay_boundary(
            h8_artifact={
                "schema_version": "psi0h-h8.bounded-historical-backfill-execution.v1",
                "status": "PASS",
                "milestone": "PSI0H-H8",
                "execution_status": "COMPLETED",
                "execution": {"primitive_rows": []},
            },
            d8_surface=_load_artifact(D8),
            d5_projection={"candidate": None},
        )


def test_h8_replay_runner_writes_artifact(tmp_path):
    from scripts.run_psi0h_h8_bounded_historical_replay import run

    h8 = tmp_path / "h8.json"
    h8.write_text(json.dumps({
        "schema_version": "psi0h-h8.bounded-historical-backfill-execution.v1",
        "milestone": "PSI0H-H8",
        "status": "PASS",
        "execution_status": "COMPLETED",
        "execution": {"primitive_rows": []},
        "artifact_digest": "0" * 64,
        "output_digest_bindings": {"artifact_path": "x"},
    }))
    out = run(h8_artifact=str(h8), output=str(tmp_path / "replay.json"))
    assert out["artifact"] == str(tmp_path / "replay.json")
    assert out["verdict"] == "H8_REPLAY_BOUNDARY_EMPTY_PRIMITIVE_POOL"
    result = json.loads((tmp_path / "replay.json").read_text(encoding="utf-8"))
    assert result["artifact_digest"] == out["artifact_digest"]
    assert result["status"] == "HOLD"
