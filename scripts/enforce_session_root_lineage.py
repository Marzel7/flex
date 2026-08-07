#!/usr/bin/env python3
"""Apply the identity-neutral Tier-1 session lineage contract.

Only historical session rows independently classified as exact directional
relationships are admitted. Raw session history is never altered.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops.lineage_quarantine import (  # noqa: E402
    ensure_lineage_quarantine_schema,
    record_verified_session_edge,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ops-db", default=str(ROOT / "database/wt_ops_v2.db"))
    parser.add_argument(
        "--substrate", default=str(ROOT / "database/transaction_first_lineage.db")
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    ops = sqlite3.connect(args.ops_db, timeout=30)
    ops.row_factory = sqlite3.Row
    substrate = sqlite3.connect(args.substrate, timeout=30)
    substrate.row_factory = sqlite3.Row
    verified = substrate.execute(
        """
        SELECT session_id,stored_root,stored_child,signature
          FROM tf_session_comparison
         WHERE comparison_class='CORRECT_DIRECT_RELATIONSHIP'
        """
    ).fetchall()
    exact = []
    for row in verified:
        current = ops.execute(
            """
            SELECT id,treasury_wallet,subprov_wallet,funding_signature
              FROM wt_active_subprov_sessions WHERE id=?
            """,
            (row["session_id"],),
        ).fetchone()
        if current and (
            current["treasury_wallet"],
            current["subprov_wallet"],
            current["funding_signature"],
        ) == (row["stored_root"], row["stored_child"], row["signature"]):
            exact.append(row)

    raw = ops.execute("SELECT COUNT(*) FROM wt_active_subprov_sessions").fetchone()[0]
    report = {
        "raw_sessions": raw,
        "independently_verified_exact_edges": len(exact),
        "would_remain_tier1": len(exact),
        "would_be_historical_context_only": raw - len(exact),
        "applied": bool(args.apply),
    }
    if args.apply:
        ensure_lineage_quarantine_schema(ops)
        now = int(time.time())
        for row in exact:
            record_verified_session_edge(
                ops,
                session_id=row["session_id"],
                sender=row["stored_root"],
                recipient=row["stored_child"],
                signature=row["signature"],
                evidence_source="X78.13_TRANSACTION_REPLAY",
                verified_at=now,
            )
        ops.commit()
        report["live_tier1_sessions"] = ops.execute(
            "SELECT COUNT(*) FROM wt_lineage_eligible_sessions"
        ).fetchone()[0]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
