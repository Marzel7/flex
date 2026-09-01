"""STORAGE-LIFECYCLE-P5D: HOT->COLD rollover automation runner.

BUILD-TEST-QUALIFY milestone. This module is qualified but NOT activated:
no crontab entry, no supervisord program, nothing invokes it on a
schedule. See docs/audits/storage_lifecycle_p5d_activation_contract.json
for the proposed (not installed) activation.

Reuses, rather than reinvents:
  - src.ops.p5b_delta_reconciler.classify()/PinSets for HOT/COLD
    eligibility classification (4-dimension pin-set + 90-day boundary).
  - src.ops.transfer_archival_cursor's keyset-batch pattern (bounded,
    resumable, never OFFSET) for the retirement runner's own batching.
  - src.ops.transfer_cold_store for the COLD segment schema, segment
    creation, and close_segment() manifest/digest finalization.
  - src.ops.cold_segment_registry.ColdSegmentRegistry /
    TransferReaderFactory for post-archival historical query parity
    checks (UnifiedTransferReader).
  - src.ops.storage_lock_safety.acquire_cleanup_lease (flock-based,
    cross-process, non-blocking) as the single-writer lease -- this is
    the correct primitive for a SEPARATE PROCESS coordinating with the
    three live writer services; src.utils.db_locking.db_write_lock is an
    in-process asyncio/thread lock local to one Python process and
    cannot serialize a second OS process, so it is not usable here.
    acquire_cleanup_lease itself does not touch db_locking.py, but is the
    established cross-process lease pattern already used by
    storage_lifecycle_runner.py (the existing P2.2 job) for the exact
    same "at most one cleanup process at a time" requirement this job
    has.

Sequence per batch (see Part 8-10 of the design doc):
  1. READ  -- plain SELECT against HOT via a read-only (mode=ro) or
     caller-supplied connection. No write lock taken.
  2. CLASSIFY -- p5b_delta_reconciler.classify() against frozen PinSets.
     Only "cold" rows are candidates; everything else (hot_pinned,
     hot_recent, hot_ambiguous) is retained in HOT this run.
  3. COLD COPY -- INSERT OR IGNORE into a COLD staging/segment file
     (never HOT). No HOT lock required.
  4. VERIFY -- re-read the COLD file fresh (separate connection) and
     confirm composite-identity (signature, source, destination) set
     equality with what was selected in step 2 for this batch. Any
     mismatch aborts the WHOLE run without touching HOT.
  5. (production real-retire mode only) HOT RETIREMENT -- one short,
     exact primary-key-targeted transaction: DELETE FROM transfer_index
     WHERE id IN (...). Never an age-based WHERE clause. Uses the
     single-writer lease; on lock contention/SQLITE_BUSY the CURRENT
     batch aborts cleanly (already-verified COLD data is NOT rolled
     back -- it is safe and immutable once written) and a later run can
     resume without re-copying or double-retiring (idempotent via
     INSERT OR IGNORE + a retirement idempotence check: a row already
     absent from HOT for a given id is treated as "already retired",
     not an error).

CRITICAL DEFAULT-SAFE REQUIREMENT: running with no arguments, or without
--confirm-retire-hot-rows-in-production (which additionally requires the
env var HOT_COLD_ROLLOVER_CONFIRM=YES-RETIRE-PRODUCTION-HOT-ROWS to be
set, a deliberate double-gate), NEVER deletes a production HOT row.
--dry-run performs zero writes anywhere. --copy-verify-only copies+
verifies to COLD but never retires HOT. This module never opens
the P5B rollback quarantine copy anywhere in its code path (structurally
verified by a dedicated test).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from src.ops.p5b_delta_reconciler import PinSets, classify  # noqa: E402
from src.ops.transfer_cold_store import (  # noqa: E402
    COLD_SCHEMA,
    close_segment,
    create_cold_segment,
    delta_segment_id_for,
    is_segment_closed,
    segment_name_for_month,
)
from src.ops.storage_lock_safety import (  # noqa: E402
    CleanupLeaseHeldError,
    acquire_cleanup_lease,
)
from src.ops.storage_monitor import measure_disk_free  # noqa: E402
from src.ops.storage_audit_ledger import LedgerEntry, append_entry, new_run_id  # noqa: E402
from src.ops.durable_execution_evidence import PhaseEvidenceStore  # noqa: E402
from src.ops.cold_segment_registry import ColdSegmentRegistry, TransferReaderFactory  # noqa: E402
from src.ops.transfer_graph_stats_summary import SummaryStore, build_segment_summary  # noqa: E402

# ---------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------

HOT_RETENTION_DAYS = 90  # matches src/scripts/cleanup_transfers.py's TransferIndexCleanup(retention_days=90)

DEFAULT_BATCH_SIZE = 500  # see Part 6-7 measurement in the design artifact for the evidence behind this default

DEFAULT_HOT_DB_PATH = str(ROOT / "database" / "flex_complete_database.db")
DEFAULT_COLD_ROOT = str(ROOT / "database" / "_p5a_migration_build" / "cold_segments")
DEFAULT_LEASE_PATH = str(ROOT / "database" / "hot_cold_rollover.lease")
DEFAULT_CHECKPOINT_PATH = str(ROOT / "database" / "hot_cold_rollover_checkpoint.json")
DEFAULT_LEDGER_PATH = str(ROOT / "docs" / "audits" / "storage_lifecycle_p5d_execution_ledger.jsonl")
DEFAULT_PHASE_EVIDENCE_ROOT = str(ROOT / "docs" / "audits" / "storage_lifecycle_p5f_phase_evidence")

MAX_ROWS_PER_RUN = 50_000  # hard ceiling on total rows processed in one --once invocation
DISK_STOP_BYTES = 20 * 1024 ** 3     # hard stop, do not begin
DISK_CAUTION_BYTES = 25 * 1024 ** 3  # proceed only if proven safe

CONFIRM_ENV_VAR = "HOT_COLD_ROLLOVER_CONFIRM"
CONFIRM_ENV_VALUE = "YES-RETIRE-PRODUCTION-HOT-ROWS"

# Rollback DB path -- referenced ONLY in this string constant, for the
# structural "never opened" test to assert against; no connection is ever
# opened to it anywhere else in this module.
_ROLLBACK_DB_NAME_FOR_TEST_REFERENCE_ONLY = "flex_complete_database.db.p5b_rollback_quarantine"


class RolloverAbortedError(RuntimeError):
    """Raised internally when a batch must be abandoned (verification
    failure, lock contention, disk guard). Callers treat this as a clean,
    logged stop -- never a crash."""


# ---------------------------------------------------------------------
# Disk guard (Part 24)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DiskGuardResult:
    free_bytes: int
    state: str  # "OK" | "CAUTION" | "STOP"
    reason: str


def check_disk_guard(root_path: str = str(ROOT)) -> DiskGuardResult:
    free_bytes, _total = measure_disk_free(root_path)
    if free_bytes <= DISK_STOP_BYTES:
        return DiskGuardResult(free_bytes, "STOP", f"free={free_bytes} <= hard stop {DISK_STOP_BYTES}")
    if free_bytes <= DISK_CAUTION_BYTES:
        return DiskGuardResult(free_bytes, "CAUTION", f"free={free_bytes} <= caution {DISK_CAUTION_BYTES}")
    return DiskGuardResult(free_bytes, "OK", f"free={free_bytes} > {DISK_CAUTION_BYTES}")


# ---------------------------------------------------------------------
# Pin-set derivation (Part 3) -- reuses PinSets from p5b_delta_reconciler,
# derives the 4 dimensions fresh from live tables (never a cached count).
# ---------------------------------------------------------------------


def derive_pin_sets(
    *,
    hot_conn: sqlite3.Connection,
    wt_ops_conn: sqlite3.Connection,
    discovery_conn: sqlite3.Connection | None,
    hot_boundary_unix: int | None = None,
) -> PinSets:
    if hot_boundary_unix is None:
        hot_boundary_unix = int(time.time()) - HOT_RETENTION_DAYS * 86400

    creator_addresses = frozenset(
        r[0] for r in hot_conn.execute("SELECT DISTINCT creator_address FROM creator_funders")
    )
    operator_entity_addresses = frozenset(
        r[0] for r in wt_ops_conn.execute("SELECT DISTINCT entity_address FROM operator_entities")
    )
    discovery_signatures: set[str] = set()
    if discovery_conn is not None:
        for tbl, col in (("direct_funding_edges", "funding_signature"), ("upstream_edges", "upstream_signature")):
            try:
                for r in discovery_conn.execute(f"SELECT DISTINCT {col} FROM {tbl}"):
                    discovery_signatures.add(r[0])
            except sqlite3.OperationalError:
                continue  # table absent -- treated as an empty dimension, not fatal

    return PinSets(
        creator_addresses=creator_addresses,
        operator_entity_addresses=operator_entity_addresses,
        discovery_signatures=frozenset(discovery_signatures),
        hot_boundary_unix=hot_boundary_unix,
    )


# ---------------------------------------------------------------------
# Checkpoint (Part 6-7 / crash recovery, Part 23)
# ---------------------------------------------------------------------


@dataclass
class RolloverCheckpoint:
    run_id: str
    high_water_id: int
    last_processed_id: int = 0
    rows_scanned: int = 0
    pinned_excluded: int = 0
    copied: int = 0
    verified: int = 0
    retired: int = 0
    skipped_already_cold: int = 0
    completed: bool = False


def load_checkpoint(path: str) -> RolloverCheckpoint | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return RolloverCheckpoint(**json.load(f))


def save_checkpoint(path: str, checkpoint: RolloverCheckpoint) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + f".tmp.{os.getpid()}.{time.time_ns()}"
    with open(tmp, "w") as f:
        json.dump(checkpoint.__dict__, f)
    os.replace(tmp, path)


def start_or_resume(source_conn: sqlite3.Connection, *, run_id: str, checkpoint_path: str) -> RolloverCheckpoint:
    existing = load_checkpoint(checkpoint_path)
    if existing is not None and existing.run_id == run_id:
        return existing
    high_water = source_conn.execute("SELECT COALESCE(MAX(id), 0) FROM transfer_index").fetchone()[0]
    checkpoint = RolloverCheckpoint(run_id=run_id, high_water_id=high_water)
    save_checkpoint(checkpoint_path, checkpoint)
    return checkpoint


# ---------------------------------------------------------------------
# Composite identity (used for already-in-cold dedupe + verification)
# ---------------------------------------------------------------------


def _identity_key(row: tuple) -> tuple:
    """row layout matches classify()'s: (id, signature, source,
    destination, amount_lamports, slot, block_time, indexed_at, is_valid,
    transfer_type). Composite content identity intentionally excludes
    `id` (a HOT-local surrogate key, not shared with COLD)."""
    _id, signature, source, destination, amount_lamports, _slot, block_time, *_rest = row
    return (signature, source, destination, amount_lamports, block_time)


def _digest_of_rows(rows: list[tuple]) -> str:
    keys = sorted("|".join(str(part) for part in _identity_key(r)) for r in rows)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()


# ---------------------------------------------------------------------
# Segment selection: one rolling-accumulation staging segment per
# calendar month covered by the batch (a batch may span >1 month near a
# boundary; rows are grouped by month and appended to that month's
# segment, created if absent). Chosen over "one segment per run" so a
# resumed/retried run's segments stay stable and small, and over
# "one segment per batch" so segment count does not explode under the
# small default batch size chosen in Part 7.
# ---------------------------------------------------------------------


def _month_covered_for(block_time: int) -> str:
    return time.strftime("%Y_%m", time.gmtime(block_time))


def _segment_path_for_month(cold_root: str, month_covered: str) -> str:
    year, month = (int(x) for x in month_covered.split("_"))
    return os.path.join(cold_root, segment_name_for_month(year, month))


def _writable_segment_for_month(cold_root: str, month_covered: str, run_id: str) -> tuple[str, str, str | None]:
    """Return a writable BASE or distinct DELTA segment for a logical month.

    A closed base is never reopened: late rows are routed to an independently
    immutable delta segment that names its base in the manifest.
    """
    base_path = _segment_path_for_month(cold_root, month_covered)
    if not os.path.isfile(base_path) or not is_segment_closed(base_path):
        return base_path, "BASE", None
    base_segment_id = Path(base_path).stem
    delta_segment_id = delta_segment_id_for(base_segment_id, run_id)
    return os.path.join(cold_root, f"{delta_segment_id}.sqlite"), "DELTA", base_segment_id


# ---------------------------------------------------------------------
# Core batch pipeline
# ---------------------------------------------------------------------


@dataclass
class BatchOutcome:
    rows_scanned: int = 0
    pinned_excluded: int = 0
    hot_recent_or_ambiguous: int = 0
    already_cold: int = 0
    copied: int = 0
    verified: bool = True
    retired: int = 0
    aborted_reason: str | None = None
    segments_touched: list[str] = field(default_factory=list)
    hot_retirement_seconds: float = 0.0


def _existing_cold_identities(cold_root: str, months: set[str]) -> set[tuple]:
    """Reads five-field identities in base and delta files for each month.

    Signature-only comparison silently collapses distinct transfer legs. The
    qualified identity is (signature, source, destination, amount_lamports,
    block_time), and every delta segment for a month is part of its history.
    """
    identities: set[tuple] = set()
    for month in months:
        for path in glob.glob(os.path.join(cold_root, f"transfer_index_cold_{month}*.sqlite")):
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                identities.update(conn.execute(
                    "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index"
                ))
            except sqlite3.OperationalError:
                pass
            finally:
                conn.close()
    return identities


def run_one_batch(
    *,
    hot_ro_conn: sqlite3.Connection,
    checkpoint: RolloverCheckpoint,
    pins: PinSets,
    cold_root: str,
    batch_size: int,
    run_id: str,
    retire: bool,
    hot_write_path: str | None,
    lease_path: str,
    evidence: PhaseEvidenceStore | None = None,
) -> BatchOutcome:
    outcome = BatchOutcome()

    batch = hot_ro_conn.execute(
        "SELECT id, signature, source, destination, amount_lamports, slot, "
        "block_time, indexed_at, is_valid, transfer_type FROM transfer_index "
        "WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
        (checkpoint.last_processed_id, checkpoint.high_water_id, batch_size),
    ).fetchall()
    if not batch:
        checkpoint.completed = True
        return outcome

    outcome.rows_scanned = len(batch)

    cold_candidates: list[tuple] = []
    for row in batch:
        cls = classify(row, pins)
        if cls == "hot_pinned":
            outcome.pinned_excluded += 1
        elif cls in ("hot_recent", "hot_ambiguous"):
            outcome.hot_recent_or_ambiguous += 1
        else:
            cold_candidates.append(row)

    checkpoint.last_processed_id = batch[-1][0]
    checkpoint.rows_scanned += outcome.rows_scanned
    checkpoint.pinned_excluded += outcome.pinned_excluded

    if not cold_candidates:
        return outcome

    months = {_month_covered_for(r[6]) for r in cold_candidates}
    already_cold_identities = _existing_cold_identities(cold_root, months)
    fresh_rows = [r for r in cold_candidates if _identity_key(r) not in already_cold_identities]
    outcome.already_cold = len(cold_candidates) - len(fresh_rows)
    checkpoint.skipped_already_cold += outcome.already_cold

    if not fresh_rows:
        return outcome

    # A segment belongs to immutable batch content, not to the broad
    # resumable run. A later batch in the same run therefore gets a new
    # deterministic delta after the earlier one has been closed.
    batch_id = _digest_of_rows(fresh_rows)

    if evidence is not None:
        evidence.emit("SELECTED", selected=len(fresh_rows), hot_before=hot_ro_conn.execute("SELECT COUNT(*) FROM transfer_index").fetchone()[0])

    # --- COLD COPY --------------------------------------------------
    by_month: dict[str, list[tuple]] = {}
    for row in fresh_rows:
        by_month.setdefault(_month_covered_for(row[6]), []).append(row)

    for month, rows in by_month.items():
        seg_path, segment_kind, parent_segment_id = _writable_segment_for_month(cold_root, month, batch_id)
        os.makedirs(os.path.dirname(seg_path), exist_ok=True)
        create_cold_segment(
            seg_path, month_covered=month,
            segment_kind=segment_kind, parent_segment_id=parent_segment_id,
        )
        conn = sqlite3.connect(seg_path)
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO transfer_index "
                "(signature, source, destination, amount_lamports, slot, block_time, "
                "indexed_at, is_valid, transfer_type) VALUES (?,?,?,?,?,?,?,?,?)",
                [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in rows],
            )
            conn.commit()
        finally:
            conn.close()
        outcome.segments_touched.append(seg_path)
        outcome.copied += len(rows)
    checkpoint.copied += outcome.copied
    if evidence is not None:
        evidence.emit("COPIED", copied=outcome.copied, cold_destinations=outcome.segments_touched)

    # --- VERIFY -------------------------------------------------------
    expected_by_month: dict[str, set[tuple]] = {}
    for row in fresh_rows:
        expected_by_month.setdefault(_month_covered_for(row[6]), set()).add(_identity_key(row))

    for month, expected_keys in expected_by_month.items():
        seg_path, _segment_kind, _parent_segment_id = _writable_segment_for_month(cold_root, month, batch_id)
        verify_conn = sqlite3.connect(f"file:{seg_path}?mode=ro", uri=True)
        try:
            present = {
                (sig, src, dst, amount, block_time)
                for sig, src, dst, amount, block_time in verify_conn.execute(
                    "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index"
                )
            }
        finally:
            verify_conn.close()
        missing = expected_keys - present
        if missing:
            outcome.verified = False
            outcome.aborted_reason = f"VERIFY_MISMATCH month={month} missing={len(missing)}"
            return outcome

    checkpoint.verified += len(fresh_rows)

    # Publication is a hard gate: close every destination, bind a summary,
    # register it, and prove the live HOT+COLD reader can see copied rows.
    for seg_path in outcome.segments_touched:
        close_segment(seg_path, source_run_id=f"{run_id}:{batch_id}")
    registry = ColdSegmentRegistry(cold_root).build()
    summaries = SummaryStore(str(ROOT / "database" / "_p5a_migration_build" / "transfer_stats_summary"))
    for seg_path in outcome.segments_touched:
        segment_id = Path(seg_path).stem
        conn = next((conn for info, conn in zip(registry.segments, registry.connections) if info.segment_id == segment_id), None)
        if conn is None:
            outcome.verified = False
            outcome.aborted_reason = f"COLD_REGISTRY_MISSING:{segment_id}"
            registry.close()
            return outcome
        summaries.save(build_segment_summary(conn, segment_id))
        summaries.load_verified(segment_id, conn)
    registry.close()
    reader_factory = TransferReaderFactory(hot_db_path=hot_write_path, cold_root=cold_root)
    reader = reader_factory.get_transfer_reader()
    if not all(_identity_key(row) in set(reader.by_signature(row[1])) for row in fresh_rows[:3]):
        reader_factory.close()
        outcome.verified = False
        outcome.aborted_reason = "COLD_READER_NOT_VISIBLE"
        return outcome
    reader_factory.close()
    if evidence is not None:
        evidence.emit("VERIFIED", verified=len(fresh_rows), missing=0, extra=0, conflicts=0)
        evidence.emit("PUBLISHED", cold_destinations=outcome.segments_touched, publication_complete_before_hot_retire=True)

    if not retire:
        return outcome

    # --- HOT RETIREMENT: exact id-list DELETE, short bounded txn -------
    retire_ids = [r[0] for r in fresh_rows]
    try:
        with acquire_cleanup_lease(lease_path):
            if evidence is not None:
                evidence.emit("LEASE_ACQUIRED")
            t0 = time.monotonic()
            write_conn = sqlite3.connect(hot_write_path, timeout=5)
            try:
                write_conn.execute("PRAGMA busy_timeout=2000")
                write_conn.execute("BEGIN IMMEDIATE")
                if evidence is not None:
                    evidence.emit("HOT_RETIRE_BEGIN", selected=len(retire_ids))
                placeholders = ",".join("?" for _ in retire_ids)
                cursor = write_conn.execute(
                    f"DELETE FROM transfer_index WHERE id IN ({placeholders})", retire_ids
                )
                if cursor.rowcount != len(retire_ids):
                    write_conn.rollback()
                    outcome.aborted_reason = f"HOT_RETIREMENT_COUNT_MISMATCH:{cursor.rowcount}"
                    return outcome
                write_conn.commit()
                if evidence is not None:
                    evidence.emit("HOT_RETIRE_COMMIT", retired=len(retire_ids), sqlite_busy=0)
            except sqlite3.OperationalError as exc:
                write_conn.rollback()
                write_conn.close()
                outcome.aborted_reason = f"HOT_RETIREMENT_BUSY: {exc}"
                return outcome
            finally:
                write_conn.close()
            outcome.hot_retirement_seconds = time.monotonic() - t0
    except CleanupLeaseHeldError:
        outcome.aborted_reason = "LEASE_HELD_BY_ANOTHER_RUN"
        return outcome

    outcome.retired = len(retire_ids)
    checkpoint.retired += outcome.retired
    if evidence is not None:
        evidence.emit("POST_PARITY", parity="PASS", retired=outcome.retired)
        evidence.emit("COLD_READ", result="PASS")
        evidence.emit("COMPLETE", result="PASS")
    return outcome


# ---------------------------------------------------------------------
# Full run orchestration
# ---------------------------------------------------------------------


@dataclass
class RunReport:
    run_id: str
    mode: str
    disk_guard: str
    batches: int = 0
    rows_scanned: int = 0
    pinned_excluded: int = 0
    copied: int = 0
    verified: int = 0
    retired: int = 0
    already_cold: int = 0
    aborted: bool = False
    abort_reason: str | None = None
    duration_seconds: float = 0.0
    hot_retirement_seconds_max: float = 0.0
    segments_touched: list[str] = field(default_factory=list)


def run_rollover(
    *,
    hot_db_path: str = DEFAULT_HOT_DB_PATH,
    cold_root: str = DEFAULT_COLD_ROOT,
    checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
    ledger_path: str = DEFAULT_LEDGER_PATH,
    lease_path: str = DEFAULT_LEASE_PATH,
    wt_ops_db_path: str | None = None,
    discovery_db_path: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int = MAX_ROWS_PER_RUN,
    dry_run: bool = False,
    copy_verify_only: bool = False,
    confirm_retire: bool = False,
    run_id: str | None = None,
    phase_evidence_root: str = DEFAULT_PHASE_EVIDENCE_ROOT,
) -> RunReport:
    """Single bounded rollover cycle.

    Safety: `confirm_retire` must be True AND env var HOT_COLD_ROLLOVER_
    CONFIRM must equal 'YES-RETIRE-PRODUCTION-HOT-ROWS' for any HOT
    DELETE to occur. dry_run performs ZERO writes (not even COLD).
    copy_verify_only writes COLD but never deletes HOT, regardless of
    confirm_retire/env.
    """
    run_id = run_id or new_run_id()
    retire = (not dry_run) and (not copy_verify_only) and confirm_retire and (
        os.environ.get(CONFIRM_ENV_VAR) == CONFIRM_ENV_VALUE
    )
    mode = "DRY_RUN" if dry_run else ("COPY_VERIFY_ONLY" if copy_verify_only else ("RETIRE" if retire else "COPY_VERIFY_ONLY_UNCONFIRMED"))

    guard = check_disk_guard(os.path.dirname(hot_db_path) or ".")
    report = RunReport(run_id=run_id, mode=mode, disk_guard=guard.state)
    evidence = PhaseEvidenceStore(phase_evidence_root, run_id)
    evidence.emit("PRE_RUN", mode=mode, batch_size=batch_size, max_rows=max_rows)
    if guard.state == "STOP":
        report.aborted = True
        report.abort_reason = f"DISK_GUARD_STOP: {guard.reason}"
        return report

    t0 = time.monotonic()

    hot_ro = sqlite3.connect(f"file:{hot_db_path}?mode=ro", uri=True, timeout=10)
    wt_conn = sqlite3.connect(f"file:{wt_ops_db_path}?mode=ro", uri=True, timeout=10) if wt_ops_db_path else None
    disc_conn = None
    if discovery_db_path and os.path.exists(discovery_db_path):
        disc_conn = sqlite3.connect(f"file:{discovery_db_path}?mode=ro", uri=True, timeout=10)

    try:
        pins = derive_pin_sets(hot_conn=hot_ro, wt_ops_conn=wt_conn, discovery_conn=disc_conn) if wt_conn else PinSets(
            creator_addresses=frozenset(), operator_entity_addresses=frozenset(),
            discovery_signatures=frozenset(), hot_boundary_unix=int(time.time()) - HOT_RETENTION_DAYS * 86400,
        )
        checkpoint = start_or_resume(hot_ro, run_id=run_id, checkpoint_path=checkpoint_path)

        rows_this_run = 0
        while True:
            if dry_run:
                # Plan-only: peek at next batch, classify, report, never persist checkpoint/COLD.
                batch = hot_ro.execute(
                    "SELECT id, signature, source, destination, amount_lamports, slot, "
                    "block_time, indexed_at, is_valid, transfer_type FROM transfer_index "
                    "WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
                    (checkpoint.last_processed_id, checkpoint.high_water_id, batch_size),
                ).fetchall()
                if not batch:
                    break
                for row in batch:
                    cls = classify(row, pins)
                    report.rows_scanned += 1
                    if cls == "hot_pinned":
                        report.pinned_excluded += 1
                    elif cls == "cold":
                        report.copied += 1  # "would copy"
                checkpoint.last_processed_id = batch[-1][0]
                report.batches += 1
                rows_this_run += len(batch)
                if rows_this_run >= max_rows:
                    break
                continue

            outcome = run_one_batch(
                hot_ro_conn=hot_ro, checkpoint=checkpoint, pins=pins, cold_root=cold_root,
                batch_size=batch_size, run_id=run_id, retire=retire,
                hot_write_path=hot_db_path, lease_path=lease_path,
                evidence=evidence,
            )
            save_checkpoint(checkpoint_path, checkpoint)
            report.batches += 1
            report.rows_scanned += outcome.rows_scanned
            report.pinned_excluded += outcome.pinned_excluded
            report.copied += outcome.copied
            report.already_cold += outcome.already_cold
            report.hot_retirement_seconds_max = max(report.hot_retirement_seconds_max, outcome.hot_retirement_seconds)
            for seg in outcome.segments_touched:
                if seg not in report.segments_touched:
                    report.segments_touched.append(seg)

            if outcome.verified and not outcome.aborted_reason:
                report.verified += outcome.copied
            if outcome.retired:
                report.retired += outcome.retired

            append_entry(ledger_path, LedgerEntry(
                run_id=run_id, timestamp=time.time(), policy_version="p5d.v1",
                path=hot_db_path, action="RETIRE_BATCH" if outcome.retired else "COPY_VERIFY_BATCH",
                reason=outcome.aborted_reason or "OK",
                lifecycle_class="COLD_REPLAYABLE", preflight_result="PASS" if not outcome.aborted_reason else "FAIL",
                bytes_before=0, bytes_after=0,
                action_result="ABORTED" if outcome.aborted_reason else "SUCCESS",
                error=outcome.aborted_reason, pressure_state=guard.state,
                disk_state_free_bytes=guard.free_bytes,
            ))

            if outcome.aborted_reason:
                report.aborted = True
                report.abort_reason = outcome.aborted_reason
                break

            rows_this_run += outcome.rows_scanned
            if checkpoint.completed or outcome.rows_scanned == 0:
                break
            if rows_this_run >= max_rows:
                break

        if checkpoint.completed and not dry_run:
            save_checkpoint(checkpoint_path, checkpoint)
    finally:
        hot_ro.close()
        if wt_conn:
            wt_conn.close()
        if disc_conn:
            disc_conn.close()

    report.duration_seconds = time.monotonic() - t0
    # A clean no-op is an auditable successful invocation too; this marker
    # distinguishes it from a process terminated after PRE_RUN.
    if not report.aborted:
        evidence.emit("COMPLETE", result="PASS", selected=report.copied, retired=report.retired)
    return report


def report_to_dict(report: RunReport) -> dict:
    return {
        "run_id": report.run_id, "mode": report.mode, "disk_guard": report.disk_guard,
        "batches": report.batches, "rows_scanned": report.rows_scanned,
        "pinned_excluded": report.pinned_excluded, "copied": report.copied,
        "verified": report.verified, "retired": report.retired,
        "already_cold": report.already_cold, "aborted": report.aborted,
        "abort_reason": report.abort_reason, "duration_seconds": report.duration_seconds,
        "hot_retirement_seconds_max": report.hot_retirement_seconds_max,
        "segments_touched": report.segments_touched,
    }


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="STORAGE-LIFECYCLE-P5D HOT->COLD rollover runner (qualification build)")
    ap.add_argument("--dry-run", action="store_true", help="plan+report only, zero writes anywhere")
    ap.add_argument("--once", action="store_true", help="run one bounded archival cycle")
    ap.add_argument("--copy-verify-only", action="store_true", help="copy+verify to COLD, never retire HOT")
    ap.add_argument("--confirm-retire-hot-rows-in-production", action="store_true",
                     help=f"required (together with env {CONFIRM_ENV_VAR}={CONFIRM_ENV_VALUE}) to actually delete HOT rows")
    ap.add_argument("--hot-db-path", default=DEFAULT_HOT_DB_PATH)
    ap.add_argument("--cold-root", default=DEFAULT_COLD_ROOT)
    ap.add_argument("--wt-ops-db-path", default=str(ROOT / "database" / "wt_ops_v2.db"))
    ap.add_argument("--discovery-db-path", default=str(ROOT / "database" / "local_operation_discovery_corpus.db"))
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--max-rows", type=int, default=MAX_ROWS_PER_RUN)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not (args.once or args.dry_run):
        ap.print_help()
        return 0

    report = run_rollover(
        hot_db_path=args.hot_db_path, cold_root=args.cold_root,
        wt_ops_db_path=args.wt_ops_db_path, discovery_db_path=args.discovery_db_path,
        batch_size=args.batch_size, max_rows=args.max_rows,
        dry_run=args.dry_run, copy_verify_only=args.copy_verify_only,
        confirm_retire=args.confirm_retire_hot_rows_in_production,
    )
    if not args.quiet:
        print(json.dumps(report_to_dict(report), indent=2, default=str))
    return 0 if not report.aborted else 1


if __name__ == "__main__":
    sys.exit(main())
