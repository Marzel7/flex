"""
FLEX Liquidity Worker

Background worker that continuously refreshes liquidity data.

Tasks:
1. Fetch liquidity from Dexscreener for tracked tokens
2. Store snapshots every 30-120 seconds
3. Compute health and risk scores
4. Update cache

Architecture:
Tracked Token Registry
    ↓
Fetch Liquidity (Dexscreener)
    ↓
Store Snapshot
    ↓
Compute Scores
    ↓
Update Cache
"""

import sqlite3
import logging
import time
import threading
import aiohttp
import asyncio
from typing import Dict, Optional, List
from src.core.liquidity_intelligence import get_liquidity_intelligence
from src.core.price_worker import PriceWorkerRegistry
from src.core.price_service import get_price_service

logger = logging.getLogger(__name__)


class LiquidityWorker:
    """Background worker for liquidity tracking."""

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
        self.intelligence = get_liquidity_intelligence(db_path)
        self.price_service = get_price_service(db_path)
        self.registry = PriceWorkerRegistry(db_path)
        self.running = False
        self.thread = None
        self.stats = {
            'cycles': 0,
            'snapshots_stored': 0,
            'health_scores_computed': 0,
            'risk_scores_computed': 0,
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

    def _refresh_cycle(self) -> None:
        """One complete refresh cycle."""
        cycle_start = time.time()
        self.stats['cycles'] += 1

        # Get all active tracked tokens
        tokens = self.registry.get_tracked_tokens(active_only=True)

        if not tokens:
            logger.debug("No tracked tokens for liquidity refresh")
            return

        # Fetch prices (which include liquidity)
        mints = [t['mint'] for t in tokens]

        # Process in batches
        for i in range(0, len(mints), self.batch_size):
            batch = mints[i:i + self.batch_size]
            self._process_liquidity_batch(batch)

        duration = time.time() - cycle_start
        self.stats['last_run'] = duration

        logger.debug(
            f"Liquidity cycle {self.stats['cycles']}: "
            f"{len(mints)} tokens, {duration:.2f}s, "
            f"{self.stats['snapshots_stored']} snapshots stored"
        )

    def _process_liquidity_batch(self, mints: List[str]) -> None:
        """Process liquidity for a batch of tokens."""
        try:
            # Fetch prices (includes liquidity)
            prices = self.price_service.get_token_prices_sync(mints, cache_type='org')

            for mint, price in prices.items():
                if price.source == 'unavailable':
                    continue

                # Store liquidity snapshot
                stored = self.intelligence.store_snapshot(
                    mint=mint,
                    liquidity_usd=price.liquidity_usd,
                    liquidity_sol=price.price_sol,  # Approximate SOL equivalent
                    pair_address=price.pair_address
                )

                if stored:
                    self.stats['snapshots_stored'] += 1

                # Compute and cache health score
                health = self.intelligence.compute_health_score(mint)
                self.intelligence.cache_health_assessment(mint, health)
                self.stats['health_scores_computed'] += 1

                # Compute and cache risk score
                risk = self.intelligence.detect_rug_pull_risk(mint)
                self.intelligence.cache_risk_assessment(mint, risk)
                self.stats['risk_scores_computed'] += 1

        except Exception as e:
            logger.error(f"Error processing liquidity batch: {e}")
            self.stats['errors'] += 1

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
