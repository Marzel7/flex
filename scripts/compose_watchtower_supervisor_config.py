#!/usr/bin/env python3
"""Offline-only candidate/backup renderer; never invokes Supervisor."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.watchtower_supervisor_composition import RuntimePaths, compose


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--canonical-db", required=True)
    parser.add_argument("--api-operations-db", required=True)
    parser.add_argument("--worker-operations-db", required=True)
    parser.add_argument("--api-queue", required=True)
    parser.add_argument("--worker-queue", required=True)
    parser.add_argument("--api-monitor-ui-db", required=True)
    parser.add_argument("--monitor-state-root", required=True)
    parser.add_argument("--monitor-env-file", required=True)
    parser.add_argument("--audit-ledger", required=True)
    parser.add_argument("--shadow-ledger", required=True)
    parser.add_argument("--api-stdout-log", required=True)
    parser.add_argument("--api-stderr-log", required=True)
    parser.add_argument("--worker-stdout-log", required=True)
    parser.add_argument("--worker-stderr-log", required=True)
    args = parser.parse_args()
    source = args.source.read_bytes()
    args.backup.write_bytes(source)
    paths = RuntimePaths(**{
        key: value
        for key, value in vars(args).items()
        if key not in {"source", "candidate", "backup"}
    })
    args.candidate.write_bytes(compose(source, paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
