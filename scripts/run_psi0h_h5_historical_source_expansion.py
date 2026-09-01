"""PSI0H-H5 historical source-expansion reconciliation runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h5_historical_source_expansion import (
    Psi0hH5HistoricalSourceExpansionError,
    qualify_historical_source_expansion,
    verify_historical_source_expansion,
)
from src.evidence.contracts.psi0h_h4_historical_operation_census import verify_historical_operation_census


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    *,
    h4_artifact: str,
    evidence_root: str | None = None,
    output: str | None = None,
    maximum_sources: int = 500,
) -> dict:
    h4_path = Path(h4_artifact)
    if not h4_path.is_file():
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_H4_ARTIFACT_MISSING")

    h4_data = _read_json(h4_path)
    verify_historical_operation_census(h4_data)
    result = qualify_historical_source_expansion(
        h4_artifact=h4_data,
        evidence_root=evidence_root,
        maximum_sources=maximum_sources,
    )
    verify_historical_source_expansion(result)

    output_path = Path(output or "docs/audits/psi0h_h5_source_expansion_reconciliation.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "expanded_population_count": result["reconstructed_additional_operation_population_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H5 historical source-expansion reconciliation."
    )
    parser.add_argument("--h4-artifact", required=True, help="Path to PSI0H-H4 artifact JSON.")
    parser.add_argument("--evidence-root", help="Optional root path for retained evidence stores.")
    parser.add_argument("--output", default="docs/audits/psi0h_h5_source_expansion_reconciliation.json")
    parser.add_argument("--maximum-sources", type=int, default=500)
    args = parser.parse_args()

    try:
        result = run(
            h4_artifact=args.h4_artifact,
            evidence_root=args.evidence_root,
            output=args.output,
            maximum_sources=args.maximum_sources,
        )
    except Psi0hH5HistoricalSourceExpansionError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
