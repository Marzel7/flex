#!/usr/bin/env python3
"""Run PSI0H-H7R legacy candidate-source reconstruction reconciliation boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h7r_legacy_candidate_reconstruction_reconciliation import (
    Psi0hH7RLegacyCandidateReconstructionReconciliationError,
    qualify_legacy_candidate_reconstruction_reconciliation,
    verify_h7r_reconciliation,
)


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_INPUT_NOT_OBJECT")
    return payload


def run(*, h7_artifact: str, output: str | None = None) -> dict:
    h7_path = Path(h7_artifact)
    if not h7_path.is_file():
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_H7_ARTIFACT_MISSING")

    h7_data = _read_json(h7_path)
    result = qualify_legacy_candidate_reconstruction_reconciliation(h7_artifact=h7_data)
    verify_h7r_reconciliation(result)

    output_path = Path(output or "docs/audits/psi0h_h7r_legacy_candidate_reconstruction_reconciliation.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"PSI0H_H7R_OUTPUT_EXISTS:{output_path}")
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "legacy_source_count": result["legacy_source_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H7R legacy candidate reconstruction reconciliation boundary."
    )
    parser.add_argument("--h7-artifact", required=True, help="Path to PSI0H-H7 preflight artifact JSON")
    parser.add_argument(
        "--output",
        default="docs/audits/psi0h_h7r_legacy_candidate_reconstruction_reconciliation.json",
    )

    args = parser.parse_args()
    try:
        result = run(h7_artifact=args.h7_artifact, output=args.output)
    except Psi0hH7RLegacyCandidateReconstructionReconciliationError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
