#!/usr/bin/env python3
"""
Targeted outbound intelligence pass for migrated WATCH creators.

This is deliberately narrower than the full graph analyzer suite:
1. select WATCH creators
2. scan only those creators' outbound transfers
3. classify outbound findings
4. rebuild direct creator-to-creator edges
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.creator_outbound_builder import CreatorOutboundBuilder
from src.core.creator_outbound_worker import (
    CreatorOutboundWorker,
    enqueue_creator_for_watch_outbound_scan,
    ensure_watch_outbound_schedule_schema,
    schedule_watch_outbound_stages,
)
from src.core.c2c_edge_builder import C2CEdgeBuilder

DEFAULT_DB = ROOT / "database" / "flex_complete_database.db"


def seed_missing_schedules(conn: sqlite3.Connection) -> int:
    ensure_watch_outbound_schedule_schema(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT tps.creator_address, tps.mint, ta.migrated_at
        FROM token_prediction_scores tps
        JOIN token_analysis ta ON ta.mint = tps.mint
        WHERE tps.creator_address IS NOT NULL
          AND tps.risk_level = 'WATCH'
          AND tps.prediction_status = 'COMPLETE'
          AND ta.lifecycle_stage = 'migrated'
          AND ta.migrated_at IS NOT NULL
        """
    ).fetchall()
    return sum(schedule_watch_outbound_stages(conn, r[0], r[1], int(r[2])) for r in rows)


def select_due_watch_creators(conn: sqlite3.Connection, limit: int) -> tuple[list[str], list[tuple[str, str, str]]]:
    now = __import__("time").time()
    rows = conn.execute(
        """
        SELECT creator_address, mint, stage
        FROM watch_outbound_scan_schedule
        WHERE status='pending' AND due_at <= ?
        ORDER BY due_at ASC
        LIMIT ?
        """,
        (int(now), limit),
    ).fetchall()
    scan_rows = [(r[0], r[1], r[2]) for r in rows]
    creators = list(dict.fromkeys(r[0] for r in rows))
    return creators, scan_rows


def select_watch_creators(conn: sqlite3.Connection, *, fresh_only: bool, limit: int) -> list[str]:
    fresh_clause = "AND COALESCE(tps.creator_was_fresh, 0) = 1" if fresh_only else ""
    limit_clause = "" if limit <= 0 else "LIMIT ?"
    params = () if limit <= 0 else (limit,)
    rows = conn.execute(
        f"""
        SELECT DISTINCT tps.creator_address
        FROM token_prediction_scores tps
        JOIN token_analysis ta ON ta.mint = tps.mint
        WHERE tps.creator_address IS NOT NULL
          AND tps.risk_level = 'WATCH'
          {fresh_clause}
          AND tps.prediction_status = 'COMPLETE'
          AND ta.lifecycle_stage = 'migrated'
        ORDER BY tps.predicted_at DESC
        {limit_clause}
        """,
        params,
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all-watch", action="store_true", help="Scan all migrated WATCH creators once, not just due fresh-WATCH stages")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    seeded = 0
    due_rows = []
    if args.all_watch:
        creators = select_watch_creators(conn, fresh_only=False, limit=args.limit)
    else:
        seeded = seed_missing_schedules(conn)
        creators, due_rows = select_due_watch_creators(conn, args.limit)
    if args.dry_run:
        conn.close()
        print({
            "mode": "all_watch_once" if args.all_watch else "due_watch_stages",
            "watch_creators": len(creators),
            "due_stages": len(due_rows),
            "seeded_stages": seeded,
            "dry_run": True,
        })
        return

    for creator in creators:
        enqueue_creator_for_watch_outbound_scan(conn, creator, priority=50)
    conn.commit()
    conn.close()

    worker = CreatorOutboundWorker(args.db)
    batches = [creators[i:i + args.batch_size] for i in range(0, len(creators), args.batch_size)]
    scan_results = [worker.run_for_creators(batch, force=True) for batch in batches]
    outbound = CreatorOutboundBuilder(args.db).run_for_creators(creators)
    c2c = C2CEdgeBuilder(args.db).build_for_sources(creators)
    if due_rows:
        conn = sqlite3.connect(args.db, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        now = int(__import__("time").time())
        conn.executemany("""
            UPDATE watch_outbound_scan_schedule
            SET status='done', scanned_at=?, last_error=NULL
            WHERE creator_address=? AND mint=? AND stage=?
        """, [(now, creator, mint, stage) for creator, mint, stage in due_rows])
        conn.commit()
        conn.close()
    scan = {
        "status": "success",
        "batches": len(scan_results),
        "creators_scanned": sum(r.get("creators_scanned", 0) for r in scan_results),
        "transfers_written": sum(r.get("transfers_written", 0) for r in scan_results),
        "rpc_calls_used": sum(r.get("rpc_calls_used", 0) for r in scan_results),
        "errors": sum(r.get("errors", 0) for r in scan_results),
        "batch_results": scan_results,
    }
    print({
        "mode": "all_watch_once" if args.all_watch else "due_watch_stages",
        "watch_creators": len(creators),
        "due_stages": len(due_rows),
        "seeded_stages": seeded,
        "scan": scan,
        "outbound_classification": outbound,
        "c2c": c2c,
    })


if __name__ == "__main__":
    main()
