#!/usr/bin/env python3
"""PSI0H-H8 bounded historical backfill execution runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h8_bounded_historical_backfill_execution import (
    Psi0hH8BoundedHistoricalBackfillExecutionError,
    verify_h8_backfill_execution,
    qualify_h8_backfill_execution,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, h7_artifact: str, output: str | None = None) -> dict:
    h7_path = Path(h7_artifact)
    if not h7_path.is_file():
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_ARTIFACT_MISSING")

    h7_data = _read_json(h7_path)
    if output is None:
        destination_root = h7_data.get("destination", {}).get("destination_root", "docs/audits")
        output = str(Path(destination_root) / "psi0h_h8_bounded_historical_backfill_execution.json")
    result = qualify_h8_backfill_execution(h7_artifact_path=h7_path, output_artifact_path=output or "docs/audits/psi0h_h8_bounded_historical_backfill_execution.json")
    verify_h8_backfill_execution(result)

    output_path = Path(output or "docs/audits/psi0h_h8_bounded_historical_backfill_execution.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "execution_status": result["execution_status"],
        "verdict": result["status"],
        "evidence_count": result["execution"].get("evidence_count"),
        "primitive_count": result["execution"].get("primitive_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H8 bounded historical backfill execution."
    )
    parser.add_argument("--h7-artifact", required=True, help="Path to PSI0H-H7 artifact JSON.")
    parser.add_argument("--output", default=None, help="Optional output artifact path (defaults to H7 destination root).")

    args = parser.parse_args()

    try:
        result = run(h7_artifact=args.h7_artifact, output=args.output)
    except Psi0hH8BoundedHistoricalBackfillExecutionError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
