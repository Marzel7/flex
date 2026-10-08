#!/usr/bin/env python3
"""Capture/evaluate a compact, read-only bounded-soak safety window."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.watchtower_soak_safety import evaluate, read_snapshot, snapshot, write_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    before = sub.add_parser("snapshot")
    before.add_argument("--state", type=Path, required=True)
    before.add_argument("--log", type=Path, required=True)
    before.add_argument("--wal", type=Path, required=True)
    before.add_argument("--lock", type=Path, required=True)
    after = sub.add_parser("evaluate")
    after.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        write_snapshot(args.state, snapshot(args.log, args.wal, args.lock))
        print("WATCHTOWER_SOAK_FRESH_WINDOW_SNAPSHOTTED")
        return 0
    result = evaluate(read_snapshot(args.state))
    print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
    return 0 if result.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
