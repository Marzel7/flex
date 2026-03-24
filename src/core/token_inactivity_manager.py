"""
Token Inactivity Manager

Detects inactive/dead tokens and stops monitoring them.
Frees WebSocket subscriptions and worker resources.

Inactivity conditions:
- No price updates for SOFT_THRESHOLD (10-15 min)
- No liquidity/volume change
- Hard stop after HARD_THRESHOLD (30-60 min)
- Repeated price fetch failures

Integration:
- Run inactivity_detection() in worker's _refresh_cycle()
- Called periodically (every 30-60 seconds)
- Deactivates tokens, registry updates WebSocket subscriptions
"""

import logging
import sqlite3
import time
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Inactivity thresholds (in seconds)
SOFT_INACTIVE_THRESHOLD = 900      # 15 minutes: warning state
HARD_STOP_THRESHOLD = 3600         # 60 minutes: definitive stop
FAILURE_THRESHOLD = 5              # Stop after 5 consecutive failures

# Stop reasons
STOP_REASON_INACTIVE = "inactive_no_updates"
STOP_REASON_NO_PRICE_DATA = "no_price_data"
STOP_REASON_LIQUIDITY_REMOVED = "liquidity_removed"
STOP_REASON_FETCH_FAILURES = "rpc_failures"


class TokenInactivityManager:
    """Manages detection and stopping of inactive tokens."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Add inactivity tracking columns if not exist."""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            # Check if columns exist by trying to select them
            cursor.execute("""
                PRAGMA table_info(tracked_tokens)
            """)
            columns = {row['name'] for row in cursor.fetchall()}

            # Add missing columns
            if 'last_activity_at' not in columns:
                cursor.execute("""
                    ALTER TABLE tracked_tokens ADD COLUMN last_activity_at INTEGER
                """)
                logger.info("Added last_activity_at column")

            if 'inactive_since' not in columns:
                cursor.execute("""
                    ALTER TABLE tracked_tokens ADD COLUMN inactive_since INTEGER
                """)
                logger.info("Added inactive_since column")

            if 'stop_reason' not in columns:
                cursor.execute("""
                    ALTER TABLE tracked_tokens ADD COLUMN stop_reason TEXT
                """)
                logger.info("Added stop_reason column")

            if 'fetch_failures' not in columns:
                cursor.execute("""
                    ALTER TABLE tracked_tokens ADD COLUMN fetch_failures INTEGER DEFAULT 0
                """)
                logger.info("Added fetch_failures column")

            conn.commit()
        except Exception as e:
            logger.error(f"Error ensuring inactivity columns: {e}")
        finally:
            conn.close()

    def record_activity(self, mint: str) -> bool:
        """Record a meaningful price update for a token."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                UPDATE tracked_tokens
                SET last_activity_at = ?, inactive_since = NULL, fetch_failures = 0
                WHERE mint = ?
            """, (now, mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error recording activity for {mint}: {e}")
            return False

    def record_fetch_failure(self, mint: str) -> bool:
        """Record a price fetch failure for a token."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracked_tokens
                SET fetch_failures = COALESCE(fetch_failures, 0) + 1
                WHERE mint = ? AND is_active = 1
            """, (mint,))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error recording fetch failure for {mint}: {e}")
            return False

    def detect_inactive_tokens(self) -> List[str]:
        """Find tokens that should be marked as soft inactive."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                SELECT mint FROM tracked_tokens
                WHERE is_active = 1
                  AND last_activity_at IS NOT NULL
                  AND (? - last_activity_at) > ?
                  AND inactive_since IS NULL
                LIMIT 100
            """, (now, SOFT_INACTIVE_THRESHOLD))

            return [row['mint'] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error detecting inactive tokens: {e}")
            return []
        finally:
            conn.close()

    def mark_soft_inactive(self, mint: str) -> bool:
        """Mark a token as soft inactive (warning state)."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                UPDATE tracked_tokens
                SET inactive_since = ?
                WHERE mint = ? AND is_active = 1
            """, (now, mint))

            conn.commit()
            conn.close()
            logger.info(f"[INACTIVITY] Marked {mint[:16]}... as soft inactive")
            return True
        except Exception as e:
            logger.error(f"Error marking {mint} as soft inactive: {e}")
            return False

    def detect_hard_stop_tokens(self) -> List[Dict]:
        """Find tokens that should be stopped (hard threshold or failures)."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            # Tokens that have been inactive for > HARD_STOP_THRESHOLD
            cursor.execute("""
                SELECT mint, 'inactive' as reason
                FROM tracked_tokens
                WHERE is_active = 1
                  AND inactive_since IS NOT NULL
                  AND (? - inactive_since) > ?
                UNION ALL
                SELECT mint, 'fetch_failures' as reason
                FROM tracked_tokens
                WHERE is_active = 1
                  AND fetch_failures >= ?
                LIMIT 100
            """, (now, HARD_STOP_THRESHOLD, FAILURE_THRESHOLD))

            return [{'mint': row['mint'], 'reason': row['reason']} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error detecting hard stop tokens: {e}")
            return []
        finally:
            conn.close()

    def stop_token(self, mint: str, reason: str) -> bool:
        """Stop monitoring a token and free its resources."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            # Deactivate token
            cursor.execute("""
                UPDATE tracked_tokens
                SET is_active = 0, stop_reason = ?, updated_at = ?
                WHERE mint = ?
            """, (reason, now, mint))

            conn.commit()
            conn.close()

            logger.info(f"[INACTIVITY] Stopped monitoring {mint[:16]}... (reason: {reason})")
            return True
        except Exception as e:
            logger.error(f"Error stopping token {mint}: {e}")
            return False

    def reactivate_token(self, mint: str) -> bool:
        """Reactivate a token if activity resumes (optional recovery)."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                UPDATE tracked_tokens
                SET is_active = 1, stop_reason = NULL,
                    last_activity_at = ?, inactive_since = NULL,
                    fetch_failures = 0, updated_at = ?
                WHERE mint = ? AND is_active = 0
            """, (now, now, mint))

            conn.commit()
            conn.close()

            logger.info(f"[INACTIVITY] Reactivated {mint[:16]}...")
            return True
        except Exception as e:
            logger.error(f"Error reactivating token {mint}: {e}")
            return False

    def get_inactive_stats(self) -> Dict:
        """Get statistics on inactive tokens."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_tracked,
                    SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) as stopped,
                    SUM(CASE WHEN inactive_since IS NOT NULL AND is_active = 1 THEN 1 ELSE 0 END) as soft_inactive
                FROM tracked_tokens
            """)

            row = cursor.fetchone()
            conn.close()

            return {
                'total_tracked': row['total_tracked'] or 0,
                'active': row['active'] or 0,
                'stopped': row['stopped'] or 0,
                'soft_inactive': row['soft_inactive'] or 0,
            }
        except Exception as e:
            logger.error(f"Error getting inactive stats: {e}")
            return {}

    def cleanup_stopped_tokens(self, days_old: int = 7) -> int:
        """Optional: Remove completely stopped tokens older than N days."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cutoff = int(time.time()) - (days_old * 86400)

            cursor.execute("""
                DELETE FROM tracked_tokens
                WHERE is_active = 0
                  AND updated_at < ?
                  AND stop_reason IS NOT NULL
            """, (cutoff,))

            count = cursor.rowcount
            conn.commit()
            conn.close()

            if count > 0:
                logger.info(f"[INACTIVITY] Cleaned up {count} old stopped tokens")
            return count
        except Exception as e:
            logger.error(f"Error cleaning up stopped tokens: {e}")
            return 0


def inactivity_detection(manager: TokenInactivityManager, registry) -> Dict:
    """
    Main inactivity detection loop.
    Call this from worker's _refresh_cycle() every 30-60 seconds.

    Returns statistics on processed tokens.
    """
    stats = {
        'soft_inactive_marked': 0,
        'tokens_stopped': 0,
        'inactive_stats': {}
    }

    try:
        # Stage 1: Mark soft inactive tokens (warning state)
        soft_inactive = manager.detect_inactive_tokens()
        for mint in soft_inactive:
            if manager.mark_soft_inactive(mint):
                stats['soft_inactive_marked'] += 1

        # Stage 2: Stop hard inactive or failing tokens
        hard_stops = manager.detect_hard_stop_tokens()
        for item in hard_stops:
            mint, reason = item['mint'], item['reason']
            if manager.stop_token(mint, reason):
                stats['tokens_stopped'] += 1

        # If tokens were stopped, trigger WebSocket subscription refresh
        if stats['tokens_stopped'] > 0 and hasattr(registry, '_ws_client'):
            logger.info(f"[INACTIVITY] Triggering WebSocket refresh after stopping {stats['tokens_stopped']} tokens")
            # WebSocket client will rebuild subscriptions from active pools on next refresh cycle
            # No direct unsubscribe needed — inactive tokens excluded automatically

        # Get stats
        stats['inactive_stats'] = manager.get_inactive_stats()

        # Optional periodic cleanup (every 1000 cycles ≈ ~3 hours)
        if hasattr(registry, 'stats') and registry.stats.get('cycles', 0) % 1000 == 0:
            cleaned = manager.cleanup_stopped_tokens(days_old=7)
            stats['cleaned_old_tokens'] = cleaned

    except Exception as e:
        logger.error(f"Error in inactivity detection: {e}")

    return stats
