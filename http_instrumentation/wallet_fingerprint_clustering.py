"""
Wallet Fingerprint Clustering Optimization

Purpose:
Avoids repeated scans of the same wallet across different creators by
introducing a global fingerprint cache that stores wallet classifications
and confidence scores.

This layer sits between FunderPrefilter and TwoPassScanner in the optimization
pipeline:

    CreatorExtractor
        ↓
    FunderPrefilter (shortlist high-signal funders)
        ↓
    WalletFingerprintCluster ← NEW (this module)
        ↓
    TwoPassScanner (adaptive scanning)
        ↓
    TombstoneManager (skip empty wallets)

Usage in extraction flow:

    from wallet_fingerprint_clustering import WalletFingerprintCluster

    cluster = WalletFingerprintCluster('flex_complete_database.db')

    # Before scanning each funder:
    action, cached_type, cached_confidence = cluster.lookup_wallet(funder_address)

    if action == 'SKIP':
        # Skip entirely - high confidence cached classification
        continue

    elif action == 'REFRESH':
        # Run 1-page refresh scan to update fingerprint
        pass_a_type, confidence = await scanner.pass_a_fingerprint(funder)
        cluster.save_fingerprint(funder, pass_a_type, confidence)

    else:  # action == 'FULL_SCAN'
        # Run normal TwoPassScanner flow
        type, confidence = await scanner.pass_a_fingerprint(funder)
        if should_do_pass_b(...):
            pages = await scanner.pass_b_deep_scan(funder)
        cluster.save_fingerprint(funder, type, confidence, pages_scanned)

Monitoring:

Add to wallet_scan_metrics recording:
    - fingerprint_cache_hit: 1 if skipped via fingerprint, 0 otherwise
    - fingerprint_refresh: 1 if refreshed, 0 otherwise
"""

import sqlite3
import hashlib
import json
from datetime import datetime
from typing import Tuple, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class FingerprintAction:
    """Action recommendations based on fingerprint confidence."""
    SKIP = 'SKIP'           # confidence >= 0.9
    REFRESH = 'REFRESH'     # 0.7 <= confidence < 0.9
    FULL_SCAN = 'FULL_SCAN' # confidence < 0.7 or not found


class WalletFingerprint:
    """Represents a cached wallet fingerprint."""

    def __init__(
        self,
        wallet_address: str,
        wallet_type: str,
        confidence: float,
        fingerprint_hash: str = None,
        tx_sample_hash: str = None,
        scan_count: int = 1,
        skip_reason: str = None
    ):
        self.wallet_address = wallet_address
        self.wallet_type = wallet_type
        self.confidence = max(0.0, min(1.0, confidence))  # Clamp 0-1
        self.fingerprint_hash = fingerprint_hash
        self.tx_sample_hash = tx_sample_hash
        self.scan_count = scan_count
        self.skip_reason = skip_reason

    def recommend_action(self) -> str:
        """Determine recommended scanning action based on confidence."""
        if self.confidence >= 0.9:
            return FingerprintAction.SKIP
        elif self.confidence >= 0.7:
            return FingerprintAction.REFRESH
        else:
            return FingerprintAction.FULL_SCAN

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'wallet_address': self.wallet_address,
            'wallet_type': self.wallet_type,
            'confidence': self.confidence,
            'fingerprint_hash': self.fingerprint_hash,
            'tx_sample_hash': self.tx_sample_hash,
            'scan_count': self.scan_count,
            'skip_reason': self.skip_reason,
            'action': self.recommend_action(),
        }


