#!/usr/bin/env python3
"""
WT_OPS_BOUNDED_MAINTENANCE — cache + telemetry retention runner.

Standalone process (NOT a gunicorn thread, NOT part of watchtower_listener,
NOT part of any Walkback/operation-scheduler transaction). Bounds two
independently-growing tables that were confirmed to have no operation/P3R/
Walkback discovery-authority dependency:

  rpc_response_cache   (database/wt_ops_v2.db)          -- REBUILDABLE_CACHE
  wss_metrics          (database/flex_complete_database.db) -- BOUNDED_TELEMETRY
  wt_subprov_sig_retry (database/wt_ops_v2.db)          -- status='DONE' only;
                          MIXED_ACTIVE_AND_TERMINAL_STATE, qualified by the
                          SUBPROV_SIG_RETRY_LIFECYCLE_QUALIFIED audit.
                          PENDING/RUNNING/FAILED are permanently protected --
                          every query touching this table hardcodes
                          status='DONE'; no code path in this runner can
                          widen it.
  wt_cdc_outbound_events (database/wt_ops_v2.db)        -- raw historical
                          event rows only; BOUNDED_TELEMETRY, qualified by
                          the CDC_LIFECYCLE_QUALIFIED audit. The canonical
                          Capital Distributor Candidate authority table,
                          wt_capital_distributor_candidates, is NEVER touched
                          by this runner -- every query touching the events
                          table hardcodes the table name; no code path here
                          can widen it to the candidates table.

Explicitly does NOT touch:
  wt_candidate_websocket_watches -- MIXED behavioural/topology evidence,
  multiple live consumers (funding_topology.py, campaign_classification.py,
  ws_cascade_store.py) depend on raw row multiplicity / distinct-candidate
  counts / existence semantics that are not yet proven compatible with any
  compaction. NO cleanup, NO TTL, NO writer change to that table here, ever,
  from this runner.

Architecture (mirrors src/core/operation_scheduler.py's isolation contract):
a single standalone process launched via cron/supervisord `--once`. If this
crashes the live app survives; if the live app restarts this survives. Reads
each target DB directly, writes only bounded batches to the two tables named
above, never touches operation/membership/Walkback/behavioural tables.

Run:
    python -m src.ops.cache_metrics_retention_runner --once
    python -m src.ops.cache_metrics_retention_runner --once --dry-run
    python -m src.ops.cache_metrics_retention_runner --once --quiet
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from typing import Optional

from src.core.rpc_cache import RPCCache
from src.metrics import usage_tracker
from src.ops import subprov_sig_retry_retention
from src.ops import cdc_outbound_event_retention
from src.ops.retention_batch_supervisor import run_rpc_batch

# ── NON-NEGOTIABLE APPLICATION PRESERVATION CONTRACT ────────────────────────
# The only two tables this runner (or the primitives it calls) may ever
# mutate. This is defense-in-depth on top of the hardcoded DELETE statements
# in rpc_cache.py/usage_tracker.py -- no wildcard, no dynamically-computed,
# no other table may ever be touched from this codepath.
AUTHORIZED_MUTATION_TABLES = frozenset(
    {"rpc_response_cache", "wss_metrics", "wt_subprov_sig_retry", "wt_cdc_outbound_events"}
)


def _assert_authorized_table(table_name: str) -> None:
    """Hard stop if anything ever tries to mutate a table outside the
    two explicitly authorized targets. Never bypassed, never silenced."""
    if table_name not in AUTHORIZED_MUTATION_TABLES:
        raise RuntimeError(
            f"APPLICATION_SEMANTIC_PRESERVATION_FAILURE: attempted mutation of "
            f"unauthorized table {table_name!r}; only {sorted(AUTHORIZED_MUTATION_TABLES)} "
            f"are permitted under the application preservation contract."
        )


# ── configuration (env-overridable, conservative defaults per WT_OPS audit) ─
WT_OPS_DB_PATH = os.environ.get("WT_OPS_DB_PATH", "database/wt_ops_v2.db")
FLEX_DB_PATH = os.environ.get("DB_PATH", "database/flex_complete_database.db")

RPC_BATCH_SIZE = int(os.environ.get("CACHE_RETENTION_RPC_BATCH_SIZE", "200"))
WSS_BATCH_SIZE = int(os.environ.get("CACHE_RETENTION_WSS_BATCH_SIZE", "1500"))

# Hard per-invocation row ceilings, independent of elapsed-time/WAL/disk
# guards -- the runner will never delete more than these totals in a single
# `run_once()` call regardless of how many batches that would otherwise take.
# Conservative production defaults; the first live activation overrides
# CACHE_RETENTION_MAX_RPC_ROWS_PER_RUN much lower via env var.
MAX_RPC_ROWS_PER_RUN = int(os.environ.get("CACHE_RETENTION_MAX_RPC_ROWS_PER_RUN", "2000"))
MAX_WSS_ROWS_PER_RUN = int(os.environ.get("CACHE_RETENTION_MAX_WSS_ROWS_PER_RUN", "20000"))

WSS_RETENTION_SECONDS = int(os.environ.get("CACHE_RETENTION_WSS_SECONDS", str(7 * 86400)))  # 7 days

# wt_subprov_sig_retry DONE-row retention. 30 days, per the measured
# dedupe-hit-age evidence in wt_subprov_sig_dedupe_stats (max observed hit
# age ~19.7 days across a 43-day observation horizon; zero hits ever
# recorded beyond 30 days). 7 days was considered and rejected -- 16 real
# hits (0.74% of all observed) landed in the 14d-30d bucket.
SUBPROV_SIG_RETRY_RETENTION_SECONDS = int(
    os.environ.get("CACHE_RETENTION_SUBPROV_SIG_RETRY_SECONDS", str(30 * 86400))
)
RETRY_BATCH_SIZE = int(os.environ.get("CACHE_RETENTION_RETRY_BATCH_SIZE", "500"))
MAX_RETRY_ROWS_PER_RUN = int(os.environ.get("CACHE_RETENTION_MAX_RETRY_ROWS_PER_RUN", "5000"))

# wt_cdc_outbound_events raw-event retention. 7 days, per this milestone's
# explicit qualified decision -- the sole consumer (dashboard "recent
# activity" widget) only ever needs the latest 50 rows, but 7 days is kept
# deliberately conservative to preserve ample recent observability/debugging
# history without allowing permanent accumulation. Rows are small (~390
# bytes combined table+index per qualification measurement), so the batch
# size may be materially larger than the RPC-cache phase's.
CDC_RETENTION_SECONDS = int(os.environ.get("CACHE_RETENTION_CDC_SECONDS", str(7 * 86400)))
CDC_BATCH_SIZE = int(os.environ.get("CACHE_RETENTION_CDC_BATCH_SIZE", "1000"))
MAX_CDC_ROWS_PER_RUN = int(os.environ.get("CACHE_RETENTION_MAX_CDC_ROWS_PER_RUN", "5000"))

MAX_ELAPSED_SECONDS = float(os.environ.get("CACHE_RETENTION_MAX_ELAPSED_SEC", "300"))  # 5 min
INTER_BATCH_SLEEP_SECONDS = float(os.environ.get("CACHE_RETENTION_BATCH_SLEEP_SEC", "0.75"))

# Fixed, independent WAL ceilings per DB -- never raised adaptively. If a run
# hits this, it stops cleanly and lets the next scheduled invocation resume
# after natural checkpointing; this runner never issues a manual checkpoint.
WT_OPS_WAL_CEILING_BYTES = int(os.environ.get("CACHE_RETENTION_WT_OPS_WAL_CEILING_BYTES", str(200 * 1024 * 1024)))
FLEX_WAL_CEILING_BYTES = int(os.environ.get("CACHE_RETENTION_FLEX_WAL_CEILING_BYTES", str(200 * 1024 * 1024)))

# Hard emergency floor plus a higher conservative start-of-run threshold,
# consistent with the 2GB MIN_FREE_BYTES convention already used in
# scripts/retire_price_history_batch.py and scripts/retire_liquidity_history_batch.py.
DISK_HARD_FLOOR_BYTES = int(os.environ.get("CACHE_RETENTION_DISK_HARD_FLOOR_BYTES", str(2 * 1024 * 1024 * 1024)))
DISK_START_THRESHOLD_BYTES = int(os.environ.get("CACHE_RETENTION_DISK_START_THRESHOLD_BYTES", str(4 * 1024 * 1024 * 1024)))

DATA_VOLUME_PATH = os.environ.get("CACHE_RETENTION_DISK_CHECK_PATH", "/System/Volumes/Data")
RPC_CHILD_TIMEOUT_SECONDS = float(os.environ.get("CACHE_RETENTION_RPC_CHILD_TIMEOUT_SEC", "12"))


def _wal_bytes(db_path: str) -> int:
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


def _free_disk_bytes(path: str = DATA_VOLUME_PATH) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        # Fall back to the repo's own filesystem if the configured path is
        # unavailable (e.g. a different mount layout in a test environment).
        return shutil.disk_usage(".").free


def run_once(dry_run: bool = False, quiet: bool = False) -> dict:
    """
    Single bounded maintenance pass. Returns a compact counters dict; never
    raises. Safe to call repeatedly (idempotent) and to interrupt at any
    point -- state that matters lives only in the two target tables
    themselves, not in any runner-local file.
    """
    start = time.monotonic()
    now = time.time()
    wss_cutoff = now - WSS_RETENTION_SECONDS
    retry_cutoff = now - SUBPROV_SIG_RETRY_RETENTION_SECONDS
    cdc_cutoff = now - CDC_RETENTION_SECONDS

    counters = {
        "rpc_deleted": 0,
        "rpc_deleted_exact": True,
        "rpc_child_status": None,
        "wss_deleted": 0,
        "retry_done_deleted": 0,
        "cdc_deleted": 0,
        "rpc_remaining_expired": None,
        "wss_remaining_expired": None,
        "retry_done_remaining_expired": None,
        "cdc_remaining_expired": None,
        "rpc_wal_bytes": _wal_bytes(WT_OPS_DB_PATH),
        "wss_wal_bytes": _wal_bytes(FLEX_DB_PATH),
        "free_bytes": _free_disk_bytes(),
        "elapsed_seconds": 0.0,
        "stop_reason": None,
        "dry_run": dry_run,
    }

    # ── disk guards (checked before any work) ───────────────────────────────
    # Hard floor: absolute emergency boundary, never allow any work below it.
    if counters["free_bytes"] < DISK_HARD_FLOOR_BYTES:
        counters["stop_reason"] = "DISK_FLOOR_STOP"
        counters["elapsed_seconds"] = time.monotonic() - start
        return counters
    # Conservative start threshold: this is a routine-hygiene maintenance
    # runner, not an emergency reclaim tool. Below this threshold we refuse
    # to start any mutation at all (not merely warn) -- there is no urgency
    # that justifies spending headroom below this line on cache/telemetry
    # cleanup specifically, versus leaving that margin for the live app.
    if counters["free_bytes"] < DISK_START_THRESHOLD_BYTES:
        counters["stop_reason"] = "DISK_START_THRESHOLD_STOP"
        counters["elapsed_seconds"] = time.monotonic() - start
        return counters

    supervised_rpc = os.environ.get("CACHE_RETENTION_RPC_CHILD_ENABLED", "0") == "1"
    # A supervised parent must not open a write-capable RPC-cache connection;
    # only the bounded child may enter that writer lane.
    rpc_cache = RPCCache(WT_OPS_DB_PATH) if not supervised_rpc or dry_run else None
    # Never preflight rpc_response_cache with COUNT(*).  Production rows have
    # very large overflow payloads and the expiry expression is not indexed;
    # SQLite can therefore spend minutes in one read-only sqlite3_step().
    # MAX_ELAPSED_SECONDS cannot interrupt a statement already executing.
    # The bounded batch primitive is the sole RPC eligibility probe and its
    # SQLite VM now shares the maintenance deadline.  Exact remaining count
    # is deliberately unavailable rather than obtained with an unbounded
    # scan.
    counters["rpc_remaining_expired"] = None
    if MAX_WSS_ROWS_PER_RUN > 0:
        counters["wss_remaining_expired"] = usage_tracker.count_old_wss_metrics(FLEX_DB_PATH, wss_cutoff)
    else:
        counters["wss_remaining_expired"] = None
    if MAX_RETRY_ROWS_PER_RUN > 0:
        counters["retry_done_remaining_expired"] = subprov_sig_retry_retention.count_old_subprov_sig_retry_done(
            WT_OPS_DB_PATH, retry_cutoff)
    else:
        counters["retry_done_remaining_expired"] = None
    if MAX_CDC_ROWS_PER_RUN > 0:
        counters["cdc_remaining_expired"] = cdc_outbound_event_retention.count_old_cdc_outbound_events(
            WT_OPS_DB_PATH, cdc_cutoff)
    else:
        counters["cdc_remaining_expired"] = None

    if dry_run:
        counters["stop_reason"] = "DRY_RUN_NO_MUTATION"
        counters["elapsed_seconds"] = time.monotonic() - start
        return counters

    _STOP_KINDS = ("MAX_ELAPSED_STOP", "DISK_FLOOR_STOP", "WAL_CEILING_STOP",
                   "MAX_ROWS_PER_RUN_STOP", "RPC_CHILD_INDETERMINATE_STOP")

    # ── RPC cache: bounded batches against wt_ops_v2.db ─────────────────────
    # Bug fix: MAX_RPC_ROWS_PER_RUN=0 (used to intentionally disable RPC for
    # a WSS-only invocation) used to fall into the loop's own "ceiling
    # reached" check on its very first iteration, producing the same
    # MAX_ROWS_PER_RUN_STOP that a genuine mid-run stop would -- and since
    # that reason is in _STOP_KINDS, it silently skipped the WSS phase too,
    # even though WSS was fully independent and its own budget was untouched.
    # A zero-or-negative configured ceiling is "RPC intentionally not
    # requested this run," not a stop condition reached mid-work, and must
    # never gate the WSS phase below.
    if MAX_RPC_ROWS_PER_RUN <= 0:
        counters["stop_reason"] = "RPC_DISABLED_THIS_RUN"
    while MAX_RPC_ROWS_PER_RUN > 0:
        if counters["rpc_deleted"] >= MAX_RPC_ROWS_PER_RUN:
            counters["stop_reason"] = "MAX_ROWS_PER_RUN_STOP"
            break

        if time.monotonic() - start >= MAX_ELAPSED_SECONDS:
            counters["stop_reason"] = "MAX_ELAPSED_STOP"
            break

        counters["free_bytes"] = _free_disk_bytes()
        if counters["free_bytes"] < DISK_HARD_FLOOR_BYTES:
            counters["stop_reason"] = "DISK_FLOOR_STOP"
            break

        counters["rpc_wal_bytes"] = _wal_bytes(WT_OPS_DB_PATH)
        if counters["rpc_wal_bytes"] > WT_OPS_WAL_CEILING_BYTES:
            counters["stop_reason"] = "WAL_CEILING_STOP"
            break

        _assert_authorized_table("rpc_response_cache")
        remaining_budget = MAX_RPC_ROWS_PER_RUN - counters["rpc_deleted"]
        this_batch_size = min(RPC_BATCH_SIZE, remaining_budget)
        if supervised_rpc:
            outcome = run_rpc_batch(
                WT_OPS_DB_PATH, this_batch_size, now,
                timeout_seconds=RPC_CHILD_TIMEOUT_SECONDS,
            )
            counters["rpc_child_status"] = outcome["status"]
            print(
                "[RETENTION_RPC_CHILD] "
                f"status={outcome['status']} "
                f"deleted={outcome['deleted'] if outcome['deleted'] is not None else 'unknown'} "
                f"reaped={outcome['child_reaped']}",
                file=sys.stderr, flush=True,
            )
            if outcome["status"] != "complete":
                # Commit may have succeeded just before termination. Never
                # report zero or continue to another batch on uncertainty.
                counters["rpc_deleted_exact"] = False
                counters["stop_reason"] = "RPC_CHILD_INDETERMINATE_STOP"
                break
            deleted = outcome["deleted"]
        else:
            deleted = rpc_cache.cleanup_expired_batch(this_batch_size, now=now)
        counters["rpc_deleted"] += deleted
        if deleted == 0:
            # The bounded primitive deliberately returns 0 for an empty
            # backlog, a busy lane, or an interrupted eligibility scan.  Do
            # not run an unbounded COUNT merely to distinguish observability
            # labels; report the safe neutral result.
            counters["stop_reason"] = "NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE"
            break
        time.sleep(INTER_BATCH_SLEEP_SECONDS)

    if counters["stop_reason"] not in _STOP_KINDS:
        # ── WSS metrics: bounded batches against flex_complete_database.db ─
        # Symmetric fix: a zero-or-negative WSS ceiling means "not requested
        # this run," not a stop condition -- must not overwrite whatever the
        # RPC phase already reported (e.g. leave a genuine
        # MAINTENANCE_SKIPPED_WRITE_LANE_BUSY from RPC visible rather than
        # masking it with an unrelated WSS_DISABLED marker).
        if MAX_WSS_ROWS_PER_RUN <= 0:
            if counters["stop_reason"] is None:
                counters["stop_reason"] = "WSS_DISABLED_THIS_RUN"
        while MAX_WSS_ROWS_PER_RUN > 0:
            if counters["wss_deleted"] >= MAX_WSS_ROWS_PER_RUN:
                counters["stop_reason"] = "MAX_ROWS_PER_RUN_STOP"
                break

            if time.monotonic() - start >= MAX_ELAPSED_SECONDS:
                counters["stop_reason"] = "MAX_ELAPSED_STOP"
                break

            counters["free_bytes"] = _free_disk_bytes()
            if counters["free_bytes"] < DISK_HARD_FLOOR_BYTES:
                counters["stop_reason"] = "DISK_FLOOR_STOP"
                break

            counters["wss_wal_bytes"] = _wal_bytes(FLEX_DB_PATH)
            if counters["wss_wal_bytes"] > FLEX_WAL_CEILING_BYTES:
                counters["stop_reason"] = "WAL_CEILING_STOP"
                break

            _assert_authorized_table("wss_metrics")
            was_first_wss_batch = (counters["wss_deleted"] == 0)
            remaining_budget = MAX_WSS_ROWS_PER_RUN - counters["wss_deleted"]
            this_batch_size = min(WSS_BATCH_SIZE, remaining_budget)
            deleted = usage_tracker.cleanup_old_wss_metrics_batch(FLEX_DB_PATH, this_batch_size, wss_cutoff)
            counters["wss_deleted"] += deleted
            if deleted == 0:
                if was_first_wss_batch and counters["wss_remaining_expired"] and counters["wss_remaining_expired"] > 0:
                    counters["stop_reason"] = "MAINTENANCE_SKIPPED_WRITE_LANE_BUSY"
                else:
                    counters["stop_reason"] = "NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE"
                break
            time.sleep(INTER_BATCH_SLEEP_SECONDS)

    # A prior phase's MAINTENANCE_SKIPPED_WRITE_LANE_BUSY is not itself in
    # _STOP_KINDS (by design -- RPC being lane-busy must not prevent WSS from
    # attempting its own independent lane), but with a third phase now
    # chained after it, a subsequent phase finding genuinely zero eligible
    # rows would silently overwrite that real signal with
    # NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE. Preserve whichever busy/stop
    # signal was set first.
    if counters["stop_reason"] not in _STOP_KINDS and counters["stop_reason"] != "MAINTENANCE_SKIPPED_WRITE_LANE_BUSY":
        # ── wt_subprov_sig_retry DONE rows: bounded batches against
        # wt_ops_v2.db, same WAL ceiling as the RPC phase (same DB file).
        # Hardcoded status='DONE' in every query -- see
        # subprov_sig_retry_retention.py's module docstring.
        if MAX_RETRY_ROWS_PER_RUN <= 0:
            if counters["stop_reason"] is None:
                counters["stop_reason"] = "RETRY_DONE_DISABLED_THIS_RUN"
        while MAX_RETRY_ROWS_PER_RUN > 0:
            if counters["retry_done_deleted"] >= MAX_RETRY_ROWS_PER_RUN:
                counters["stop_reason"] = "MAX_ROWS_PER_RUN_STOP"
                break

            if time.monotonic() - start >= MAX_ELAPSED_SECONDS:
                counters["stop_reason"] = "MAX_ELAPSED_STOP"
                break

            counters["free_bytes"] = _free_disk_bytes()
            if counters["free_bytes"] < DISK_HARD_FLOOR_BYTES:
                counters["stop_reason"] = "DISK_FLOOR_STOP"
                break

            counters["rpc_wal_bytes"] = _wal_bytes(WT_OPS_DB_PATH)
            if counters["rpc_wal_bytes"] > WT_OPS_WAL_CEILING_BYTES:
                counters["stop_reason"] = "WAL_CEILING_STOP"
                break

            _assert_authorized_table("wt_subprov_sig_retry")
            was_first_retry_batch = (counters["retry_done_deleted"] == 0)
            remaining_budget = MAX_RETRY_ROWS_PER_RUN - counters["retry_done_deleted"]
            this_batch_size = min(RETRY_BATCH_SIZE, remaining_budget)
            deleted = subprov_sig_retry_retention.cleanup_old_subprov_sig_retry_done_batch(
                WT_OPS_DB_PATH, this_batch_size, retry_cutoff)
            counters["retry_done_deleted"] += deleted
            if deleted == 0:
                if (was_first_retry_batch and counters["retry_done_remaining_expired"]
                        and counters["retry_done_remaining_expired"] > 0):
                    counters["stop_reason"] = "MAINTENANCE_SKIPPED_WRITE_LANE_BUSY"
                else:
                    counters["stop_reason"] = "NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE"
                break
            time.sleep(INTER_BATCH_SLEEP_SECONDS)

    if counters["stop_reason"] not in _STOP_KINDS and counters["stop_reason"] != "MAINTENANCE_SKIPPED_WRITE_LANE_BUSY":
        # ── wt_cdc_outbound_events: bounded batches against wt_ops_v2.db,
        # same WAL ceiling as the RPC/retry phases (same DB file). Hardcoded
        # table name in every query -- see
        # cdc_outbound_event_retention.py's module docstring. Never touches
        # wt_capital_distributor_candidates (the canonical CDC authority).
        if MAX_CDC_ROWS_PER_RUN <= 0:
            if counters["stop_reason"] is None:
                counters["stop_reason"] = "CDC_DISABLED_THIS_RUN"
        while MAX_CDC_ROWS_PER_RUN > 0:
            if counters["cdc_deleted"] >= MAX_CDC_ROWS_PER_RUN:
                counters["stop_reason"] = "MAX_ROWS_PER_RUN_STOP"
                break

            if time.monotonic() - start >= MAX_ELAPSED_SECONDS:
                counters["stop_reason"] = "MAX_ELAPSED_STOP"
                break

            counters["free_bytes"] = _free_disk_bytes()
            if counters["free_bytes"] < DISK_HARD_FLOOR_BYTES:
                counters["stop_reason"] = "DISK_FLOOR_STOP"
                break

            counters["rpc_wal_bytes"] = _wal_bytes(WT_OPS_DB_PATH)
            if counters["rpc_wal_bytes"] > WT_OPS_WAL_CEILING_BYTES:
                counters["stop_reason"] = "WAL_CEILING_STOP"
                break

            _assert_authorized_table("wt_cdc_outbound_events")
            was_first_cdc_batch = (counters["cdc_deleted"] == 0)
            remaining_budget = MAX_CDC_ROWS_PER_RUN - counters["cdc_deleted"]
            this_batch_size = min(CDC_BATCH_SIZE, remaining_budget)
            deleted = cdc_outbound_event_retention.cleanup_old_cdc_outbound_events_batch(
                WT_OPS_DB_PATH, this_batch_size, cdc_cutoff)
            counters["cdc_deleted"] += deleted
            if deleted == 0:
                if (was_first_cdc_batch and counters["cdc_remaining_expired"]
                        and counters["cdc_remaining_expired"] > 0):
                    counters["stop_reason"] = "MAINTENANCE_SKIPPED_WRITE_LANE_BUSY"
                else:
                    counters["stop_reason"] = "NO_ELIGIBLE_ROWS_OR_BATCH_UNAVAILABLE"
                break
            time.sleep(INTER_BATCH_SLEEP_SECONDS)

    if counters["stop_reason"] is None:
        counters["stop_reason"] = "COMPLETE"

    # rpc_remaining_expired intentionally remains None: an exact final count
    # would repeat the same unbounded full-table scan removed above.
    if MAX_WSS_ROWS_PER_RUN > 0:
        counters["wss_remaining_expired"] = usage_tracker.count_old_wss_metrics(FLEX_DB_PATH, wss_cutoff)
    if MAX_RETRY_ROWS_PER_RUN > 0:
        counters["retry_done_remaining_expired"] = subprov_sig_retry_retention.count_old_subprov_sig_retry_done(
            WT_OPS_DB_PATH, retry_cutoff)
    if MAX_CDC_ROWS_PER_RUN > 0:
        counters["cdc_remaining_expired"] = cdc_outbound_event_retention.count_old_cdc_outbound_events(
            WT_OPS_DB_PATH, cdc_cutoff)
    counters["rpc_wal_bytes"] = _wal_bytes(WT_OPS_DB_PATH)
    counters["wss_wal_bytes"] = _wal_bytes(FLEX_DB_PATH)
    counters["free_bytes"] = _free_disk_bytes()
    counters["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return counters


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded retention for rpc_response_cache, wss_metrics, "
                     "wt_subprov_sig_retry (status='DONE' only), and "
                     "wt_cdc_outbound_events -- no other tables.")
    parser.add_argument("--once", action="store_true", help="Run a single bounded pass and exit.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Report eligible-row counts and guard state; perform zero mutation.")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-essential stdout.")
    args = parser.parse_args()

    if not args.once:
        parser.print_help()
        return 1

    counters = run_once(dry_run=args.dry_run, quiet=args.quiet)

    if not args.quiet:
        print(json.dumps(counters, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
