#!/usr/bin/env python3
"""Run PSI0H-H7D historical funding corpus source mapping boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h7d_historical_funding_corpus_source_mapping import (
    Psi0hH7DHistoricalFundingCorpusSourceMappingError,
    qualify_historical_funding_corpus_source_mapping,
    verify_h7d_source_mapping,
)


def _parse_sources(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def run(
    *,
    funding_dbs: list[str],
    output: str | None = None,
    max_rows_per_source: int = 250000,
) -> dict:
    if not funding_dbs:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_SOURCE_LIST_MISSING")

    artifact_path = Path(output) if output else Path("docs/audits/psi0h_h7d_historical_funding_corpus_source_mapping.json")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if artifact_path.exists():
        raise FileExistsError(f"H7D_OUTPUT_EXISTS:{artifact_path}")

    artifact = qualify_historical_funding_corpus_source_mapping(
        funding_sources=funding_dbs,
        maximum_rows_per_source=max_rows_per_source,
    )
    verify_h7d_source_mapping(artifact)
    artifact_path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(artifact_path),
        "artifact_digest": artifact["artifact_digest"],
        "status": artifact["status"],
        "verdict": artifact["verdict"],
        "reconstructable_source_count": artifact["source_inventory"]["reconstructable_source_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H7D historical funding corpus source mapping.")
    parser.add_argument(
        "--funding-dbs",
        required=False,
        help="Comma-separated list of SQLite DB paths to inspect for historical funding corpus.",
    )
    parser.add_argument(
        "--funding-db",
        action="append",
        dest="funding_db",
        help="Add one funding corpus SQLite path (repeatable).",
    )
    parser.add_argument(
        "--output",
        default="docs/audits/psi0h_h7d_historical_funding_corpus_source_mapping.json",
        help="Output artifact path.",
    )
    parser.add_argument(
        "--max-rows-per-source",
        type=int,
        default=250000,
        help="Per-source row ceiling for exploratory statistics and bounded scan.",
    )

    args = parser.parse_args()
    sources: list[str] = []
    if args.funding_db:
        sources.extend(args.funding_db)
    sources.extend(_parse_sources(args.funding_dbs))

    if not sources:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_SOURCE_LIST_MISSING")

    try:
        result = run(
            funding_dbs=sources,
            output=args.output,
            max_rows_per_source=args.max_rows_per_source,
        )
    except Psi0hH7DHistoricalFundingCorpusSourceMappingError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
