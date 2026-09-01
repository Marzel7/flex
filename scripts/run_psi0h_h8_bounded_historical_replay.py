#!/usr/bin/env python3
"""Run PSI0H-H8 replay boundary against a retained H8 execution artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h8_bounded_historical_replay import (
    Psi0hH8BoundedReplayBoundaryError,
    qualify_h8_bounded_replay_boundary,
    verify_h8_bounded_replay_boundary,
)

ROOT = Path(__file__).resolve().parents[1]
D5 = ROOT / "docs/audits/psi0g_runs/psi0g-d5-real-provenance-retention-20260817-02/projection.json"
D8 = ROOT / "docs/audits/psi0g_runs/psi0g-d8-first-real-provenance-surface-20260817-01/surface.json"


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.loads(handle.read())
    if not isinstance(payload, dict):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_ARTIFACT_INVALID")
    return payload


def run(*, h8_artifact: str, output: str | None = None) -> dict:
    h8_path = Path(h8_artifact)
    if not h8_path.is_file():
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_H8_ARTIFACT_MISSING")

    h8_payload = _read_json(h8_path)
    d5_projection = _read_json(D5)
    d8_surface = _read_json(D8)
    result = qualify_h8_bounded_replay_boundary(
        h8_artifact=h8_payload,
        d5_projection=d5_projection,
        d8_surface=d8_surface,
    )
    verify_h8_bounded_replay_boundary(result)

    output_path = Path(output or "docs/audits/psi0h_h8_replay_boundary.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"PSI0H_H8_REPLAY_BOUNDARY_OUTPUT_EXISTS:{output_path}")
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result.get("verdict"),
        "observation_count": result.get("observation_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H8 bounded historical replay boundary.")
    parser.add_argument("--h8-artifact", required=True, help="Path to PSI0H-H8 execution artifact JSON.")
    parser.add_argument("--output", default=None, help="Optional output artifact path.")
    args = parser.parse_args()

    try:
        result = run(h8_artifact=args.h8_artifact, output=args.output)
    except Psi0hH8BoundedReplayBoundaryError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
