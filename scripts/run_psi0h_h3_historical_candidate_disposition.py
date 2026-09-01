#!/usr/bin/env python3
"""PSI0H-H3 historical candidate-disposition runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h3_historical_candidate_disposition import (
    Psi0hH3HistoricalCandidateDispositionError,
    qualify_historical_candidate_disposition,
    verify_historical_candidate_disposition,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, h2_artifact: str, output: str | None = None) -> dict:
    h2_path = Path(h2_artifact)
    if not h2_path.is_file():
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_H2_ARTIFACT_MISSING")
    h2_data = _read_json(h2_path)
    result = qualify_historical_candidate_disposition(h2_artifact=h2_data)
    verify_historical_candidate_disposition(result)
    output_path = Path(output or "docs/audits/psi0h_h3_historical_candidate_disposition.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H3 historical candidate disposition.")
    parser.add_argument("--h2-artifact", required=True, help="Path to PSI0H-H2 artifact JSON.")
    parser.add_argument("--output", default="docs/audits/psi0h_h3_historical_candidate_disposition.json")
    args = parser.parse_args()

    try:
        result = run(h2_artifact=args.h2_artifact, output=args.output)
    except Psi0hH3HistoricalCandidateDispositionError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
