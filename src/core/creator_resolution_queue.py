"""
Durable P0 creator resolution queue.

This queue covers the critical gap where a migrated token is known, but the
creator wallet is not yet resolved. Once resolved, it hands off to the existing
creator_funding_queue so funder extraction can proceed normally.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


TERMINAL_STATUSES = {"complete", "ignored"}
ACTIVE_STATUSES = {"pending", "retry", "running"}
P0_PRIORITY = 100


def connect(db_path: str, timeout: int = 30) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creator_resolution_queue (
            mint TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 100,
            reason TEXT,
            source TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            locked_until INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            resolved_creator TEXT,
            create_tx_signature TEXT,
            migrated_at INTEGER,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            resolved_at INTEGER
        )
        """
    )
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(creator_resolution_queue)").fetchall()}
    for ddl in (
        "ALTER TABLE creator_resolution_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 100",
        "ALTER TABLE creator_resolution_queue ADD COLUMN reason TEXT",
        "ALTER TABLE creator_resolution_queue ADD COLUMN source TEXT",
        "ALTER TABLE creator_resolution_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE creator_resolution_queue ADD COLUMN next_attempt_at INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE creator_resolution_queue ADD COLUMN locked_until INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE creator_resolution_queue ADD COLUMN last_error TEXT",
        "ALTER TABLE creator_resolution_queue ADD COLUMN resolved_creator TEXT",
        "ALTER TABLE creator_resolution_queue ADD COLUMN create_tx_signature TEXT",
        "ALTER TABLE creator_resolution_queue ADD COLUMN migrated_at INTEGER",
        "ALTER TABLE creator_resolution_queue ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE creator_resolution_queue ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE creator_resolution_queue ADD COLUMN resolved_at INTEGER",
    ):
        column = ddl.split(" ADD COLUMN ", 1)[1].split()[0]
        if column not in existing:
            conn.execute(ddl)
    now = int(time.time())
    conn.execute("UPDATE creator_resolution_queue SET next_attempt_at=? WHERE COALESCE(next_attempt_at,0)=0", (now,))
    conn.execute("UPDATE creator_resolution_queue SET created_at=? WHERE COALESCE(created_at,0)=0", (now,))
    conn.execute("UPDATE creator_resolution_queue SET updated_at=? WHERE COALESCE(updated_at,0)=0", (now,))
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_creator_resolution_queue_status
        ON creator_resolution_queue(status, priority DESC, next_attempt_at, locked_until)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_creator_resolution_queue_updated
        ON creator_resolution_queue(updated_at DESC)
        """
    )


def _creator_missing(row: sqlite3.Row) -> bool:
    return not ((row["earliest_tx_creator"] or "").strip() or (row["pf_ws_creator"] or "").strip())


def enqueue_missing_creator(
    conn: sqlite3.Connection,
    mint: str,
    *,
    reason: str = "missing_creator",
    source: str = "unknown",
    priority: int = P0_PRIORITY,
    delay_seconds: int = 0,
) -> bool:
    if not mint:
        return False
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT mint, earliest_tx_creator, pf_ws_creator, create_tx_signature,
               migrated_at, lifecycle_stage
        FROM token_analysis
        WHERE mint = ?
        LIMIT 1
        """,
        (mint,),
    ).fetchone()
    if not row or not _creator_missing(row):
        return False
    if (row["lifecycle_stage"] or "") != "migrated":
        return False

    now = int(time.time())
    next_attempt_at = now + max(0, int(delay_seconds or 0))
    cur = conn.execute(
        """
        INSERT INTO creator_resolution_queue (
            mint, status, priority, reason, source, next_attempt_at, locked_until,
            attempts, last_error, create_tx_signature, migrated_at, created_at, updated_at
        )
        VALUES (?, 'pending', ?, ?, ?, ?, 0, 0, NULL, ?, ?, ?, ?)
        ON CONFLICT(mint) DO UPDATE SET
            priority = MAX(creator_resolution_queue.priority, excluded.priority),
            reason = COALESCE(creator_resolution_queue.reason, excluded.reason),
            source = excluded.source,
            create_tx_signature = COALESCE(creator_resolution_queue.create_tx_signature, excluded.create_tx_signature),
            migrated_at = COALESCE(creator_resolution_queue.migrated_at, excluded.migrated_at),
            updated_at = excluded.updated_at,
            next_attempt_at = CASE
                WHEN creator_resolution_queue.status IN ('failed','complete','ignored')
                THEN creator_resolution_queue.next_attempt_at
                ELSE MIN(creator_resolution_queue.next_attempt_at, excluded.next_attempt_at)
            END,
            status = CASE
                WHEN creator_resolution_queue.status IN ('complete','ignored')
                THEN creator_resolution_queue.status
                WHEN creator_resolution_queue.status = 'failed'
                THEN 'retry'
                ELSE creator_resolution_queue.status
            END
        """,
        (
            mint,
            int(priority),
            reason,
            source,
            next_attempt_at,
            row["create_tx_signature"],
            row["migrated_at"],
            now,
            now,
        ),
    )
    return cur.rowcount > 0


