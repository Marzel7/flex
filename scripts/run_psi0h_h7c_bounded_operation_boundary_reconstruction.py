#!/usr/bin/env python3
"""Run PSI0H-H7C bounded operation-boundary evidence reconstruction boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h7c_bounded_operation_boundary_reconstruction import (
    Psi0hH7COperationBoundaryReconstructionError,
    qualify_h7c_operation_boundary_reconstruction,
    verify_h7c_operation_boundary_reconstruction,
)


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Psi0hH7COperationBoundaryReconstructionError("PSI0H_H7C_INPUT_NOT_OBJECT")
    return payload


def run(
    *,
    h7r_artifact: str,
    output: str | None = None,
    maximum_candidates: int = 7,
    row_ceiling: int = 200,
) -> dict:
    source = Path(h7r_artifact)
    if not source.is_file():
        raise Psi0hH7COperationBoundaryReconstructionError("PSI0H_H7C_H7R_ARTIFACT_MISSING")

    h7r_data = _read_json(source)
    artifact_path = Path(output) if output else Path(
        "docs/audits/psi0h_h7c_bounded_operation_boundary_reconstruction.json"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if artifact_path.exists():
        raise FileExistsError(f"PSI0H_H7C_OUTPUT_EXISTS:{artifact_path}")

    artifact = qualify_h7c_operation_boundary_reconstruction(
        h7r_artifact=h7r_data,
        maximum_candidates=maximum_candidates,
        row_ceiling_default=row_ceiling,
        destination=artifact_path.parent,
    )
    verify_h7c_operation_boundary_reconstruction(artifact)

    artifact_path.write_text(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    return {
        "artifact": str(artifact_path),
        "artifact_digest": artifact["artifact_digest"],
        "status": artifact["status"],
        "verdict": artifact["verdict"],
        "reconstructable_source_count": artifact["reconstructable_source_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H7C bounded operation-boundary evidence reconstruction."
    )
    parser.add_argument("--h7r-artifact", required=True, help="Path to PSI0H-H7R artifact JSON.")
    parser.add_argument(
        "--output",
        default="docs/audits/psi0h_h7c_bounded_operation_boundary_reconstruction.json",
    )
    parser.add_argument(
        "--maximum-candidates",
        type=int,
        default=7,
        help="Maximum number of H7R diagnostics to inspect.",
    )
    parser.add_argument(
        "--row-ceiling",
        type=int,
        default=200,
        help="Default row ceiling per source during boundary reconstruction.",
    )

    args = parser.parse_args()

    try:
        result = run(
            h7r_artifact=args.h7r_artifact,
            output=args.output,
            maximum_candidates=args.maximum_candidates,
            row_ceiling=args.row_ceiling,
        )
    except Psi0hH7COperationBoundaryReconstructionError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
