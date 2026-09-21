"""X63 WATCHTOWER candidate generation from persisted operational evidence.

This module is deliberately attribution-neutral. It detects one creator-handoff
primitive, records a candidate, and raises walkback priority. Only walkback can
set the final result.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Optional

from src.ops.operational_intelligence import classify_quick_birth_migration

PRIMITIVE = "EPHEMERAL_WSOL_CREATOR_HANDOFF"
PARTIAL_PRIMITIVE = "EPHEMERAL_WSOL_CREATOR_HANDOFF_PARTIAL"
VARIANTS = frozenset({"WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"})
HIGH_PRIORITY = 100
REASONS = ("QUICK_BIRTH_MIGRATION", PRIMITIVE)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wt_watchtower_candidates (
            candidate_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            mint                 TEXT NOT NULL UNIQUE,
            launch_signature     TEXT,
            launch_time          INTEGER,
            creator              TEXT NOT NULL,
            candidate_type       TEXT NOT NULL DEFAULT 'WATCHTOWER',
            candidate_status     TEXT NOT NULL DEFAULT 'PENDING_WALKBACK',
            candidate_reason     TEXT NOT NULL,
            primitive            TEXT NOT NULL,
            handoff_variant      TEXT NOT NULL DEFAULT 'UNKNOWN',
            handoff_signature    TEXT,
            handoff_time         INTEGER,
            evidence_status      TEXT NOT NULL DEFAULT 'COMPLETE',
            candidate_confidence TEXT NOT NULL DEFAULT 'HIGH',
            missing_evidence     TEXT NOT NULL DEFAULT '[]',
            created_at           INTEGER NOT NULL,
            walkback_started_at  INTEGER,
            walkback_completed_at INTEGER,
            walkback_result      TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_wt_candidates_status_created
            ON wt_watchtower_candidates(candidate_status, created_at DESC);
    """)
    for column, typedef in (
        ("priority", "INTEGER NOT NULL DEFAULT 0"),
        ("priority_reason", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE wt_walkback_queue ADD COLUMN {column} {typedef}")
        except sqlite3.OperationalError:
            pass
    for column, typedef in (
        ("evidence_status", "TEXT NOT NULL DEFAULT 'COMPLETE'"),
        ("candidate_confidence", "TEXT NOT NULL DEFAULT 'HIGH'"),
        ("missing_evidence", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        try:
            conn.execute(f"ALTER TABLE wt_watchtower_candidates ADD COLUMN {column} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_wbq_priority "
        "ON wt_walkback_queue(status, priority DESC, enqueued_at ASC)"
    )
    conn.commit()


def detect_ephemeral_wsol_creator_handoff(evidence: dict[str, Any], creator: str) -> dict[str, Any]:
    variant = evidence.get("funding_mechanism") or "UNKNOWN"
    close_destination = evidence.get("close_destination")
    signature = evidence.get("wrap_close_signature") or evidence.get("tx_signature")
    temporary = evidence.get("temp_wsol_account")
    if variant not in VARIANTS:
        return {"primitive_detected": False, "partial_evidence": False, "variant": "UNKNOWN"}
    if close_destination != creator or not signature:
        return {"primitive_detected": False, "partial_evidence": False, "variant": variant}
    if not temporary:
        return {
            "primitive_detected": False,
            "partial_evidence": True,
            "variant": variant,
            "primitive": PARTIAL_PRIMITIVE,
            "signature": signature,
            "block_time": evidence.get("wrap_close_time") or evidence.get("funded_at"),
            "missing_evidence": ["TEMP_WSOL_ACCOUNT"],
        }
    return {
        "primitive_detected": True,
        "partial_evidence": False,
        "variant": variant,
        "primitive": PRIMITIVE,
        "signature": signature,
        "block_time": evidence.get("wrap_close_time") or evidence.get("funded_at"),
    }


def _handoff_evidence(conn: sqlite3.Connection, creator: str) -> Optional[dict[str, Any]]:
    if _table_exists(conn, "wt_candidate_websocket_watches"):
        row = conn.execute(
            "SELECT candidate_wallet,subprov_wallet,wrap_close_signature,wrap_close_time,"
            "temp_wsol_account,close_destination,funding_mechanism "
            "FROM wt_candidate_websocket_watches WHERE candidate_wallet=? "
            "AND funding_mechanism IN ('WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE') "
            "ORDER BY detected_at DESC LIMIT 1", (creator,),
        ).fetchone()
        if row:
            return dict(row)
    if _table_exists(conn, "wt_wrap_close_candidates"):
        row = conn.execute(
            "SELECT creator,tx_signature,funded_at,temp_wsol_account,close_destination,"
            "funding_mechanism FROM wt_wrap_close_candidates WHERE creator=? "
            "AND funding_mechanism IN ('WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE') "
            "ORDER BY detected_at DESC LIMIT 1", (creator,),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _live_timing(live_conn: sqlite3.Connection, mint: str) -> dict[str, Any]:
    if not _table_exists(live_conn, "token_analysis"):
        return {}
    row = live_conn.execute(
        "SELECT create_tx_signature,created_at,migrated_at FROM token_analysis WHERE mint=?",
        (mint,),
    ).fetchone()
    timing = dict(row) if row else {}
    if not timing.get("create_tx_signature") and _table_exists(live_conn, "creator_funding_queue"):
        anchor = live_conn.execute(
            "SELECT create_tx_signature FROM creator_funding_queue WHERE mint=? "
            "AND create_tx_signature IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
            (mint,),
        ).fetchone()
        if anchor:
            timing["create_tx_signature"] = anchor["create_tx_signature"]
    return timing


def evaluate_and_enqueue_candidate(
    ops_conn: sqlite3.Connection, *, mint: str, creator: Optional[str],
    live_conn: Optional[sqlite3.Connection] = None,
    handoff_evidence: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Create an idempotent candidate when both mandatory signals are persisted."""
    ensure_schema(ops_conn)
    if not creator:
        return None
    evidence = handoff_evidence or _handoff_evidence(ops_conn, creator)
    primitive = detect_ephemeral_wsol_creator_handoff(evidence or {}, creator)
    if not (primitive["primitive_detected"] or primitive.get("partial_evidence")):
        return None

    owned_live = None
    if live_conn is None:
        path = os.environ.get("FLEX_DB_PATH", os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "database",
            "flex_complete_database.db",
        ))
        try:
            owned_live = sqlite3.connect(f"file:{os.path.realpath(path)}?mode=ro", uri=True)
            owned_live.row_factory = sqlite3.Row
            live_conn = owned_live
        except sqlite3.Error:
            live_conn = None
    timing = _live_timing(live_conn, mint) if live_conn else {}
    if owned_live:
        owned_live.close()
    quick = classify_quick_birth_migration(
        primitive.get("block_time"), timing.get("created_at"), timing.get("migrated_at")
    )
    if not quick["is_quick_birth_migration"]:
        return None

    now = int(time.time())
    is_complete = primitive["primitive_detected"]
    reasons = REASONS if is_complete else ("QUICK_BIRTH_MIGRATION", PARTIAL_PRIMITIVE)
    reasons_json = json.dumps(reasons, separators=(",", ":"))
    evidence_status = "COMPLETE" if is_complete else "PARTIAL"
    confidence = "HIGH" if is_complete else "PROBABLE"
    missing_json = json.dumps(primitive.get("missing_evidence", []), separators=(",", ":"))
    ops_conn.execute(
        "INSERT INTO wt_watchtower_candidates "
        "(mint,launch_signature,launch_time,creator,candidate_type,candidate_status,"
        "candidate_reason,primitive,handoff_variant,handoff_signature,handoff_time,"
        "evidence_status,candidate_confidence,missing_evidence,created_at) "
        "VALUES (?,?,?,?, 'WATCHTOWER','PENDING_WALKBACK',?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET "
        "launch_signature=COALESCE(excluded.launch_signature,launch_signature),"
        "launch_time=COALESCE(excluded.launch_time,launch_time),creator=excluded.creator,"
        "candidate_reason=excluded.candidate_reason,primitive=excluded.primitive,"
        "handoff_variant=excluded.handoff_variant,"
        "handoff_signature=excluded.handoff_signature,handoff_time=excluded.handoff_time,"
        "evidence_status=excluded.evidence_status,"
        "candidate_confidence=excluded.candidate_confidence,"
        "missing_evidence=excluded.missing_evidence",
        (mint, timing.get("create_tx_signature"), quick.get("create_at"), creator,
         reasons_json, primitive["primitive"], primitive["variant"],
         primitive.get("signature"), primitive.get("block_time"), evidence_status,
         confidence, missing_json, now),
    )
    ops_conn.execute(
        "UPDATE wt_walkback_queue SET priority=?,priority_reason=?,updated_at=? "
        "WHERE mint=? AND status IN ('pending','waiting')",
        (HIGH_PRIORITY, "+".join(REASONS), now, mint),
    )
    # Funding extraction can finish after an ordinary walkback has already run.
    # In that race, retain the candidate evidence and mirror the authoritative
    # completed result instead of presenting it as pending.
    sync_walkback_result(ops_conn, mint)
    ops_conn.commit()
    print(
        "WATCHTOWER Candidate\n"
        f"Creator: {creator}\nReason: Quick Birth → Migration\n"
        f"Primitive: {primitive['primitive']}\nVariant: {primitive['variant']}\n"
        f"Evidence: {evidence_status}\nConfidence: {confidence}\n"
        "Queued for walkback.", flush=True,
    )
    return {
        "mint": mint, "creator": creator, "candidate_status": "PENDING_WALKBACK",
        "primitive_detected": is_complete, "partial_evidence": not is_complete,
        "evidence_status": evidence_status, "candidate_confidence": confidence,
        "variant": primitive["variant"],
    }


def funding_signature_for_quick_launch(
    live_conn: sqlite3.Connection, *, mint: str, creator: str,
) -> Optional[str]:
    """Return one persisted creator-funding signature only when timing is quick."""
    if not _table_exists(live_conn, "transfer_index"):
        return None
    timing = _live_timing(live_conn, mint)
    create_at = timing.get("created_at")
    if create_at is None:
        return None
    row = live_conn.execute(
        "SELECT signature,block_time FROM transfer_index WHERE destination=? "
        "AND signature IS NOT NULL AND block_time IS NOT NULL AND block_time<=? "
        "ORDER BY block_time ASC LIMIT 1", (creator, create_at),
    ).fetchone()
    if not row:
        return None
    quick = classify_quick_birth_migration(
        row["block_time"], timing.get("created_at"), timing.get("migrated_at")
    )
    return row["signature"] if quick["is_quick_birth_migration"] else None


def funding_signature_for_quick_launch_via_hot_cold(
    reader, *, mint: str, creator: str, create_at: int, migrated_at: Optional[int],
) -> Optional[str]:
    """STORAGE-LIFECYCLE-P5B-R2 Part 5/7 adapter (NOT YET WIRED INTO THE
    CALLERS OF funding_signature_for_quick_launch() ABOVE -- that function
    remains unmodified production code, still querying live_conn's
    transfer_index directly).

    Reproduces funding_signature_for_quick_launch()'s earliest-edge lookup
    (creator's earliest funding signature at/before create_at) via the
    HOT+COLD UnifiedTransferReader instead of a single-table query, so an
    old/historical creator-funding signature that has been archived to a
    COLD segment (i.e. older than the 90-day HOT window) is still found.
    This is the Watchtower "earliest-edge lookup" case flagged in Part 1's
    consumer classification as HISTORICAL_HOT_COLD_REQUIRED: a creator's
    genuine earliest funding transaction can be arbitrarily old.

    `reader` is a src.ops.unified_transfer_reader.UnifiedTransferReader
    (constructed via src.ops.cold_segment_registry.get_transfer_reader()).
    Preserves exact tie-break semantics of the original query: ORDER BY
    block_time ASC LIMIT 1, i.e. the single earliest row at or before
    create_at. by_destination() returns rows sorted block_time DESC, so
    this filters to block_time<=create_at and takes the MIN by block_time
    (equivalent to reversing the DESC-sorted, pre-filtered list and taking
    the first -- same tie behavior as SQLite's own ORDER BY ASC LIMIT 1 for
    a single winning row, since block_time is the only sort key in both the
    original query and here).
    """
    rows = reader.by_destination(creator, limit=1_000_000)
    eligible = [r for r in rows if r[0] and r[4] is not None and r[4] <= create_at]
    if not eligible:
        return None
    earliest = min(eligible, key=lambda r: r[4])
    quick = classify_quick_birth_migration(earliest[4], create_at, migrated_at)
    return earliest[0] if quick["is_quick_birth_migration"] else None


def evaluate_transaction_candidate(
    ops_conn: sqlite3.Connection, *, mint: str, creator: str, signature: str,
    tx: dict[str, Any], live_conn: sqlite3.Connection,
) -> Optional[dict[str, Any]]:
    """Parse one already-identified funding transaction and evaluate X63."""
    from src.core.wrap_close_detector import extract_close_destinations
    match = next((item for item in extract_close_destinations(tx)
                  if item.get("candidate") == creator), None)
    if not match:
        return None
    evidence = {
        "funding_mechanism": match.get("funding_mechanism", "UNKNOWN"),
        "close_destination": creator,
        "wrap_close_signature": signature,
        "wrap_close_time": tx.get("blockTime"),
        "temp_wsol_account": match.get("temp_wsol_account"),
    }
    return evaluate_and_enqueue_candidate(
        ops_conn, mint=mint, creator=creator, live_conn=live_conn,
        handoff_evidence=evidence,
    )


def sync_walkback_result(conn: sqlite3.Connection, mint: str) -> None:
    """Mirror walkback state into the candidate row; never decide attribution."""
    if not _table_exists(conn, "wt_watchtower_candidates"):
        return
    row = conn.execute(
        "SELECT status,intelligence_outcome,started_at,completed_at FROM wt_walkback_queue WHERE mint=?",
        (mint,),
    ).fetchone()
    if not row:
        return
    status, outcome = row["status"], row["intelligence_outcome"]
    if status == "running":
        candidate_status, result = "WALKBACK_RUNNING", None
    elif status in ("complete", "failed", "skipped"):
        candidate_status = "WALKBACK_COMPLETE"
        if outcome == "WATCHTOWER_CONFIRMED":
            result = "CONFIRMED_WATCHTOWER"
        elif outcome == "NON_WATCHTOWER":
            result = "REJECTED"
        else:
            result = "INCONCLUSIVE"
    else:
        candidate_status, result = "PENDING_WALKBACK", None
    conn.execute(
        "UPDATE wt_watchtower_candidates SET candidate_status=?,walkback_started_at=?,"
        "walkback_completed_at=?,walkback_result=? WHERE mint=?",
        (candidate_status, row["started_at"], row["completed_at"], result, mint),
    )
