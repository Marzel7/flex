#!/usr/bin/env python3
"""Generate the deterministic OIP v2.1 census and pre-RPC recovery budget."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.intelligence.migrated_coverage import census, recovery_plan

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hard-call-limit", type=int)
    args = parser.parse_args()
    rows = census(ROOT / "database/flex_complete_database.db",
                  ROOT / "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db")
    print(json.dumps(recovery_plan(rows, hard_call_limit=args.hard_call_limit), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
