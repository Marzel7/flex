"""
FLEX Liquidity Worker

Background worker that maintains BOUNDED liquidity state for real, currently-
held positions only. Today that eligible set is always empty (see
REAL_POSITION_AUTHORITY_NOT_IMPLEMENTED below) -- capability is retained,
persistence is not activated.

LIQUIDITY_STORAGE_LIFECYCLE_MIGRATION: dense unconditional liquidity-history
persistence (token_liquidity_snapshots, appended for every active tracked
token every ~60s regardless of ownership) has been retired. It served no
current operation/P3R/Walkback/discovery path (LIQUIDITY_STORAGE_DECOMMISSIONING
_QUALIFICATION), and its legacy health/risk derivation
(compute_health_score/detect_rug_pull_risk -> token_liquidity_health/
token_liquidity_risks) was independently proven LEGACY_DERIVED_STATE (stale
since 2026-04-28, zero live consumers).

New model (LIQUIDITY_REAL_POSITION_BOUNDARY correction):
  - No real-position authority currently exists anywhere in this codebase
    (real-money submit is explicitly disabled: SUBMIT_DISABLED=true,
    ENABLE_CREATE_INTERCEPTOR=false, INTERCEPTOR_MODE=PASSIVE; no table/code
    path tracks an actually-executed buy or held wallet balance).
    REAL_POSITION_AUTHORITY_NOT_IMPLEMENTED.
  - Therefore _get_live_position_mints() is a small, explicit, currently-empty
    eligibility interface: it returns [] until a real trading system
    implements the authoritative source.
  - Only mints returned by _get_live_position_mints() are processed at all --
    today that is always zero, so ordinary/migrated/candidate/operation
    tokens (and paper-simulation tokens) are skipped entirely: no
    price_service call, no DB write, no loop work for them here.
  - First qualifying observation for a mint freezes an ENTRY liquidity value
    in the compact token_owned_liquidity_state table (one row per mint).
  - Subsequent observations UPDATE the LATEST value in place -- no new
    dense historical row is ever appended.
  - Once a mint drops out of _get_live_position_mints() (position closed),
    it is no longer processed and its state simply stops updating.
  - Liquidity persistence is optional/best-effort: any failure here must
    never interrupt birth/migration/Walkback/operation-discovery, so all
    per-mint work is wrapped and logged, never raised.

Architecture:
_get_live_position_mints() (currently always [])
    ↓
Fetch Liquidity (via price_service, same acquisition path as before)
    ↓
Freeze ENTRY (first time) / Update LATEST (subsequent) in
token_owned_liquidity_state
"""

import sqlite3
import logging
import time
import threading
from typing import Dict, Optional, List
from src.core.price_service import get_price_service

logger = logging.getLogger(__name__)