class WalletFingerprintCluster:
    """
    Global wallet fingerprint cache for cross-creator deduplication.

    Stores wallet classifications and confidence scores to avoid re-scanning
    the same wallet across different creators.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table_exists()

    def _ensure_table_exists(self):
        """Verify wallet_fingerprints table exists."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='wallet_fingerprints'"
            )

            if cursor.fetchone() is None:
                logger.warning(
                    "wallet_fingerprints table not found. "
                    "Run: sqlite3 %s < wallet_fingerprint_clustering_schema.sql",
                    self.db_path
                )

            conn.close()
        except Exception as e:
            logger.error(f"Error checking wallet_fingerprints table: {e}")

    def lookup_wallet(self, wallet_address: str) -> Tuple[str, Optional[str], Optional[float]]:
        """
        Look up wallet in fingerprint cache.

        Args:
            wallet_address: Wallet to look up

        Returns:
            (action, cached_wallet_type, cached_confidence)
            where action in {SKIP, REFRESH, FULL_SCAN, NOT_FOUND}

        action meanings:
            SKIP: confidence >= 0.9 (skip scan entirely)
            REFRESH: 0.7 <= confidence < 0.9 (do 1-page refresh)
            FULL_SCAN: confidence < 0.7 (run normal scanner)
            NOT_FOUND: wallet not in cache (run normal scanner)
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT wallet_type, confidence, scan_count
                FROM wallet_fingerprints
                WHERE wallet_address = ?
                """,
                (wallet_address,)
            )

            row = cursor.fetchone()
            conn.close()

            if row is None:
                return FingerprintAction.FULL_SCAN, None, None

            wallet_type, confidence, scan_count = row

            # Determine action
            if confidence >= 0.9:
                action = FingerprintAction.SKIP
            elif confidence >= 0.7:
                action = FingerprintAction.REFRESH
            else:
                action = FingerprintAction.FULL_SCAN

            return action, wallet_type, confidence

        except Exception as e:
            logger.error(f"Error looking up wallet {wallet_address}: {e}")
            return FingerprintAction.FULL_SCAN, None, None

    def save_fingerprint(
        self,
        wallet_address: str,
        wallet_type: str,
        confidence: float,
        fingerprint_hash: str = None,
        tx_sample_hash: str = None,
        pages_scanned: int = 1,
        skip_reason: str = None
    ) -> bool:
        """
        Save or update wallet fingerprint in cache.

        Args:
            wallet_address: Wallet address
            wallet_type: Classification ('cex', 'infra', 'bot', 'unknown')
            confidence: Confidence score (0.0-1.0)
            fingerprint_hash: Hash of wallet's transaction patterns
            tx_sample_hash: Hash of first page's transactions
            pages_scanned: Number of pages scanned
            skip_reason: Optional skip reason for debugging

        Returns:
            True if successful, False otherwise
        """
        try:
            # Clamp confidence to 0-1
            confidence = max(0.0, min(1.0, confidence))

            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            # Check if wallet exists
            cursor.execute(
                "SELECT scan_count FROM wallet_fingerprints WHERE wallet_address = ?",
                (wallet_address,)
            )

            existing = cursor.fetchone()

            if existing:
                # Update existing fingerprint
                scan_count = existing[0] + 1

                cursor.execute(
                    """
                    UPDATE wallet_fingerprints
                    SET wallet_type = ?,
                        confidence = ?,
                        fingerprint_hash = COALESCE(?, fingerprint_hash),
                        tx_sample_hash = COALESCE(?, tx_sample_hash),
                        scan_count = ?,
                        last_seen = CURRENT_TIMESTAMP,
                        skip_reason = ?
                    WHERE wallet_address = ?
                    """,
                    (
                        wallet_type,
                        confidence,
                        fingerprint_hash,
                        tx_sample_hash,
                        scan_count,
                        skip_reason,
                        wallet_address
                    )
                )

                logger.debug(
                    f"Updated fingerprint for {wallet_address}: "
                    f"type={wallet_type}, conf={confidence:.2f}, scans={scan_count}"
                )

            else:
                # Insert new fingerprint
                cursor.execute(
                    """
                    INSERT INTO wallet_fingerprints
                    (wallet_address, wallet_type, confidence, fingerprint_hash,
                     tx_sample_hash, scan_count, skip_reason, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        wallet_address,
                        wallet_type,
                        confidence,
                        fingerprint_hash,
                        tx_sample_hash,
                        1,
                        skip_reason
                    )
                )

                logger.debug(
                    f"Saved new fingerprint for {wallet_address}: "
                    f"type={wallet_type}, conf={confidence:.2f}"
                )

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error saving fingerprint for {wallet_address}: {e}")
            return False

    def get_fingerprint(self, wallet_address: str) -> Optional[WalletFingerprint]:
        """
        Get full fingerprint object for a wallet.

        Args:
            wallet_address: Wallet to get

        Returns:
            WalletFingerprint object or None if not found
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT wallet_type, confidence, fingerprint_hash, tx_sample_hash,
                       scan_count, skip_reason
                FROM wallet_fingerprints
                WHERE wallet_address = ?
                """,
                (wallet_address,)
            )

            row = cursor.fetchone()
            conn.close()

            if row is None:
                return None

            wallet_type, confidence, fp_hash, tx_hash, scan_count, skip_reason = row

            return WalletFingerprint(
                wallet_address=wallet_address,
                wallet_type=wallet_type,
                confidence=confidence,
                fingerprint_hash=fp_hash,
                tx_sample_hash=tx_hash,
                scan_count=scan_count,
                skip_reason=skip_reason
            )

        except Exception as e:
            logger.error(f"Error getting fingerprint for {wallet_address}: {e}")
            return None

    def get_stats(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get fingerprint cache statistics.

        Args:
            hours: Time window in hours (default 24)

        Returns:
            Dictionary with cache statistics
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            # Total stats
            cursor.execute(
                """
                SELECT
                    COUNT(DISTINCT wallet_address) as total,
                    SUM(CASE WHEN last_seen >= datetime('now', ? || ' hours') THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN confidence >= 0.9 THEN 1 ELSE 0 END) as high_conf,
                    SUM(CASE WHEN confidence >= 0.7 AND confidence < 0.9 THEN 1 ELSE 0 END) as med_conf,
                    SUM(CASE WHEN confidence < 0.7 THEN 1 ELSE 0 END) as low_conf,
                    ROUND(AVG(scan_count), 1) as avg_scans,
                    ROUND(AVG(confidence), 3) as avg_conf
                FROM wallet_fingerprints
                """,
                (f'-{hours}',)
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return {
                    'total_fingerprints': 0,
                    'active_' + str(hours) + 'h': 0,
                    'high_confidence': 0,
                    'medium_confidence': 0,
                    'low_confidence': 0,
                    'avg_scans_per_wallet': 0.0,
                    'avg_confidence': 0.0,
                }

            (total, active, high, med, low, avg_scans, avg_conf) = row

            return {
                'total_fingerprints': total or 0,
                f'active_{hours}h': active or 0,
                'high_confidence': high or 0,
                'medium_confidence': med or 0,
                'low_confidence': low or 0,
                'avg_scans_per_wallet': avg_scans or 0.0,
                'avg_confidence': avg_conf or 0.0,
            }

        except Exception as e:
            logger.error(f"Error getting fingerprint stats: {e}")
            return {}

    def get_type_distribution(self) -> Dict[str, Dict[str, Any]]:
        """
        Get distribution of wallet types in cache.

        Returns:
            Dict mapping wallet_type to statistics
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    wallet_type,
                    COUNT(*) as count,
                    ROUND(AVG(confidence), 3) as avg_confidence,
                    ROUND(AVG(scan_count), 1) as avg_scans,
                    SUM(CASE WHEN last_seen >= datetime('now', '-7 days') THEN 1 ELSE 0 END) as active_7d
                FROM wallet_fingerprints
                GROUP BY wallet_type
                ORDER BY count DESC
                """
            )

            rows = cursor.fetchall()
            conn.close()

            result = {}
            for wallet_type, count, avg_conf, avg_scans, active_7d in rows:
                result[wallet_type or 'unknown'] = {
                    'count': count,
                    'avg_confidence': avg_conf,
                    'avg_scans': avg_scans,
                    'active_7d': active_7d or 0,
                }

            return result

        except Exception as e:
            logger.error(f"Error getting type distribution: {e}")
            return {}

    def get_top_frequent_wallets(self, limit: int = 20) -> list:
        """
        Get most frequently scanned wallets (candidates for reuse).

        Args:
            limit: Maximum number to return

        Returns:
            List of (wallet_address, type, confidence, scan_count) tuples
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT wallet_address, wallet_type, confidence, scan_count
                FROM wallet_fingerprints
                WHERE scan_count >= 2
                ORDER BY scan_count DESC
                LIMIT ?
                """,
                (limit,)
            )

            rows = cursor.fetchall()
            conn.close()

            return rows or []

        except Exception as e:
            logger.error(f"Error getting frequent wallets: {e}")
            return []

    def estimate_credits_saved(self) -> Dict[str, Any]:
        """
        Estimate credits saved by fingerprint cache.

        Assumptions:
            - SKIP action: 0 credits (cached, no scan)
            - REFRESH action: 50 credits (1 page)
            - FULL_SCAN from cache: 150 credits (estimated, vs fresh scan)

        Returns:
            Dictionary with savings estimates
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            # Count wallets by action category
            cursor.execute(
                """
                SELECT
                    SUM(CASE WHEN confidence >= 0.9 THEN 1 ELSE 0 END) as skip_count,
                    SUM(CASE WHEN confidence >= 0.7 AND confidence < 0.9 THEN 1 ELSE 0 END) as refresh_count,
                    SUM(CASE WHEN confidence < 0.7 THEN 1 ELSE 0 END) as full_scan_count
                FROM wallet_fingerprints
                """
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return {
                    'estimated_skipped_scans': 0,
                    'estimated_refreshed_scans': 0,
                    'estimated_credits_saved': 0,
                }

            skip_count, refresh_count, full_scan_count = row

            skip_count = skip_count or 0
            refresh_count = refresh_count or 0
            full_scan_count = full_scan_count or 0

            # Credit calculations
            skipped_credits_saved = skip_count * 200  # Full scan avoidance
            refreshed_credit_cost = refresh_count * 50  # Light refresh
            refreshed_credits_saved = refreshed_credit_cost * 0.75  # vs full scan

            total_saved = skipped_credits_saved + refreshed_credits_saved

            return {
                'total_fingerprints': skip_count + refresh_count + full_scan_count,
                'skip_action_count': skip_count,
                'refresh_action_count': refresh_count,
                'full_scan_action_count': full_scan_count,
                'estimated_skipped_scans': skip_count,
                'estimated_skipped_credits': skipped_credits_saved,
                'estimated_refreshed_scans': refresh_count,
                'estimated_refreshed_credit_cost': refreshed_credit_cost,
                'estimated_refreshed_credits_saved': round(refreshed_credits_saved),
                'total_estimated_credits_saved': round(total_saved),
                'notes': 'Based on 200cr/full-scan, 50cr/refresh, 75% savings on refreshes',
            }

        except Exception as e:
            logger.error(f"Error estimating credits saved: {e}")
            return {}

    def cleanup_old_fingerprints(self, days_old: int = 30) -> int:
        """
        Remove old fingerprints not accessed in N days.

        Args:
            days_old: Remove if last_seen > days_old

        Returns:
            Number of rows deleted
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            cursor.execute(
                """
                DELETE FROM wallet_fingerprints
                WHERE last_seen < datetime('now', ? || ' days')
                """,
                (f'-{days_old}',)
            )

            count = cursor.rowcount
            conn.commit()
            conn.close()

            if count > 0:
                logger.info(f"Cleaned up {count} old fingerprints (> {days_old} days)")

            return count

        except Exception as e:
            logger.error(f"Error cleaning up fingerprints: {e}")
            return 0


# Helper function for integration
def create_fingerprint_cluster(db_path: str) -> WalletFingerprintCluster:
    """
    Factory function to create and initialize fingerprint cluster.

    Args:
        db_path: Path to database

    Returns:
        WalletFingerprintCluster instance
    """
    return WalletFingerprintCluster(db_path)
