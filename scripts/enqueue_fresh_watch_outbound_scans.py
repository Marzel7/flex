#!/usr/bin/env python3
"""
Backfill one-pass outbound scans for migrated fresh WATCH creators.

This does not perform RPC work itself; it only seeds creator_outbound_queue so
CreatorOutboundWorker can inspect their post-migration outflows.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.creator_outbound_worker import apply_migration, enqueue_creator_for_watch_outbound_scan


DEFAULT_DB = ROOT / "database" / "flex_complete_database.db"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    apply_migration(args.db)
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT DISTINCT tps.creator_address
        FROM token_prediction_scores tps
        JOIN token_analysis ta ON ta.mint = tps.mint
        WHERE tps.creator_address IS NOT NULL
          AND tps.risk_level = 'WATCH'
          AND COALESCE(tps.creator_was_fresh, 0) = 1
          AND tps.prediction_status = 'COMPLETE'
          AND ta.lifecycle_stage = 'migrated'
        ORDER BY tps.predicted_at DESC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()

    already_done = 0
    already_pending = 0
    to_enqueue = []
    for row in rows:
        creator = row["creator_address"]
        status_row = conn.execute(
            "SELECT status FROM creator_outbound_queue WHERE creator_address = ?",
            (creator,),
        ).fetchone()
        if status_row:
            if status_row["status"] == "done":
                already_done += 1
            else:
                already_pending += 1
            continue
        to_enqueue.append(creator)

    if not args.dry_run:
        for creator in to_enqueue:
            enqueue_creator_for_watch_outbound_scan(conn, creator, priority=35)
        conn.commit()

    print(
        {
            "eligible_fresh_watch_creators": len(rows),
            "to_enqueue": len(to_enqueue),
            "already_pending_or_scanning": already_pending,
            "already_done": already_done,
            "dry_run": args.dry_run,
        }
    )
    conn.close()


if __name__ == "__main__":
    main()
