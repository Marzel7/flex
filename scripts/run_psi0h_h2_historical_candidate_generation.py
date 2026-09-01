#!/usr/bin/env python3
"""PSI0H-H2 historical candidate-generation runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from math import comb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h1_historical_discovery import DEFAULT_MANIFEST_PATH
from src.evidence.contracts.psi0h_h2_historical_candidate_generation import (
    Psi0hH2HistoricalCandidateGenerationError,
    qualify_historical_candidate_generation,
    verify_historical_candidate_generation,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, h1_artifact: str, manifest: str | None = None, output: str | None = None,
        maximum_candidates: int = 0) -> dict:
    h1_path = Path(h1_artifact)
    if not h1_path.is_file():
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ARTIFACT_MISSING")
    h1_data = _read_json(h1_path)
    resolved_manifest = manifest or str(DEFAULT_MANIFEST_PATH)
    if maximum_candidates <= 0:
        eligible = h1_data.get("eligible_operations") if isinstance(h1_data, dict) else []
        if not isinstance(eligible, list):
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ELIGIBLE_INVALID")
        n = len(eligible)
        maximum_candidates = comb(n, 2)
    result = qualify_historical_candidate_generation(
        h1_artifact=h1_data, manifest_path=resolved_manifest, maximum_candidates=maximum_candidates,
    )
    verify_historical_candidate_generation(result)
    output_path = Path(output or "docs/audits/psi0h_h2_historical_candidate_generation.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H2 historical candidate-generation.")
    parser.add_argument("--h1-artifact", required=True, help="Path to H1 eligibility artifact JSON.")
    parser.add_argument("--manifest", help="Override manifest path used to recover operation-level behaviour observations.")
    parser.add_argument("--output", default="docs/audits/psi0h_h2_historical_candidate_generation.json")
    parser.add_argument("--maximum-candidates", type=int, default=0, help="Optional hard pair limit; 0 uses full eligible-pair coverage.")
    args = parser.parse_args()

    try:
        result = run(
            h1_artifact=args.h1_artifact,
            manifest=args.manifest,
            output=args.output,
            maximum_candidates=args.maximum_candidates,
        )
    except Psi0hH2HistoricalCandidateGenerationError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
