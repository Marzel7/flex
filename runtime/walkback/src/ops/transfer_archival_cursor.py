"""STORAGE-LIFECYCLE-P4 Part 9: deterministic keyset archival cursor.

Replaces P3's OFFSET-based prototype migration (O(n) per batch, not
production-safe on a 5.2M-row table) with a stable keyset cursor bound
to `id` (the table's INTEGER PRIMARY KEY AUTOINCREMENT -- immutable once
assigned, strictly monotonic, never reused even after deletes, which is
exactly the "immutable unique key" property required; block_time or
indexed_at are explicitly NOT used as the cursor because they are
mutable/backfillable event-time fields, not write-order-guaranteed).

Contract: SOURCE READ -> COLD COPY -> VERIFY -> CLOSE SEGMENT. This
module never deletes from the source. A checkpoint file records
(high_water_id, rows_processed) after each successful batch commit, so
an interrupted run resumes from the last committed batch rather than
restarting or skipping rows.

High-water binding: an archival run freezes its own `max_id` ceiling at
run start (read once, held fixed for the whole run) so rows inserted
into the source WHILE archival is in progress are correctly excluded
from the current run and picked up by a future run -- this prevents a
long-running archival from chasing a moving target indefinitely.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CursorCheckpoint:
    run_id: str
    high_water_id: int  # frozen ceiling for this run, set once at start
    last_processed_id: int = 0
    rows_processed: int = 0
    completed: bool = False


def load_checkpoint(checkpoint_path: str) -> CursorCheckpoint | None:
    if not os.path.exists(checkpoint_path):
        return None
    with open(checkpoint_path) as f:
        data = json.load(f)
    return CursorCheckpoint(**data)


def save_checkpoint(checkpoint_path: str, checkpoint: CursorCheckpoint) -> None:
    """Atomic write: write to a temp file then os.replace() onto the real
    path, so a crash mid-write never leaves a corrupt/partial checkpoint
    file that a resume would misread."""
    os.makedirs(os.path.dirname(checkpoint_path) or ".", exist_ok=True)
    tmp_path = checkpoint_path + f".tmp.{os.getpid()}.{time.time_ns()}"
    with open(tmp_path, "w") as f:
        json.dump(checkpoint.__dict__, f)
    os.replace(tmp_path, checkpoint_path)


def start_or_resume_run(source_conn: sqlite3.Connection, *, run_id: str, checkpoint_path: str) -> CursorCheckpoint:
    """Determines the high-water id for this run. If a checkpoint already
    exists for this exact run_id, resumes from it (same frozen ceiling,
    same last_processed_id) -- a restart of the SAME run never re-freezes
    a new, later ceiling, which would silently include rows that arrived
    after the original run started (violating the high-water contract).
    A genuinely NEW run_id always freezes a fresh ceiling from the
    source's current MAX(id)."""
    existing = load_checkpoint(checkpoint_path)
    if existing is not None and existing.run_id == run_id:
        return existing

    row = source_conn.execute("SELECT COALESCE(MAX(id), 0) FROM transfer_index").fetchone()
    high_water = row[0]
    checkpoint = CursorCheckpoint(run_id=run_id, high_water_id=high_water)
    save_checkpoint(checkpoint_path, checkpoint)
    return checkpoint


def next_batch(
    source_conn: sqlite3.Connection,
    checkpoint: CursorCheckpoint,
    *,
    where_clause: str = "1=1",
    params: tuple = (),
    batch_size: int = 5_000,
) -> list[tuple]:
    """Fetches the next bounded batch strictly after last_processed_id and
    at or below the frozen high_water_id -- an indexed, O(batch_size)
    range scan on the primary key, not an O(n) OFFSET scan. Deterministic
    ordering (ORDER BY id) guarantees no skipped rows and no duplicates
    across successive calls as long as last_processed_id only ever
    advances to the actual max id seen in the returned batch."""
    return source_conn.execute(
        f"SELECT id, signature, source, destination, amount_lamports, slot, "
        f"block_time, indexed_at, is_valid, transfer_type FROM transfer_index "
        f"WHERE id > ? AND id <= ? AND ({where_clause}) ORDER BY id LIMIT ?",
        (checkpoint.last_processed_id, checkpoint.high_water_id, *params, batch_size),
    ).fetchall()


@dataclass
class ArchivalRunResult:
    run_id: str
    high_water_id: int
    total_rows_processed: int = 0
    batches_committed: int = 0
    completed: bool = False
    rows_per_batch: list[int] = field(default_factory=list)


def run_keyset_archival(
    source_conn: sqlite3.Connection,
    dest_conn: sqlite3.Connection,
    *,
    run_id: str,
    checkpoint_path: str,
    where_clause: str = "1=1",
    params: tuple = (),
    batch_size: int = 5_000,
    max_batches: int | None = None,
) -> ArchivalRunResult:
    """Full crash-safe, resumable keyset archival loop. Each batch is
    committed and checkpointed independently (no giant transaction).
    dest_conn is caller-owned (typically an already-open cold segment
    connection with the standard schema already applied)."""
    checkpoint = start_or_resume_run(source_conn, run_id=run_id, checkpoint_path=checkpoint_path)
    result = ArchivalRunResult(run_id=run_id, high_water_id=checkpoint.high_water_id)

    batches_this_call = 0
    while True:
        if max_batches is not None and batches_this_call >= max_batches:
            break
        batch = next_batch(source_conn, checkpoint, where_clause=where_clause, params=params, batch_size=batch_size)
        if not batch:
            checkpoint.completed = True
            save_checkpoint(checkpoint_path, checkpoint)
            result.completed = True
            break

        rows_for_insert = [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in batch]
        dest_conn.executemany(
            "INSERT OR IGNORE INTO transfer_index "
            "(signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows_for_insert,
        )
        dest_conn.commit()

        checkpoint.last_processed_id = batch[-1][0]
        checkpoint.rows_processed += len(batch)
        save_checkpoint(checkpoint_path, checkpoint)

        result.total_rows_processed += len(batch)
        result.batches_committed += 1
        result.rows_per_batch.append(len(batch))
        batches_this_call += 1

    return result
