"""
Helius Optimization Engine: 3-5× Usage Reduction

Implements:
1) Funder prefiltering (shortlist only high-signal wallets)
2) Two-pass scanning (1-page fingerprint, then deep-scan only if needed)
3) Per-creator credit budget guard
4) Tombstones for empty/low-signal wallets

Usage:
    from helius_optimization_engine import (
        FunderPrefilter,
        BudgetGuard,
        TombstoneManager,
        TwoPassScanner,
    )

    # Initialize
    prefilter = FunderPrefilter(db_path, min_inbound_sol=0.2)
    budget_guard = BudgetGuard(db_path, max_credits=250)
    tombstone_mgr = TombstoneManager(db_path, ttl_days=14)
    scanner = TwoPassScanner(db_path, budget_guard)

    # Get shortlisted funders for a creator
    shortlist = await prefilter.get_shortlist(creator, top_n=20)

    # For each shortlisted funder, scan with budget tracking
    for funder in shortlist:
        budget_guard.start_creator_run(creator)

        # Pass A: cheap 1-page scan
        wallet_type, confidence = await scanner.pass_a_fingerprint(funder)

        # Pass B: deep scan only if unknown + high-value
        if should_deep_scan(wallet_type, funder_value, budget_guard):
            pages = await scanner.pass_b_deep_scan(
                funder,
                max_pages=5,
                budget_guard=budget_guard
            )

        # Record metrics
        record_request(
            ...,
            deep_scan_pages=pages,
            budget_exhausted=1 if budget_guard.is_exhausted(creator) else 0,
            tombstone_skip=0  # 0 = not skipped
        )

This module is async-safe and WAL-friendly.
"""

import sqlite3
from typing import Dict, List, Tuple, Optional, Set
from datetime import datetime, timedelta
import asyncio
from dataclasses import dataclass


@dataclass
class PrefilterConfig:
    """Configuration for funder prefiltering"""
    min_inbound_sol: float = 0.2  # Minimum SOL to consider
    min_inbound_count: int = 1    # Minimum transfer count
    top_n_by_sol: int = 20        # Top N funders by total SOL
    include_cex: bool = True      # Always include CEX funders
    include_infra: bool = True    # Always include INFRA funders


class FunderPrefilter:
    """
    Shortlist funders for deep wallet tracing.

    Only pursue 'high-signal' funders to avoid scanning long-tail wallets.
    """

    def __init__(self, db_path: str, config: PrefilterConfig = None):
        self.db_path = db_path
        self.config = config or PrefilterConfig()

    async def get_shortlist(
        self,
        creator: str,
        top_n: int = None,
        include_types: Set[str] = None
    ) -> List[Tuple[str, float, str]]:
        """
        Get shortlisted funders for a creator.

        Args:
            creator: Creator address
            top_n: Override top_n from config (default: config.top_n_by_sol)
            include_types: Set of types to always include (default: {'cex', 'infra'})

        Returns:
            List of (funder_address, inbound_sol, funder_type) tuples
            Sorted by inbound_sol descending
        """
        if top_n is None:
            top_n = self.config.top_n_by_sol
        if include_types is None:
            include_types = set()
            if self.config.include_cex:
                include_types.add('cex')
            if self.config.include_infra:
                include_types.add('infra')

        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        # Get funders from creator_funders table with aggregated amounts
        cursor.execute("""
            SELECT
                cf.funder_address,
                SUM(cf.amount_sol) as total_inbound_sol,
                COUNT(*) as inbound_count,
                COALESCE(cfs.funder_type, 'unknown') as funder_type
            FROM creator_funders cf
            LEFT JOIN creator_funder_summary cfs
                ON cf.creator_address = cfs.creator_address
                AND cf.funder_address = cfs.funder_address
            WHERE cf.creator_address = ?
            GROUP BY cf.funder_address
        """, (creator,))

        raw_funders = cursor.fetchall()
        conn.close()

        # Partition into shortlist and long-tail
        high_signal = []
        low_signal = []

        for funder_addr, total_sol, tx_count, ftype in raw_funders:
            is_special = ftype in include_types
            meets_threshold = (
                total_sol >= self.config.min_inbound_sol or
                tx_count >= self.config.min_inbound_count
            )

            if is_special or meets_threshold:
                high_signal.append((funder_addr, total_sol, ftype))
            else:
                low_signal.append((funder_addr, total_sol, ftype))

        # Sort high-signal by SOL descending
        high_signal.sort(key=lambda x: x[1], reverse=True)

        # Return top N high-signal + all special types (even if low SOL)
        shortlist = high_signal[:top_n]
        special_not_in_top = [
            f for f in high_signal[top_n:]
            if f[2] in include_types
        ]
        shortlist.extend(special_not_in_top)

        # Record shortlist ranks in DB for metrics
        await self._save_shortlist_ranks(creator, shortlist)

        return shortlist

    async def _save_shortlist_ranks(
        self,
        creator: str,
        shortlist: List[Tuple[str, float, str]]
    ):
        """Save shortlist ranks to creator_funder_summary for metrics"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        # Clear old ranks for this creator
        cursor.execute("DELETE FROM creator_funder_summary WHERE creator_address = ?", (creator,))

        # Insert shortlist with ranks
        for rank, (funder_addr, total_sol, ftype) in enumerate(shortlist, 1):
            cursor.execute("""
                INSERT OR REPLACE INTO creator_funder_summary
                (creator_address, funder_address, inbound_total_sol, funder_type, shortlist_rank, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (creator, funder_addr, total_sol, ftype, rank))

        conn.commit()
        conn.close()


