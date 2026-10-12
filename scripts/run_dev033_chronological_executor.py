#!/usr/bin/env python3
"""Explicit manual entrypoint for the bounded DEV033 evidence session."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.watchtower_dev033_executor import execute, load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen DEV033 one-minute evidence manifest.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--execute-authorized", action="store_true")
    arguments = parser.parse_args()
    if not arguments.execute_authorized:
        manifest = load_manifest(arguments.manifest)
        print(json.dumps({"status": "VERIFIED_NOT_EXECUTED", "manifest_content_hash": manifest["content_hash"], "record_count": len(manifest["records"])}, sort_keys=True))
        return 0
    journal = execute(
        manifest_path=arguments.manifest, journal_path=arguments.journal,
        budget_root=arguments.budget_root, env_path=arguments.env, execution_authorized=True,
    )
    print(json.dumps({"status": "EXECUTION_FINISHED", "terminal_records": sum(record["state"] in {"ORDERED_CONTIGUOUS", "ORDERED_SPARSE", "INSUFFICIENT_EVIDENCE", "OUTCOME_UNKNOWN"} for record in journal["records"].values())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
