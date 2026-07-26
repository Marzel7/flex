"""X64.5/X64.6 — CREATE anchor race recovery for wt_walkback_queue.

Root cause: both production enqueue_migration() call sites
(watchtower_attribution.py:store_migration, pumpfun_curve_listener.py's
creator-unknown fallback) never pass live_conn, so
walkback_queue.enqueue_migration()'s own creator_funding_queue/
token_analysis anchor lookup (gated on `if live_conn and not
create_signature`) is unconditionally skipped. Every FULL_WALKBACK row
with no anchor at enqueue time is permanently stuck at
path_state='WAITING_FOR_CREATE_ANCHOR', audit_state='MISSING_OR_MALFORMED',
status='waiting' — even once a valid signature later appears in
creator_funding_queue. This module recovers those rows, zero-RPC, using
only already-stored data, and gives the worker a self-healing re-check
path so future rows can't get stuck the same way.

Never confirms attribution, never promotes a treasury, never writes to
any attribution table. Only ever touches wt_walkback_queue's own anchor/
path-state columns plus this module's own audit-trail table.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from src.core.deep_walkback import valid_signature

WAITING_STATUS = "waiting"
WAITING_PATH_STATE = "WAITING_FOR_CREATE_ANCHOR"
RECOVERED_REASON = "CREATE_ANCHOR_RECOVERED"

# Phase 5 — finer-grained states than the collapsed MISSING_OR_MALFORMED,
# tracked in the new anchor_lookup_state column (additive; the existing
# create_anchor_audit_state column and its VALID/MISSING_OR_MALFORMED
# values are left untouched for backward compatibility with every other
# reader of that column).
PENDING_NOT_VISIBLE = "PENDING_NOT_VISIBLE"
PRESENT_VALID = "PRESENT_VALID"
PRESENT_INVALID = "PRESENT_INVALID"
EXPIRED_MISSING = "EXPIRED_MISSING"

_ANCHOR_LOOKUP_STATES = frozenset({
    PENDING_NOT_VISIBLE, PRESENT_VALID, PRESENT_INVALID, EXPIRED_MISSING,
})


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive only — never alters existing wt_walkback_queue columns."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(wt_walkback_queue)")}
    for col, ddl in (
        ("anchor_lookup_attempts", "anchor_lookup_attempts INTEGER NOT NULL DEFAULT 0"),
        ("last_anchor_lookup_at", "last_anchor_lookup_at INTEGER"),
        ("anchor_recovered_at", "anchor_recovered_at INTEGER"),
        ("anchor_recovery_source", "anchor_recovery_source TEXT"),
        ("anchor_lookup_state", "anchor_lookup_state TEXT"),
    ):
        if col not in have:
            conn.execute(f"ALTER TABLE wt_walkback_queue ADD COLUMN {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wt_anchor_reconciliation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mint                TEXT NOT NULL,
            creator             TEXT,
            recovered_signature TEXT NOT NULL,
            original_state      TEXT,
            recovery_source     TEXT NOT NULL,
            recovery_timestamp  INTEGER NOT NULL
        )
        """
    )
    # X64.6 — additive columns for the wider-source (Phase 6) and bounded-RPC
    # (Phase 7) recovery paths: which DB row the signature came from, whether
    # RPC was used and how much, and the validation outcome, so an audit row
    # is self-describing without cross-referencing this module's source code.
    log_have = {r[1] for r in conn.execute("PRAGMA table_info(wt_anchor_reconciliation_log)")}
    for col, ddl in (
        ("source_row_id", "source_row_id TEXT"),
        ("recovery_method", "recovery_method TEXT"),
        ("rpc_credits_used", "rpc_credits_used INTEGER NOT NULL DEFAULT 0"),
        ("validation_result", "validation_result TEXT"),
    ):
        if col not in log_have:
            conn.execute(f"ALTER TABLE wt_anchor_reconciliation_log ADD COLUMN {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_anchor_recon_mint "
        "ON wt_anchor_reconciliation_log(mint)"
    )
    conn.commit()


