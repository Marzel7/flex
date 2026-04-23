#!/usr/bin/env python3
"""
One-off backfill: populate transfer_index from historical funding tables.

Sources (in priority order):
  1. creator_inbound_transfers  — real tx signatures, best source for 1-hop  [SKIPPED: 0 rows]
  2. creator_funders            — no signatures; '' placeholder, dedupe on (source,dest) pair
  3. creator_outgoing_transfers — real tx signatures, 100% coverage
  4. funder_incoming_transfers  — real tx signatures (2-hop, optional)
  5. funder_outgoing_transfers  — real tx signatures (2-hop, optional)

Skipped:
  - creator_receivers           — 0% signature coverage; no safe dedupe key
  - creator_inbound_transfers   — 0 qualifying rows in this DB

Signature policy:
  creator_funders has no signature column.  We use '' (empty string) as the
  placeholder.  The UNIQUE constraint is (signature, source, destination), so
  '' rows deduplicate correctly per (funder→creator) pair and do not collide
  with real-signature rows in the other tables.  Analyzers that join on
  signature will simply not match these rows, which is safe.

Usage:
  python scripts/backfill_transfer_index.py --dry-run
  python scripts/backfill_transfer_index.py
  python scripts/backfill_transfer_index.py --skip-2hop
  python scripts/backfill_transfer_index.py --batch-size 5000
"""

