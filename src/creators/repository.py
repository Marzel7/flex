"""
Creator activity redesign — async repository layer.

Plain SQL against SQLite.  All writes go through the shared db_lock to
serialise with the listener's own DB access.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Optional

from src.utils.db_locking import db_connect
from src.creators.models import (
    CreatorProfile,
    CreatorActivityState,
    CreatorActivityJob,
    HistoryStatus,
    CoverageMode,
    JobType,
    StreamHealthStatus,
)


class CreatorRepository:

    def __init__(self, db_path: str, db_lock: asyncio.Lock) -> None:
        self._db_path = db_path
        self._lock    = db_lock

    # -----------------------------------------------------------------------
    # Schema bootstrap
    # -----------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Apply DDL for new tables.  Safe to call on every startup."""
        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            cur = conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS creator_profile (
                    creator_address          TEXT PRIMARY KEY,
                    history_status           TEXT NOT NULL DEFAULT 'unknown'
                                                 CHECK (history_status IN ('unknown','partial','baselined','stale')),
                    coverage_mode            TEXT NOT NULL DEFAULT 'forward_only'
                                                 CHECK (coverage_mode IN ('forward_only','full')),
                    classification_status    TEXT,
                    token_count_seen         INTEGER NOT NULL DEFAULT 0,
                    last_launch_at           INTEGER,
                    last_create_tx_signature TEXT,
                    first_seen_at            INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    last_activity_at         INTEGER,
                    last_full_scan_at        INTEGER,
                    baselined_at             INTEGER,
                    last_incremental_scan_at INTEGER,
                    webhook_status           TEXT CHECK (webhook_status IN ('active','stopped','error')),
                    webhook_started_at       INTEGER,
                    created_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    updated_at               INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );

                CREATE INDEX IF NOT EXISTS idx_creator_profile_history_status
                    ON creator_profile(history_status);

                CREATE INDEX IF NOT EXISTS idx_creator_profile_last_launch
                    ON creator_profile(last_launch_at DESC);

                CREATE TABLE IF NOT EXISTS creator_activity_state (
                    creator_address          TEXT PRIMARY KEY,
                    last_seen_signature      TEXT,
                    last_seen_slot           INTEGER,
                    last_seen_at             INTEGER,
                    oldest_scanned_signature TEXT,
                    newest_scanned_signature TEXT,
                    last_reconciled_at       INTEGER,
                    needs_reconcile          INTEGER NOT NULL DEFAULT 0 CHECK (needs_reconcile IN (0,1)),
                    last_gap_detected_at     INTEGER,
                    resume_cursor            TEXT,
                    stream_health_status     TEXT CHECK (stream_health_status IN
                                                     ('healthy','lagging','gap_detected','unknown')),
                    created_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    updated_at               INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );

                CREATE INDEX IF NOT EXISTS idx_creator_activity_state_needs_reconcile
                    ON creator_activity_state(needs_reconcile)
                    WHERE needs_reconcile = 1;

                CREATE TABLE IF NOT EXISTS creator_activity_jobs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address  TEXT    NOT NULL,
                    job_type         TEXT    NOT NULL
                                         CHECK (job_type IN ('baseline','incremental_reconcile')),
                    status           TEXT    NOT NULL DEFAULT 'pending'
                                         CHECK (status IN ('pending','running','complete','failed')),
                    priority         INTEGER NOT NULL DEFAULT 100,
                    attempt_count    INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    locked_at        INTEGER,
                    started_at       INTEGER,
                    completed_at     INTEGER,
                    error            TEXT,
                    source_mint      TEXT,
                    source_reason    TEXT,
                    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uidx_creator_activity_jobs_active
                    ON creator_activity_jobs(creator_address, job_type)
                    WHERE status IN ('pending','running');

                CREATE INDEX IF NOT EXISTS idx_creator_activity_jobs_worker
                    ON creator_activity_jobs(status, priority, next_attempt_at)
                    WHERE status = 'pending';
            """)
            conn.commit()
            conn.close()

    # -----------------------------------------------------------------------
    # creator_profile
    # -----------------------------------------------------------------------

    async def get_creator_profile(self, creator_address: str) -> Optional[CreatorProfile]:
        async with self._lock:
            conn = db_connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM creator_profile WHERE creator_address = ? LIMIT 1",
                (creator_address,),
            ).fetchone()
            conn.close()
        return CreatorProfile.from_row(dict(row)) if row else None

    async def upsert_creator_profile(
        self,
        creator_address:          str,
        *,
        history_status:           Optional[HistoryStatus] = None,
        coverage_mode:            Optional[CoverageMode]  = None,
        classification_status:    Optional[str]           = None,
        last_launch_at:           Optional[int]           = None,
        last_create_tx_signature: Optional[str]           = None,
        last_activity_at:         Optional[int]           = None,
        last_full_scan_at:        Optional[int]           = None,
        baselined_at:             Optional[int]           = None,
        last_incremental_scan_at: Optional[int]           = None,
        webhook_status:           Optional[str]           = None,
        webhook_started_at:       Optional[int]           = None,
    ) -> None:
        now = int(time.time())

        # Build a single UPDATE SET clause from provided kwargs.
        # This avoids N round-trips for N fields and is safe under concurrent writes.
        set_parts: list[str] = ["updated_at = ?"]
        params:    list      = [now]

        if history_status is not None:
            set_parts.append("history_status = ?")
            params.append(history_status.value)
        if coverage_mode is not None:
            set_parts.append("coverage_mode = ?")
            params.append(coverage_mode.value)
        if classification_status is not None:
            set_parts.append("classification_status = ?")
            params.append(classification_status)
        if last_launch_at is not None:
            set_parts.append("last_launch_at = ?")
            params.append(last_launch_at)
        if last_create_tx_signature is not None:
            set_parts.append("last_create_tx_signature = ?")
            params.append(last_create_tx_signature)
        if last_activity_at is not None:
            set_parts.append("last_activity_at = ?")
            params.append(last_activity_at)
        if last_full_scan_at is not None:
            set_parts.append("last_full_scan_at = ?")
            params.append(last_full_scan_at)
        if baselined_at is not None:
            set_parts.append("baselined_at = ?")
            params.append(baselined_at)
        if last_incremental_scan_at is not None:
            set_parts.append("last_incremental_scan_at = ?")
            params.append(last_incremental_scan_at)
        if webhook_status is not None:
            set_parts.append("webhook_status = ?")
            params.append(webhook_status)
        if webhook_started_at is not None:
            set_parts.append("webhook_started_at = ?")
            params.append(webhook_started_at)

        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # Ensure row exists
            conn.execute("""
                INSERT INTO creator_profile (creator_address, first_seen_at, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(creator_address) DO NOTHING
            """, (creator_address, now, now, now))
            # Apply all updates in one statement
            conn.execute(
                f"UPDATE creator_profile SET {', '.join(set_parts)} WHERE creator_address = ?",
                [*params, creator_address],
            )
            conn.commit()
            conn.close()

    async def increment_creator_token_count(
        self,
        creator_address: str,
        *,
        last_launch_at: Optional[int] = None,
    ) -> None:
        now = int(time.time())
        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                INSERT INTO creator_profile (creator_address, token_count_seen, last_launch_at,
                                             first_seen_at, created_at, updated_at)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(creator_address) DO UPDATE SET
                    token_count_seen = token_count_seen + 1,
                    last_launch_at   = COALESCE(?, creator_profile.last_launch_at),
                    updated_at       = ?
            """, (creator_address, last_launch_at or now, now, now, now,
                  last_launch_at or now, now))
            conn.commit()
            conn.close()

    # -----------------------------------------------------------------------
    # creator_activity_state
    # -----------------------------------------------------------------------

    async def get_creator_activity_state(self, creator_address: str) -> Optional[CreatorActivityState]:
        async with self._lock:
            conn = db_connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM creator_activity_state WHERE creator_address = ? LIMIT 1",
                (creator_address,),
            ).fetchone()
            conn.close()
        return CreatorActivityState.from_row(dict(row)) if row else None

    async def upsert_creator_activity_state(
        self,
        creator_address:          str,
        *,
        last_seen_signature:      Optional[str]                = None,
        last_seen_slot:           Optional[int]                = None,
        last_seen_at:             Optional[int]                = None,
        oldest_scanned_signature: Optional[str]                = None,
        newest_scanned_signature: Optional[str]                = None,
        last_reconciled_at:       Optional[int]                = None,
        needs_reconcile:          Optional[bool]               = None,
        last_gap_detected_at:     Optional[int]                = None,
        resume_cursor:            Optional[dict]               = None,
        clear_resume_cursor:      bool                         = False,
        stream_health_status:     Optional[StreamHealthStatus] = None,
    ) -> None:
        now = int(time.time())

        set_parts: list[str] = ["updated_at = ?"]
        params:    list      = [now]

        if last_seen_signature is not None:
            set_parts.append("last_seen_signature = ?")
            params.append(last_seen_signature)
        if last_seen_slot is not None:
            set_parts.append("last_seen_slot = ?")
            params.append(last_seen_slot)
        if last_seen_at is not None:
            set_parts.append("last_seen_at = ?")
            params.append(last_seen_at)
        if oldest_scanned_signature is not None:
            set_parts.append("oldest_scanned_signature = ?")
            params.append(oldest_scanned_signature)
        if newest_scanned_signature is not None:
            set_parts.append("newest_scanned_signature = ?")
            params.append(newest_scanned_signature)
        if last_reconciled_at is not None:
            set_parts.append("last_reconciled_at = ?")
            params.append(last_reconciled_at)
        if needs_reconcile is not None:
            set_parts.append("needs_reconcile = ?")
            params.append(int(needs_reconcile))
        if last_gap_detected_at is not None:
            set_parts.append("last_gap_detected_at = ?")
            params.append(last_gap_detected_at)
        if clear_resume_cursor:
            set_parts.append("resume_cursor = NULL")
        elif resume_cursor is not None:
            set_parts.append("resume_cursor = ?")
            params.append(json.dumps(resume_cursor))
        if stream_health_status is not None:
            set_parts.append("stream_health_status = ?")
            params.append(stream_health_status.value)

        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                INSERT INTO creator_activity_state (creator_address, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(creator_address) DO NOTHING
            """, (creator_address, now, now))
            conn.execute(
                f"UPDATE creator_activity_state SET {', '.join(set_parts)} WHERE creator_address = ?",
                [*params, creator_address],
            )
            conn.commit()
            conn.close()

    # -----------------------------------------------------------------------
    # creator_activity_jobs
    # -----------------------------------------------------------------------

    async def enqueue_creator_activity_job(
        self,
        creator_address: str,
        job_type:        JobType,
        *,
        priority:      int           = 100,
        source_mint:   Optional[str] = None,
        source_reason: Optional[str] = None,
        delay_seconds: int           = 0,
    ) -> Optional[int]:
        """
        Insert a pending job.  Returns the new row id, or None if a pending/running
        job of the same type already exists for this creator (UNIQUE index blocks it).
        """
        now             = int(time.time())
        next_attempt_at = now + delay_seconds

        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                cur = conn.execute("""
                    INSERT INTO creator_activity_jobs
                        (creator_address, job_type, status, priority, next_attempt_at,
                         source_mint, source_reason, created_at, updated_at)
                    VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """, (creator_address, job_type.value, priority, next_attempt_at,
                      source_mint, source_reason, now, now))
                conn.commit()
                job_id = cur.lastrowid
                conn.close()
                return job_id
            except sqlite3.IntegrityError:
                # Partial unique index blocked the insert — already queued or running.
                conn.close()
                return None

    async def get_next_creator_activity_job(self, *, lock_seconds: int = 180) -> Optional[CreatorActivityJob]:
        """
        Atomically claim the next eligible pending job.
        Stale running jobs (locked_at older than lock_seconds) are released first.
        """
        now = int(time.time())
        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row

            # Release stale locks so they can be retried
            conn.execute("""
                UPDATE creator_activity_jobs
                SET status = 'pending', locked_at = NULL, updated_at = ?
                WHERE status = 'running'
                  AND locked_at IS NOT NULL
                  AND locked_at < ?
            """, (now, now - lock_seconds))

            row = conn.execute("""
                SELECT * FROM creator_activity_jobs
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY priority ASC, next_attempt_at ASC
                LIMIT 1
            """, (now,)).fetchone()

            if not row:
                conn.close()
                return None

            conn.execute("""
                UPDATE creator_activity_jobs
                SET status = 'running', locked_at = ?, started_at = ?, updated_at = ?
                WHERE id = ?
            """, (now, now, now, row["id"]))
            conn.commit()
            job = CreatorActivityJob.from_row(dict(row))
            conn.close()
        return job

    async def mark_creator_activity_job_complete(self, job_id: int) -> None:
        now = int(time.time())
        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                UPDATE creator_activity_jobs
                SET status = 'complete', completed_at = ?, locked_at = NULL, updated_at = ?
                WHERE id = ?
            """, (now, now, job_id))
            conn.commit()
            conn.close()

    async def mark_creator_activity_job_failed(
        self,
        job_id:       int,
        error:        str,
        *,
        retry_delay:  int = 300,
        max_attempts: int = 5,
    ) -> None:
        now = int(time.time())
        async with self._lock:
            conn = db_connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT attempt_count FROM creator_activity_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            attempts = (row["attempt_count"] if row else 0) + 1

            if attempts >= max_attempts:
                conn.execute("""
                    UPDATE creator_activity_jobs
                    SET status = 'failed', error = ?, attempt_count = ?,
                        locked_at = NULL, updated_at = ?
                    WHERE id = ?
                """, (error[:500], attempts, now, job_id))
            else:
                backoff = now + retry_delay * attempts
                conn.execute("""
                    UPDATE creator_activity_jobs
                    SET status = 'pending', error = ?, attempt_count = ?,
                        next_attempt_at = ?, locked_at = NULL, updated_at = ?
                    WHERE id = ?
                """, (error[:500], attempts, backoff, now, job_id))

            conn.commit()
            conn.close()
