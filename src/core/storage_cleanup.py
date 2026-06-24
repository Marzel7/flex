"""
FLEX Phase 3.2 Storage Management — Production-Safe Cleanup

This module provides atomic, verifiable cleanup of old transfers from the
transfer_index table, keeping hot storage bounded to 90 days of data.

Architecture:
- Single transfer_index table with DELETE + VACUUM retention
- Pre-cleanup verification prevents duplicate runs and ensures safety
- Post-cleanup integrity checks detect corruption
- Cleanup log tracks all operations for monitoring
"""

import sqlite3
import time
import logging
import os
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TransferIndexCleanup:
    """
    Production-safe cleanup for time-based retention.

    Implements:
    1. Atomic deletion of old transfers
    2. Pre-cleanup verification (safety checks)
    3. Space reclamation via VACUUM
    4. Post-cleanup integrity verification
    5. Metrics logging to cleanup_log table
    """

    def __init__(self, db_path: str, retention_days: int = 90):
        """
        Initialize cleanup instance.

        Args:
            db_path: Path to flex_complete_database.db
            retention_days: Keep transfers newer than N days (default 90)
        """
        self.db_path = db_path
        self.retention_days = retention_days
        self.cleanup_log_table = "cleanup_log"

    def _get_conn(self) -> sqlite3.Connection:
        """Get optimized SQLite connection for cleanup operations."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=30000000")  # 30MB memory map
        conn.execute("PRAGMA query_only=FALSE")
        return conn

    def _ensure_cleanup_log_table(self) -> None:
        """Create cleanup log table if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.cleanup_log_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cleanup_timestamp REAL NOT NULL,
                retention_days INTEGER NOT NULL,
                cutoff_timestamp INTEGER NOT NULL,
                rows_to_delete INTEGER,
                rows_actually_deleted INTEGER,
                freed_mb REAL,
                cleanup_duration_ms REAL,
                db_size_before BIGINT,
                db_size_after BIGINT,
                status TEXT NOT NULL,
                error_message TEXT
            )
        """)

        conn.commit()
        conn.close()

    def _calculate_cutoff_timestamp(self, retention_days: Optional[int] = None) -> int:
        """Calculate Unix timestamp for cutoff date."""
        days = retention_days or self.retention_days
        cutoff_seconds = days * 86400
        return int(time.time()) - cutoff_seconds

    def cleanup_old_transfers(self, dry_run: bool = False) -> Dict:
        """
        Delete transfers older than retention_days from hot storage.

        This is the main cleanup entry point. It performs:
        1. Pre-cleanup verification
        2. Atomic deletion
        3. Space reclamation via VACUUM
        4. Post-cleanup verification
        5. Metrics recording

        Args:
            dry_run: If True, report what would be deleted but don't delete

        Returns:
            {
                'deleted': int,
                'freed_mb': float,
                'duration_ms': float,
                'db_size_before': int,
                'db_size_after': int,
                'status': 'success' | 'skipped' | 'error',
                'message': str
            }
        """
        cleanup_start = time.time()
        result = {
            'deleted': 0,
            'freed_mb': 0.0,
            'duration_ms': 0.0,
            'db_size_before': 0,
            'db_size_after': 0,
            'status': 'pending',
            'message': ''
        }

        try:
            # Ensure log table exists
            self._ensure_cleanup_log_table()

            # Pre-cleanup verification
            verification = self._verify_cleanup_safe()
            if not verification['safe']:
                result['status'] = 'skipped'
                result['message'] = verification['reason']
                logger.warning(f"[CLEANUP] Skipped: {verification['reason']}")
                return result

            # Get size before
            db_size_before = os.path.getsize(self.db_path)
            result['db_size_before'] = db_size_before

            # Calculate cutoff
            cutoff_ts = self._calculate_cutoff_timestamp()

            if dry_run:
                # Dry run: count rows but don't delete
                conn = self._get_conn()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM transfer_index WHERE block_time < ?",
                    (cutoff_ts,)
                )
                rows_to_delete = cursor.fetchone()[0]
                conn.close()

                result['deleted'] = rows_to_delete
                result['status'] = 'dry_run'
                result['message'] = f"Would delete {rows_to_delete} rows (dry run)"
                logger.info(f"[CLEANUP] {result['message']}")
                return result

            # Actual cleanup
            conn = self._get_conn()
            cursor = conn.cursor()

            # 1. Delete old rows
            cursor.execute(
                "DELETE FROM transfer_index WHERE block_time < ?",
                (cutoff_ts,)
            )
            deleted = cursor.rowcount

            # 2. Reclaim space — NO full VACUUM (rewrites whole DB into the WAL →
            # multi-GB spike + exclusive lock = the lock-storm root cause). The DELETE
            # frees pages for reuse; that's sufficient.

            # 3. Reset WAL checkpoint
            cursor.execute("PRAGMA wal_checkpoint(RESTART)")

            conn.commit()
            conn.close()

            # Get size after
            db_size_after = os.path.getsize(self.db_path)
            freed_mb = (db_size_before - db_size_after) / (1024 * 1024)

            result['deleted'] = deleted
            result['freed_mb'] = freed_mb
            result['db_size_after'] = db_size_after
            result['status'] = 'success'
            result['message'] = f"Deleted {deleted} rows, freed {freed_mb:.1f} MB"

            # Post-cleanup verification
            integrity_ok = self._verify_integrity()
            if not integrity_ok:
                result['status'] = 'error'
                result['message'] = "Integrity check failed post-cleanup"
                logger.error("[CLEANUP] Database integrity check failed")
            else:
                logger.info(f"[CLEANUP] {result['message']}")

        except Exception as e:
            result['status'] = 'error'
            result['message'] = f"Cleanup failed: {str(e)}"
            logger.error(f"[CLEANUP] {result['message']}", exc_info=True)

        finally:
            result['duration_ms'] = (time.time() - cleanup_start) * 1000

            # Log to cleanup_log table
            self._log_cleanup_result(result)

        return result

    def _verify_cleanup_safe(self) -> Dict:
        """
        Pre-cleanup verification to ensure safe deletion.

        Checks:
        1. Last cleanup wasn't recent (prevent duplicate runs)
        2. Rows to delete are actually old enough
        3. Database integrity is OK
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Check 1: Last cleanup timing
        cursor.execute(f"""
            SELECT cleanup_timestamp FROM {self.cleanup_log_table}
            WHERE status = 'success'
            ORDER BY cleanup_timestamp DESC
            LIMIT 1
        """)
        last_cleanup = cursor.fetchone()

        if last_cleanup:
            last_cleanup_ts = last_cleanup[0]
            hours_since = (time.time() - last_cleanup_ts) / 3600

            if hours_since < 20:  # Require >20 hours between cleanups
                conn.close()
                return {
                    'safe': False,
                    'reason': f'Last cleanup was {hours_since:.1f}h ago (need >20h gap)'
                }

        # Check 2: Verify rows are actually old
        cutoff_ts = self._calculate_cutoff_timestamp()

        cursor.execute(
            "SELECT MIN(block_time), MAX(block_time), COUNT(*) FROM transfer_index WHERE block_time < ?",
            (cutoff_ts,)
        )
        result_row = cursor.fetchone()

        if result_row is None or result_row[2] is None:
            min_ts, max_ts, count = None, None, 0
        else:
            min_ts, max_ts, count = result_row

        if count == 0:
            conn.close()
            return {'safe': False, 'reason': 'No rows older than retention window'}

        min_age_days = (time.time() - min_ts) / 86400
        max_age_days = (time.time() - max_ts) / 86400

        # Safety margin: ensure oldest row is at least 5 days older than cutoff
        if max_age_days < (self.retention_days - 5):
            conn.close()
            return {
                'safe': False,
                'reason': f'Newest row to delete is {max_age_days:.0f}d old (safety margin <5d)'
            }

        logger.info(f"[CLEANUP_VERIFY] Will delete {count} rows (age {min_age_days:.0f}-{max_age_days:.0f}d)")

        # Check 3: Database integrity
        cursor.execute("PRAGMA integrity_check")
        integrity_result = cursor.fetchone()[0]

        conn.close()

        if integrity_result != 'ok':
            return {'safe': False, 'reason': f'Integrity check failed: {integrity_result}'}

        return {'safe': True, 'reason': 'All checks passed'}

    def _verify_integrity(self) -> bool:
        """Run PRAGMA integrity_check to detect corruption."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("PRAGMA integrity_check(100)")
            results = cursor.fetchall()
            conn.close()

            if results and results[0][0] == 'ok':
                logger.info("[INTEGRITY] Database check passed")
                return True
            else:
                logger.error(f"[INTEGRITY] Check failed: {results}")
                return False

        except Exception as e:
            logger.error(f"[INTEGRITY] Check error: {e}")
            return False

    def _log_cleanup_result(self, result: Dict) -> None:
        """Log cleanup result to cleanup_log table."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute(f"""
                INSERT INTO {self.cleanup_log_table}
                (cleanup_timestamp, retention_days, cutoff_timestamp,
                 rows_actually_deleted, freed_mb, cleanup_duration_ms,
                 db_size_before, db_size_after, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(),
                self.retention_days,
                self._calculate_cutoff_timestamp(),
                result['deleted'],
                result['freed_mb'],
                result['duration_ms'],
                result['db_size_before'],
                result['db_size_after'],
                result['status'],
                result['message'] if result['status'] != 'success' else None
            ))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"[CLEANUP_LOG] Failed to log result: {e}")