import argparse
import sqlite3
import time
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database', 'flex_complete_database.db')
BATCH_SIZE_DEFAULT = 500
LAMPORTS_PER_SOL = 1_000_000_000
INTER_BATCH_SLEEP = 0.05   # 50ms pause between batches — yields write lock to the app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open for reads only — never blocks writers."""
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def open_readwrite(db_path: str) -> sqlite3.Connection:
    """Open for writes with WAL and a generous busy timeout."""
    conn = sqlite3.connect(os.path.abspath(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def insert_batch(db_path: str, rows: list[tuple], dry_run: bool) -> int:
    """Open a fresh connection per batch — avoids stale lock state."""
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    for attempt in range(10):
        conn = None
        try:
            conn = open_readwrite(db_path)
            conn.executemany("""
                INSERT OR IGNORE INTO transfer_index
                    (signature, source, destination, amount_lamports,
                     slot, block_time, indexed_at, is_valid, transfer_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, rows)
            conn.commit()
            return len(rows)
        except sqlite3.OperationalError as e:
            if 'locked' in str(e) and attempt < 9:
                if conn:
                    try: conn.close()
                    except: pass
                wait = min(1.0 * (attempt + 1), 8.0)
                time.sleep(wait)
                continue
            raise
        finally:
            if conn:
                try: conn.close()
                except: pass
    return 0


import datetime as _dt


def count_table(db_path: str, table: str) -> int:
    conn = open_readonly(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def lamports(amount_sol: float) -> int | None:
    if amount_sol is None or amount_sol <= 0:
        return None
    v = int(amount_sol * LAMPORTS_PER_SOL)
    return v if v > 0 else None


def parse_ts(val) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return int(_dt.datetime.strptime(val, fmt).timestamp())
        except (ValueError, TypeError):
            continue
    return 0


def run_source(label: str, db_path: str, query: str, row_mapper, batch_size: int, dry_run: bool, now: float) -> int:
    """
    Fetch all rows via read-only connection, then write in batches using
    fresh rw connections.  Reads never block; writes yield between batches.
    """
    print(f"\n{label}")

    rconn = open_readonly(db_path)
    try:
        rows_raw = rconn.execute(query).fetchall()
    finally:
        rconn.close()

    print(f"      source rows: {len(rows_raw):,}")

    inserted = skipped = 0
    batch = []

    for row in rows_raw:
        mapped = row_mapper(row, now)
        if mapped is None:
            skipped += 1
            continue
        batch.append(mapped)
        if len(batch) >= batch_size:
            inserted += insert_batch(db_path, batch, dry_run)
            batch = []
            print(f"      … {inserted:,} inserted", end='\r')
            if not dry_run:
                time.sleep(INTER_BATCH_SLEEP)

    inserted += insert_batch(db_path, batch, dry_run)
    print(f"      inserted: {inserted:,}  skipped: {skipped:,}  (dry_run={dry_run})")
    return inserted


# ---------------------------------------------------------------------------
# row mappers
# ---------------------------------------------------------------------------

def map_creator_funder(row, now: float):
    lamps = lamports(row['amount_sol'])
    bt = parse_ts(row['first_detected_at'])
    if lamps is None or bt <= 0:
        return None
    return ('', row['funder_address'], row['creator_address'], lamps, 0, bt, now, 'backfill_creator_funder')


def map_creator_outgoing(row, now: float):
    lamps = lamports(row['amount_sol'])
    if lamps is None:
        return None
    return (row['transaction_signature'], row['creator_address'], row['recipient_address'],
            lamps, 0, int(row['block_time']), now, 'backfill_creator_outgoing')


def map_funder_incoming(row, now: float):
    lamps = lamports(row['amount_sol'])
    if lamps is None:
        return None
    return (row['transaction_signature'], row['sender_address'], row['funder_address'],
            lamps, 0, int(row['block_time']), now, 'backfill_funder_incoming')


def map_funder_outgoing(row, now: float):
    lamps = lamports(row['amount_sol'])
    if lamps is None:
        return None
    return (row['transaction_signature'], row['funder_address'], row['recipient_address'],
            lamps, 0, int(row['block_time']), now, 'backfill_funder_outgoing')


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Backfill transfer_index from historical tables')
    parser.add_argument('--dry-run',    action='store_true', help='Read-only — count rows without inserting')
    parser.add_argument('--skip-2hop', action='store_true', help='Skip funder_incoming / funder_outgoing (2-hop)')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE_DEFAULT, help=f'Insert batch size (default {BATCH_SIZE_DEFAULT})')
    parser.add_argument('--db',         default=DB_PATH, help='Path to SQLite DB')
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"DB:        {db_path}")
    print(f"Dry run:   {args.dry_run}")
    print(f"Skip 2hop: {args.skip_2hop}")
    print(f"Batch sz:  {args.batch_size:,}")

    before = count_table(db_path, 'transfer_index')
    print(f"\ntransfer_index before: {before:,}")

    now = time.time()
    t0 = time.time()
    total = 0

    total += run_source(
        "[1/4] creator_funders  (placeholder signature='')", db_path,
        "SELECT funder_address, creator_address, amount_sol, first_detected_at FROM creator_funders WHERE amount_sol > 0",
        map_creator_funder, args.batch_size, args.dry_run, now,
    )

    total += run_source(
        "[2/4] creator_outgoing_transfers  (real signatures)", db_path,
        "SELECT creator_address, recipient_address, amount_sol, transaction_signature, block_time FROM creator_outgoing_transfers WHERE amount_sol > 0 AND transaction_signature IS NOT NULL AND transaction_signature != '' AND block_time > 0",
        map_creator_outgoing, args.batch_size, args.dry_run, now,
    )

    if not args.skip_2hop:
        total += run_source(
            "[3/4] funder_incoming_transfers  (2-hop)", db_path,
            "SELECT sender_address, funder_address, amount_sol, transaction_signature, block_time FROM funder_incoming_transfers WHERE amount_sol > 0 AND transaction_signature IS NOT NULL AND transaction_signature != '' AND block_time > 0",
            map_funder_incoming, args.batch_size, args.dry_run, now,
        )
        total += run_source(
            "[4/4] funder_outgoing_transfers  (2-hop)", db_path,
            "SELECT funder_address, recipient_address, amount_sol, transaction_signature, block_time FROM funder_outgoing_transfers WHERE amount_sol > 0 AND transaction_signature IS NOT NULL AND transaction_signature != '' AND block_time > 0",
            map_funder_outgoing, args.batch_size, args.dry_run, now,
        )
    else:
        print("\n[3/4] funder_incoming_transfers  — skipped (--skip-2hop)")
        print("[4/4] funder_outgoing_transfers  — skipped (--skip-2hop)")

    elapsed = time.time() - t0

    if not args.dry_run:
        after = count_table(db_path, 'transfer_index')
        net = after - before
        print(f"\n{'='*50}")
        print(f"transfer_index before : {before:,}")
        print(f"transfer_index after  : {after:,}")
        print(f"net new rows          : {net:,}")
        print(f"elapsed               : {elapsed:.1f}s")
    else:
        print(f"\n{'='*50}")
        print(f"DRY RUN — would attempt to insert up to ~{total:,} rows")
        print(f"(actual inserts depend on existing duplicates)")
        print(f"elapsed               : {elapsed:.1f}s")


if __name__ == '__main__':
    main()
