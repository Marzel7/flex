#!/usr/bin/env python3
"""Run PSI0H-H9 historical backfill blocker-reconciliation boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h9_historical_backfill_blocker_reconciliation import (
    Psi0hH9HistoricalBackfillBlockerReconciliationError,
    qualify_h9_backfill_blocker_reconciliation,
    verify_h9_blocker_reconciliation,
)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_INPUT_NOT_OBJECT")
    return payload


def run(*, h7_artifact: str, h8_artifact: str, output: str | None = None) -> dict:
    h7_path = Path(h7_artifact)
    h8_path = Path(h8_artifact)
    if not h7_path.is_file():
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H7_ARTIFACT_MISSING")
    if not h8_path.is_file():
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H8_ARTIFACT_MISSING")

    h7_data = _read_json(h7_path)
    h8_data = _read_json(h8_path)
    result = qualify_h9_backfill_blocker_reconciliation(h7_artifact=h7_data, h8_artifact=h8_data)
    verify_h9_blocker_reconciliation(result)

    output_path = Path(output or "docs/audits/psi0h_h9_historical_backfill_blocker_reconciliation.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"PSI0H_H9_OUTPUT_EXISTS:{output_path}")
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
        "h8_primitive_rows": result["h8_primitive_rows"],
        "diagnostic_count": len(result["diagnostics"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PSI0H-H9 historical backfill blocker-reconciliation boundary."
    )
    parser.add_argument("--h7-artifact", required=True, help="Path to PSI0H-H7 artifact JSON.")
    parser.add_argument("--h8-artifact", required=True, help="Path to PSI0H-H8 artifact JSON.")
    parser.add_argument("--output", default="docs/audits/psi0h_h9_historical_backfill_blocker_reconciliation.json")
    args = parser.parse_args()

    try:
        result = run(h7_artifact=args.h7_artifact, h8_artifact=args.h8_artifact, output=args.output)
    except Psi0hH9HistoricalBackfillBlockerReconciliationError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
