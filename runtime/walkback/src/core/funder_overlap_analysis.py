"""
FLEX Funder Overlap Signal Analysis

Detects coordination between funding wallets based on the overlap of creators they fund.

This signal identifies:
- Coordinated dev activity (same creators funded by multiple wallets)
- Dev farm wallet networks (operator rotating funding sources)
- Developer organization clusters (shared infrastructure)
- Launch preparation patterns (coordinated team funding)

Core metric: overlap_ratio = shared_creators / min(funder_a_creators, funder_b_creators)
Range: 0.0 (no overlap) to 1.0 (identical creator sets)

Classification:
- 0.50-0.74: Medium coordination
- 0.75-0.99: High coordination
- 1.00 with 3+ creators: Very strong dev relationship
"""

import sqlite3
import logging
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from src.utils.infra_mapping import build_excluded_set, sync_infra_wallets

logger = logging.getLogger(__name__)


class FunderCreatorExtractor:
    """
    Extracts funder → creator relationships from transfer_index.

    Filters to seed-phase transfers:
    - amount_sol: 0.5 to 10 SOL
    - is_valid: 1 (valid transactions)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def extract_funder_creator_pairs(self, cursor: sqlite3.Cursor,
                                        excluded: frozenset = frozenset()) -> Dict[str, set]:
        """
        Extract funder → creator relationships, excluding CEX/infra wallets.

        Returns: {funder_wallet: {creator_wallet1, creator_wallet2, ...}}
        """
        cursor.execute("""
            SELECT DISTINCT
                source AS funder_wallet,
                destination AS creator_wallet
            FROM transfer_index
            WHERE amount_sol BETWEEN 0.5 AND 10
            AND is_valid = 1
        """)

        funder_creators = defaultdict(set)
        for row in cursor.fetchall():
            funder = row['funder_wallet']
            creator = row['creator_wallet']
            if funder and creator and funder not in excluded:
                funder_creators[funder].add(creator)

        return dict(funder_creators)

    def get_funder_creator_count(self, funder: str, creators: Dict[str, set]) -> int:
        """Get number of unique creators funded by a wallet."""
        return len(creators.get(funder, set()))


class FunderOverlapAnalyzer:
    """
    Analyzes overlap between funder wallets.

    Identifies wallet pairs that fund the same creators,
    computing overlap_ratio as a measure of coordination.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.extractor = FunderCreatorExtractor(db_path)
        self.start_time = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create funder_overlap table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_overlap (
                overlap_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                funder_a            TEXT NOT NULL,
                funder_b            TEXT NOT NULL,
                shared_creators     INTEGER DEFAULT 0,
                overlap_ratio       REAL DEFAULT 0,
                funder_a_creators   INTEGER DEFAULT 0,
                funder_b_creators   INTEGER DEFAULT 0,
                coordination_level  TEXT,
                detected_at         INTEGER NOT NULL,
                UNIQUE(funder_a, funder_b)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fo_overlap_ratio
            ON funder_overlap(overlap_ratio DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fo_funder_a
            ON funder_overlap(funder_a)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fo_funder_b
            ON funder_overlap(funder_b)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fo_shared_creators
            ON funder_overlap(shared_creators DESC)
        """)

        conn.commit()
        conn.close()

    def _classify_coordination_level(self, overlap_ratio: float, shared_creators: int) -> str:
        """
        Classify coordination level based on overlap metrics.

        Returns: 'very_strong'|'high'|'medium'|'low'
        """
        if overlap_ratio >= 1.0 and shared_creators >= 3:
            return 'very_strong'
        elif overlap_ratio >= 0.75:
            return 'high'
        elif overlap_ratio >= 0.50:
            return 'medium'
        else:
            return 'low'

    def compute_funder_overlaps(self) -> Dict[Tuple[str, str], Dict]:
        """
        Compute overlap between all funder wallet pairs.

        Returns: {(funder_a, funder_b): {shared, ratio, counts}}
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Build unified CEX/infra exclusion set (static registry + live cex_wallets table)
        sync_infra_wallets(conn)
        excluded = build_excluded_set(conn)

        # Extract funder → creator relationships, excluding CEX/infra wallets
        funder_creators = self.extractor.extract_funder_creator_pairs(cursor, excluded)
        conn.close()

        if not funder_creators:
            logger.warning("No funder-creator relationships found")
            return {}

        funders = sorted(list(funder_creators.keys()))
        overlaps = {}

        # Compute pairwise overlaps
        for i, funder_a in enumerate(funders):
            creators_a = funder_creators[funder_a]
            count_a = len(creators_a)

            for funder_b in funders[i + 1:]:
                creators_b = funder_creators[funder_b]
                count_b = len(creators_b)

                # Compute shared creators
                shared = len(creators_a & creators_b)

                # Only include if shared_creators >= 2
                if shared < 2:
                    continue

                # Compute overlap ratio
                min_count = min(count_a, count_b)
                overlap_ratio = shared / min_count if min_count > 0 else 0

                overlaps[(funder_a, funder_b)] = {
                    'shared_creators': shared,
                    'funder_a_creators': count_a,
                    'funder_b_creators': count_b,
                    'overlap_ratio': overlap_ratio,
                    'coordination_level': self._classify_coordination_level(overlap_ratio, shared)
                }

        return overlaps

    def store_overlaps(self, overlaps: Dict[Tuple[str, str], Dict]) -> int:
        """
        Store computed overlaps in database.

        Returns: Number of overlaps stored
        """
        if not overlaps:
            logger.warning("No overlaps to store")
            return 0

        self._ensure_tables()

        conn = self._get_conn()
        cursor = conn.cursor()
        now = int(time.time())

        stored = 0
        for (funder_a, funder_b), metrics in overlaps.items():
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO funder_overlap
                    (funder_a, funder_b, shared_creators, overlap_ratio,
                     funder_a_creators, funder_b_creators, coordination_level, detected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    funder_a,
                    funder_b,
                    metrics['shared_creators'],
                    metrics['overlap_ratio'],
                    metrics['funder_a_creators'],
                    metrics['funder_b_creators'],
                    metrics['coordination_level'],
                    now
                ))
                stored += 1
            except Exception as e:
                logger.error(f"Error storing overlap for {funder_a}-{funder_b}: {e}")
                continue

        conn.commit()
        conn.close()

        return stored

    def analyze_and_store(self) -> Dict:
        """
        Main orchestrator: compute overlaps and store results.

        Returns:
        {
            'status': 'success'|'error',
            'message': str,
            'overlaps_found': int,
            'overlaps_stored': int,
            'high_coordination_count': int,
            'very_strong_count': int,
            'duration_ms': float
        }
        """
        try:
            logger.info("Starting funder overlap analysis")

            # Compute overlaps
            overlaps = self.compute_funder_overlaps()

            if not overlaps:
                logger.info("No funder overlaps found (< 2 shared creators)")
                return {
                    'status': 'success',
                    'message': 'No funder overlaps found',
                    'overlaps_found': 0,
                    'overlaps_stored': 0,
                    'high_coordination_count': 0,
                    'very_strong_count': 0,
                    'duration_ms': int((time.time() - self.start_time) * 1000)
                }

            # Count by coordination level
            high_coordination = sum(
                1 for m in overlaps.values()
                if m['coordination_level'] in ['high', 'very_strong']
            )
            very_strong = sum(
                1 for m in overlaps.values()
                if m['coordination_level'] == 'very_strong'
            )

            # Store overlaps
            stored = self.store_overlaps(overlaps)

            logger.info(
                f"Funder overlap analysis complete: {stored} overlaps stored, "
                f"{high_coordination} high coordination, {very_strong} very strong"
            )

            return {
                'status': 'success',
                'message': f'Analyzed {len(overlaps)} funder wallet pairs',
                'overlaps_found': len(overlaps),
                'overlaps_stored': stored,
                'high_coordination_count': high_coordination,
                'very_strong_count': very_strong,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }

        except Exception as e:
            logger.error(f"Funder overlap analysis failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'overlaps_found': 0,
                'overlaps_stored': 0,
                'high_coordination_count': 0,
                'very_strong_count': 0,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }


class FunderOverlapScorer:
    """
    Produces organization-level and system-level funder overlap scores.

    Aggregates wallet-pair overlaps to measure:
    - Organization funder coordination
    - System-wide dev farm activity
    - Launch preparation signals
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def get_wallet_overlap_score(self, wallet: str, cursor: sqlite3.Cursor) -> float:
        """
        Get funder overlap score for a wallet (0-100).

        Combines:
        - Max overlap_ratio with other wallets
        - Count of high-coordination partners
        - Severity of overlaps
        """
        cursor.execute("""
            SELECT
                COUNT(*) as partner_count,
                AVG(overlap_ratio) as avg_overlap,
                MAX(overlap_ratio) as max_overlap,
                SUM(CASE WHEN coordination_level IN ('high', 'very_strong') THEN 1 ELSE 0 END) as high_partners
            FROM funder_overlap
            WHERE funder_a = ? OR funder_b = ?
        """, (wallet, wallet))

        result = cursor.fetchone()
        if not result or result['partner_count'] == 0:
            return 0

        partner_count = result['partner_count'] or 0
        avg_overlap = result['avg_overlap'] or 0
        max_overlap = result['max_overlap'] or 0
        high_partners = result['high_partners'] or 0

        # Score combines max overlap, average overlap, and partner count
        max_overlap_score = max_overlap * 100
        avg_overlap_score = avg_overlap * 100
        partner_score = min(100, (high_partners / max(partner_count, 1)) * 100)

        composite_score = (max_overlap_score * 0.4 +
                          avg_overlap_score * 0.3 +
                          partner_score * 0.3)

        return min(100, composite_score)

    def get_organization_funder_overlap(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Get organization-level funder overlap metrics.

        Returns:
        {
            'org_id': int,
            'org_funders': int,
            'high_coordination_pairs': int,
            'very_strong_pairs': int,
            'avg_overlap_ratio': float,
            'max_overlap_ratio': float,
            'funder_overlap_signal': float (0-100)
        }
        """
        # Get all funders for this organization
        cursor.execute("""
            SELECT DISTINCT wallet
            FROM (
                SELECT source as wallet FROM transfer_index
                WHERE destination IN (
                    SELECT creator_wallet FROM dev_organization_members
                    WHERE organization_id = ?
                )
                UNION
                SELECT source as wallet FROM transfer_index
                WHERE destination IN (
                    SELECT creator_wallet FROM dev_organization_members
                    WHERE organization_id = ?
                )
            )
        """, (org_id, org_id))

        org_wallets = [row[0] for row in cursor.fetchall()]

        if len(org_wallets) < 2:
            return {
                'org_id': org_id,
                'org_funders': len(org_wallets),
                'high_coordination_pairs': 0,
                'very_strong_pairs': 0,
                'avg_overlap_ratio': 0,
                'max_overlap_ratio': 0,
                'funder_overlap_signal': 0
            }

        # Find overlaps between org wallets
        cursor.execute("""
            SELECT
                overlap_ratio,
                coordination_level
            FROM funder_overlap
            WHERE (funder_a IN ({}) AND funder_b IN ({}))
               OR (funder_b IN ({}) AND funder_a IN ({}))
        """.format(
            ','.join(['?' for _ in org_wallets]),
            ','.join(['?' for _ in org_wallets]),
            ','.join(['?' for _ in org_wallets]),
            ','.join(['?' for _ in org_wallets])
        ), org_wallets * 4)

        results = cursor.fetchall()

        if not results:
            return {
                'org_id': org_id,
                'org_funders': len(org_wallets),
                'high_coordination_pairs': 0,
                'very_strong_pairs': 0,
                'avg_overlap_ratio': 0,
                'max_overlap_ratio': 0,
                'funder_overlap_signal': 0
            }

        overlaps = [row['overlap_ratio'] for row in results]
        levels = [row['coordination_level'] for row in results]

        high_count = sum(1 for level in levels if level in ['high', 'very_strong'])
        very_strong_count = sum(1 for level in levels if level == 'very_strong')

        avg_overlap = sum(overlaps) / len(overlaps)
        max_overlap = max(overlaps)

        # Signal: 0-100 based on coordination
        signal = max_overlap * 100 if max_overlap > 0 else 0

        return {
            'org_id': org_id,
            'org_funders': len(org_wallets),
            'high_coordination_pairs': high_count,
            'very_strong_pairs': very_strong_count,
            'avg_overlap_ratio': avg_overlap,
            'max_overlap_ratio': max_overlap,
            'funder_overlap_signal': signal
        }
