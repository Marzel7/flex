"""STORAGE-LIFECYCLE-P5B Stage 2 Part 7: deterministic delta reconciler.

Extends the P4 keyset-cursor pattern (src/ops/transfer_archival_cursor.py)
with the Stage-1 pin-set HOT/COLD classification contract (see
docs/audits/storage_lifecycle_p5b_delta_reconciliation.json) and a HARD,
never-implicit upper bound on the source id range that may be read or
written.

Contract:
  - Keyset cursor on `id` (never OFFSET).
  - `upper_bound_id` is a REQUIRED, EXPLICIT constructor argument. There is
    no "current max id" fallback anywhere in this module -- a caller who
    wants the live high-water must read it themselves and pass it in.
  - Read-only against source: this module never executes INSERT/UPDATE/
    DELETE against the connection passed as `source_conn`.
  - Deterministic, crash-safe resume via a JSON checkpoint file (same
    atomic replace() pattern as transfer_archival_cursor.py).
  - Idempotent candidate writes: INSERT OR IGNORE against both the HOT
    candidate connection and any COLD segment connection.
  - Exact accounting returned for every run: rows seen, classified HOT
    (broken into pinned/recent/ambiguous), classified COLD, written,
    skipped-as-duplicate.
  - Hard guard: `next_batch` sql itself bounds `id <= upper_bound_id`; in
    addition, `run` asserts this invariant against every batch returned,
    so even a caller error constructing a bad WHERE clause cannot leak a
    row past the bound -- it raises instead of proceeding.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field


@dataclass
class ReconcilerCheckpoint:
    run_id: str
    upper_bound_id: int          # frozen, explicit, never re-derived
    last_processed_id: int = 0
    rows_seen: int = 0
    hot_pinned: int = 0
    hot_recent: int = 0
    hot_ambiguous: int = 0
    cold: int = 0
    hot_written: int = 0
    hot_skipped_duplicate: int = 0
    cold_written: int = 0
    cold_skipped_duplicate: int = 0
    completed: bool = False


def load_checkpoint(path: str) -> ReconcilerCheckpoint | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return ReconcilerCheckpoint(**json.load(f))


def save_checkpoint(path: str, checkpoint: ReconcilerCheckpoint) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + f".tmp.{os.getpid()}.{time.time_ns()}"
    with open(tmp, "w") as f:
        json.dump(checkpoint.__dict__, f)
    os.replace(tmp, path)


@dataclass
class PinSets:
    """Frozen, pre-derived pin sets for HOT classification. Callers derive
    these fresh (never trust a stale count) and pass them in -- this
    module does not query wt_ops_v2.db / creator_funders / the discovery
    corpus itself, keeping it independently testable with synthetic sets.
    """
    creator_addresses: frozenset[str]
    operator_entity_addresses: frozenset[str]
    discovery_signatures: frozenset[str]
    hot_boundary_unix: int


def classify(row: tuple, pins: PinSets) -> str:
    """row = (id, signature, source, destination, amount_lamports, slot,
    block_time, indexed_at, is_valid, transfer_type).

    Returns one of: "hot_pinned", "hot_recent", "hot_ambiguous", "cold".
    Mirrors the Stage-1 contract exactly:
      destination in creator_funders pin set
        OR source/destination in operator_entities pin set
        OR signature in discovery corpus pin set
      -> HOT (pinned)
      else block_time >= hot_boundary_unix -> HOT (recent)
      else -> COLD
      ambiguous/invalid block_time -> held HOT
    """
    _id, signature, source, destination, _amt, _slot, block_time, *_ = row

    if (
        destination in pins.creator_addresses
        or source in pins.operator_entity_addresses
        or destination in pins.operator_entity_addresses
        or signature in pins.discovery_signatures
    ):
        return "hot_pinned"

    if block_time is None or not isinstance(block_time, (int, float)) or block_time <= 0:
        return "hot_ambiguous"

    if block_time >= pins.hot_boundary_unix:
        return "hot_recent"

    return "cold"


@dataclass
class ReconcileResult:
    checkpoint: ReconcilerCheckpoint
    batches: int = 0
    elapsed_seconds: float = 0.0


class P5BDeltaReconciler:
    """Deterministic, resumable, boundary-hard-guarded delta reconciler.

    Usage:
        r = P5BDeltaReconciler(
            source_conn=ro_conn, hot_dest_conn=candidate_hot_conn,
            cold_dest_conn=cold_delta_conn, pins=pins,
            upper_bound_id=6765610, run_id="p5b-stage2-part9-...",
            checkpoint_path=".../checkpoint.json",
        )
        result = r.run(lower_bound_id_exclusive=6743251)
    """

    def __init__(
        self,
        *,
        source_conn: sqlite3.Connection,
        hot_dest_conn: sqlite3.Connection,
        cold_dest_conn: sqlite3.Connection | None,
        pins: PinSets,
        upper_bound_id: int,
        run_id: str,
        checkpoint_path: str,
        batch_size: int = 5_000,
    ):
        if upper_bound_id is None:
            raise ValueError("upper_bound_id is required and must be explicit -- no implicit/current fallback")
        self.source_conn = source_conn
        self.hot_dest_conn = hot_dest_conn
        self.cold_dest_conn = cold_dest_conn
        self.pins = pins
        self.upper_bound_id = int(upper_bound_id)
        self.run_id = run_id
        self.checkpoint_path = checkpoint_path
        self.batch_size = batch_size

    def _start_or_resume(self, lower_bound_id_exclusive: int) -> ReconcilerCheckpoint:
        existing = load_checkpoint(self.checkpoint_path)
        if existing is not None and existing.run_id == self.run_id:
            if existing.upper_bound_id != self.upper_bound_id:
                raise RuntimeError(
                    f"checkpoint {self.checkpoint_path} run_id matches but upper_bound_id "
                    f"differs ({existing.upper_bound_id} != {self.upper_bound_id}) -- refusing "
                    "to resume with a moved boundary"
                )
            return existing
        checkpoint = ReconcilerCheckpoint(
            run_id=self.run_id,
            upper_bound_id=self.upper_bound_id,
            last_processed_id=lower_bound_id_exclusive,
        )
        save_checkpoint(self.checkpoint_path, checkpoint)
        return checkpoint

    def _next_batch(self, checkpoint: ReconcilerCheckpoint) -> list[tuple]:
        return self.source_conn.execute(
            "SELECT id, signature, source, destination, amount_lamports, slot, "
            "block_time, indexed_at, is_valid, transfer_type FROM transfer_index "
            "WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
            (checkpoint.last_processed_id, self.upper_bound_id, self.batch_size),
        ).fetchall()

    def run(self, *, lower_bound_id_exclusive: int, max_batches: int | None = None) -> ReconcileResult:
        t0 = time.monotonic()
        checkpoint = self._start_or_resume(lower_bound_id_exclusive)
        batches_this_call = 0

        while True:
            if max_batches is not None and batches_this_call >= max_batches:
                break
            batch = self._next_batch(checkpoint)
            if not batch:
                checkpoint.completed = True
                save_checkpoint(self.checkpoint_path, checkpoint)
                break

            # Hard guard: assert every row in this batch obeys the frozen
            # boundary, regardless of how it was fetched.
            for row in batch:
                if row[0] > self.upper_bound_id:
                    raise RuntimeError(
                        f"HARD GUARD VIOLATION: row id {row[0]} exceeds upper_bound_id "
                        f"{self.upper_bound_id} -- refusing to process"
                    )

            hot_rows: list[tuple] = []
            cold_rows: list[tuple] = []
            for row in batch:
                cls = classify(row, self.pins)
                if cls == "hot_pinned":
                    checkpoint.hot_pinned += 1
                    hot_rows.append(row)
                elif cls == "hot_recent":
                    checkpoint.hot_recent += 1
                    hot_rows.append(row)
                elif cls == "hot_ambiguous":
                    checkpoint.hot_ambiguous += 1
                    hot_rows.append(row)
                else:
                    checkpoint.cold += 1
                    cold_rows.append(row)
            checkpoint.rows_seen += len(batch)

            if hot_rows:
                before = self.hot_dest_conn.total_changes
                self.hot_dest_conn.executemany(
                    "INSERT OR IGNORE INTO transfer_index "
                    "(signature, source, destination, amount_lamports, slot, block_time, "
                    "indexed_at, is_valid, transfer_type) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in hot_rows],
                )
                self.hot_dest_conn.commit()
                actually_inserted = self.hot_dest_conn.total_changes - before
                checkpoint.hot_written += actually_inserted
                checkpoint.hot_skipped_duplicate += (len(hot_rows) - actually_inserted)

            if cold_rows:
                if self.cold_dest_conn is None:
                    raise RuntimeError("COLD rows produced but no cold_dest_conn supplied")
                before = self.cold_dest_conn.total_changes
                self.cold_dest_conn.executemany(
                    "INSERT OR IGNORE INTO transfer_index "
                    "(signature, source, destination, amount_lamports, slot, block_time, "
                    "indexed_at, is_valid, transfer_type) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in cold_rows],
                )
                self.cold_dest_conn.commit()
                actually_inserted = self.cold_dest_conn.total_changes - before
                checkpoint.cold_written += actually_inserted
                checkpoint.cold_skipped_duplicate += (len(cold_rows) - actually_inserted)

            checkpoint.last_processed_id = batch[-1][0]
            save_checkpoint(self.checkpoint_path, checkpoint)
            batches_this_call += 1

        return ReconcileResult(checkpoint=checkpoint, batches=batches_this_call, elapsed_seconds=time.monotonic() - t0)