def enqueue_missing_migrated_tokens(
    db_path: str,
    *,
    limit: int = 50,
    source: str = "approval_queue",
    max_age_seconds: int = None,
) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT mint
            FROM token_analysis
            WHERE lifecycle_stage='migrated'
              AND COALESCE(NULLIF(TRIM(earliest_tx_creator), ''), NULLIF(TRIM(pf_ws_creator), '')) IS NULL
              AND mint NOT IN (
                  SELECT mint FROM creator_resolution_queue
                  WHERE status IN ('pending','retry','running','complete','ignored')
              )
            ORDER BY COALESCE(migrated_at, created_at, analyzed_at, 0) DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        count = 0
        for row in rows:
            if enqueue_missing_creator(conn, row["mint"], source=source, reason="missing_creator_p0"):
                count += 1
        conn.commit()
        return count


def _iso_from_epoch(value: Any) -> str:
    try:
        if value:
            return datetime.utcfromtimestamp(int(float(value))).isoformat() + "Z"
    except Exception:
        pass
    return datetime.utcnow().isoformat() + "Z"


def _enqueue_funding_handoff(
    conn: sqlite3.Connection,
    *,
    creator: str,
    mint: str,
    migration_timestamp: str,
    create_tx_signature: Optional[str],
) -> bool:
    now = int(time.time())
    row = conn.execute(
        "SELECT COUNT(*) FROM creator_funders WHERE creator_address = ?",
        (creator,),
    ).fetchone()
    funder_count = int(row[0] or 0) if row else 0
    job_priority = 1 if funder_count == 0 else 0
    priority_reason = "p0_creator_resolved_new_creator" if job_priority else "p0_creator_resolved_known_creator"
    cur = conn.execute(
        """
        INSERT INTO creator_funding_queue (
            creator_address, mint, migration_timestamp, create_tx_signature,
            status, source, next_attempt_at, locked_until, attempts, last_error,
            funding_enqueued_at, curve_completed_slot, enqueued_slot,
            job_priority, priority_reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'pending', 'creator_resolution_queue', ?, 0, 0, NULL,
                  ?, NULL, NULL, ?, ?, ?, ?)
        ON CONFLICT(creator_address, mint) DO NOTHING
        """,
        (
            creator,
            mint,
            migration_timestamp,
            create_tx_signature,
            now,
            now,
            job_priority,
            priority_reason,
            now,
            now,
        ),
    )
    conn.execute(
        """
        UPDATE token_analysis
        SET funding_job_enqueued_slot = COALESCE(funding_job_enqueued_slot, migration_slot, curve_completed_slot)
        WHERE mint = ?
        """,
        (mint,),
    )
    return cur.rowcount > 0


