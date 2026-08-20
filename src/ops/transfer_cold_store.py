"""STORAGE-LIFECYCLE-P3: hot/cold transfer_index segmentation prototype.

Design-and-prototype only -- no production database is written to by
any function in this module during normal use. All migration functions
operate on (source_conn, dest_path) pairs supplied by the caller; the
caller is responsible for ensuring source_conn is a READ-ONLY connection
when pointed at a live production database (this module does not open
its own connection to any hardcoded production path).

Segment naming: transfer_index_cold_<YYYY>_<MM>.sqlite (time-partitioned,
not size-partitioned -- see storage_lifecycle_p3_hot_cold_retention_
contract.json for the reasoning).
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

COLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_index (
    id                  INTEGER PRIMARY KEY,
    signature           TEXT NOT NULL,
    source              TEXT NOT NULL,
    destination         TEXT NOT NULL,
    amount_lamports     INTEGER NOT NULL,
    slot                INTEGER NOT NULL DEFAULT 0,
    block_time          INTEGER NOT NULL,
    indexed_at          REAL NOT NULL,
    is_valid            BOOLEAN NOT NULL DEFAULT 1,
    transfer_type       TEXT DEFAULT 'standard',
    UNIQUE (signature, source, destination)
);
CREATE INDEX IF NOT EXISTS idx_cold_destination_time ON transfer_index(destination, block_time DESC);
CREATE INDEX IF NOT EXISTS idx_cold_source_time ON transfer_index(source, block_time DESC);
CREATE INDEX IF NOT EXISTS idx_cold_block_time ON transfer_index(block_time DESC);

CREATE TABLE IF NOT EXISTS segment_manifest (
    segment_id          TEXT PRIMARY KEY,
    created_at          REAL NOT NULL,
    closed_at           REAL,
    row_count           INTEGER NOT NULL DEFAULT 0,
    sha256_of_sorted_signatures TEXT,
    source_run_id       TEXT,
    month_covered        TEXT NOT NULL
);
"""


def segment_name_for_month(year: int, month: int) -> str:
    return f"transfer_index_cold_{year:04d}_{month:02d}.sqlite"


@dataclass
class MigrationResult:
    segment_path: str
    rows_migrated: int
    rows_verified: int
    signatures_digest: str
    duration_seconds: float


def create_cold_segment(dest_path: str, *, month_covered: str) -> None:
    """Creates a new, empty cold segment file with the standard schema.
    Idempotent: safe to call on an already-created segment (CREATE TABLE
    IF NOT EXISTS)."""
    conn = sqlite3.connect(dest_path)
    try:
        conn.executescript(COLD_SCHEMA)
        segment_id = Path(dest_path).stem
        conn.execute(
            "INSERT OR IGNORE INTO segment_manifest "
            "(segment_id, created_at, row_count, month_covered) VALUES (?,?,0,?)",
            (segment_id, time.time(), month_covered),
        )
        conn.commit()
    finally:
        conn.close()


def migrate_rows_to_cold(
    source_conn: sqlite3.Connection,
    dest_path: str,
    *,
    where_clause: str,
    params: tuple,
    batch_size: int = 5_000,
    run_id: str = "unspecified",
) -> MigrationResult:
    """Copies rows matching where_clause from source_conn (caller-owned,
    should be mode=ro when pointed at production) into a cold segment, in
    bounded batches (never one giant transaction). Does NOT delete
    anything from the source -- this is copy-then-verify; deletion from
    the live HOT table is an explicitly SEPARATE, later, human-authorized
    step (per the instruction: 'copy old eligible rows to closed cold
    segment -> verify counts/digests -> mark archived -> future clean-
    slate/partition strategy', not delete-as-you-go).

    Uses OFFSET-based pagination for reproducibility within a single
    call; this is a prototype for isolated fixture testing, not
    production-scale (production-scale migration would need a keyset-
    based cursor to avoid OFFSET's O(n) cost on large tables -- flagged
    as a known limitation for the P4 implementation)."""
    start = time.monotonic()
    dest_conn = sqlite3.connect(dest_path)
    dest_conn.executescript(COLD_SCHEMA)
    segment_id = Path(dest_path).stem
    dest_conn.execute(
        "INSERT OR IGNORE INTO segment_manifest (segment_id, created_at, row_count, month_covered) "
        "VALUES (?,?,0,?)",
        (segment_id, time.time(), "unspecified"),
    )
    dest_conn.commit()

    total_migrated = 0
    signatures: list[str] = []
    offset = 0
    while True:
        rows = source_conn.execute(
            f"SELECT signature, source, destination, amount_lamports, slot, "
            f"block_time, indexed_at, is_valid, transfer_type FROM transfer_index "
            f"WHERE {where_clause} ORDER BY id LIMIT ? OFFSET ?",
            (*params, batch_size, offset),
        ).fetchall()
        if not rows:
            break
        dest_conn.executemany(
            "INSERT OR IGNORE INTO transfer_index "
            "(signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        dest_conn.commit()
        total_migrated += len(rows)
        signatures.extend(r[0] for r in rows)
        offset += batch_size

    dest_conn.close()

    digest = hashlib.sha256("\n".join(sorted(signatures)).encode()).hexdigest()
    verify_conn = sqlite3.connect(f"file:{dest_path}?mode=ro", uri=True)
    verified_count = verify_conn.execute("SELECT COUNT(*) FROM transfer_index").fetchone()[0]
    verify_conn.close()

    return MigrationResult(
        segment_path=dest_path, rows_migrated=total_migrated,
        rows_verified=verified_count, signatures_digest=digest,
        duration_seconds=time.monotonic() - start,
    )


def close_segment(dest_path: str, *, source_run_id: str) -> None:
    """Marks a segment closed (immutable) and records the final row count
    + signature digest in the manifest. After this, the segment should
    never be written to again by any future migration call."""
    conn = sqlite3.connect(dest_path)
    try:
        rows = [r[0] for r in conn.execute("SELECT signature FROM transfer_index ORDER BY signature")]
        digest = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        segment_id = Path(dest_path).stem
        conn.execute(
            "INSERT OR IGNORE INTO segment_manifest (segment_id, created_at, row_count, month_covered) "
            "VALUES (?,?,0,'unspecified')",
            (segment_id, time.time()),
        )
        cursor = conn.execute(
            "UPDATE segment_manifest SET closed_at=?, row_count=?, "
            "sha256_of_sorted_signatures=?, source_run_id=? WHERE segment_id=?",
            (time.time(), len(rows), digest, source_run_id, segment_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            raise RuntimeError(f"close_segment: expected to update exactly 1 manifest row, updated {cursor.rowcount}")
    finally:
        conn.close()


def is_segment_closed(dest_path: str) -> bool:
    conn = sqlite3.connect(f"file:{dest_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT closed_at FROM segment_manifest LIMIT 1").fetchone()
        return bool(row and row[0] is not None)
    finally:
        conn.close()
