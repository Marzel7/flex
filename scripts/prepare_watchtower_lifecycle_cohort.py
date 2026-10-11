#!/usr/bin/env python3
"""Create one compact immutable lifecycle cohort authorization; no provider access."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.ops.watchtower_lifecycle_cohort import (
    attempted_lifecycle_identities, build_cohort_authorization, eligible_authorization_records,
)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OPENING = ROOT / "docs/audits/dev014_watchtower_opening_hour_reconciliation_20261011.v1.json"
OUT = ROOT / "docs/audits/watchtower_lifecycle_cohort_authorization_20261011.v1.json"
JOURNAL_ROOT = Path("/private/tmp/dev014-lifecycle-probes")
LEGACY_JOURNALS = (
    Path("/private/tmp/dev014-lifecycle-probe/journal.json"),
    Path("/private/tmp/dev014-lifecycle-probe-2/journal.json"),
    Path("/private/tmp/dev014-lifecycle-probe-3/journal.json"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--authority-id", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("LIFECYCLE_COHORT_AUTHORIZATION_ALREADY_EXISTS")
    opening = json.loads(OPENING.read_text())
    journals = [path for path in LEGACY_JOURNALS if path.exists()] + sorted(JOURNAL_ROOT.glob("*/journal.json"))
    blocked_ids, blocked_mints = attempted_lifecycle_identities(journals)
    records = eligible_authorization_records(opening["records"], blocked_identities=blocked_ids, blocked_mints=blocked_mints)
    value = build_cohort_authorization(records, chronology_hash=opening["chronology_content_hash"], authority_id=args.authority_id)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) >= 1_000_000:
        raise SystemExit("LIFECYCLE_COHORT_AUTHORIZATION_BOUND_EXCEEDED")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(args.output)
    print(json.dumps({"content_hash": value["content_hash"], "records": len(records), "bytes": len(raw)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
