"""
FLEX Phase 3.3 Dev Farm Detection + Developer Reputation

This module provides wallet clustering for dev farm detection and per-developer
reputation scoring from rug history + token success metrics.

Architecture:
- Dev farm detection via multi-creator funding patterns on transfer_index
- Confidence scoring (0-100) based on creator count, consistency, duration, activity
- Developer reputation merging rug_count + token success metrics
- Burst detection for synchronized funding within 1-hour windows
- Wallet age computed from first block_time in transfer_index
- Incremental processing via row-cursor to skip already-processed wallets
"""

import os
import sqlite3
import time
import logging
import json
import statistics
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from src.utils.infra_mapping import build_excluded_set, sync_infra_wallets

logger = logging.getLogger(__name__)

ENGINE_NAME = "WalletClusteringEngine"


# ---------------------------------------------------------------------------
# Union-Find (DSU) with path compression + union by rank
# ---------------------------------------------------------------------------

class UnionFind:
    """Disjoint Set Union with path compression and union by rank."""

    def __init__(self):
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])  # path compression
        return self.parent[x]

    def union(self, x: str, y: str) -> bool:
        """Union two sets. Returns True if they were in different sets."""
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        # union by rank
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        return True

    def same(self, x: str, y: str) -> bool:
        return self.find(x) == self.find(y)

    def roots(self) -> Dict[str, List[str]]:
        """Return mapping root → [members]."""
        groups: Dict[str, List[str]] = {}
        for x in self.parent:
            r = self.find(x)
            groups.setdefault(r, []).append(x)
        return groups


# ---------------------------------------------------------------------------
# WalletClusteringEngine
# ---------------------------------------------------------------------------