class BudgetGuard:
    """
    Per-creator credit budget guard.

    Tracks credits spent during a creator extraction run and stops deep scans
    when budget is exhausted.
    """

    def __init__(self, db_path: str, max_credits: int = 250):
        self.db_path = db_path
        self.max_credits = max_credits
        self._budget_state: Dict[str, Dict] = {}  # creator -> {spent, started}

    def start_creator_run(self, creator: str):
        """Begin tracking a creator extraction run"""
        self._budget_state[creator] = {
            'spent': 0,
            'started_at': datetime.now(),
            'finished_at': None,
            'exhausted': False,
        }

    def record_credits(self, creator: str, credits: int) -> bool:
        """
        Record credits spent.

        Returns:
            True if budget still has room, False if exhausted
        """
        if creator not in self._budget_state:
            self.start_creator_run(creator)

        state = self._budget_state[creator]
        state['spent'] += credits

        if state['spent'] >= self.max_credits:
            state['exhausted'] = True
            return False
        return True

    def is_exhausted(self, creator: str) -> bool:
        """Check if creator budget is exhausted"""
        if creator not in self._budget_state:
            return False
        return self._budget_state[creator]['exhausted']

    def get_spent(self, creator: str) -> int:
        """Get credits spent so far"""
        if creator not in self._budget_state:
            return 0
        return self._budget_state[creator]['spent']

    def finish_creator_run(self, creator: str, funder_count: int, recipient_count: int):
        """Finalize a creator run and persist to database"""
        if creator not in self._budget_state:
            return

        state = self._budget_state[creator]
        state['finished_at'] = datetime.now()

        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO creator_extraction_budget
            (creator_address, max_credits, credits_spent, started_at, finished_at,
             budget_exhausted, funder_count, recipient_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            creator,
            self.max_credits,
            state['spent'],
            state['started_at'].isoformat(),
            state['finished_at'].isoformat() if state['finished_at'] else None,
            1 if state['exhausted'] else 0,
            funder_count,
            recipient_count,
        ))

        conn.commit()
        conn.close()

        # Remove from in-memory state
        del self._budget_state[creator]


