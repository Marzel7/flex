"""
FLEX Launch Outcome Tracker

Tracks post-launch performance of tokens associated with detected organizations.

Enables:
- Launch prediction quality measurement
- Organization reputation scoring
- Historical token performance analysis
- Rug pull detection
"""

import sqlite3
import logging
import time
from typing import Dict, Optional, List
from dataclasses import dataclass
from src.core.price_service import get_price_service, TokenPrice

logger = logging.getLogger(__name__)


@dataclass
class LaunchOutcome:
    """Post-launch token performance tracking."""
    mint: str
    organization_id: Optional[int]
    launch_price_usd: float
    current_price_usd: float
    ath_price_usd: float
    return_multiple: float  # current / launch
    rug_flag: bool
    launched_at: int
    updated_at: int


class LaunchOutcomeTracker:
    """Tracks token outcomes for launched tokens."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self.price_service = get_price_service(db_path)
        self._ensure_tables()
        self.rug_threshold = 0.1  # If price drops 90%, mark as rug
        self.rug_volume_threshold = 10000  # Rug flags need low volume

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create token_launch_outcomes table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_launch_outcomes (
                mint                TEXT PRIMARY KEY,
                organization_id     INTEGER,
                launch_price_usd    REAL NOT NULL,
                current_price_usd   REAL NOT NULL,
                ath_price_usd       REAL NOT NULL,
                return_multiple     REAL DEFAULT 1.0,
                rug_flag            BOOLEAN DEFAULT 0,
                launched_at         INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lot_org_id
            ON token_launch_outcomes(organization_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lot_rug_flag
            ON token_launch_outcomes(rug_flag)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lot_return_multiple
            ON token_launch_outcomes(return_multiple DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lot_updated
            ON token_launch_outcomes(updated_at DESC)
        """)

        conn.commit()
        conn.close()
        logger.info("Launch outcome tracker initialized")

    def register_launch(self, mint: str, launch_price_usd: float,
                       organization_id: Optional[int] = None,
                       launch_timestamp: Optional[int] = None) -> bool:
        """Register a new token launch."""
        try:
            if launch_timestamp is None:
                launch_timestamp = int(time.time())

            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                INSERT OR REPLACE INTO token_launch_outcomes
                (mint, organization_id, launch_price_usd, current_price_usd,
                 ath_price_usd, return_multiple, launched_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (mint, organization_id, launch_price_usd, launch_price_usd,
                  launch_price_usd, 1.0, launch_timestamp, now))

            conn.commit()
            conn.close()
            logger.info(f"Registered launch: {mint} at ${launch_price_usd:.8f}")
            return True
        except Exception as e:
            logger.error(f"Error registering launch for {mint}: {e}")
            return False

    def update_outcome(self, mint: str, current_price_usd: float,
                      ath_price_usd: Optional[float] = None) -> Optional[LaunchOutcome]:
        """Update token outcome with current price."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Get existing record
            cursor.execute("""
                SELECT * FROM token_launch_outcomes WHERE mint = ?
            """, (mint,))
            row = cursor.fetchone()

            if not row:
                logger.warning(f"Token {mint} not found in outcomes")
                conn.close()
                return None

            launch_price = row['launch_price_usd']
            existing_ath = row['ath_price_usd']

            # Update ATH if new price is higher
            new_ath = max(existing_ath, current_price_usd)
            if ath_price_usd and ath_price_usd > new_ath:
                new_ath = ath_price_usd

            # Compute return multiple
            return_multiple = current_price_usd / launch_price if launch_price > 0 else 1.0

            # Detect rug pull
            rug_flag = self._detect_rug_pull(mint, current_price_usd, launch_price)

            now = int(time.time())

            cursor.execute("""
                UPDATE token_launch_outcomes
                SET current_price_usd = ?, ath_price_usd = ?,
                    return_multiple = ?, rug_flag = ?, updated_at = ?
                WHERE mint = ?
            """, (current_price_usd, new_ath, return_multiple, rug_flag, now, mint))

            conn.commit()
            conn.close()

            logger.debug(
                f"Updated {mint}: price=${current_price_usd:.8f}, "
                f"return={return_multiple:.2f}x, rug={rug_flag}"
            )

            return LaunchOutcome(
                mint=mint,
                organization_id=row['organization_id'],
                launch_price_usd=launch_price,
                current_price_usd=current_price_usd,
                ath_price_usd=new_ath,
                return_multiple=return_multiple,
                rug_flag=rug_flag,
                launched_at=row['launched_at'],
                updated_at=now
            )
        except Exception as e:
            logger.error(f"Error updating outcome for {mint}: {e}")
            return None

    def _detect_rug_pull(self, mint: str, current_price: float,
                        launch_price: float) -> bool:
        """
        Detect rug pull based on price collapse and volume.

        A token is flagged as rug if:
        - Price dropped 90% from launch
        - Current volume is very low
        """
        if launch_price == 0:
            return False

        price_ratio = current_price / launch_price
        if price_ratio > 0.1:  # Not below 90% loss threshold
            return False

        # Check current volume to confirm rug
        try:
            price = self.price_service.get_token_price_sync(mint, cache_type='org')
            if price.volume_24h < self.rug_volume_threshold:
                return True
        except Exception as e:
            logger.debug(f"Could not verify rug pull for {mint}: {e}")

        return False

    def get_outcome(self, mint: str) -> Optional[LaunchOutcome]:
        """Get outcome for a specific token."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM token_launch_outcomes WHERE mint = ?
            """, (mint,))

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            return LaunchOutcome(
                mint=row['mint'],
                organization_id=row['organization_id'],
                launch_price_usd=row['launch_price_usd'],
                current_price_usd=row['current_price_usd'],
                ath_price_usd=row['ath_price_usd'],
                return_multiple=row['return_multiple'],
                rug_flag=bool(row['rug_flag']),
                launched_at=row['launched_at'],
                updated_at=row['updated_at']
            )
        except Exception as e:
            logger.error(f"Error getting outcome for {mint}: {e}")
            return None

    def get_organization_outcomes(self, organization_id: int) -> List[LaunchOutcome]:
        """Get all outcomes for tokens from an organization."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM token_launch_outcomes
                WHERE organization_id = ?
                ORDER BY return_multiple DESC
            """, (organization_id,))

            rows = cursor.fetchall()
            conn.close()

            return [
                LaunchOutcome(
                    mint=row['mint'],
                    organization_id=row['organization_id'],
                    launch_price_usd=row['launch_price_usd'],
                    current_price_usd=row['current_price_usd'],
                    ath_price_usd=row['ath_price_usd'],
                    return_multiple=row['return_multiple'],
                    rug_flag=bool(row['rug_flag']),
                    launched_at=row['launched_at'],
                    updated_at=row['updated_at']
                )
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Error getting outcomes for org {organization_id}: {e}")
            return []

    def get_statistics(self, organization_id: Optional[int] = None) -> Dict:
        """Get statistics about launches."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            if organization_id:
                filter_clause = "WHERE organization_id = ?"
                params = (organization_id,)
            else:
                filter_clause = ""
                params = ()

            # Total launches
            cursor.execute(f"SELECT COUNT(*) as count FROM token_launch_outcomes {filter_clause}", params)
            total = cursor.fetchone()['count']

            # Rug pulls
            cursor.execute(
                f"SELECT COUNT(*) as count FROM token_launch_outcomes WHERE rug_flag = 1 {' AND organization_id = ?' if organization_id else ''}",
                params if not organization_id else (organization_id,)
            )
            rugs = cursor.fetchone()['count']

            # Average return
            cursor.execute(
                f"SELECT AVG(return_multiple) as avg_return FROM token_launch_outcomes {filter_clause}",
                params
            )
            avg_return = cursor.fetchone()['avg_return'] or 1.0

            # Max return
            cursor.execute(
                f"SELECT MAX(return_multiple) as max_return FROM token_launch_outcomes {filter_clause}",
                params
            )
            max_return = cursor.fetchone()['max_return'] or 1.0

            conn.close()

            return {
                'total_launches': total,
                'rug_pulls': rugs,
                'rug_rate': (rugs / total * 100) if total > 0 else 0,
                'avg_return_multiple': avg_return,
                'max_return_multiple': max_return
            }
        except Exception as e:
            logger.error(f"Error getting launch statistics: {e}")
            return {}

    def sync_outcomes_from_prices(self, batch_size: int = 50) -> int:
        """
        Sync outcomes with current prices.

        Fetches current prices for all tracked tokens and updates outcomes.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("SELECT mint FROM token_launch_outcomes WHERE rug_flag = 0 LIMIT 1000")
            mints = [row['mint'] for row in cursor.fetchall()]
            conn.close()

            if not mints:
                logger.info("No outcomes to sync")
                return 0

            # Fetch prices in batches
            updated = 0
            for i in range(0, len(mints), batch_size):
                batch = mints[i:i + batch_size]
                try:
                    prices = self.price_service.get_token_prices_sync(batch, cache_type='org')
                    for mint, price in prices.items():
                        if price.source != 'unavailable':
                            self.update_outcome(mint, price.price_usd)
                            updated += 1
                except Exception as e:
                    logger.error(f"Error syncing batch: {e}")

            logger.info(f"Synced {updated} outcomes")
            return updated
        except Exception as e:
            logger.error(f"Error syncing outcomes: {e}")
            return 0


# Singleton instance
_outcome_tracker: Optional[LaunchOutcomeTracker] = None


def get_outcome_tracker(db_path: str = 'database/flex_complete_database.db') -> LaunchOutcomeTracker:
    """Get or create singleton tracker."""
    global _outcome_tracker
    if _outcome_tracker is None:
        _outcome_tracker = LaunchOutcomeTracker(db_path)
    return _outcome_tracker