def _ensure_owned_liquidity_state_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_owned_liquidity_state (
            mint                  TEXT PRIMARY KEY,
            entry_liquidity_usd   REAL,
            entry_liquidity_at    INTEGER,
            latest_liquidity_usd  REAL,
            latest_liquidity_at   INTEGER,
            final_liquidity_usd   REAL,
            final_liquidity_at    INTEGER,
            source                TEXT,
            updated_at            INTEGER
        )
    """)


class LiquidityWorker:
    """Background worker maintaining bounded liquidity state for real, currently-held positions (see _get_live_position_mints)."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db',
                 interval: int = 60, batch_size: int = 20):
        """
        Initialize worker.

        Args:
            db_path: Database path
            interval: Refresh interval in seconds (default 60)
            batch_size: Tokens to fetch per API call (default 20)
        """
        self.db_path = db_path
        self.interval = interval
        self.batch_size = batch_size
        self.price_service = get_price_service(db_path)
        self.running = False
        self.thread = None
        self.stats = {
            'cycles': 0,
            'entry_values_frozen': 0,
            'latest_values_updated': 0,
            'live_positions_seen': 0,
            'errors': 0,
            'last_run': None,
            'last_error': None
        }

    def start(self) -> None:
        """Start the background worker."""
        if self.running:
            logger.warning("Liquidity worker already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info(f"Liquidity worker started (interval={self.interval}s, batch={self.batch_size})")

    def stop(self) -> None:
        """Stop the background worker."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Liquidity worker stopped")

    def _run_loop(self) -> None:
        """Main worker loop."""
        while self.running:
            try:
                self._refresh_cycle()
                time.sleep(self.interval)
            except Exception as e:
                logger.error(f"Worker cycle error: {e}", exc_info=True)
                self.stats['last_error'] = str(e)
                self.stats['errors'] += 1
                time.sleep(self.interval)

    def _get_live_position_mints(self) -> List[str]:
        """The single, explicit eligibility interface for production liquidity
        acquisition/persistence.

        REAL_POSITION_AUTHORITY_NOT_IMPLEMENTED: no real-money position
        authority exists in this codebase today (real-trade submission is
        disabled system-wide). This deliberately returns [] until a future
        real trading system provides an authoritative source of currently-held
        positions. It must never infer ownership from tracked-token metadata
        as a substitute for a future authoritative real-position source.

        Fail closed: any future implementation of this method must also
        return [] rather than guess on any uncertainty about position state.
        """
        return []

    def _refresh_cycle(self) -> None:
        """One complete refresh cycle. Only processes mints returned by
        _get_live_position_mints() -- today that is always zero (no real
        position authority exists), so ordinary/migrated/candidate/operation
        tokens AND paper-simulation tokens are never touched here."""
        cycle_start = time.time()
        self.stats['cycles'] += 1

        mints = self._get_live_position_mints()
        self.stats['live_positions_seen'] = len(mints)

        if not mints:
            logger.debug("No real eligible positions; liquidity worker skipping cycle")
            return

        for i in range(0, len(mints), self.batch_size):
            batch = mints[i:i + self.batch_size]
            self._process_liquidity_batch(batch)

        duration = time.time() - cycle_start
        self.stats['last_run'] = duration

        logger.debug(
            f"Liquidity cycle {self.stats['cycles']}: "
            f"{len(mints)} live positions, {duration:.2f}s"
        )

    def _process_liquidity_batch(self, mints: List[str]) -> None:
        """Fetch and persist bounded liquidity state for a batch of real,
        currently-eligible position mints (from _get_live_position_mints).
        Never raises -- a liquidity failure must not interrupt the wider
        production pipeline."""
        try:
            prices = self.price_service.get_token_prices_sync(mints, cache_type='org')
        except Exception as e:
            logger.error(f"Liquidity worker: price fetch failed for batch: {e}")
            self.stats['errors'] += 1
            return

        for mint, price in prices.items():
            try:
                if price.source == 'unavailable':
                    continue
                self._upsert_owned_liquidity_state(
                    mint=mint,
                    liquidity_usd=price.liquidity_usd,
                    source=price.source,
                )
            except Exception as e:
                logger.error(f"Liquidity worker: failed to persist state for {mint}: {e}")
                self.stats['errors'] += 1
                # Continue with the rest of the batch -- one mint's failure
                # must not abort others, and never propagates upward.

    def _upsert_owned_liquidity_state(self, mint: str, liquidity_usd: Optional[float],
                                       source: Optional[str]) -> None:
        """First observation for a mint freezes ENTRY; every observation
        after that updates LATEST in place. No dense historical row is ever
        appended -- state stays bounded at one row per mint."""
        if liquidity_usd is None:
            return
        now = int(time.time())
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            _ensure_owned_liquidity_state_table(conn)
            conn.execute("""
                INSERT INTO token_owned_liquidity_state
                    (mint, entry_liquidity_usd, entry_liquidity_at,
                     latest_liquidity_usd, latest_liquidity_at, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    latest_liquidity_usd = excluded.latest_liquidity_usd,
                    latest_liquidity_at  = excluded.latest_liquidity_at,
                    source               = excluded.source,
                    updated_at           = excluded.updated_at
            """, (mint, liquidity_usd, now, liquidity_usd, now, source, now))
            conn.commit()
            if conn.total_changes:
                # Distinguish first-insert (entry frozen) from update for stats;
                # cheap enough to just check whether entry_liquidity_at == now.
                row = conn.execute(
                    "SELECT entry_liquidity_at, latest_liquidity_at FROM token_owned_liquidity_state WHERE mint = ?",
                    (mint,)
                ).fetchone()
                if row and row[0] == now and row[1] == now:
                    self.stats['entry_values_frozen'] += 1
                else:
                    self.stats['latest_values_updated'] += 1
        finally:
            conn.close()

    def close_position_liquidity(self, mint: str) -> None:
        """Optional: freeze a final liquidity value when a position closes.
        Best-effort, never raises. Not required for correctness -- the
        latest value already reflects the last observation before close."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            try:
                conn.execute("PRAGMA busy_timeout = 5000")
                _ensure_owned_liquidity_state_table(conn)
                conn.execute("""
                    UPDATE token_owned_liquidity_state
                    SET final_liquidity_usd = latest_liquidity_usd,
                        final_liquidity_at  = ?
                    WHERE mint = ? AND final_liquidity_at IS NULL
                """, (int(time.time()), mint))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"close_position_liquidity best-effort failed for {mint}: {e}")

    def get_stats(self) -> Dict:
        """Get worker statistics."""
        return self.stats.copy()


# Global worker instance
_liquidity_worker: Optional[LiquidityWorker] = None


def get_liquidity_worker(db_path: str = 'database/flex_complete_database.db') -> LiquidityWorker:
    """Get or create singleton liquidity worker."""
    global _liquidity_worker
    if _liquidity_worker is None:
        _liquidity_worker = LiquidityWorker(db_path)
    return _liquidity_worker


def start_liquidity_worker(db_path: str = 'database/flex_complete_database.db') -> LiquidityWorker:
    """Start the background liquidity worker."""
    worker = get_liquidity_worker(db_path)
    worker.start()
    return worker