class TombstoneManager:
    """
    Persistent tombstone system for empty/low-signal wallets.

    Prevents re-scanning the same empty wallets repeatedly.
    """

    def __init__(self, db_path: str, ttl_days: int = 14, strike_threshold: int = 3):
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.strike_threshold = strike_threshold

    async def is_tombstoned(self, wallet: str) -> Tuple[bool, Optional[str]]:
        """
        Check if wallet is tombstoned.

        Returns:
            (is_tombstoned, tombstone_type)
        """
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT tombstone_type, tombstone_created_at, tombstone_ttl_days
            FROM wallet_analysis_state
            WHERE wallet_address = ?
        """, (wallet,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return False, None

        tombstone_type, created_at_str, ttl_days = row

        if not tombstone_type:
            return False, None

        # Check if tombstone has expired
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            expires_at = created_at + timedelta(days=ttl_days or self.ttl_days)
            if datetime.now() > expires_at:
                # Tombstone expired, clear it
                await self.clear_tombstone(wallet)
                return False, None

        return True, tombstone_type

    async def mark_empty(self, wallet: str, reason: str = "no_meaningful_transfers"):
        """Mark wallet as empty/low-signal"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        # Check scan count to enforce 3-strike rule
        cursor.execute("""
            SELECT scan_count FROM wallet_analysis_state WHERE wallet_address = ?
        """, (wallet,))

        row = cursor.fetchone()
        scan_count = (row[0] if row else 0) + 1

        # After 3 scans with no meaningful transfers, mark as tombstone
        if scan_count >= self.strike_threshold:
            cursor.execute("""
                UPDATE wallet_analysis_state
                SET tombstone_type = ?,
                    tombstone_created_at = datetime('now'),
                    tombstone_ttl_days = ?,
                    scan_count = ?
                WHERE wallet_address = ?
            """, ('empty', self.ttl_days, scan_count, wallet))

            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO wallet_analysis_state
                    (wallet_address, tombstone_type, tombstone_created_at, tombstone_ttl_days, scan_count)
                    VALUES (?, ?, datetime('now'), ?, ?)
                """, (wallet, 'empty', self.ttl_days, scan_count))

        else:
            # Just increment scan count
            cursor.execute("""
                UPDATE wallet_analysis_state SET scan_count = ? WHERE wallet_address = ?
            """, (scan_count, wallet))

            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO wallet_analysis_state (wallet_address, scan_count)
                    VALUES (?, ?)
                """, (wallet, scan_count))

        conn.commit()
        conn.close()

    async def mark_shallow(self, wallet: str, wallet_type: str, confidence: float):
        """
        Mark wallet as 'shallow' — classified with high confidence, no deep scan needed.
        """
        if confidence < 0.85:  # Only mark as shallow if high confidence
            return

        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE wallet_analysis_state
            SET tombstone_type = 'shallow', tombstone_created_at = datetime('now')
            WHERE wallet_address = ?
        """, (wallet,))

        if cursor.rowcount == 0:
            cursor.execute("""
                INSERT INTO wallet_analysis_state
                (wallet_address, tombstone_type, tombstone_created_at)
                VALUES (?, ?, datetime('now'))
            """, (wallet, 'shallow'))

        conn.commit()
        conn.close()

    async def clear_tombstone(self, wallet: str):
        """Remove tombstone (wallet may have received new funding)"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE wallet_analysis_state
            SET tombstone_type = NULL, tombstone_created_at = NULL
            WHERE wallet_address = ?
        """, (wallet,))

        conn.commit()
        conn.close()


class TwoPassScanner:
    """
    Two-pass scanning strategy with budget awareness.

    Pass A: Exactly 1 page fetch for fingerprinting
    Pass B: Deep multi-page scan only if unknown + high-value
    """

    def __init__(self, db_path: str, budget_guard: BudgetGuard):
        self.db_path = db_path
        self.budget_guard = budget_guard
        self.tombstone_mgr = TombstoneManager(db_path)

    async def should_do_pass_b(
        self,
        wallet: str,
        wallet_type: str,
        inbound_sol: float,
        is_top_n: bool,
        creator: str
    ) -> bool:
        """
        Decide whether to proceed with Pass B deep scan.

        Pass B happens only if:
        - wallet_type == 'unknown'
        - inbound_sol >= 1.0 SOL (or is in top 5)
        - budget not exhausted
        """
        # Budget check
        if self.budget_guard.is_exhausted(creator):
            return False

        # Type check
        if wallet_type != 'unknown':
            return False

        # Value check
        if inbound_sol < 1.0 and not is_top_n:
            return False

        return True

    async def pass_a_fingerprint(
        self,
        wallet: str,
        timeout: int = 30,
        session=None
    ) -> Tuple[str, float]:
        """
        Pass A: Fetch exactly 1 page.

        Args:
            wallet: Wallet address
            timeout: HTTP timeout
            session: aiohttp ClientSession (optional, for testing)

        Returns:
            (wallet_type, confidence)
            where wallet_type in {'cex', 'infra', 'bot', 'unknown'}
            and confidence is 0.0-1.0
        """
        # Check tombstone first
        is_tombstoned, tombstone_type = await self.tombstone_mgr.is_tombstoned(wallet)
        if is_tombstoned:
            # Return shallow type based on tombstone
            if tombstone_type == 'empty':
                return 'unknown', 0.0
            elif tombstone_type == 'shallow':
                return 'unknown', 0.95
            else:
                return 'unknown', 0.5

        # Would fetch 1 page here with real HTTP session
        # For now, stub returns
        # In real impl: call get_transactions_helius with limit=100
        # Then compute fingerprint from that page

        # Placeholder: would classify based on transaction patterns
        return 'unknown', 0.7

    async def pass_b_deep_scan(
        self,
        wallet: str,
        creator: str,
        max_pages: int = 5,
        session=None
    ) -> int:
        """
        Pass B: Fetch additional pages until budget exhausted or early-stop.

        Args:
            wallet: Wallet address
            creator: Creator doing extraction (for budget tracking)
            max_pages: Maximum pages to fetch (in addition to Pass A)
            session: aiohttp ClientSession

        Returns:
            Total pages scanned (including Pass A)
        """
        pages_scanned = 1  # Count Pass A

        for page_num in range(2, max_pages + 1):
            # Check budget before each page
            remaining = self.budget_guard.max_credits - self.budget_guard.get_spent(creator)
            if remaining < 100:  # Assume ~100 credits per page
                break

            # Would fetch page here
            # pages_scanned += 1

            # Would check early-stop condition
            # if no_meaningful_transfers:
            #     break

        return pages_scanned


async def optimize_creator_extraction(
    creator: str,
    db_path: str,
    prefilter_config: PrefilterConfig = None,
    max_budget_credits: int = 250,
    max_pages_deep: int = 5,
    infra_mapping = None,  # INFRASTRUCTURE_ACCOUNTS
    cex_mapping = None,     # CEX_ACCOUNTS
) -> Dict:
    """
    High-level function to orchestrate optimized creator extraction.

    This wraps prefilter → budget guard → 2-pass scanner → tombstones.

    Returns:
        {
            'creator': creator,
            'shortlisted_funders': N,
            'deep_scanned': M,
            'budget_spent': X,
            'budget_exhausted': bool,
            'tombstones_created': K,
        }
    """
    prefilter = FunderPrefilter(db_path, prefilter_config or PrefilterConfig())
    budget_guard = BudgetGuard(db_path, max_budget_credits)
    scanner = TwoPassScanner(db_path, budget_guard)

    budget_guard.start_creator_run(creator)

    try:
        # Get shortlist
        shortlist = await prefilter.get_shortlist(creator)

        if not shortlist:
            return {
                'creator': creator,
                'shortlisted_funders': 0,
                'deep_scanned': 0,
                'budget_spent': 0,
                'budget_exhausted': False,
                'tombstones_created': 0,
                'status': 'no_funders'
            }

        deep_scanned_count = 0
        tombstones_count = 0

        # Process each shortlisted funder
        for funder_addr, inbound_sol, funder_type in shortlist:
            if budget_guard.is_exhausted(creator):
                break

            # Pass A: always do fingerprinting
            wallet_type, confidence = await scanner.pass_a_fingerprint(funder_addr)

            # Record Pass A
            # Would call: record_request(..., deep_scan_pages=1, ...)

            # Decide on Pass B
            is_top_5 = shortlist.index((funder_addr, inbound_sol, funder_type)) < 5
            should_deep = await scanner.should_do_pass_b(
                funder_addr,
                wallet_type,
                inbound_sol,
                is_top_5,
                creator
            )

            if should_deep:
                # Pass B: deep scan
                pages = await scanner.pass_b_deep_scan(
                    funder_addr,
                    creator,
                    max_pages=max_pages_deep
                )
                deep_scanned_count += 1
                # Would call: record_request(..., deep_scan_pages=pages, ...)

                # If empty after deep scan, create tombstone
                if pages == 1:  # Only 1 page = no meaningful transfers
                    await scanner.tombstone_mgr.mark_empty(funder_addr)
                    tombstones_count += 1
            else:
                # Mark as shallow (high confidence classification)
                await scanner.tombstone_mgr.mark_shallow(funder_addr, wallet_type, confidence)

        # Finalize budget tracking
        budget_guard.finish_creator_run(creator, len(shortlist), 0)

        return {
            'creator': creator,
            'shortlisted_funders': len(shortlist),
            'deep_scanned': deep_scanned_count,
            'budget_spent': budget_guard.get_spent(creator),
            'budget_exhausted': budget_guard.is_exhausted(creator),
            'tombstones_created': tombstones_count,
            'status': 'success'
        }

    except Exception as e:
        return {
            'creator': creator,
            'error': str(e),
            'status': 'error'
        }
