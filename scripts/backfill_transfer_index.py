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
BATCH_SIZE_DEFAULT = 2000
LAMPORTS_PER_SOL = 1_000_000_000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.abspath(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")  # 30s retry on lock
    return conn


def insert_batch(conn: sqlite3.Connection, rows: list[tuple], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        return len(rows)   # count what would be attempted (dupes still ignored at runtime)
    for attempt in range(12):
        try:
            conn.executemany("""
                INSERT OR IGNORE INTO transfer_index
                    (signature, source, destination, amount_lamports,
                     slot, block_time, indexed_at, is_valid, transfer_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, rows)
            conn.commit()
            return len(rows)
        except sqlite3.OperationalError as e:
            if 'locked' in str(e) and attempt < 11:
                wait = min(2 ** attempt * 0.3, 15)   # caps at 15s
                time.sleep(wait)
                continue
            raise
    return 0


def count_table(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def lamports(amount_sol: float) -> int | None:
    if amount_sol is None or amount_sol <= 0:
        return None
    v = int(amount_sol * LAMPORTS_PER_SOL)
    return v if v > 0 else None


# ---------------------------------------------------------------------------
# source 1: creator_funders  (no signatures — placeholder '')
# ---------------------------------------------------------------------------

def backfill_creator_funders(conn: sqlite3.Connection, batch_size: int, dry_run: bool, now: float) -> int:
    print("\n[1/4] creator_funders  (placeholder signature='')")
    total_src = conn.execute(
        "SELECT COUNT(*) FROM creator_funders WHERE amount_sol > 0"
    ).fetchone()[0]
    print(f"      source rows: {total_src:,}")

    cursor = conn.execute("""
        SELECT funder_address, creator_address, amount_sol, first_detected_at
        FROM creator_funders
        WHERE amount_sol > 0
    """)

    inserted = skipped = 0
    batch = []
    for row in cursor:
        lamps = lamports(row['amount_sol'])
        if lamps is None:
            skipped += 1
            continue

        bt = 0
        if row['first_detected_at']:
            try:
                import datetime
                dt = row['first_detected_at']
                if isinstance(dt, str):
                    # handles '2025-01-01 12:00:00' or ISO format
                    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                        try:
                            bt = int(datetime.datetime.strptime(dt, fmt).timestamp())
                            break
                        except ValueError:
                            continue
                elif isinstance(dt, (int, float)):
                    bt = int(dt)
            except Exception:
                bt = 0

        if bt <= 0:
            skipped += 1
            continue

        batch.append((
            '',                             # signature placeholder
            row['funder_address'],          # source
            row['creator_address'],         # destination
            lamps,                          # amount_lamports
            0,                             # slot
            bt,                            # block_time
            now,                           # indexed_at
            'backfill_creator_funder',     # transfer_type
        ))

        if len(batch) >= batch_size:
            inserted += insert_batch(conn, batch, dry_run)
            batch = []
            print(f"      … {inserted:,} inserted", end='\r')

    inserted += insert_batch(conn, batch, dry_run)
    print(f"      inserted: {inserted:,}  skipped: {skipped:,}  (dry_run={dry_run})")
    return inserted


# ---------------------------------------------------------------------------
# source 2: creator_outgoing_transfers  (real signatures, 100% coverage)
# ---------------------------------------------------------------------------

def backfill_creator_outgoing(conn: sqlite3.Connection, batch_size: int, dry_run: bool, now: float) -> int:
    print("\n[2/4] creator_outgoing_transfers  (real signatures)")
    total_src = conn.execute(
        "SELECT COUNT(*) FROM creator_outgoing_transfers WHERE amount_sol > 0 AND transaction_signature IS NOT NULL AND transaction_signature != ''"
    ).fetchone()[0]
    print(f"      source rows: {total_src:,}")

    cursor = conn.execute("""
        SELECT creator_address, recipient_address, amount_sol,
               transaction_signature, block_time
        FROM creator_outgoing_transfers
        WHERE amount_sol > 0
          AND transaction_signature IS NOT NULL
          AND transaction_signature != ''
          AND block_time IS NOT NULL
          AND block_time > 0
    """)

    inserted = skipped = 0
    batch = []
    for row in cursor:
        lamps = lamports(row['amount_sol'])
        if lamps is None:
            skipped += 1
            continue

        batch.append((
            row['transaction_signature'],
            row['creator_address'],         # source  (creator is the sender here)
            row['recipient_address'],       # destination
            lamps,
            0,                             # slot not stored in this table
            int(row['block_time']),
            now,
            'backfill_creator_outgoing',
        ))

        if len(batch) >= batch_size:
            inserted += insert_batch(conn, batch, dry_run)
            batch = []
            print(f"      … {inserted:,} inserted", end='\r')

    inserted += insert_batch(conn, batch, dry_run)
    print(f"      inserted: {inserted:,}  skipped: {skipped:,}  (dry_run={dry_run})")
    return inserted


# ---------------------------------------------------------------------------
# source 3: funder_incoming_transfers  (2-hop, real signatures)
# ---------------------------------------------------------------------------

def backfill_funder_incoming(conn: sqlite3.Connection, batch_size: int, dry_run: bool, now: float) -> int:
    print("\n[3/4] funder_incoming_transfers  (2-hop, real signatures)")
    total_src = conn.execute(
        "SELECT COUNT(*) FROM funder_incoming_transfers WHERE amount_sol > 0 AND transaction_signature IS NOT NULL AND transaction_signature != '' AND block_time > 0"
    ).fetchone()[0]
    print(f"      source rows: {total_src:,}")

    cursor = conn.execute("""
        SELECT sender_address, funder_address, amount_sol,
               transaction_signature, block_time
        FROM funder_incoming_transfers
        WHERE amount_sol > 0
          AND transaction_signature IS NOT NULL
          AND transaction_signature != ''
          AND block_time IS NOT NULL
          AND block_time > 0
    """)

    inserted = skipped = 0
    batch = []
    for row in cursor:
        lamps = lamports(row['amount_sol'])
        if lamps is None:
            skipped += 1
            continue

        batch.append((
            row['transaction_signature'],
            row['sender_address'],          # source
            row['funder_address'],          # destination
            lamps,
            0,
            int(row['block_time']),
            now,
            'backfill_funder_incoming',
        ))

        if len(batch) >= batch_size:
            inserted += insert_batch(conn, batch, dry_run)
            batch = []
            print(f"      … {inserted:,} inserted", end='\r')

    inserted += insert_batch(conn, batch, dry_run)
    print(f"      inserted: {inserted:,}  skipped: {skipped:,}  (dry_run={dry_run})")
    return inserted


# ---------------------------------------------------------------------------
# source 4: funder_outgoing_transfers  (2-hop, real signatures)
# ---------------------------------------------------------------------------

def backfill_funder_outgoing(conn: sqlite3.Connection, batch_size: int, dry_run: bool, now: float) -> int:
    print("\n[4/4] funder_outgoing_transfers  (2-hop, real signatures)")
    total_src = conn.execute(
        "SELECT COUNT(*) FROM funder_outgoing_transfers WHERE amount_sol > 0 AND transaction_signature IS NOT NULL AND transaction_signature != '' AND block_time > 0"
    ).fetchone()[0]
    print(f"      source rows: {total_src:,}")

    cursor = conn.execute("""
        SELECT funder_address, recipient_address, amount_sol,
               transaction_signature, block_time
        FROM funder_outgoing_transfers
        WHERE amount_sol > 0
          AND transaction_signature IS NOT NULL
          AND transaction_signature != ''
          AND block_time IS NOT NULL
          AND block_time > 0
    """)

    inserted = skipped = 0
    batch = []
    for row in cursor:
        lamps = lamports(row['amount_sol'])
        if lamps is None:
            skipped += 1
            continue

        batch.append((
            row['transaction_signature'],
            row['funder_address'],          # source
            row['recipient_address'],       # destination
            lamps,
            0,
            int(row['block_time']),
            now,
            'backfill_funder_outgoing',
        ))

        if len(batch) >= batch_size:
            inserted += insert_batch(conn, batch, dry_run)
            batch = []
            print(f"      … {inserted:,} inserted", end='\r')

    inserted += insert_batch(conn, batch, dry_run)
    print(f"      inserted: {inserted:,}  skipped: {skipped:,}  (dry_run={dry_run})")
    return inserted


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

    conn = connect(db_path)
    before = count_table(conn, 'transfer_index')
    print(f"\ntransfer_index before: {before:,}")

    now = time.time()
    t0 = time.time()

    total = 0
    total += backfill_creator_funders(conn, args.batch_size, args.dry_run, now)
    total += backfill_creator_outgoing(conn, args.batch_size, args.dry_run, now)

    if not args.skip_2hop:
        total += backfill_funder_incoming(conn, args.batch_size, args.dry_run, now)
        total += backfill_funder_outgoing(conn, args.batch_size, args.dry_run, now)
    else:
        print("\n[3/4] funder_incoming_transfers  — skipped (--skip-2hop)")
        print("[4/4] funder_outgoing_transfers  — skipped (--skip-2hop)")

    conn.close()

    elapsed = time.time() - t0

    if not args.dry_run:
        conn2 = connect(db_path)
        after = count_table(conn2, 'transfer_index')
        conn2.close()
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
