#!/usr/bin/env python3
"""PSI0H-H4 historical operation-population census runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h4_historical_operation_census import (
    Psi0hH4HistoricalOperationCensusError,
    qualify_historical_operation_census,
    verify_historical_operation_census,
)
from src.evidence.contracts.psi0h_h1_historical_discovery import DEFAULT_MANIFEST_PATH


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    *,
    h1_artifact: str,
    manifest: str | None = None,
    output: str | None = None,
    maximum_operations: int = 200,
) -> dict:
    h1_path = Path(h1_artifact)
    if not h1_path.is_file():
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_H1_ARTIFACT_MISSING")

    h1_data = _read_json(h1_path)
    resolved_manifest = manifest or str(DEFAULT_MANIFEST_PATH)
    result = qualify_historical_operation_census(
        h1_artifact=h1_data,
        manifest_path=resolved_manifest,
        maximum_operations=maximum_operations,
    )
    verify_historical_operation_census(result)

    output_path = Path(output or "docs/audits/psi0h_h4_historical_operation_census.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "operation_count": result["operation_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H4 historical operation census.")
    parser.add_argument("--h1-artifact", required=True, help="Path to PSI0H-H1 artifact JSON.")
    parser.add_argument(
        "--manifest",
        help="Optional manifest override path. Defaults to default PSI0G-B retained derivation manifest.",
    )
    parser.add_argument("--output", default="docs/audits/psi0h_h4_historical_operation_census.json")
    parser.add_argument("--maximum-operations", type=int, default=200)
    args = parser.parse_args()

    try:
        result = run(
            h1_artifact=args.h1_artifact,
            manifest=args.manifest,
            output=args.output,
            maximum_operations=args.maximum_operations,
        )
    except Psi0hH4HistoricalOperationCensusError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
