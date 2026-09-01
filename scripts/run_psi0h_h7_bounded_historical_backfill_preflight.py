#!/usr/bin/env python3
"""PSI0H-H7 bounded historical backfill execution planning/preflight boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h7_bounded_historical_backfill_preflight import (
    Psi0hH7BoundedHistoricalBackfillPreflightError,
    qualify_historical_backfill_preflight,
    verify_historical_backfill_preflight,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    *, h6_artifact: str,
    output: str | None = None,
    maximum_sources: int = 40,
    cohort_max_rows: int = 500,
    source_max_bytes: int = 268435456,
    max_event_gap_seconds: int = 86400,
) -> dict:
    h6_path = Path(h6_artifact)
    if not h6_path.is_file():
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_H6_ARTIFACT_MISSING")

    h6_data = _read_json(h6_path)
    result = qualify_historical_backfill_preflight(
        h6_artifact=h6_data,
        maximum_sources=maximum_sources,
        cohort_max_rows=cohort_max_rows,
        source_max_bytes=source_max_bytes,
        max_event_gap_seconds=max_event_gap_seconds,
    )
    verify_historical_backfill_preflight(result)

    output_path = Path(output or "docs/audits/psi0h_h7_bounded_historical_backfill_preflight.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "candidate_count": result["source_plan"]["candidate_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build PSI0H-H7 bounded historical backfill preflight plan.",
    )
    parser.add_argument("--h6-artifact", required=True, help="Path to PSI0H-H6 output artifact JSON.")
    parser.add_argument("--output", default="docs/audits/psi0h_h7_bounded_historical_backfill_preflight.json")
    parser.add_argument("--maximum-sources", type=int, default=40)
    parser.add_argument("--cohort-max-rows", type=int, default=500)
    parser.add_argument("--source-max-bytes", type=int, default=268435456)
    parser.add_argument("--max-event-gap-seconds", type=int, default=86400)
    args = parser.parse_args()

    try:
        result = run(
            h6_artifact=args.h6_artifact,
            output=args.output,
            maximum_sources=args.maximum_sources,
            cohort_max_rows=args.cohort_max_rows,
            source_max_bytes=args.source_max_bytes,
            max_event_gap_seconds=args.max_event_gap_seconds,
        )
    except Psi0hH7BoundedHistoricalBackfillPreflightError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