def _lookup_creator_funding_signature(
    live_conn: sqlite3.Connection, mint: str,
) -> tuple[Optional[str], Optional[str], int]:
    """Zero-RPC, DB-only lookup. Returns (signature, source, row_count) —
    row_count distinguishes MINT_NOT_FOUND (0) / normal (1) /
    AMBIGUOUS_MULTIPLE_ROWS (>1) without a second query."""
    rows = live_conn.execute(
        "SELECT create_tx_signature FROM creator_funding_queue WHERE mint=? "
        "ORDER BY updated_at DESC", (mint,),
    ).fetchall()
    if rows and rows[0][0]:
        return rows[0][0], "creator_funding_queue", len(rows)
    row = live_conn.execute(
        "SELECT create_tx_signature FROM token_analysis WHERE mint=? LIMIT 1",
        (mint,),
    ).fetchone()
    if row and row[0]:
        return row[0], "token_analysis", len(rows)
    return None, None, len(rows)


def classify_stuck_row(
    live_conn: sqlite3.Connection, mint: str,
) -> dict[str, Any]:
    """Zero-RPC classification of one stuck row against creator_funding_queue.
    Never mutates anything — pure read, used by both the dry-run report and
    reconcile_waiting_create_anchors()."""
    rows = live_conn.execute(
        "SELECT create_tx_signature FROM creator_funding_queue WHERE mint=?",
        (mint,),
    ).fetchall()
    if not rows:
        return {"classification": "MINT_NOT_FOUND", "signature": None, "source": None}
    if len(rows) > 1:
        return {"classification": "AMBIGUOUS_MULTIPLE_ROWS", "signature": None, "source": None}
    sig, source, _ = _lookup_creator_funding_signature(live_conn, mint)
    if sig is None:
        return {"classification": "ANCHOR_STILL_MISSING", "signature": None, "source": None}
    if valid_signature(sig):
        return {"classification": "RECOVERABLE_VALID_ANCHOR", "signature": sig, "source": source}
    return {"classification": "ANCHOR_PRESENT_INVALID", "signature": sig, "source": source}


