#!/usr/bin/env python3
"""PSI0H-H7B bounded historical reconstruction capture runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h7b_bounded_historical_reconstruction_capture import (
    _digest,
    Psi0hH7BBoundedHistoricalReconstructionCaptureError,
    qualify_h7b_reconstruction_capture,
    verify_h7b_reconstruction_capture,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    *,
    h7r_artifact: str,
    output: str | None = None,
    maximum_candidates: int = 7,
    row_ceiling: int = 200,
) -> dict:
    h7r_path = Path(h7r_artifact)
    if not h7r_path.is_file():
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_H7R_ARTIFACT_MISSING")

    h7r_data = _read_json(h7r_path)
    artifact_path = Path(output) if output else Path("docs/audits/psi0h_h7b_bounded_historical_reconstruction_capture.json")
    result = qualify_h7b_reconstruction_capture(
        h7r_artifact=h7r_data,
        maximum_candidates=maximum_candidates,
        row_ceiling_default=row_ceiling,
        destination=artifact_path.parent,
    )
    result["artifact_path"] = str(artifact_path)
    from src.evidence.contracts.psi0h_h7b_bounded_historical_reconstruction_capture import _digest

    result["artifact_digest"] = _digest({k: v for k, v in result.items() if k != "artifact_digest"})
    verify_h7b_reconstruction_capture(result)

    artifact_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(artifact_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "reconstructable_source_count": result["reconstructable_source_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H7B bounded historical reconstruction capture.",
    )
    parser.add_argument("--h7r-artifact", required=True, help="Path to PSI0H-H7R artifact JSON.")
    parser.add_argument("--output", default="docs/audits/psi0h_h7b_bounded_historical_reconstruction_capture.json")
    parser.add_argument("--maximum-candidates", type=int, default=7, help="Maximum number of H7R candidates to attempt.")
    parser.add_argument("--row-ceiling", type=int, default=200, help="Default row ceiling per source.")
    args = parser.parse_args()

    try:
        result = run(
            h7r_artifact=args.h7r_artifact,
            output=args.output,
            maximum_candidates=args.maximum_candidates,
            row_ceiling=args.row_ceiling,
        )
    except Psi0hH7BBoundedHistoricalReconstructionCaptureError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