def enqueue_missing_funding_jobs(
    db_path: str,
    *,
    limit: int = 50,
    source: str = "funding_coverage_sweep",
) -> int:
    """Backfill migrated tokens that already have a creator but no funding job."""
    now = int(time.time())
    enqueued = 0
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT
                ta.mint,
                COALESCE(NULLIF(TRIM(ta.pf_ws_creator), ''), NULLIF(TRIM(ta.earliest_tx_creator), '')) AS creator,
                ta.create_tx_signature,
                ta.migrated_at
            FROM token_analysis ta
            WHERE ta.lifecycle_stage='migrated'
              AND COALESCE(NULLIF(TRIM(ta.pf_ws_creator), ''), NULLIF(TRIM(ta.earliest_tx_creator), '')) IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM creator_funding_queue cfq WHERE cfq.mint = ta.mint
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM creator_funders cf
                  WHERE cf.creator_address = COALESCE(NULLIF(TRIM(ta.pf_ws_creator), ''), NULLIF(TRIM(ta.earliest_tx_creator), ''))
              )
            ORDER BY COALESCE(ta.migrated_at, ta.created_at, ta.analyzed_at, 0) DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        for row in rows:
            if _enqueue_funding_handoff(
                conn,
                creator=row["creator"],
                mint=row["mint"],
                migration_timestamp=_iso_from_epoch(row["migrated_at"]),
                create_tx_signature=row["create_tx_signature"],
            ):
                conn.execute(
                    """
                    UPDATE creator_funding_queue
                    SET source=?, updated_at=?
                    WHERE mint=?
                      AND source='creator_resolution_queue'
                    """,
                    (source, now, row["mint"]),
                )
                enqueued += 1
        conn.commit()
    return enqueued


def _resolve_creator_rpc(mint: str) -> Dict[str, Any]:
    from src.analysis.pump_fun_post_migration_analyzer import PostMigrationAnalyzer

    analyzer = PostMigrationAnalyzer(
        mint,
        rpc_url=os.environ.get("RPC_HTTP") or os.environ.get("RPC_URL") or os.environ.get("HELIUS_RPC_URL"),
    )
    provenance = asyncio.run(analyzer.get_creator_from_earliest_tx())
    creator = provenance.get("creator") if provenance else None
    if not creator:
        raise RuntimeError("creator not found from earliest transaction")

    created_at = None
    if provenance and provenance.get("blockTime"):
        created_at = datetime.utcfromtimestamp(provenance["blockTime"]).isoformat() + "Z"

    is_pumpfun_create = bool(provenance.get("is_pumpfun_create")) if provenance else False
    create_tx_signature = (
        getattr(analyzer, "_create_tx_signature", None)
        if is_pumpfun_create and hasattr(analyzer, "_create_tx_signature")
        else None
    )
    return {
        "creator": creator,
        "created_at": created_at,
        "bonding_curve_pda": provenance.get("bonding_curve_pda") if provenance else None,
        "create_tx_signature": create_tx_signature,
    }


def process_queue(db_path: str, *, limit: int = 3, lock_seconds: int = 180) -> Dict[str, Any]:
    now = int(time.time())
    processed = resolved = failed = funding_enqueued = 0
    errors: List[Dict[str, str]] = []

    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT mint, attempts
            FROM creator_resolution_queue
            WHERE status IN ('pending','retry')
              AND locked_until < ?
              AND next_attempt_at <= ?
            ORDER BY priority DESC, next_attempt_at ASC, created_at ASC
            LIMIT ?
            """,
            (now, now, max(1, int(limit))),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE creator_resolution_queue
                SET status='running', locked_until=?, updated_at=?
                WHERE mint=?
                """,
                (now + lock_seconds, now, row["mint"]),
            )
        conn.commit()

    for row in rows:
        mint = row["mint"]
        attempts = int(row["attempts"] or 0)
        processed += 1
        try:
            data = _resolve_creator_rpc(mint)
            creator = data["creator"]
            with connect(db_path) as conn:
                ensure_schema(conn)
                token = conn.execute(
                    "SELECT migrated_at, created_at, create_tx_signature FROM token_analysis WHERE mint=?",
                    (mint,),
                ).fetchone()
                migration_ts = _iso_from_epoch(token["migrated_at"] if token else None)
                create_tx = data.get("create_tx_signature") or (token["create_tx_signature"] if token else None)
                conn.execute(
                    """
                    UPDATE token_analysis
                    SET earliest_tx_creator = COALESCE(NULLIF(TRIM(earliest_tx_creator), ''), ?),
                        pf_ws_creator = COALESCE(NULLIF(TRIM(pf_ws_creator), ''), ?),
                        created_at = COALESCE(created_at, ?),
                        bonding_curve_pda = COALESCE(bonding_curve_pda, ?),
                        create_tx_signature = COALESCE(create_tx_signature, ?),
                        creator_resolved_slot = COALESCE(creator_resolved_slot, migration_slot, curve_completed_slot)
                    WHERE mint = ?
                    """,
                    (
                        creator,
                        creator,
                        data.get("created_at"),
                        data.get("bonding_curve_pda"),
                        create_tx,
                        mint,
                    ),
                )
                did_enqueue = _enqueue_funding_handoff(
                    conn,
                    creator=creator,
                    mint=mint,
                    migration_timestamp=migration_ts,
                    create_tx_signature=create_tx,
                )
                if did_enqueue:
                    funding_enqueued += 1
                conn.execute(
                    """
                    UPDATE creator_resolution_queue
                    SET status='complete',
                        locked_until=0,
                        attempts=?,
                        last_error=NULL,
                        resolved_creator=?,
                        create_tx_signature=COALESCE(create_tx_signature, ?),
                        resolved_at=?,
                        updated_at=?
                    WHERE mint=?
                    """,
                    (attempts + 1, creator, create_tx, int(time.time()), int(time.time()), mint),
                )
                conn.commit()
                resolved += 1
        except Exception as exc:
            failed += 1
            error = str(exc)[:500]
            retry_at = int(time.time()) + min(900, 30 * (attempts + 1))
            with connect(db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    """
                    UPDATE creator_resolution_queue
                    SET status=?,
                        locked_until=0,
                        attempts=?,
                        next_attempt_at=?,
                        last_error=?,
                        updated_at=?
                    WHERE mint=?
                    """,
                    (
                        "failed" if attempts >= 5 else "retry",
                        attempts + 1,
                        retry_at,
                        error,
                        int(time.time()),
                        mint,
                    ),
                )
                conn.commit()
            errors.append({"mint": mint, "error": error})

    return {
        "status": "ok",
        "processed": processed,
        "resolved": resolved,
        "failed": failed,
        "funding_enqueued": funding_enqueued,
        "errors": errors[:10],
    }


