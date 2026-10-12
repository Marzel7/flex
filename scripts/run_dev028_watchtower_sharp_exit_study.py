#!/usr/bin/env python3
"""Prepare or execute the bounded DEV028 sharp-exit study."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.ops.watchtower_dev028_study import execute, initialize_journal, prepare_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--prepeak-db", type=Path, default=Path("/private/tmp/dev020-watchtower-prepeak/journal.db"))
    parser.add_argument("--lifecycle-root", type=Path, default=Path("/private/tmp/dev014-watchtower-lifecycle-cohort"))
    parser.add_argument("--state-root", type=Path, default=Path("/private/tmp/dev028-watchtower-sharp-exit"))
    parser.add_argument("--budget-root", type=Path, default=Path("/private/tmp/dev020-watchtower-prepeak"))
    parser.add_argument("--env", type=Path, default=Path("/Users/kevinkeaveney/Dev/claude/flex/.env"))
    args = parser.parse_args()
    if args.prepare == args.execute:
        raise SystemExit("EXACTLY_ONE_OF_PREPARE_OR_EXECUTE_REQUIRED")
    manifest_path = args.state_root / "manifest.json"
    journal_path = args.state_root / "journal.json"
    if args.prepare:
        manifest = prepare_manifest(prepeak_db=args.prepeak_db, lifecycle_root=args.lifecycle_root, destination=manifest_path)
        initialize_journal(manifest=manifest, destination=journal_path)
        print({"manifest_content_hash": manifest["content_hash"], "records": len(manifest["records"])})
    else:
        result = execute(manifest_path=manifest_path, journal_path=journal_path, budget_root=args.budget_root, env_path=args.env)
        states = {key: sum(1 for row in result["records"].values() if row["state"] == key)
                  for key in sorted({row["state"] for row in result["records"].values()})}
        print({"states": states})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