class WalletClusteringEngine:
    """
    Dev farm detection and developer reputation scoring.

    Implements:
    1. Detection of dev farm wallets (3+ creators, 0.5-5 SOL transfers)
    2. Confidence scoring based on transfer patterns
    3. Burst detection for synchronized funding
    4. Developer reputation from rug history + token success
    5. Incremental processing via clustering_cursor table
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.min_creators = 3
        self.min_transfer_sol = 0.5
        self.max_transfer_sol = 10.0
        self.min_days_active = 2

    def _get_conn(self) -> sqlite3.Connection:
        """Get optimized SQLite connection for clustering operations."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=30000000")  # 30MB memory map
        conn.execute("PRAGMA query_only=FALSE")
        return conn

    def _ensure_tables(self) -> None:
        """Create wallet_clusters, dev_reputation, and auxiliary tables if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # wallet_clusters table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallet_clusters (
                cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                funder_wallet       TEXT NOT NULL UNIQUE,
                creator_addresses   TEXT NOT NULL,
                creator_count       INTEGER NOT NULL,
                confidence_score    REAL DEFAULT 0,
                avg_transfer_sol    REAL DEFAULT 0,
                transfer_stddev     REAL DEFAULT 0,
                days_active         INTEGER DEFAULT 0,
                first_transfer_ts   INTEGER,
                last_transfer_ts    INTEGER,
                has_burst           BOOLEAN DEFAULT 0,
                wallet_age_days     REAL DEFAULT 0,
                detected_at         REAL NOT NULL,
                updated_at          REAL NOT NULL,
                first_seen_at       INTEGER,
                last_updated_at     INTEGER
            )
        """)

        # Add new columns to existing table if they don't exist
        for col, col_type in [("first_seen_at", "INTEGER"), ("last_updated_at", "INTEGER")]:
            try:
                cursor.execute(f"ALTER TABLE wallet_clusters ADD COLUMN {col} {col_type}")
                logger.info(f"[CLUSTERING] Added column wallet_clusters.{col}")
            except sqlite3.OperationalError:
                pass  # column already exists

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_clusters_confidence
            ON wallet_clusters(confidence_score DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_clusters_funder
            ON wallet_clusters(funder_wallet)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_clusters_detected
            ON wallet_clusters(detected_at DESC)
        """)

        # dev_reputation table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dev_reputation (
                wallet              TEXT PRIMARY KEY,
                tokens_launched     INTEGER DEFAULT 0,
                tokens_rugged       INTEGER DEFAULT 0,
                tokens_above_2x     INTEGER DEFAULT 0,
                tokens_above_10x    INTEGER DEFAULT 0,
                rug_rate            REAL DEFAULT 0,
                success_rate        REAL DEFAULT 0,
                reputation_score    REAL DEFAULT 50,
                first_seen_ts       INTEGER,
                wallet_age_days     REAL DEFAULT 0,
                cluster_id          INTEGER,
                last_updated        REAL NOT NULL,
                FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dev_reputation_score
            ON dev_reputation(reputation_score ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dev_reputation_rug
            ON dev_reputation(rug_rate DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dev_reputation_cluster
            ON dev_reputation(cluster_id)
        """)

        # cluster_detection_log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cluster_detection_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at         REAL NOT NULL,
                clusters_found      INTEGER DEFAULT 0,
                reputations_updated INTEGER DEFAULT 0,
                duration_ms         REAL DEFAULT 0,
                status              TEXT DEFAULT 'success',
                error_message       TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_detection_log_time
            ON cluster_detection_log(detected_at DESC)
        """)

        # clustering_cursor table — tracks incremental progress
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clustering_cursor (
                engine_name         TEXT PRIMARY KEY,
                last_processed_rowid INTEGER NOT NULL DEFAULT 0,
                last_run_at         TEXT,
                wallets_processed   INTEGER DEFAULT 0,
                clusters_created    INTEGER DEFAULT 0,
                clusters_merged     INTEGER DEFAULT 0
            )
        """)

        # cluster_merge_log — audit trail for DSU merges
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cluster_merge_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                winning_id      TEXT NOT NULL,
                losing_id       TEXT NOT NULL,
                trigger_wallet  TEXT NOT NULL,
                merged_at       INTEGER NOT NULL,
                wallets_absorbed INTEGER NOT NULL
            )
        """)

        # clustering_lock — prevent concurrent runs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clustering_lock (
                engine_name TEXT PRIMARY KEY,
                locked_at   TEXT,
                pid         INTEGER
            )
        """)

        sync_infra_wallets(conn)
        removed = cursor.execute("""
            DELETE FROM wallet_clusters
            WHERE funder_wallet IN (SELECT address FROM infra_wallets)
        """).rowcount
        if removed:
            logger.info(f"[CLUSTERING] Removed {removed} infra/CEX wallet_clusters")

        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Concurrency guard
    # ------------------------------------------------------------------

    def _acquire_lock(self, conn: sqlite3.Connection) -> bool:
        """Try to acquire clustering lock. Returns True if acquired."""
        try:
            conn.execute(
                "INSERT OR FAIL INTO clustering_lock (engine_name, locked_at, pid) VALUES (?, ?, ?)",
                (ENGINE_NAME, datetime.utcnow().isoformat(), os.getpid())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Lock held — check if stale (>30 min)
            row = conn.execute(
                "SELECT locked_at, pid FROM clustering_lock WHERE engine_name = ?",
                (ENGINE_NAME,)
            ).fetchone()
            if row:
                try:
                    locked_dt = datetime.fromisoformat(row[0])
                    age_min = (datetime.utcnow() - locked_dt).total_seconds() / 60
                    if age_min > 30:
                        logger.warning(f"[CLUSTERING] Stale lock (age={age_min:.1f}m, pid={row[1]}), breaking it")
                        conn.execute("DELETE FROM clustering_lock WHERE engine_name = ?", (ENGINE_NAME,))
                        conn.execute(
                            "INSERT INTO clustering_lock (engine_name, locked_at, pid) VALUES (?, ?, ?)",
                            (ENGINE_NAME, datetime.utcnow().isoformat(), os.getpid())
                        )
                        conn.commit()
                        return True
                except Exception:
                    pass
            return False

    def _release_lock(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("DELETE FROM clustering_lock WHERE engine_name = ?", (ENGINE_NAME,))
            conn.commit()
        except Exception as e:
            logger.error(f"[CLUSTERING] Failed to release lock: {e}")

    # ------------------------------------------------------------------
    # Cursor helpers
    # ------------------------------------------------------------------

    def _read_cursor(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT last_processed_rowid FROM clustering_cursor WHERE engine_name = ?",
            (ENGINE_NAME,)
        ).fetchone()
        return row[0] if row else 0

    def _advance_cursor(self, conn: sqlite3.Connection, new_rowid: int,
                        wallets_processed: int, clusters_created: int, clusters_merged: int) -> None:
        conn.execute("""
            INSERT INTO clustering_cursor (engine_name, last_processed_rowid, last_run_at,
                wallets_processed, clusters_created, clusters_merged)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(engine_name) DO UPDATE SET
                last_processed_rowid = excluded.last_processed_rowid,
                last_run_at          = excluded.last_run_at,
                wallets_processed    = clustering_cursor.wallets_processed + excluded.wallets_processed,
                clusters_created     = clustering_cursor.clusters_created  + excluded.clusters_created,
                clusters_merged      = clustering_cursor.clusters_merged   + excluded.clusters_merged
        """, (ENGINE_NAME, new_rowid, datetime.utcnow().isoformat(),
              wallets_processed, clusters_created, clusters_merged))

    def _touch_cursor_time(self, conn: sqlite3.Connection) -> None:
        """Update last_run_at without changing rowid (used on no-op runs)."""
        conn.execute("""
            INSERT INTO clustering_cursor (engine_name, last_processed_rowid, last_run_at)
            VALUES (?, 0, ?)
            ON CONFLICT(engine_name) DO UPDATE SET last_run_at = excluded.last_run_at
        """, (ENGINE_NAME, datetime.utcnow().isoformat()))

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def detect_and_store(self, force_full_rebuild: bool = False) -> Dict:
        """
        Main entry point: detect dev farms and update reputation scores.

        Args:
            force_full_rebuild: If True, ignore cursor and reprocess everything.

        Returns:
            {
                'clusters_found': int,
                'reputations_updated': int,
                'status': 'success' | 'error',
                'duration_ms': float,
                'message': str,
                'incremental': bool
            }
        """
        detect_start = time.time()
        result = {
            'clusters_found': 0,
            'reputations_updated': 0,
            'status': 'pending',
            'duration_ms': 0.0,
            'message': '',
            'incremental': not force_full_rebuild,
        }

        lock_conn = None
        try:
            self._ensure_tables()

            # Acquire lock
            lock_conn = self._get_conn()
            if not self._acquire_lock(lock_conn):
                result['status'] = 'error'
                result['message'] = 'Another clustering run is in progress'
                return result

            if force_full_rebuild:
                stored, updated = self._full_rebuild()
            else:
                stored, updated = self._incremental_run()

            result['clusters_found'] = stored
            result['reputations_updated'] = updated
            result['status'] = 'success'
            result['message'] = (
                f"{'Incremental' if result['incremental'] else 'Full'}: "
                f"detected {stored} clusters, updated {updated} reputations"
            )
            logger.info(f"[CLUSTERING] {result['message']}")

        except Exception as e:
            result['status'] = 'error'
            result['message'] = f"Clustering failed: {str(e)}"
            logger.error(f"[CLUSTERING] {result['message']}", exc_info=True)

        finally:
            result['duration_ms'] = (time.time() - detect_start) * 1000
            self._log_run(result)
            if lock_conn:
                self._release_lock(lock_conn)
                lock_conn.close()

        return result

    # ------------------------------------------------------------------
    # Full rebuild (original behaviour, triggered by force_full_rebuild=True)
    # ------------------------------------------------------------------

    def _full_rebuild(self) -> Tuple[int, int]:
        """Full scan of transfer_index — original algorithm, unmodified."""
        farms = self._detect_dev_farms()
        logger.info(f"[CLUSTERING] Full rebuild: found {len(farms)} potential dev farms")

        scored_farms = []
        for farm in farms:
            farm['confidence_score'] = self._score_cluster(farm)
            farm['has_burst'] = self._detect_bursts(farm['funder_wallet'])
            scored_farms.append(farm)

        stored = self._store_clusters(scored_farms)

        # Reset cursor so incremental knows we're fresh
        conn = self._get_conn()
        try:
            max_rowid = conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM wallet_clusters"
            ).fetchone()[0]
            self._advance_cursor(conn, max_rowid, len(farms), stored, 0)
            conn.commit()
        finally:
            conn.close()

        return stored, 0

    # ------------------------------------------------------------------
    # Incremental run
    # ------------------------------------------------------------------

    def _incremental_run(self) -> Tuple[int, int]:
        """
        Process only transfer_index rows not yet clustered.

        Strategy:
        - Use clustering_cursor.last_processed_rowid as the watermark into
          transfer_index (the append-only source of truth).
        - Derive new funder_wallets from new rows.
        - Skip funders that already have a wallet_clusters entry (idempotent).
        - Score and store only genuinely new clusters.
        - Then rebuild dev_reputation from the full wallet_clusters table
          (fast — small table).
        """
        conn = self._get_conn()
        cursor_val = self._read_cursor(conn)
        conn.close()

        logger.info(f"[CLUSTERING] Incremental run from transfer_index rowid > {cursor_val}")

        # Find highest rowid in transfer_index to mark as new cursor
        scan_conn = self._get_conn()
        try:
            max_rowid_row = scan_conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM transfer_index"
            ).fetchone()
            max_rowid = max_rowid_row[0] if max_rowid_row else 0

            if max_rowid <= cursor_val:
                logger.info("[CLUSTERING] No new transfer_index rows — skipping")
                self._touch_cursor_time(scan_conn)
                scan_conn.commit()
                return 0, 0

            # Fetch new unique funder wallets from new rows
            new_funders_rows = scan_conn.execute("""
                SELECT DISTINCT source
                FROM transfer_index
                WHERE rowid > ?
                  AND amount_sol BETWEEN ? AND ?
                  AND is_valid = 1
            """, (cursor_val, self.min_transfer_sol, self.max_transfer_sol)).fetchall()
        finally:
            scan_conn.close()

        new_funders = {r[0] for r in new_funders_rows}
        logger.info(f"[CLUSTERING] {len(new_funders)} distinct new funder wallets to check")

        if not new_funders:
            conn = self._get_conn()
            self._advance_cursor(conn, max_rowid, 0, 0, 0)
            conn.commit()
            conn.close()
            return 0, 0

        # Filter out wallets already fully clustered (idempotency)
        placeholders = ",".join("?" * len(new_funders))
        check_conn = self._get_conn()
        try:
            existing = {r[0] for r in check_conn.execute(
                f"SELECT funder_wallet FROM wallet_clusters WHERE funder_wallet IN ({placeholders})",
                list(new_funders)
            ).fetchall()}
        finally:
            check_conn.close()

        to_process = new_funders - existing
        logger.info(f"[CLUSTERING] {len(to_process)} wallets need fresh cluster evaluation (excluding {len(existing)} already stored)")

        if not to_process:
            conn = self._get_conn()
            self._advance_cursor(conn, max_rowid, len(new_funders), 0, 0)
            conn.commit()
            conn.close()
            return 0, 0

        # Build exclusion set once
        excl_conn = self._get_conn()
        excluded = build_excluded_set(excl_conn)
        excl_conn.close()
        to_process -= excluded

        # Evaluate each candidate funder against clustering criteria
        farms = self._evaluate_funders(list(to_process))
        logger.info(f"[CLUSTERING] {len(farms)} new farms qualify after criteria check")

        stored = 0
        if farms:
            scored = []
            for farm in farms:
                farm['confidence_score'] = self._score_cluster(farm)
                farm['has_burst'] = self._detect_bursts(farm['funder_wallet'])
                scored.append(farm)
            stored = self._store_clusters(scored)

        # Advance cursor — only after successful storage
        adv_conn = self._get_conn()
        try:
            self._advance_cursor(adv_conn, max_rowid, len(to_process), stored, 0)
            adv_conn.commit()
        finally:
            adv_conn.close()

        return stored, 0

    def _evaluate_funders(self, funders: List[str]) -> List[Dict]:
        """
        Given a list of funder wallets, run the clustering criteria query
        limited to just those wallets. Returns qualifying farm dicts.
        """
        if not funders:
            return []

        conn = self._get_conn()
        cursor = conn.cursor()

        placeholders = ",".join("?" * len(funders))
        query = f"""
            SELECT
                source AS funder,
                COUNT(DISTINCT destination) AS creators,
                COUNT(*) AS transfers,
                ROUND(AVG(amount_sol), 3) AS avg_amount,
                COUNT(DISTINCT DATE(datetime(block_time, 'unixepoch'))) AS days_active,
                (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days,
                MIN(block_time) AS first_ts,
                MAX(block_time) AS last_ts,
                GROUP_CONCAT(DISTINCT destination) AS creator_list,
                GROUP_CONCAT(amount_sol) AS amounts
            FROM transfer_index
            WHERE source IN ({placeholders})
              AND amount_sol BETWEEN ? AND ?
              AND is_valid = 1
            GROUP BY source
            HAVING creators >= ?
              AND days_active >= ?
            ORDER BY creators DESC
        """

        cursor.execute(query, funders + [self.min_transfer_sol, self.max_transfer_sol,
                                         self.min_creators, self.min_days_active])
        rows = cursor.fetchall()
        conn.close()

        farms = []
        for row in rows:
            funder, creators, transfers, avg_amt, days_active, span_days, first_ts, last_ts, creator_list, amounts_str = row

            stddev_amt = 0.0
            if amounts_str:
                try:
                    amounts = [float(x) for x in amounts_str.split(',')]
                    if len(amounts) > 1:
                        mean = sum(amounts) / len(amounts)
                        variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
                        stddev_amt = variance ** 0.5
                except Exception:
                    stddev_amt = 0.0

            wallet_age = self._compute_wallet_age(funder)

            farms.append({
                'funder_wallet': funder,
                'creator_addresses': creator_list.split(',') if creator_list else [],
                'creator_count': creators,
                'transfers': transfers,
                'avg_transfer_sol': avg_amt or 0.0,
                'transfer_stddev': round(stddev_amt, 3),
                'days_active': days_active,
                'span_days': span_days,
                'first_transfer_ts': int(first_ts) if first_ts else None,
                'last_transfer_ts': int(last_ts) if last_ts else None,
                'wallet_age_days': wallet_age,
            })

        return farms

    # ------------------------------------------------------------------
    # Original detection methods (unchanged — used by full rebuild)
    # ------------------------------------------------------------------

    def _detect_dev_farms(self) -> List[Dict]:
        """
        Detect wallets funding 3+ creators with 0.5-10 SOL amounts.

        Returns:
            List of farm dicts with funder_wallet, creator_addresses, transfers, etc.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Build unified exclusion set: static registry (CEX + infra) + live cex_wallets table
        excluded = build_excluded_set(conn)

        # Main detection query (note: no STDDEV function in SQLite, compute in Python)
        query = """
            SELECT
                source AS funder,
                COUNT(DISTINCT destination) AS creators,
                COUNT(*) AS transfers,
                ROUND(AVG(amount_sol), 3) AS avg_amount,
                COUNT(DISTINCT DATE(datetime(block_time, 'unixepoch'))) AS days_active,
                (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days,
                MIN(block_time) AS first_ts,
                MAX(block_time) AS last_ts,
                GROUP_CONCAT(DISTINCT destination) AS creator_list,
                GROUP_CONCAT(amount_sol) AS amounts
            FROM transfer_index
            WHERE amount_sol BETWEEN ? AND ?
              AND is_valid = 1
            GROUP BY source
            HAVING creators >= ?
              AND days_active >= ?
            ORDER BY creators DESC
        """

        cursor.execute(
            query,
            (self.min_transfer_sol, self.max_transfer_sol, self.min_creators, self.min_days_active)
        )

        rows = cursor.fetchall()
        conn.close()

        farms = []
        skipped_excluded = 0
        for row in rows:
            funder, creators, transfers, avg_amt, days_active, span_days, first_ts, last_ts, creator_list, amounts_str = row

            # Skip CEX / infra wallets using unified exclusion set
            if funder in excluded:
                skipped_excluded += 1
                continue

            # Compute stddev in Python
            stddev_amt = 0.0
            if amounts_str:
                try:
                    amounts = [float(x) for x in amounts_str.split(',')]
                    if len(amounts) > 1:
                        mean = sum(amounts) / len(amounts)
                        variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
                        stddev_amt = variance ** 0.5
                except Exception:
                    stddev_amt = 0.0

            wallet_age = self._compute_wallet_age(funder)

            farms.append({
                'funder_wallet': funder,
                'creator_addresses': creator_list.split(',') if creator_list else [],
                'creator_count': creators,
                'transfers': transfers,
                'avg_transfer_sol': avg_amt or 0.0,
                'transfer_stddev': round(stddev_amt, 3),
                'days_active': days_active,
                'span_days': span_days,
                'first_transfer_ts': int(first_ts) if first_ts else None,
                'last_transfer_ts': int(last_ts) if last_ts else None,
                'wallet_age_days': wallet_age
            })

        logger.info(f"[CLUSTERING_DETECT] Found {len(farms)} farms (excluded {skipped_excluded} CEX/infra wallets)")
        return farms

    def _detect_bursts(self, funder_wallet: str) -> bool:
        """
        Check if wallet funded 2+ creators in the same 1-hour window.

        Args:
            funder_wallet: The wallet to check

        Returns:
            True if burst detected, False otherwise
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Group by 1-hour window and check for 2+ creators
        cursor.execute("""
            SELECT COUNT(DISTINCT destination) as creators_in_hour
            FROM transfer_index
            WHERE source = ?
              AND is_valid = 1
            GROUP BY (block_time / 3600) * 3600
            HAVING creators_in_hour >= 2
            LIMIT 1
        """, (funder_wallet,))

        result = cursor.fetchone()
        conn.close()

        return result is not None

    def _compute_wallet_age(self, wallet: str) -> float:
        """
        Compute wallet age in days from first transfer in transfer_index.

        Args:
            wallet: Wallet address

        Returns:
            Age in days (0 if never transferred)
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT MIN(block_time) FROM transfer_index WHERE source = ?",
            (wallet,)
        )
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            return (time.time() - row[0]) / 86400.0

        return 0.0

    def _compute_wallet_age_via_hot_cold(self, reader, wallet: str) -> float:
        """STORAGE-LIFECYCLE-P5B-R2 Part 5 adapter (NOT YET WIRED INTO
        _compute_wallet_age() ABOVE OR ITS CALLERS -- that method remains
        unmodified production code, still querying self._get_conn()'s
        single transfer_index table directly).

        Reproduces _compute_wallet_age()'s MIN(block_time) WHERE source=?
        semantics via the HOT+COLD UnifiedTransferReader
        (src.ops.cold_segment_registry.get_transfer_reader), so a wallet
        whose true earliest transfer has aged into a COLD segment is not
        under-reported as younger than it really is -- a wallet-age signal
        that silently truncated to only the HOT window would be a
        correctness regression for exactly the dev-farm-detection use case
        this method feeds. `reader` is a UnifiedTransferReader.
        """
        rows = reader.by_source(wallet, limit=1_000_000)
        block_times = [r[4] for r in rows if r[4] is not None]
        if not block_times:
            return 0.0
        earliest = min(block_times)
        return (time.time() - earliest) / 86400.0

    def _score_cluster(self, farm: Dict) -> float:
        """
        Compute confidence score (0-100) from farm characteristics.

        Components:
        - Creators (0-25): >=10→25, >=5→18, >=3→10
        - Consistency (0-25): stddev<1→25, <2→18, <3→10
        - Duration (0-25): span>=7d→25, >=3d→18, >=1d→10
        - Activity (0-25): transfers>=20→25, >=10→18, >=5→10

        Args:
            farm: Farm dict with creator_count, transfer_stddev, span_days, transfers

        Returns:
            Confidence score 0-100
        """
        score = 0.0

        # Creator count (0-25)
        creators = farm['creator_count']
        if creators >= 10:
            score += 25
        elif creators >= 5:
            score += 18
        elif creators >= 3:
            score += 10

        # Consistency / low stddev (0-25)
        stddev = farm['transfer_stddev']
        if stddev == 0 or stddev < 1:
            score += 25
        elif stddev < 2:
            score += 18
        elif stddev < 3:
            score += 10

        # Duration / active span (0-25)
        span_days = farm['span_days']
        if span_days >= 7:
            score += 25
        elif span_days >= 3:
            score += 18
        elif span_days >= 1:
            score += 10

        # Activity / transfer count (0-25)
        transfers = farm['transfers']
        if transfers >= 20:
            score += 25
        elif transfers >= 10:
            score += 18
        elif transfers >= 5:
            score += 10

        return min(100.0, max(0.0, score))

    def _store_clusters(self, farms: List[Dict]) -> int:
        """
        Store or update wallet clusters in database.

        Args:
            farms: List of scored farm dicts

        Returns:
            Number of clusters stored/updated
        """
        if not farms:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        now_int = int(now)

        inserted = 0
        for farm in farms:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO wallet_clusters (
                        funder_wallet,
                        creator_addresses,
                        creator_count,
                        confidence_score,
                        avg_transfer_sol,
                        transfer_stddev,
                        days_active,
                        first_transfer_ts,
                        last_transfer_ts,
                        has_burst,
                        wallet_age_days,
                        detected_at,
                        updated_at,
                        first_seen_at,
                        last_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    farm['funder_wallet'],
                    json.dumps(farm['creator_addresses']),
                    farm['creator_count'],
                    farm['confidence_score'],
                    farm['avg_transfer_sol'],
                    farm['transfer_stddev'],
                    farm['days_active'],
                    farm['first_transfer_ts'],
                    farm['last_transfer_ts'],
                    1 if farm.get('has_burst') else 0,
                    farm['wallet_age_days'],
                    now,
                    now,
                    farm.get('first_transfer_ts') or now_int,
                    now_int,
                ))
                inserted += 1
            except Exception as e:
                logger.error(f"[CLUSTERING_STORE] Error storing {farm['funder_wallet']}: {e}")

        conn.commit()
        conn.close()

        logger.info(f"[CLUSTERING_STORE] Stored {inserted} clusters")
        return inserted

    def _update_dev_reputation(self) -> int:
        """
        Update dev_reputation table merging rug history + token success.

        Bulk SQL approach — computes all scores in one query, writes in batches of 500
        so the write lock is held only briefly at a time rather than for the full duration.

        Returns:
            Number of reputation records updated
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        # Check optional tables once
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='creator_blocklist'")
        has_blocklist = bool(cursor.fetchone())

        # Build all reputation data in a single bulk read query (no write lock needed)
        blocklist_join = """
            LEFT JOIN creator_blocklist bl ON bl.wallet = c.creator
        """ if has_blocklist else ""
        blocklist_col = "COALESCE(bl.rug_count, 0)" if has_blocklist else "0"

        cursor.execute(f"""
            SELECT
                c.creator,
                COALESCE(ta.tokens_launched, 0) AS tokens_launched,
                {blocklist_col}               AS tokens_rugged,
                COALESCE(ta.above_2x, 0)       AS tokens_above_2x,
                COALESCE(ta.above_10x, 0)      AS tokens_above_10x,
                wc.cluster_id,
                COALESCE(ti.first_seen, NULL)  AS first_seen_ts
            FROM (
                SELECT DISTINCT json_each.value AS creator
                FROM wallet_clusters, json_each(wallet_clusters.creator_addresses)
            ) c
            LEFT JOIN (
                SELECT earliest_tx_creator AS creator,
                       COUNT(*) AS tokens_launched,
                       SUM(CASE WHEN price_highest >= price_current * 2 THEN 1 ELSE 0 END) AS above_2x,
                       SUM(CASE WHEN market_cap_highest >= 1000000 THEN 1 ELSE 0 END) AS above_10x
                FROM token_analysis
                GROUP BY earliest_tx_creator
            ) ta ON ta.creator = c.creator
            LEFT JOIN (
                SELECT cluster_id,
                       json_each.value AS creator
                FROM wallet_clusters, json_each(wallet_clusters.creator_addresses)
            ) wc ON wc.creator = c.creator
            LEFT JOIN (
                SELECT destination AS creator, MIN(block_time) AS first_seen
                FROM transfer_index
                GROUP BY destination
            ) ti ON ti.creator = c.creator
            {blocklist_join}
        """)
        rows = cursor.fetchall()
        conn.close()

        logger.info(f"[CLUSTERING_REPUTATION] Processing {len(rows)} creators (bulk mode)")

        # Compute scores in Python, write in batches to keep write-lock windows short
        BATCH = 500
        updated = 0
        write_conn = self._get_conn()
        write_cur = write_conn.cursor()

        for i, row in enumerate(rows):
            creator, tokens_launched, rug_count, above_2x, above_10x, cluster_id, first_seen_ts = row
            tokens_launched = tokens_launched or 0
            rug_count = rug_count or 0
            above_2x = above_2x or 0
            above_10x = above_10x or 0

            rug_rate = (rug_count / tokens_launched) if tokens_launched > 0 else 0.0
            success_rate = (above_2x / tokens_launched) if tokens_launched > 0 else 0.0

            reputation_score = 50.0
            reputation_score += success_rate * 30.0
            reputation_score -= rug_rate * 50.0
            if cluster_id:
                reputation_score -= 10.0

            wallet_age = self._compute_wallet_age(creator)
            if wallet_age > 90:
                reputation_score += 10.0
            reputation_score = max(0.0, min(100.0, reputation_score))

            write_cur.execute("""
                INSERT OR REPLACE INTO dev_reputation (
                    wallet, tokens_launched, tokens_rugged, tokens_above_2x, tokens_above_10x,
                    rug_rate, success_rate, reputation_score, first_seen_ts,
                    wallet_age_days, cluster_id, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (creator, tokens_launched, rug_count, above_2x, above_10x,
                  rug_rate, success_rate, reputation_score, first_seen_ts,
                  wallet_age, cluster_id, now))
            updated += 1

            # Commit every BATCH rows to release write lock briefly
            if updated % BATCH == 0:
                write_conn.commit()

        write_conn.commit()
        write_conn.close()

        logger.info(f"[CLUSTERING_REPUTATION] Updated {updated} reputation records")
        return updated

    def _log_run(self, result: Dict) -> None:
        """Log detection run to cluster_detection_log."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO cluster_detection_log (
                    detected_at,
                    clusters_found,
                    reputations_updated,
                    duration_ms,
                    status,
                    error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                time.time(),
                result['clusters_found'],
                result['reputations_updated'],
                result['duration_ms'],
                result['status'],
                result.get('message', '')
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[CLUSTERING_LOG] Failed to log run: {e}")


class DevReputationUpdater:
    """
    Standalone updater for dev_reputation — reads creator_blocklist, token_analysis,
    and wallet_clusters. Decoupled from WalletClusteringEngine so it always runs
    every analyzer cycle regardless of whether new transfer_index rows arrived.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def run(self) -> dict:
        started_at = time.time()
        try:
            engine = WalletClusteringEngine(self.db_path)
            updated = engine._update_dev_reputation()
            return {
                'status': 'success',
                'reputations_updated': updated,
                'duration_seconds': round(time.time() - started_at, 2),
            }
        except Exception as e:
            logger.error(f"[DEV_REPUTATION] Failed: {e}", exc_info=True)
            return {
                'status': 'failed',
                'reputations_updated': 0,
                'duration_seconds': round(time.time() - started_at, 2),
                'error': str(e),
            }
