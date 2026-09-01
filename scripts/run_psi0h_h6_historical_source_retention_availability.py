#!/usr/bin/env python3
"""PSI0H-H6 historical source-retention availability and acquisition design runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h6_historical_source_retention_availability import (
    Psi0hH6HistoricalSourceRetentionAvailabilityError,
    qualify_historical_source_retention_availability,
    verify_historical_source_retention_availability,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    *, h5_artifact: str, evidence_root: str | None = None, output: str | None = None, maximum_sources: int = 400
) -> dict:
    artifact_path = Path(h5_artifact)
    if not artifact_path.is_file():
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_H5_ARTIFACT_MISSING")

    h5_data = _read_json(artifact_path)
    result = qualify_historical_source_retention_availability(
        h5_artifact=h5_data,
        evidence_root=evidence_root,
        maximum_sources=maximum_sources,
    )
    verify_historical_source_retention_availability(result)

    output_path = Path(output or "docs/audits/psi0h_h6_historical_source_retention_availability.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "reconstructable_operation_count": result["reconstructable_operation_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H6 historical source-retention availability and acquisition design."
    )
    parser.add_argument("--h5-artifact", required=True, help="Path to PSI0H-H5 artifact JSON.")
    parser.add_argument("--evidence-root", help="Optional root path for retained historical evidence dbs.")
    parser.add_argument("--output", default="docs/audits/psi0h_h6_historical_source_retention_availability.json")
    parser.add_argument("--maximum-sources", type=int, default=400)
    args = parser.parse_args()

    try:
        result = run(
            h5_artifact=args.h5_artifact,
            evidence_root=args.evidence_root,
            output=args.output,
            maximum_sources=args.maximum_sources,
        )
    except Psi0hH6HistoricalSourceRetentionAvailabilityError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
