#!/usr/bin/env python3
"""PSI0H-H1 historical discovery eligibility runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h1_historical_discovery import (
    DEFAULT_MANIFEST_PATH,
    Psi0hH1HistoricalDiscoveryError,
    build_historical_discovery_eligibility,
    verify_historical_discovery,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, manifest: str | None = None, output: str | None = None, maximum_operations: int = 20) -> dict:
    manifest_path = Path(manifest or str(DEFAULT_MANIFEST_PATH))
    if not manifest_path.is_file():
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_MANIFEST_MISSING")
    payload = _read_json(manifest_path)
    result = build_historical_discovery_eligibility(manifest=payload, maximum_operations=maximum_operations)
    verify_historical_discovery(result)

    output_path = Path(output or "docs/audits/psi0h_h1_historical_discovery.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "artifact": str(output_path),
        "artifact_digest": result["artifact_digest"],
        "status": result["status"],
        "verdict": result["verdict"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H1 historical discovery eligibility.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to PSI0G-B retained derivation manifest JSON.")
    parser.add_argument("--output", default="docs/audits/psi0h_h1_historical_discovery.json", help="Artifact output path.")
    parser.add_argument("--maximum-operations", type=int, default=20)
    args = parser.parse_args()
    try:
        result = run(manifest=args.manifest, output=args.output, maximum_operations=args.maximum_operations)
    except Psi0hH1HistoricalDiscoveryError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