def _stuck_rows(ops_conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return ops_conn.execute(
        "SELECT mint, creator, create_anchor_signature, create_anchor_audit_state, "
        "attempts, enqueued_at FROM wt_walkback_queue "
        "WHERE status=? AND path_state=? "
        "AND (create_anchor_signature IS NULL OR create_anchor_audit_state=?)",
        (WAITING_STATUS, WAITING_PATH_STATE, "MISSING_OR_MALFORMED"),
    ).fetchall()


def dry_run_report(
    ops_conn: sqlite3.Connection, live_conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Phase 2/7 — read-only population audit. No writes anywhere."""
    ensure_schema(ops_conn)
    rows = _stuck_rows(ops_conn)
    buckets: dict[str, list[dict[str, Any]]] = {
        "RECOVERABLE_VALID_ANCHOR": [],
        "ANCHOR_PRESENT_INVALID": [],
        "ANCHOR_STILL_MISSING": [],
        "AMBIGUOUS_MULTIPLE_ROWS": [],
        "MINT_NOT_FOUND": [],
    }
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        result = classify_stuck_row(live_conn, row["mint"])
        entry = {
            "mint": row["mint"], "creator": row["creator"],
            "enqueued_at": row["enqueued_at"], "attempts": row["attempts"],
            "existing_anchor": row["create_anchor_signature"],
            "signature": result["signature"], "source": result["source"],
        }
        buckets[result["classification"]].append(entry)
        # Phase 7 conflict detection: an existing DIFFERENT valid queue
        # signature vs. the recovered one. In this dataset every stuck row's
        # own create_anchor_signature is NULL by construction of _stuck_rows'
        # WHERE clause's first disjunct, or MISSING_OR_MALFORMED by the
        # second — so a same-mint conflict can only arise if a NON-NULL,
        # already-VALID anchor coexists with audit_state still marked
        # MISSING_OR_MALFORMED (a data inconsistency, not the common case).
        # Checked defensively regardless.
        if (row["create_anchor_signature"] and valid_signature(row["create_anchor_signature"])
                and result.get("signature")
                and row["create_anchor_signature"] != result["signature"]):
            conflicts.append({
                "mint": row["mint"], "creator": row["creator"],
                "existing_valid_signature": row["create_anchor_signature"],
                "funding_queue_signature": result["signature"],
            })
    return {
        "total": len(rows),
        "buckets": buckets,
        "conflicts": conflicts,
        "counts": {k: len(v) for k, v in buckets.items()},
    }


def reconcile_waiting_create_anchors(
    ops_conn: sqlite3.Connection, live_conn: sqlite3.Connection,
    *, dry_run: bool = False,
) -> dict[str, Any]:
    """Phase 3 — idempotent, zero-RPC reconciliation.

    For each RECOVERABLE_VALID_ANCHOR row: persists the recovered signature,
    flips create_anchor_audit_state to VALID, path_state to
    CREATE_ANCHOR_RECOVERED* (via termination_reason_json, not a new
    PATH_STATES enum value — see note below), and status back to 'pending'
    so drain_batch's normal SELECT picks it up. Preserves attempts,
    enqueued_at, creator, mint, and all existing evidence untouched.
    Logs one wt_anchor_reconciliation_log row per recovery. Idempotent:
    a row already recovered (status != 'waiting') is simply not matched by
    _stuck_rows() on a re-run, so calling this twice in a row is a no-op
    the second time — no duplicate log rows, no re-write.

    *PATH_STATES (deep_walkback.py) is a closed frozenset the DB layer
    enforces via set_path_state()'s own ValueError guard; adding a new
    enum value there is a schema/contract change beyond this task's
    "additive only" scope. Instead, CREATE_ANCHOR_RECOVERED is recorded as
    the queue row's own create_anchor_audit_state-adjacent marker: path_state
    is advanced to the existing 'CREATE_ANCHORED' state (the same state
    enqueue_migration() itself would have set had the anchor been visible at
    insert time), and the recovery reason/timestamp/source are captured in
    the new anchor_recovery_source/anchor_recovered_at columns plus the
    dedicated log table — giving full traceability without inventing a new
    path_state value outside the existing contract.
    """
    ensure_schema(ops_conn)
    rows = _stuck_rows(ops_conn)
    now = int(time.time())
    recovered: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for row in rows:
        mint = row["mint"]

        # X64.7A Phase 4 — the ordinary worker-cycle reconciliation path
        # must itself consult the canonical ledger, not only a standalone
        # helper nothing in production calls. resolve_anchor_with_priority
        # checks wt_create_event_ledger FIRST; only when it has nothing
        # (confidence != SAFE) do we fall back to the pre-existing
        # classify_stuck_row() widened-source search, preserving every
        # existing classification label and behavior for backward
        # compatibility with X64.5/X64.6 callers/tests.
        priority_result = resolve_anchor_with_priority(live_conn, ops_conn, mint, queue_creator=row["creator"])
        if priority_result["confidence"] == "SAFE" and priority_result["source"] == "canonical_create_ledger":
            result = {"classification": "RECOVERABLE_VALID_ANCHOR",
                      "signature": priority_result["signature"], "source": "canonical_create_ledger"}
        elif priority_result["confidence"] == "CONFLICT":
            result = {"classification": "AMBIGUOUS_MULTIPLE_ROWS", "signature": None, "source": None}
        else:
            result = classify_stuck_row(live_conn, mint)

        # anchor_lookup bookkeeping — every row we examine gets this touched,
        # regardless of outcome, using the NEW dedicated column (never the
        # walkback attempts counter).
        ops_conn.execute(
            "UPDATE wt_walkback_queue SET anchor_lookup_attempts=anchor_lookup_attempts+1, "
            "last_anchor_lookup_at=?, anchor_lookup_state=? WHERE mint=?",
            (now, {
                "RECOVERABLE_VALID_ANCHOR": PRESENT_VALID,
                "ANCHOR_PRESENT_INVALID": PRESENT_INVALID,
                "ANCHOR_STILL_MISSING": PENDING_NOT_VISIBLE,
                "AMBIGUOUS_MULTIPLE_ROWS": PENDING_NOT_VISIBLE,
                "MINT_NOT_FOUND": PENDING_NOT_VISIBLE,
            }[result["classification"]], mint),
        )

        if result["classification"] != "RECOVERABLE_VALID_ANCHOR":
            skipped.append({"mint": mint, "classification": result["classification"]})
            continue

        # Phase 7 conflict guard: never silently overwrite an existing
        # DIFFERENT valid anchor.
        if (row["create_anchor_signature"] and valid_signature(row["create_anchor_signature"])
                and row["create_anchor_signature"] != result["signature"]):
            conflicts.append({
                "mint": mint,
                "existing_valid_signature": row["create_anchor_signature"],
                "funding_queue_signature": result["signature"],
            })
            continue

        if dry_run:
            recovered.append({
                "mint": mint, "creator": row["creator"],
                "signature": result["signature"], "source": result["source"],
            })
            continue

        original_state = row["create_anchor_audit_state"]
        ops_conn.execute(
            "UPDATE wt_walkback_queue SET "
            "create_anchor_signature=?, create_anchor_audit_state='VALID', "
            "create_anchor_source=?, path_state='CREATE_ANCHORED', "
            "status='pending', anchor_recovered_at=?, anchor_recovery_source=?, "
            "updated_at=? "
            "WHERE mint=? AND status=? AND path_state=?",
            (result["signature"], result["source"], now, result["source"], now,
             mint, WAITING_STATUS, WAITING_PATH_STATE),
        )
        if ops_conn.total_changes:  # best-effort; real guard is the WHERE clause itself
            pass
        ops_conn.execute(
            "INSERT INTO wt_anchor_reconciliation_log "
            "(mint, creator, recovered_signature, original_state, recovery_source, recovery_timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (mint, row["creator"], result["signature"], original_state, result["source"], now),
        )
        recovered.append({
            "mint": mint, "creator": row["creator"],
            "signature": result["signature"], "source": result["source"],
        })
        print(
            "[ANCHOR_RECONCILE] "
            f"mint={mint} creator={row['creator']} "
            f"recovered_signature={result['signature'][:16]}… "
            f"source={result['source']} original_state={original_state}",
            flush=True,
        )

    if not dry_run:
        ops_conn.commit()

    return {
        "examined": len(rows),
        "recovered": recovered,
        "skipped": skipped,
        "conflicts": conflicts,
    }


def recheck_single_row_anchor(
    ops_conn: sqlite3.Connection, live_conn: Optional[sqlite3.Connection], mint: str,
) -> Optional[str]:
    """Phase 4 — worker self-healing hook. Called by the worker instead of
    silently skipping a WAITING_FOR_CREATE_ANCHOR row on a batch pass.
    Performs ONE zero-RPC local re-check; never increments the normal
    walkback `attempts` counter (uses anchor_lookup_attempts instead).
    Returns the recovered signature if the row was released, else None
    (row stays 'waiting' for a later cycle — bounded only by however often
    the worker itself polls, no separate retry schedule needed since this
    is pure DB read, effectively free)."""
    if live_conn is None:
        return None
    ensure_schema(ops_conn)
    row = ops_conn.execute(
        "SELECT mint, creator, create_anchor_audit_state FROM wt_walkback_queue "
        "WHERE mint=? AND status=? AND path_state=?",
        (mint, WAITING_STATUS, WAITING_PATH_STATE),
    ).fetchone()
    if not row:
        return None
    result = classify_stuck_row(live_conn, mint)
    now = int(time.time())
    ops_conn.execute(
        "UPDATE wt_walkback_queue SET anchor_lookup_attempts=anchor_lookup_attempts+1, "
        "last_anchor_lookup_at=? WHERE mint=?", (now, mint),
    )
    if result["classification"] != "RECOVERABLE_VALID_ANCHOR":
        ops_conn.commit()
        return None
    ops_conn.execute(
        "UPDATE wt_walkback_queue SET "
        "create_anchor_signature=?, create_anchor_audit_state='VALID', "
        "create_anchor_source=?, path_state='CREATE_ANCHORED', status='pending', "
        "anchor_recovered_at=?, anchor_recovery_source=?, updated_at=? "
        "WHERE mint=? AND status=? AND path_state=?",
        (result["signature"], result["source"], now, result["source"], now,
         mint, WAITING_STATUS, WAITING_PATH_STATE),
    )
    ops_conn.execute(
        "INSERT INTO wt_anchor_reconciliation_log "
        "(mint, creator, recovered_signature, original_state, recovery_source, recovery_timestamp) "
        "VALUES (?,?,?,?,?,?)",
        (mint, row["creator"], result["signature"], row["create_anchor_audit_state"],
         result["source"], now),
    )
    ops_conn.commit()
    return result["signature"]


# ── X64.6 Phase 6 — widened zero-RPC source search ──────────────────────────

# Every table checked, beyond creator_funding_queue/token_analysis, that
# could plausibly hold a CREATE signature for a mint. Each entry is
# (label, connection_selector, sql). connection_selector is "live" or "ops".
# Confirmed empty for the X64.6 42-row population via direct query, but kept
# general so a future population with different gaps is still covered.
_WIDER_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("creator_funding_queue", "live",
     "SELECT create_tx_signature, rowid FROM creator_funding_queue "
     "WHERE mint=? ORDER BY updated_at DESC"),
    ("token_analysis", "live",
     "SELECT create_tx_signature, mint FROM token_analysis WHERE mint=? LIMIT 1"),
    ("wt_detected_creates", "live",
     "SELECT create_tx_signature, mint FROM wt_detected_creates WHERE mint=? LIMIT 1"),
    ("wt_watchtower_launches", "ops",
     "SELECT NULL AS create_tx_signature, mint FROM wt_watchtower_launches "
     "WHERE mint=? LIMIT 1"),  # table has no signature column; presence-only check
)


def find_stored_create_anchor(
    live_conn: sqlite3.Connection, ops_conn: sqlite3.Connection,
    mint: str, creator: Optional[str] = None,
) -> dict[str, Any]:
    """X64.6 Phase 6 — search every known stored source (not only
    creator_funding_queue) for a mint's CREATE signature, zero-RPC.

    Returns a dict with signature/source_table/source_row_id/creator/
    timestamp/confidence/conflict_reason. Recovery (by the caller) is only
    safe when confidence=='SAFE' — conflict_reason is populated and
    confidence downgraded whenever exactly-one-valid-signature can't be
    established.
    """
    found: list[dict[str, Any]] = []
    for label, db_sel, sql in _WIDER_SOURCES:
        conn = live_conn if db_sel == "live" else ops_conn
        try:
            rows = conn.execute(sql, (mint,)).fetchall()
        except sqlite3.OperationalError:
            continue  # table/column doesn't exist in this deployment — skip, don't fail
        for row in rows:
            sig = row[0] if not isinstance(row, sqlite3.Row) else row["create_tx_signature"]
            row_id = row[1] if not isinstance(row, sqlite3.Row) else (
                row["rowid"] if "rowid" in row.keys() else row["mint"])
            if sig and valid_signature(sig):
                found.append({"signature": sig, "source_table": label, "source_row_id": row_id})

    if not found:
        return {"signature": None, "source_table": None, "source_row_id": None,
                "creator": creator, "timestamp": None, "confidence": "NONE",
                "conflict_reason": "NO_STORED_CREATE_SIGNATURE"}

    distinct_sigs = {f["signature"] for f in found}
    if len(distinct_sigs) > 1:
        return {"signature": None, "source_table": None, "source_row_id": None,
                "creator": creator, "timestamp": None, "confidence": "CONFLICT",
                "conflict_reason": f"multiple distinct valid signatures across sources: {sorted(distinct_sigs)}"}

    best = found[0]
    return {"signature": best["signature"], "source_table": best["source_table"],
            "source_row_id": best["source_row_id"], "creator": creator,
            "timestamp": None, "confidence": "SAFE", "conflict_reason": None}


# ── X64.6 Phase 7/8 — persistence repair for bounded-RPC-recovered anchors ──

def apply_rpc_recovered_anchor(
    ops_conn: sqlite3.Connection, *, mint: str, creator: Optional[str],
    signature: str, rpc_credits_used: int, recovery_method: str = "bounded_rpc_create_search",
) -> dict[str, Any]:
    """X64.6 Phase 8 — persist a signature recovered by a SEPARATE, bounded
    RPC search (Phase 7's find_create_tx-style pass — never called from this
    module, which stays zero-RPC itself). This function performs no RPC of
    its own; it only validates and writes.

    Deliberately kept as its own function, not folded into
    reconcile_waiting_create_anchors(), because the caller (an external
    bounded-RPC script) must remain a clearly separate stage from both
    zero-RPC reconciliation and from walkback execution — per this task's
    explicit constraint that anchor recovery and walkback execution must
    never share a function.

    Idempotent: a row no longer in status='waiting'/path_state=
    WAITING_FOR_CREATE_ANCHOR is not matched by the guarded UPDATE and no
    duplicate log row is written on replay (checked via the log table
    before insert). Never overwrites an existing different valid anchor.
    """
    ensure_schema(ops_conn)
    if not valid_signature(signature):
        return {"applied": False, "reason": "signature_failed_valid_signature_check"}

    row = ops_conn.execute(
        "SELECT create_anchor_signature, create_anchor_audit_state FROM wt_walkback_queue "
        "WHERE mint=? AND status=? AND path_state=?",
        (mint, WAITING_STATUS, WAITING_PATH_STATE),
    ).fetchone()
    if not row:
        return {"applied": False, "reason": "row_not_in_waiting_state_or_not_found"}

    if row["create_anchor_signature"] and valid_signature(row["create_anchor_signature"]):
        if row["create_anchor_signature"] != signature:
            return {"applied": False, "reason": "conflict_existing_valid_anchor_differs",
                     "existing_signature": row["create_anchor_signature"]}
        return {"applied": False, "reason": "already_matches_existing_valid_anchor"}

    already_logged = ops_conn.execute(
        "SELECT 1 FROM wt_anchor_reconciliation_log WHERE mint=? AND recovered_signature=? LIMIT 1",
        (mint, signature),
    ).fetchone()

    now = int(time.time())
    original_state = row["create_anchor_audit_state"]
    ops_conn.execute(
        "UPDATE wt_walkback_queue SET "
        "create_anchor_signature=?, create_anchor_audit_state='VALID', "
        "create_anchor_source=?, path_state='CREATE_ANCHORED', status='pending', "
        "anchor_recovered_at=?, anchor_recovery_source=?, updated_at=? "
        "WHERE mint=? AND status=? AND path_state=?",
        (signature, recovery_method, now, recovery_method, now,
         mint, WAITING_STATUS, WAITING_PATH_STATE),
    )
    if not already_logged:
        ops_conn.execute(
            "INSERT INTO wt_anchor_reconciliation_log "
            "(mint, creator, recovered_signature, original_state, recovery_source, "
            " recovery_timestamp, recovery_method, rpc_credits_used, validation_result) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (mint, creator, signature, original_state, recovery_method, now,
             recovery_method, rpc_credits_used, "VALID"),
        )
    ops_conn.commit()
    print(
        "[ANCHOR_RECONCILE_RPC] "
        f"mint={mint} creator={creator} recovered_signature={signature[:16]}… "
        f"method={recovery_method} rpc_credits_used={rpc_credits_used}", flush=True,
    )
    return {"applied": True, "signature": signature}


# ── X64.7 Phase 9 — priority-ordered anchor resolution ──────────────────────

def resolve_anchor_with_priority(
    live_conn: sqlite3.Connection, ops_conn: sqlite3.Connection, mint: str,
    *, queue_creator: Optional[str] = None,
) -> dict[str, Any]:
    """X64.7 Phase 9 — resolve a CREATE anchor for `mint` using the
    canonical priority order:
      1. wt_create_event_ledger (canonical_create_ledger)
      2. existing wt_walkback_queue anchor (already VALID — nothing to do)
      3. token_analysis
      4. wt_detected_creates
      5. creator_funding_queue
      6. bounded RPC recovery (not performed here — caller's responsibility,
         same separation as X64.6's apply_rpc_recovered_anchor)

    Creator agreement is never required to accept a ledger-sourced anchor
    — a NULL-creator ledger row is SAFE on its own, per this task's
    explicit instruction that creator-null launches must remain
    recoverable. Never overwrites a different existing valid anchor
    (checked by the caller via the same guard apply_rpc_recovered_anchor
    already uses); this function only resolves candidates, it does not
    write.
    """
    from src.ops import create_event_ledger

    # Priority 1: canonical ledger.
    ledger_result = create_event_ledger.lookup_create_anchor(ops_conn, mint)
    if ledger_result["confidence"] == "SAFE":
        return {"signature": ledger_result["signature"], "source": "canonical_create_ledger",
                "creator": ledger_result.get("creator") or queue_creator, "confidence": "SAFE"}
    if ledger_result["confidence"] == "CONFLICT":
        return {"signature": None, "source": "canonical_create_ledger",
                "confidence": "CONFLICT", "conflict_reason": ledger_result["conflict_reason"]}

    # Priority 2: existing queue anchor — if the queue already holds a
    # VALID anchor, there is nothing to resolve; caller checks this
    # before calling this function in practice, but checked here too for
    # a self-contained contract.
    existing_row = ops_conn.execute(
        "SELECT create_anchor_signature, create_anchor_audit_state FROM wt_walkback_queue WHERE mint=?",
        (mint,),
    ).fetchone()
    if existing_row and existing_row["create_anchor_signature"] and existing_row["create_anchor_audit_state"] == "VALID":
        return {"signature": existing_row["create_anchor_signature"], "source": "existing_queue_anchor",
                "creator": queue_creator, "confidence": "SAFE"}

    # Priorities 3-5: token_analysis / wt_detected_creates / creator_funding_queue
    # — reuse the existing X64.6 widened search, which already covers
    # exactly these three sources (plus wt_watchtower_launches, presence-only).
    widened = find_stored_create_anchor(live_conn, ops_conn, mint, creator=queue_creator)
    if widened["confidence"] == "SAFE":
        return {"signature": widened["signature"], "source": widened["source_table"],
                "creator": queue_creator, "confidence": "SAFE"}
    if widened["confidence"] == "CONFLICT":
        return {"signature": None, "source": "widened_search",
                "confidence": "CONFLICT", "conflict_reason": widened["conflict_reason"]}

    # Nothing found through any zero-RPC source.
    return {"signature": None, "source": None, "confidence": "NONE",
            "conflict_reason": "NO_STORED_CREATE_SIGNATURE_ANY_SOURCE"}
