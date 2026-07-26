"""X64.7 — Canonical CREATE-event ledger.

Root cause (see docs/design/x64_7/x64_7_create_pipeline_audit.md): the
live listener's `_update_token_entry_with_creator()`
(src/core/pumpfun_curve_listener.py:7788) is the ONLY function that ever
writes `bonding_curve_pda`/`create_tx_signature` to `token_analysis`, and
it is only reachable when `earliest_creator` was already resolved
(pumpfun_curve_listener.py:8996, `if earliest_creator:`). The RPC-based
provenance walk that would resolve a creator AND extract
`bonding_curve_pda`/`create_tx_signature` together
(`PostMigrationAnalyzer.get_creator_from_earliest_tx()`) only runs when
`CREATOR_BACKFILL_ENABLED != "0"` — and `run_listener.sh` sets
`CREATOR_BACKFILL_ENABLED=0` in production (deliberately, to stop RPC
paging from starving live migration capture — a real, documented
tradeoff, not a bug). The side effect: any mint whose fast-path creator
lookup comes up empty gets NO bonding_curve_pda/create_tx_signature
EVER, regardless of whether a creator is later resolved through a
different path (e.g. the birth reconciler, `migration_signal_source=
'birth'`), because creator resolution and CREATE-signature capture are
currently coupled into a single all-or-nothing write.

This module decouples them: a canonical, append-only ledger of observed
CREATE instructions, written as early as mint is known — independent of
creator resolution, funding extraction, wrap-close detection, or
attribution. Lives in the ops DB (wt_ops_v2.db), alongside every other
X64.x durable-intelligence table, not the live/production DB.

Never confirms attribution, never writes subprov/treasury, never requires
a creator to persist a CREATE observation.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from src.core.deep_walkback import valid_signature

SOURCE_WEBSOCKET = "WEBSOCKET"
SOURCE_RECONCILER = "RECONCILER"
SOURCE_WEBHOOK = "WEBHOOK"
SOURCE_BACKFILL = "BACKFILL"

CREATOR_RESOLUTION_UNRESOLVED = "UNRESOLVED"
CREATOR_RESOLUTION_RESOLVED = "RESOLVED"
# X64.7A — PENDING marks the initial, pre-creator-inference write: the
# CREATE was validated and durably committed, but creator inference has
# not yet run (as distinct from UNRESOLVED, which means inference ran
# and genuinely found nothing). Callers that never distinguish the two
# stages may simply never pass PENDING — it is optional, not required.
CREATOR_RESOLUTION_PENDING = "PENDING"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive only. signature is the primary key (durable, unique);
    mint is required; creator is nullable by design — a missing creator
    must never block this table's own INSERT."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wt_create_event_ledger (
            signature                TEXT PRIMARY KEY,
            mint                     TEXT NOT NULL,
            creator                  TEXT,
            slot                     INTEGER,
            block_time               INTEGER,
            observed_at              INTEGER NOT NULL,
            source                   TEXT NOT NULL,
            parser_path              TEXT,
            tx_version               INTEGER,
            instruction_index        INTEGER,
            inner_instruction_index  INTEGER,
            raw_detection_method     TEXT,
            creator_resolution_state TEXT,
            persistence_version      INTEGER NOT NULL DEFAULT 1,
            first_seen_at            INTEGER NOT NULL,
            last_seen_at             INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_wt_create_event_ledger_mint_signature "
        "ON wt_create_event_ledger(mint, signature)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wt_create_event_ledger_mint "
        "ON wt_create_event_ledger(mint)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wt_create_event_ledger_creator "
        "ON wt_create_event_ledger(creator)"
    )
    # Phase 8 conflict/audit table — same-signature-different-mint, or a
    # later creator disagreeing with an existing non-NULL creator, are
    # never silently resolved; both are recorded here for manual review.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wt_create_ledger_conflicts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conflict_type   TEXT NOT NULL,
            signature       TEXT NOT NULL,
            mint            TEXT NOT NULL,
            existing_value  TEXT,
            incoming_value  TEXT,
            detected_at     INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wt_create_ledger_conflicts_sig "
        "ON wt_create_ledger_conflicts(signature)"
    )
    # X64.7A Phase 2 — durable retry queue. A failed ledger write must not
    # exist only in logs: the full payload needed to retry is persisted
    # here so a retry worker (or the next run_loop cycle) can complete the
    # write later without re-deriving anything from chain (zero RPC).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wt_create_ledger_pending (
            signature     TEXT PRIMARY KEY,
            mint          TEXT NOT NULL,
            creator       TEXT,
            slot          INTEGER,
            block_time    INTEGER,
            source        TEXT NOT NULL,
            parser_path   TEXT,
            payload_json  TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT,
            next_retry_at INTEGER,
            created_at    INTEGER NOT NULL,
            updated_at    INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wt_create_ledger_pending_retry "
        "ON wt_create_ledger_pending(next_retry_at)"
    )
    conn.commit()


def record_create_event(
    conn: sqlite3.Connection, *, signature: str, mint: str,
    creator: Optional[str] = None, slot: Optional[int] = None,
    block_time: Optional[int] = None, source: str,
    parser_path: Optional[str] = None, tx_version: Optional[int] = None,
    instruction_index: int = -1, inner_instruction_index: int = -1,
    raw_detection_method: Optional[str] = None,
    creator_resolution_state: Optional[str] = None,
) -> dict[str, Any]:
    """Phase 7/8 — the canonical write point. Must be called as early as
    mint is known and the CREATE instruction is validated — independent
    of creator resolution. Idempotent on (signature): a duplicate
    observation of the same signature updates last_seen_at and fills a
    NULL creator if one is now known, but never silently overwrites an
    existing non-NULL creator or reassigns the signature to a different
    mint.

    `creator_resolution_state`, if supplied, overrides the auto-derived
    RESOLVED/UNRESOLVED value — used by callers implementing the X64.7A
    two-stage write (PENDING before creator inference, then RESOLVED/
    UNRESOLVED once inference has actually run) so that "inference
    hasn't run yet" is distinguishable from "inference ran and found
    nothing." Callers that only ever call this once (the common,
    X64.7-era case) can omit it — auto-derivation from `creator` covers
    that case unchanged.

    Returns {"written": True/False, "conflict": None or dict, "state": ...}.
    """
    ensure_schema(conn)
    if not mint:
        return {"written": False, "reason": "mint_required"}
    if not signature or not valid_signature(signature):
        return {"written": False, "reason": "invalid_or_missing_signature"}

    now = int(time.time())
    creator_state = creator_resolution_state or (
        CREATOR_RESOLUTION_RESOLVED if creator else CREATOR_RESOLUTION_UNRESOLVED)

    existing = conn.execute(
        "SELECT mint, creator, first_seen_at FROM wt_create_event_ledger WHERE signature=?",
        (signature,),
    ).fetchone()

    if existing:
        existing_mint = existing[0] if not isinstance(existing, sqlite3.Row) else existing["mint"]
        existing_creator = existing[1] if not isinstance(existing, sqlite3.Row) else existing["creator"]
        if existing_mint != mint:
            conn.execute(
                "INSERT INTO wt_create_ledger_conflicts "
                "(conflict_type, signature, mint, existing_value, incoming_value, detected_at) "
                "VALUES (?,?,?,?,?,?)",
                ("SIGNATURE_MINT_MISMATCH", signature, mint, existing_mint, mint, now),
            )
            conn.commit()
            return {"written": False, "conflict": "SIGNATURE_MINT_MISMATCH",
                    "existing_mint": existing_mint}
        if existing_creator and creator and existing_creator != creator:
            conn.execute(
                "INSERT INTO wt_create_ledger_conflicts "
                "(conflict_type, signature, mint, existing_value, incoming_value, detected_at) "
                "VALUES (?,?,?,?,?,?)",
                ("CREATOR_MISMATCH", signature, mint, existing_creator, creator, now),
            )
            conn.commit()
            return {"written": False, "conflict": "CREATOR_MISMATCH",
                    "existing_creator": existing_creator}
        # Same signature+mint, safe to enrich: fill NULL creator, bump last_seen_at.
        new_creator = existing_creator or creator
        new_state = creator_resolution_state or (
            CREATOR_RESOLUTION_RESOLVED if new_creator else CREATOR_RESOLUTION_UNRESOLVED)
        conn.execute(
            "UPDATE wt_create_event_ledger SET creator=COALESCE(creator,?), "
            "creator_resolution_state=?, last_seen_at=? WHERE signature=?",
            (creator, new_state, now, signature),
        )
        conn.commit()
        return {"written": True, "state": "ENRICHED", "creator": new_creator}

    conn.execute(
        "INSERT INTO wt_create_event_ledger "
        "(signature, mint, creator, slot, block_time, observed_at, source, parser_path, "
        " tx_version, instruction_index, inner_instruction_index, raw_detection_method, "
        " creator_resolution_state, persistence_version, first_seen_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
        (signature, mint, creator, slot, block_time, now, source, parser_path,
         tx_version, instruction_index, inner_instruction_index, raw_detection_method,
         creator_state, now, now),
    )
    conn.commit()
    return {"written": True, "state": "NEW"}


# ── X64.7A Phase 2 — durable failed-write recovery ───────────────────────────

_RETRY_BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)  # bounded, exponential-ish
_MAX_PENDING_ATTEMPTS = len(_RETRY_BACKOFF_SECONDS)


def persist_pending_write(
    conn: sqlite3.Connection, *, signature: str, mint: str,
    creator: Optional[str], slot: Optional[int], block_time: Optional[int],
    source: str, parser_path: Optional[str], last_error: str,
) -> dict[str, Any]:
    """Called when record_create_event's own write path fails for a
    reason OTHER than an invalid signature or missing mint (e.g. a
    transient lock) — persists the full payload needed to retry later,
    so the failure exists durably, not only in logs. Idempotent on
    signature: a second failure for the same signature updates
    attempts/last_error/next_retry_at rather than duplicating a row.
    """
    ensure_schema(conn)
    now = int(time.time())
    payload = {
        "signature": signature, "mint": mint, "creator": creator,
        "slot": slot, "block_time": block_time, "source": source,
        "parser_path": parser_path,
    }
    existing = conn.execute(
        "SELECT attempts FROM wt_create_ledger_pending WHERE signature=?", (signature,),
    ).fetchone()
    if existing:
        attempts = (existing[0] if not isinstance(existing, sqlite3.Row) else existing["attempts"]) + 1
        backoff = _RETRY_BACKOFF_SECONDS[min(attempts, _MAX_PENDING_ATTEMPTS) - 1]
        conn.execute(
            "UPDATE wt_create_ledger_pending SET attempts=?, last_error=?, "
            "next_retry_at=?, updated_at=? WHERE signature=?",
            (attempts, last_error[:500], now + backoff, now, signature),
        )
    else:
        attempts = 1
        conn.execute(
            "INSERT INTO wt_create_ledger_pending "
            "(signature, mint, creator, slot, block_time, source, parser_path, "
            " payload_json, attempts, last_error, next_retry_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signature, mint, creator, slot, block_time, source, parser_path,
             json.dumps(payload, sort_keys=True), attempts, last_error[:500],
             now + _RETRY_BACKOFF_SECONDS[0], now, now),
        )
    conn.commit()
    return {"persisted": True, "attempts": attempts}


def retry_pending_writes(
    conn: sqlite3.Connection, *, limit: int = 25,
) -> dict[str, Any]:
    """Zero-RPC, idempotent, restart-safe, lock-tolerant, bounded-backoff
    retry pass. Called from the ordinary worker cycle (or any periodic
    caller) — never spends RPC, since everything needed to retry a write
    was already captured verbatim by persist_pending_write. A row that
    exhausts _MAX_PENDING_ATTEMPTS stays in the table (not silently
    dropped) but is no longer selected — flagged via the returned
    'exhausted' count so a caller can surface it distinctly from
    'still retrying'.
    """
    ensure_schema(conn)
    now = int(time.time())
    rows = conn.execute(
        "SELECT * FROM wt_create_ledger_pending "
        "WHERE COALESCE(next_retry_at,0) <= ? AND attempts < ? "
        "ORDER BY next_retry_at ASC LIMIT ?",
        (now, _MAX_PENDING_ATTEMPTS, limit),
    ).fetchall()
    recovered = []
    still_failing = []
    for row in rows:
        sig = row["signature"]
        result = record_create_event(
            conn, signature=sig, mint=row["mint"], creator=row["creator"],
            slot=row["slot"], block_time=row["block_time"], source=row["source"],
            parser_path=row["parser_path"],
        )
        if result.get("written"):
            conn.execute("DELETE FROM wt_create_ledger_pending WHERE signature=?", (sig,))
            conn.commit()
            recovered.append(sig)
        else:
            # A CONFLICT result (not a transient failure) is not retryable —
            # remove it from the pending queue rather than retrying forever;
            # the conflict itself is already durably recorded by
            # record_create_event in wt_create_ledger_conflicts.
            if result.get("conflict"):
                conn.execute("DELETE FROM wt_create_ledger_pending WHERE signature=?", (sig,))
                conn.commit()
            else:
                persist_pending_write(
                    conn, signature=sig, mint=row["mint"], creator=row["creator"],
                    slot=row["slot"], block_time=row["block_time"], source=row["source"],
                    parser_path=row["parser_path"],
                    last_error=result.get("reason", "unknown"),
                )
            still_failing.append(sig)
    exhausted = conn.execute(
        "SELECT COUNT(*) FROM wt_create_ledger_pending WHERE attempts >= ?",
        (_MAX_PENDING_ATTEMPTS,),
    ).fetchone()[0]
    return {"examined": len(rows), "recovered": recovered,
            "still_failing": still_failing, "exhausted": exhausted}


def lookup_create_anchor(
    conn: sqlite3.Connection, mint: str,
) -> dict[str, Any]:
    """Phase 9 — the first-priority anchor source for walkback resolution.
    Returns SAFE only when exactly one signature is on record for this
    mint. Creator agreement is never required — a NULL-creator ledger row
    is just as SAFE as a resolved-creator one, per the task's explicit
    instruction that creator-null launches must be recoverable."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT signature, creator, slot, block_time, source FROM wt_create_event_ledger "
        "WHERE mint=?", (mint,),
    ).fetchall()
    if not rows:
        return {"signature": None, "confidence": "NONE", "conflict_reason": "NOT_IN_LEDGER"}
    if len(rows) > 1:
        return {"signature": None, "confidence": "CONFLICT",
                "conflict_reason": f"multiple ledger signatures for mint: {[r[0] for r in rows]}"}
    row = rows[0]
    sig = row[0] if not isinstance(row, sqlite3.Row) else row["signature"]
    creator = row[1] if not isinstance(row, sqlite3.Row) else row["creator"]
    return {"signature": sig, "creator": creator, "confidence": "SAFE",
            "conflict_reason": None, "source": "canonical_create_ledger"}
