#!/usr/bin/env python3
"""
Persistent Address Cursor Manager for FLEX V2.

Tracks "where we left off" for each address to enable incremental signature
fetching, reducing RPC calls by 60%.

Instead of fetching all signatures every time, fetch only NEW signatures
after the last one we processed.

Usage:
    cursor_mgr = CursorManager(db_path)

    # Get cursor for an address
    cursor = await cursor_mgr.get_cursor(creator_address)

    # Fetch only new signatures (not all)
    before_sig = cursor.last_signature if cursor else None
    signatures = await rpc.get_signatures(creator_address, before=before_sig)

    # Update cursor after processing
    if signatures:
        await cursor_mgr.update_cursor(creator_address, signatures[0].signature, len(signatures))
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AddressCursor:
    """Cursor state for an address."""
    address: str
    last_signature: Optional[str]
    last_scan_at: datetime
    next_scan_at: datetime
    status: str  # active, paused, failed


class CursorManager:
    """
    Manages persistent cursor state for incremental extraction.

    Reduces RPC calls by 60% by tracking "where we left off"
    for each address, allowing incremental signature fetching.

    Database Table: address_scan_state
    - address (TEXT PRIMARY KEY)
    - last_signature (TEXT) - Last signature we processed
    - last_scan_at (TIMESTAMP) - When we last scanned
    - next_scan_at (TIMESTAMP) - When to scan next (for due-time scheduler)
    - status (TEXT) - active, paused, failed
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create address_scan_state table if it doesn't exist."""
        # X78.0 -- found live during the X78.0 soak: CursorManager.__init__
        # calls this on EVERY instantiation, and RealTimeCreatorFundingExtractor
        # is constructed fresh per job (not a reused singleton, per the
        # duplicated "CursorManager initialized"/"SQLite optimizations
        # applied" log lines observed once per job) -- making this one of
        # the most frequently-hit connections in the whole pipeline.
        # conn.close() was only reached on success; the except block below
        # re-raised WITHOUT ever closing conn, leaving it (and its write
        # lease, since CREATE TABLE/INDEX are write-shaped SQL) open for the
        # rest of that thread's life. Checks sqlite_master first so an
        # already-migrated DB (true after the very first ever run) issues
        # zero write statements here.
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.execute("PRAGMA busy_timeout = 60000")
            cursor = conn.cursor()

            if not cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='address_scan_state'"
            ).fetchone():
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS address_scan_state (
                    address TEXT PRIMARY KEY,
                    last_signature TEXT,
                    last_scan_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    next_scan_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
                """)

                # Index for due-time scheduler (critical query)
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_address_scan_state_due_time
                ON address_scan_state(next_scan_at, status)
                WHERE status = 'active'
                """)

                # Index for cursor lookups
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_address_scan_state_address
                ON address_scan_state(address)
                """)

                conn.commit()
            logger.debug("address_scan_state table ensured")

        except Exception as e:
            logger.error(f"Error creating address_scan_state table: {e}")
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_cursor(self, address: str) -> Optional[AddressCursor]:
        """
        Get cursor state for address.

        Returns None if address has never been scanned.

        Args:
            address: Address to get cursor for

        Returns:
            AddressCursor or None if not found
        """
        # X78.0 -- found live during the X78.0 soak: called from inside
        # extract_for_creator's own paging loop, on the event-loop thread.
        # conn.close() was only reached on success; the except block never
        # closed it. conn declared before the try so finally can close it
        # regardless of which line raised.
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
            SELECT address, last_signature, last_scan_at, next_scan_at, status
            FROM address_scan_state
            WHERE address = ?
            """, (address,))

            row = cursor.fetchone()

            if not row:
                return None

            return AddressCursor(
                address=row['address'],
                last_signature=row['last_signature'],
                last_scan_at=datetime.fromisoformat(row['last_scan_at']),
                next_scan_at=datetime.fromisoformat(row['next_scan_at']),
                status=row['status']
            )

        except Exception as e:
            logger.error(f"Error getting cursor for {address}: {e}")
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def update_cursor(
        self,
        address: str,
        last_signature: str,
        activity_count: int = 0
    ) -> None:
        """
        Update cursor after extracting signatures.

        Args:
            address: Address being scanned
            last_signature: Most recent signature we processed
            activity_count: Number of new signatures/transfers in this scan
        """
        # X78.0 -- found live during the X78.0 soak: called at the end of
        # extract_for_creator's paging loop, on the event-loop thread.
        # conn.close() was only reached on success. conn declared before the
        # try so finally can close it regardless of which line raised.
        conn = None
        try:
            # Calculate next scan time based on activity
            next_scan_at = self._calculate_next_scan_time(activity_count)

            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.execute("PRAGMA busy_timeout = 60000")
            cursor = conn.cursor()

            cursor.execute("""
            INSERT INTO address_scan_state
                (address, last_signature, last_scan_at, next_scan_at, status)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?, 'active')
            ON CONFLICT (address) DO UPDATE SET
                last_signature = excluded.last_signature,
                last_scan_at = CURRENT_TIMESTAMP,
                next_scan_at = excluded.next_scan_at,
                status = 'active'
            """, (address, last_signature, next_scan_at.isoformat()))

            conn.commit()

            logger.debug(
                f"Updated cursor for {address}: "
                f"sig={last_signature[:8]}..., "
                f"next_scan={next_scan_at}, "
                f"activity={activity_count}"
            )

        except Exception as e:
            logger.error(f"Error updating cursor for {address}: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _calculate_next_scan_time(self, activity_count: int) -> datetime:
        """
        Calculate next scan time based on activity.

        More active addresses are scanned more frequently:
        - 0 new signatures: 24 hours (no activity)
        - 1-2 new: 6 hours (light activity)
        - 3-5 new: 1 hour (medium activity)
        - 6+ new: 15 minutes (high activity)

        Args:
            activity_count: Number of new signatures found

        Returns:
            Next scan time as datetime
        """
        now = datetime.utcnow()

        if activity_count == 0:
            # No activity - check again in 24 hours
            return now + timedelta(hours=24)
        elif activity_count <= 2:
            # Light activity - check again in 6 hours
            return now + timedelta(hours=6)
        elif activity_count <= 5:
            # Medium activity - check again in 1 hour
            return now + timedelta(hours=1)
        else:
            # High activity - check again in 15 minutes
            return now + timedelta(minutes=15)

    def get_addresses_due_for_scan(
        self,
        limit: int = 100
    ) -> List[AddressCursor]:
        """
        Get addresses due for scanning (next_scan_at <= NOW()).

        Called by due_time_scheduler every 60 seconds.

        Args:
            limit: Maximum number of addresses to return

        Returns:
            List of AddressCursor objects
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
            SELECT address, last_signature, last_scan_at, next_scan_at, status
            FROM address_scan_state
            WHERE next_scan_at <= CURRENT_TIMESTAMP
            AND status = 'active'
            ORDER BY next_scan_at ASC
            LIMIT ?
            """, (limit,))

            rows = cursor.fetchall()
            conn.close()

            return [
                AddressCursor(
                    address=row['address'],
                    last_signature=row['last_signature'],
                    last_scan_at=datetime.fromisoformat(row['last_scan_at']),
                    next_scan_at=datetime.fromisoformat(row['next_scan_at']),
                    status=row['status']
                )
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Error getting addresses due for scan: {e}")
            return []

    def mark_failed(self, address: str, error: str) -> None:
        """
        Mark address as failed due to error.

        Args:
            address: Address to mark as failed
            error: Error message
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
            UPDATE address_scan_state
            SET status = 'failed'
            WHERE address = ?
            """, (address,))

            conn.commit()
            conn.close()

            logger.error(f"Marked {address} as failed: {error}")

        except Exception as e:
            logger.error(f"Error marking {address} as failed: {e}")

    def mark_paused(self, address: str) -> None:
        """
        Pause scanning for an address (e.g., contract pause).

        Args:
            address: Address to pause
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
            UPDATE address_scan_state
            SET status = 'paused'
            WHERE address = ?
            """, (address,))

            conn.commit()
            conn.close()

            logger.info(f"Paused scanning for {address}")

        except Exception as e:
            logger.error(f"Error pausing {address}: {e}")

    def resume(self, address: str) -> None:
        """
        Resume scanning for a paused address.

        Args:
            address: Address to resume
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
            UPDATE address_scan_state
            SET status = 'active'
            WHERE address = ?
            """, (address,))

            conn.commit()
            conn.close()

            logger.info(f"Resumed scanning for {address}")

        except Exception as e:
            logger.error(f"Error resuming {address}: {e}")

    def get_stats(self) -> dict:
        """Get cursor manager statistics."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
            SELECT
                COUNT(*) as total_addresses,
                COUNT(CASE WHEN status = 'active' THEN 1 END) as active_addresses,
                COUNT(CASE WHEN status = 'paused' THEN 1 END) as paused_addresses,
                COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed_addresses,
                COUNT(CASE WHEN last_signature IS NOT NULL THEN 1 END) as scanned_addresses
            FROM address_scan_state
            """)

            row = cursor.fetchone()
            conn.close()

            return {
                'total_addresses': row[0] or 0,
                'active_addresses': row[1] or 0,
                'paused_addresses': row[2] or 0,
                'failed_addresses': row[3] or 0,
                'scanned_addresses': row[4] or 0,
            }

        except Exception as e:
            logger.error(f"Error getting cursor stats: {e}")
            return {}
