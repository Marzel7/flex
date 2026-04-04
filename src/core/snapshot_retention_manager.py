import sqlite3
import time
import logging
from src.core.ws_snapshot_logger import ws_log

logger = logging.getLogger(__name__)


class SnapshotRetentionManager:
    """
    Handles snapshot cleanup:
    - Downsamples old snapshots
    - Deletes stale tokens
    - Keeps token_snapshot_counts in sync
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

        # CONFIG (tune these)
        self.MIN_KEEP_SNAPSHOTS = 25
        self.FULL_HISTORY_WINDOW_SECS = 1800       # 30 mins keep all
        self.DELETE_AFTER_INACTIVE_SECS = 7200     # 2 hours
        self.DOWNSAMPLE_INTERVAL_SECS = 60         # keep 1 per minute for older data
        self.REMOVE_STALE_LOW_SNAP_TOKENS = True   # remove low-snap tokens from /snapshots after inactivity

    LIFECYCLE_DELETE_AFTER = 43200  # 12h: delete if below MC or no meaningful change for this long

    def _ensure_log_table(self, conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshot_cleanup_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                snapshots_deleted INTEGER NOT NULL DEFAULT 0,
                tokens_deleted INTEGER NOT NULL DEFAULT 0,
                snapshots_downsampled INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshot_cleanup_log_ts
            ON snapshot_cleanup_log(ts)
        """)
        conn.commit()  # DDL must be committed before INSERT can reference the table

    def run_cleanup(self, below_mc_since: dict = None, last_meaningful_change: dict = None) -> dict:
        ws_log.info(f"[SNAPSHOT_CLEANUP] START db={self.db_path}")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        now = int(time.time())
        self._ensure_log_table(conn)
        ws_log.debug(f"[SNAPSHOT_CLEANUP] log table ready")
        below_mc_since = below_mc_since or {}
        last_meaningful_change = last_meaningful_change or {}

        stats = {
            "tokens_processed": 0,
            "snapshots_deleted": 0,
            "tokens_deleted": 0,
            "snapshots_downsampled": 0,
        }

        def _compute_rating(peak_mc, drawdown, recovery_ratio, category):
            """Rating 1..10 based on peak MC. Immediate rugs capped low, recoveries can add +1."""
            if not peak_mc or peak_mc <= 0:
                return 1, 'no_peak_mc'
            if peak_mc < 25_000:
                base = 1
            elif peak_mc < 100_000:
                base = 2
            elif peak_mc < 250_000:
                base = 3
            elif peak_mc < 500_000:
                base = 4
            elif peak_mc < 1_000_000:
                base = 5
            elif peak_mc < 2_000_000:
                base = 6
            elif peak_mc < 5_000_000:
                base = 8
            else:
                base = 10
            reason = f'peak_mc={peak_mc:.0f}'
            if category == 'immediate_rug':
                base = min(base, 2)
                reason += ',immediate_rug_cap'
            elif recovery_ratio and recovery_ratio > 0.5 and base < 10:
                base = min(base + 1, 10)
                reason += ',recovery_bonus'
            return max(1, min(base, 10)), reason

        def _finalize_outcome(conn, mint, drop_reason):
            """Aggregate snapshot data into token_outcomes before deleting snapshots."""
            try:
                row = conn.execute("""
                    SELECT
                        MIN(captured_at) AS first_seen_at,
                        MAX(captured_at) AS last_seen_at,
                        COUNT(*) AS snap_count,
                        MAX(market_cap) AS peak_mc,
                        MIN(CASE WHEN market_cap = MAX(market_cap) OVER() THEN captured_at END) AS peak_mc_at,
                        price_usd AS latest_price,
                        market_cap AS latest_mc
                    FROM token_price_snapshots
                    WHERE mint = ?
                    ORDER BY captured_at DESC LIMIT 1
                """, (mint,)).fetchone()
                # Simpler reliable version:
                agg = conn.execute("""
                    SELECT MIN(captured_at) AS first_seen_at,
                           MAX(captured_at) AS last_seen_at,
                           COUNT(*) AS snap_count
                    FROM token_price_snapshots WHERE mint = ?
                """, (mint,)).fetchone()
                peak_row = conn.execute(
                    "SELECT market_cap, captured_at FROM token_price_snapshots WHERE mint = ? ORDER BY market_cap DESC LIMIT 1",
                    (mint,)
                ).fetchone()
                latest_row = conn.execute(
                    "SELECT price_usd, market_cap FROM token_price_snapshots WHERE mint = ? ORDER BY captured_at DESC LIMIT 1",
                    (mint,)
                ).fetchone()
                tb = conn.execute(
                    "SELECT category, confidence, tracking_quality, max_return_multiple, drawdown_from_peak, recovery_ratio, time_to_peak_secs FROM token_behavior WHERE mint = ?",
                    (mint,)
                ).fetchone()
                tt = conn.execute(
                    "SELECT created_at FROM tracked_tokens WHERE mint = ?", (mint,)
                ).fetchone()

                if not agg:
                    return

                first_seen = agg['first_seen_at']
                last_seen = agg['last_seen_at']
                snap_count = agg['snap_count']
                peak_mc = peak_row['market_cap'] if peak_row else None
                peak_mc_at = peak_row['captured_at'] if peak_row else None
                latest_price = latest_row['price_usd'] if latest_row else None
                latest_mc = latest_row['market_cap'] if latest_row else None
                lifetime = (last_seen - first_seen) if (first_seen and last_seen) else None
                time_to_peak = (peak_mc_at - first_seen) if (peak_mc_at and first_seen) else None
                if tb:
                    category = tb['category']
                    confidence = tb['confidence']
                    tracking_quality = tb['tracking_quality']
                    max_return = tb['max_return_multiple']
                    drawdown = tb['drawdown_from_peak']
                    recovery = tb['recovery_ratio']
                    ttp = tb['time_to_peak_secs'] or time_to_peak
                else:
                    category = None
                    confidence = None
                    tracking_quality = None
                    max_return = None
                    drawdown = None
                    recovery = None
                    ttp = time_to_peak

                rating, rating_reason = _compute_rating(peak_mc, drawdown, recovery, category)

                # Preserve peak MC before snapshots are deleted
                if peak_mc and peak_mc > 0:
                    conn.execute("""
                        INSERT INTO token_market_cap_peaks (mint, peak_market_cap, peak_market_cap_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(mint) DO UPDATE SET
                            peak_market_cap = MAX(peak_market_cap, excluded.peak_market_cap),
                            peak_market_cap_at = CASE WHEN excluded.peak_market_cap > peak_market_cap
                                                      THEN excluded.peak_market_cap_at
                                                      ELSE peak_market_cap_at END
                    """, (mint, peak_mc, peak_mc_at or now))

                conn.execute("""
                    INSERT INTO token_outcomes (
                        mint, first_seen_at, last_seen_at, tracking_ended_at, drop_reason,
                        peak_market_cap_usd, peak_market_cap_at, time_to_peak_secs,
                        latest_market_cap_usd, latest_price_usd, lifetime_secs,
                        snapshot_count_final, max_return_multiple, drawdown_from_peak,
                        behaviour_category, confidence, tracking_quality,
                        rating_1_to_10, rating_reason, finalized_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(mint) DO UPDATE SET
                        last_seen_at=excluded.last_seen_at,
                        tracking_ended_at=excluded.tracking_ended_at,
                        drop_reason=excluded.drop_reason,
                        peak_market_cap_usd=excluded.peak_market_cap_usd,
                        peak_market_cap_at=excluded.peak_market_cap_at,
                        time_to_peak_secs=excluded.time_to_peak_secs,
                        latest_market_cap_usd=excluded.latest_market_cap_usd,
                        latest_price_usd=excluded.latest_price_usd,
                        lifetime_secs=excluded.lifetime_secs,
                        snapshot_count_final=excluded.snapshot_count_final,
                        max_return_multiple=excluded.max_return_multiple,
                        drawdown_from_peak=excluded.drawdown_from_peak,
                        behaviour_category=excluded.behaviour_category,
                        confidence=excluded.confidence,
                        tracking_quality=excluded.tracking_quality,
                        rating_1_to_10=excluded.rating_1_to_10,
                        rating_reason=excluded.rating_reason,
                        finalized_at=excluded.finalized_at
                """, (
                    mint, first_seen, last_seen, now, drop_reason,
                    peak_mc, peak_mc_at, ttp,
                    latest_mc, latest_price, lifetime,
                    snap_count, max_return, drawdown,
                    category, confidence, tracking_quality,
                    rating, rating_reason, now
                ))
                ws_log.info(f"[OUTCOME] finalized mint={mint[:16]} category={category} rating={rating} peak_mc={peak_mc:.0f if peak_mc else 'None'} reason={drop_reason}")
            except Exception as e:
                ws_log.warning(f"[OUTCOME] finalize failed for {mint[:16]}: {e}")

        def _deactivate_pools(conn, mint, reason):
            """Deactivate pool rows after snapshot history is removed. Returns count."""
            n = conn.execute(
                "UPDATE token_pool_accounts SET is_active = 0, updated_at = strftime('%s','now') "
                "WHERE mint = ? AND is_active = 1",
                (mint,)
            ).rowcount
            if n:
                ws_log.info(f"[POOL_DEACTIVATE] mint={mint[:16]} pools={n} reason={reason}")
            return n

        def _lifecycle_delete(conn, mint):
            """Delete all snapshots + count row for mint. Returns rows deleted."""
            deleted = conn.execute(
                "DELETE FROM token_price_snapshots WHERE mint = ?", (mint,)
            ).rowcount
            conn.execute("DELETE FROM token_snapshot_counts WHERE mint = ?", (mint,))
            return deleted

        try:
            tokens = conn.execute("""
                SELECT mint, snap_count, last_updated
                FROM token_snapshot_counts
            """).fetchall()

            for row in tokens:
                mint = row["mint"]
                snap_count = row["snap_count"]
                last_updated = row["last_updated"]

                age = now - last_updated

                # --- LIFECYCLE RULE: delete if below MC or stale for 12h ---
                below_since = below_mc_since.get(mint)
                last_change = last_meaningful_change.get(mint, last_updated or 0)
                if (below_since and now - below_since >= self.LIFECYCLE_DELETE_AFTER) or \
                   (now - last_change >= self.LIFECYCLE_DELETE_AFTER):
                    _finalize_outcome(conn, mint, 'lifecycle')
                    deleted = _lifecycle_delete(conn, mint)
                    _deactivate_pools(conn, mint, 'lifecycle')
                    stats["snapshots_deleted"] += deleted
                    stats["tokens_deleted"] += 1
                    stats["tokens_processed"] += 1
                    ws_log.info(
                        f"[SNAP_DELETE] lifecycle mint={mint[:16]} "
                        f"snaps={deleted} below_mc={bool(below_since)} "
                        f"stale_secs={now - last_change}"
                    )
                    continue

                # --- RULE 1: Skip very fresh tokens ---
                if snap_count <= self.MIN_KEEP_SNAPSHOTS or age < self.FULL_HISTORY_WINDOW_SECS:
                    continue

                # --- RULE 2: Delete entire history if very old ---
                if age > self.DELETE_AFTER_INACTIVE_SECS:
                    _finalize_outcome(conn, mint, 'inactive')
                    deleted = conn.execute(
                        "DELETE FROM token_price_snapshots WHERE mint = ?",
                        (mint,)
                    ).rowcount
                    conn.execute(
                        "DELETE FROM token_snapshot_counts WHERE mint = ?",
                        (mint,)
                    )
                    _deactivate_pools(conn, mint, 'inactive')
                    stats["snapshots_deleted"] += deleted
                    stats["tokens_deleted"] += 1
                    stats["tokens_processed"] += 1
                    ws_log.info(f"[SNAP_DELETE] inactive mint={mint[:16]} snaps={deleted} age_secs={age}")
                    continue

                # --- RULE 3: Downsample ---
                deleted = self._downsample_token(conn, mint)

                if deleted > 0:
                    stats["snapshots_deleted"] += deleted
                    stats["snapshots_downsampled"] += deleted
                    stats["tokens_processed"] += 1
                    ws_log.debug(f"[SNAP_DOWNSAMPLE] mint={mint[:16]} removed={deleted}")

            # --- PASS 2: stale low-snapshot tokens (snap_count <= MIN_KEEP_SNAPSHOTS) ---
            if self.REMOVE_STALE_LOW_SNAP_TOKENS:
                stale_low = conn.execute("""
                    SELECT mint
                    FROM token_snapshot_counts
                    WHERE snap_count <= ?
                      AND last_updated < ?
                      AND last_updated > 0
                """, (self.MIN_KEEP_SNAPSHOTS, now - self.DELETE_AFTER_INACTIVE_SECS)).fetchall()

                for row in stale_low:
                    mint = row["mint"]
                    _finalize_outcome(conn, mint, 'stale_low_snap')
                    deleted = conn.execute(
                        "DELETE FROM token_price_snapshots WHERE mint = ?", (mint,)
                    ).rowcount
                    conn.execute(
                        "DELETE FROM token_snapshot_counts WHERE mint = ?", (mint,)
                    )
                    _deactivate_pools(conn, mint, 'stale_low_snap')
                    stats["snapshots_deleted"] += deleted
                    stats["tokens_deleted"] += 1
                    stats["tokens_processed"] += 1
                    ws_log.info(f"[SNAP_DELETE] stale_low mint={mint[:16]} snaps={deleted}")

            # --- PASS 3: retroactive 12h low-value / no-movement pruning ---
            # Removes snapshot + monitoring footprint only. Does NOT touch token_analysis.
            candidates = conn.execute("""
                SELECT mint,
                       MAX(market_cap)  AS max_mc,
                       CASE WHEN MAX(price_usd)  > 0
                            THEN (MAX(price_usd)  - MIN(price_usd))  / MAX(price_usd)
                            ELSE 0 END AS price_var,
                       CASE WHEN MAX(market_cap) > 0
                            THEN (MAX(market_cap) - MIN(market_cap)) / MAX(market_cap)
                            ELSE 0 END AS mc_var
                FROM token_price_snapshots
                WHERE captured_at >= ?
                GROUP BY mint
                HAVING max_mc < 5000
                    OR (price_var < 0.01 AND mc_var < 0.01)
            """, (now - self.LIFECYCLE_DELETE_AFTER,)).fetchall()

            for row in candidates:
                mint = row["mint"]
                _finalize_outcome(conn, mint, 'low_value_12h')
                deleted = conn.execute(
                    "DELETE FROM token_price_snapshots WHERE mint = ?", (mint,)
                ).rowcount
                conn.execute("DELETE FROM token_snapshot_counts WHERE mint = ?", (mint,))
                conn.execute(
                    "UPDATE tracked_tokens SET is_active = 0, stop_reason = 'low_value_12h', "
                    "inactive_since = ? WHERE mint = ? AND is_active = 1",
                    (now, mint)
                )
                _deactivate_pools(conn, mint, 'low_value_12h')
                stats["snapshots_deleted"] += deleted
                stats["tokens_deleted"] += 1
                stats["tokens_processed"] += 1
                ws_log.info(
                    f"[SNAP_DELETE] low_value mint={mint[:16]} snaps={deleted} "
                    f"max_mc={row['max_mc']:.0f} price_var={row['price_var']:.4f} mc_var={row['mc_var']:.4f}"
                )

            # Persist this cleanup run
            ws_log.debug(f"[SNAPSHOT_CLEANUP] persisting stats={stats}")
            conn.execute(
                "INSERT INTO snapshot_cleanup_log (ts, snapshots_deleted, tokens_deleted, snapshots_downsampled) "
                "VALUES (?, ?, ?, ?)",
                (now, stats["snapshots_deleted"], stats["tokens_deleted"], stats["snapshots_downsampled"])
            )
            # Prune log entries older than 7 days
            conn.execute("DELETE FROM snapshot_cleanup_log WHERE ts < ?", (now - 604800,))
            conn.commit()
            ws_log.info(
                f"[SNAPSHOT_CLEANUP] DONE snaps_deleted={stats['snapshots_deleted']} "
                f"tokens_deleted={stats['tokens_deleted']} downsampled={stats['snapshots_downsampled']}"
            )

            return stats

        except Exception as e:
            ws_log.error(f"[SNAPSHOT_CLEANUP] EXCEPTION: {e}")
            logger.error(f"[SNAPSHOT_CLEANUP] Error: {e}", exc_info=True)
            return stats

        finally:
            conn.close()

    def get_24h_stats(self) -> dict:
        """Query DB for 24h cleanup totals. Survives process restarts."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=3)
            conn.row_factory = sqlite3.Row
            self._ensure_log_table(conn)
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(snapshots_deleted), 0)   AS snapshots_deleted_24h,
                    COALESCE(SUM(tokens_deleted), 0)      AS tokens_deleted_24h,
                    COALESCE(SUM(snapshots_downsampled), 0) AS snapshots_downsampled_24h,
                    MAX(ts)                               AS last_cleanup_at
                FROM snapshot_cleanup_log
                WHERE ts >= strftime('%s','now') - 86400
            """).fetchone()
            conn.close()
            return dict(row) if row else {
                'snapshots_deleted_24h': 0, 'tokens_deleted_24h': 0,
                'snapshots_downsampled_24h': 0, 'last_cleanup_at': None,
            }
        except Exception as e:
            logger.debug(f"[CLEANUP_STATS] get_24h_stats error: {e}")
            return {
                'snapshots_deleted_24h': 0, 'tokens_deleted_24h': 0,
                'snapshots_downsampled_24h': 0, 'last_cleanup_at': None,
            }

    def _downsample_token(self, conn, mint: str) -> int:
        """
        Keep:
        - First N snapshots
        - Latest N snapshots
        - 1 snapshot per interval in between
        """

        rows = conn.execute("""
            SELECT snapshot_id, captured_at
            FROM token_price_snapshots
            WHERE mint = ?
            ORDER BY captured_at ASC
        """, (mint,)).fetchall()

        if len(rows) <= self.MIN_KEEP_SNAPSHOTS:
            return 0

        keep_ids = set()

        # Keep first N
        for r in rows[:self.MIN_KEEP_SNAPSHOTS // 2]:
            keep_ids.add(r["snapshot_id"])

        # Keep last N
        for r in rows[-(self.MIN_KEEP_SNAPSHOTS // 2):]:
            keep_ids.add(r["snapshot_id"])

        # Downsample middle
        last_kept_ts = 0
        for r in rows:
            if r["snapshot_id"] in keep_ids:
                last_kept_ts = r["captured_at"]
                continue

            if r["captured_at"] - last_kept_ts >= self.DOWNSAMPLE_INTERVAL_SECS:
                keep_ids.add(r["snapshot_id"])
                last_kept_ts = r["captured_at"]

        # Delete everything else
        all_ids = {r["snapshot_id"] for r in rows}
        delete_ids = list(all_ids - keep_ids)

        if not delete_ids:
            return 0

        conn.executemany(
            "DELETE FROM token_price_snapshots WHERE snapshot_id = ?",
            [(i,) for i in delete_ids]
        )

        # Update snapshot count
        conn.execute("""
            UPDATE token_snapshot_counts
            SET snap_count = (
                SELECT COUNT(*) FROM token_price_snapshots WHERE mint = ?
            ),
            last_updated = ?
            WHERE mint = ?
        """, (mint, int(time.time()), mint))

        return len(delete_ids)