def get_status(db_path: str, *, auto_enqueue_limit: int = 25) -> Dict[str, Any]:
    auto_enqueued = enqueue_missing_migrated_tokens(
        db_path,
        limit=auto_enqueue_limit,
        source="approval_queue_status",
    )
    funding_auto_enqueued = enqueue_missing_funding_jobs(
        db_path,
        limit=auto_enqueue_limit,
        source="approval_queue_funding_coverage",
    )
    now = int(time.time())
    with connect(db_path) as conn:
        ensure_schema(conn)
        counts = {
            row["status"]: row["cnt"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM creator_resolution_queue GROUP BY status"
            ).fetchall()
        }
        missing_recent = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM token_analysis
            WHERE lifecycle_stage='migrated'
              AND COALESCE(NULLIF(TRIM(earliest_tx_creator), ''), NULLIF(TRIM(pf_ws_creator), '')) IS NULL
              AND COALESCE(migrated_at, created_at, analyzed_at, 0) >= ?
            """,
            (now - 3600,),
        ).fetchone()["cnt"]
        funding_pending = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM creator_funding_queue
            WHERE status IN ('pending','retry','running','in_progress')
            """
        ).fetchone()["cnt"]
        funding_complete_1h = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM creator_funding_queue
            WHERE status='complete'
              AND COALESCE(funding_extracted_at, updated_at, created_at, 0) >= ?
            """,
            (now - 3600,),
        ).fetchone()["cnt"]
        rows = [dict(row) for row in conn.execute(
            """
            SELECT
                crq.*,
                COALESCE(mc.symbol, mc.name, substr(crq.mint, 1, 8)) AS symbol,
                ta.market_cap_current,
                ta.market_cap_highest,
                ta.lifecycle_stage
            FROM creator_resolution_queue crq
            LEFT JOIN token_analysis ta ON ta.mint = crq.mint
            LEFT JOIN metadata_cache mc ON mc.mint = crq.mint
            WHERE crq.status IN ('pending','retry','running','failed')
            ORDER BY crq.priority DESC, crq.next_attempt_at ASC, crq.updated_at DESC
            LIMIT 50
            """
        ).fetchall()]
    return {
        "counts": counts,
        "auto_enqueued": auto_enqueued,
        "funding_auto_enqueued": funding_auto_enqueued,
        "missing_recent_1h": missing_recent,
        "funding_pending": funding_pending,
        "funding_complete_1h": funding_complete_1h,
        "ready": sum(1 for row in rows if row["status"] in ("pending", "retry") and int(row["next_attempt_at"] or 0) <= now),
        "rows": rows,
    }
