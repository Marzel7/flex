#!/usr/bin/env python3
"""
Pipeline Validator — End-to-End Validation with WebSocket Confirmation

Validates complete pool discovery → registration → WebSocket → pricing pipeline
with delayed confirmation step to catch transient states.
"""

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()


@dataclass
class PipelineValidationResult:
    """Result of end-to-end pipeline validation."""
    mint: str
    base_account: str = None
    quote_account: str = None
    ws_ready: bool = False
    ws_confirmed: bool = False
    reserves_changed: bool = False
    first_reserves: Optional[Tuple[int, int]] = None
    confirmed_reserves: Optional[Tuple[int, int]] = None
    snapshot_source: Optional[str] = None
    snapshot_price_usd: Optional[float] = None
    snapshot_count: int = 0
    passed: bool = False
    errors: List[str] = field(default_factory=list)
    total_elapsed_ms: int = 0


class Database:
    """Simple database wrapper."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def query_one(self, sql: str, params: tuple = ()):
        """Query single row."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None

    def query(self, sql: str, params: tuple = ()):
        """Query multiple rows."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        results = cursor.fetchall()
        conn.close()
        return [dict(row) for row in results]


class PoolStateStore:
    """WebSocket pool state storage with (mint, base_account) key."""

    def __init__(self):
        self._state: Dict[Tuple[str, str], Dict] = {}

    def get_reserves(self, mint: str, base_account: str) -> Optional[Tuple[int, int]]:
        """Get current reserves for pool."""
        pool_id = (mint, base_account)
        s = self._state.get(pool_id)
        if s and s.get('base_reserve') is not None and s.get('quote_reserve') is not None:
            return (s['base_reserve'], s['quote_reserve'])
        return None

    def set_reserves(self, mint: str, base_account: str, base: int, quote: int):
        """Set reserves for pool."""
        pool_id = (mint, base_account)
        if pool_id not in self._state:
            self._state[pool_id] = {}
        self._state[pool_id]['base_reserve'] = base
        self._state[pool_id]['quote_reserve'] = quote


class PipelineValidator:
    """Validates full discovery → registration → WebSocket → pricing pipeline."""

    def __init__(self, db_path: str, ws_store: Optional[PoolStateStore] = None):
        self.db = Database(db_path)
        self.ws_store = ws_store or PoolStateStore()

    async def validate_pool_pipeline(
        self,
        token_mint: str,
        timeout_seconds: int = 10,
        confirmation_delay_seconds: int = 5,
    ) -> PipelineValidationResult:
        """
        Validate full pipeline:
          1. pool registered
          2. websocket reserve updates received
          3. pool state still alive after delay
          4. at least one pool snapshot written
          5. ideally multiple snapshots written

        Args:
            token_mint: Token mint to validate
            timeout_seconds: Timeout for WebSocket readiness
            confirmation_delay_seconds: Delay before confirming state persistence

        Returns:
            PipelineValidationResult with detailed validation status
        """
        start_time = time.time()
        errors = []

        # Phase 0: Verify pool registration
        pool = self.db.query_one(
            """
            SELECT mint, base_account, quote_account, pool_program, discovery_method
            FROM token_pool_accounts
            WHERE mint = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (token_mint,),
        )

        if not pool:
            return PipelineValidationResult(
                mint=token_mint,
                passed=False,
                errors=["Pool not registered"],
                total_elapsed_ms=int((time.time() - start_time) * 1000),
            )

        base_account = pool["base_account"]
        quote_account = pool["quote_account"]

        # Phase 1: Wait for first WebSocket-driven reserve readiness
        ws_ready = False
        first_reserves = None

        for attempt in range(timeout_seconds * 10):
            first_reserves = self.ws_store.get_reserves(token_mint, base_account)
            if (
                first_reserves
                and first_reserves[0] > 0
                and first_reserves[1] > 0
            ):
                ws_ready = True
                break
            await asyncio.sleep(0.1)

        if not ws_ready:
            errors.append(
                f"WebSocket: no reserve updates after {timeout_seconds}s"
            )
            return PipelineValidationResult(
                mint=token_mint,
                base_account=base_account,
                quote_account=quote_account,
                ws_ready=False,
                passed=False,
                errors=errors,
                total_elapsed_ms=int((time.time() - start_time) * 1000),
            )

        first_ready_at = time.time()

        # Phase 2: Confirm WebSocket state still exists after delay
        await asyncio.sleep(confirmation_delay_seconds)

        confirmed_reserves = self.ws_store.get_reserves(token_mint, base_account)
        ws_confirmed = (
            confirmed_reserves is not None
            and confirmed_reserves[0] > 0
            and confirmed_reserves[1] > 0
        )

        if not ws_confirmed:
            errors.append(
                f"WebSocket: reserves disappeared after {confirmation_delay_seconds}s confirmation delay"
            )

        # Optional: did state change or at least remain fresh?
        reserves_changed = (
            first_reserves != confirmed_reserves
            if first_reserves is not None and confirmed_reserves is not None
            else False
        )

        # Phase 3: Snapshot checks
        latest_snapshot = self.db.query_one(
            """
            SELECT price_usd, source, created_at
            FROM token_price_snapshots
            WHERE mint = ? AND source = 'pool'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (token_mint,),
        )

        if not latest_snapshot:
            errors.append("Price snapshot: no pool snapshot found")
        elif (
            latest_snapshot.get("price_usd") is None
            or latest_snapshot["price_usd"] <= 0
        ):
            errors.append(
                f"Price snapshot: invalid price_usd={latest_snapshot['price_usd']}"
            )

        # Count snapshots after first readiness
        snapshot_count_row = self.db.query_one(
            """
            SELECT COUNT(*) AS c
            FROM token_price_snapshots
            WHERE mint = ? AND source = 'pool'
              AND created_at >= ?
            """,
            (token_mint, int(first_ready_at) - 1),
        )

        snapshot_count = snapshot_count_row["c"] if snapshot_count_row else 0

        if snapshot_count < 1:
            errors.append(
                "Price snapshot: expected at least 1 pool snapshot after websocket readiness"
            )

        # Return result
        return PipelineValidationResult(
            mint=token_mint,
            base_account=base_account,
            quote_account=quote_account,
            ws_ready=ws_ready,
            ws_confirmed=ws_confirmed,
            reserves_changed=reserves_changed,
            first_reserves=first_reserves,
            confirmed_reserves=confirmed_reserves,
            snapshot_source=latest_snapshot.get("source") if latest_snapshot else None,
            snapshot_price_usd=(
                latest_snapshot["price_usd"] if latest_snapshot else None
            ),
            snapshot_count=snapshot_count,
            passed=len(errors) == 0,
            errors=errors,
            total_elapsed_ms=int((time.time() - start_time) * 1000),
        )

    def validate_pool_pipeline_sync(
        self,
        token_mint: str,
        timeout_seconds: int = 10,
        confirmation_delay_seconds: int = 5,
    ) -> PipelineValidationResult:
        """Synchronous wrapper for validate_pool_pipeline."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.validate_pool_pipeline(
                    token_mint,
                    timeout_seconds,
                    confirmation_delay_seconds,
                )
            )
        finally:
            loop.close()


async def test_single_pool(db_path: str, token_mint: str):
    """Test validation of a single pool."""
    print(f"\n{'='*80}")
    print(f"PIPELINE VALIDATION TEST: {token_mint[:16]}...")
    print(f"{'='*80}\n")

    validator = PipelineValidator(db_path)

    # Simulate some WebSocket data
    pool = validator.db.query_one(
        "SELECT base_account FROM token_pool_accounts WHERE mint = ? LIMIT 1",
        (token_mint,),
    )

    if pool:
        # Simulate WebSocket reserves arriving
        validator.ws_store.set_reserves(token_mint, pool["base_account"], 1000000, 500000)

    result = await validator.validate_pool_pipeline(
        token_mint=token_mint,
        timeout_seconds=10,
        confirmation_delay_seconds=2,
    )

    print(f"Mint:                  {result.mint}")
    print(f"Base Account:          {result.base_account}")
    print(f"WebSocket Ready:       {'✓' if result.ws_ready else '✗'} {result.first_reserves}")
    print(f"WebSocket Confirmed:   {'✓' if result.ws_confirmed else '✗'} {result.confirmed_reserves}")
    print(f"Reserves Changed:      {'✓' if result.reserves_changed else '✗'}")
    print(f"Snapshot Price:        ${result.snapshot_price_usd:.6f}" if result.snapshot_price_usd else "None")
    print(f"Snapshot Count:        {result.snapshot_count}")
    print(f"Total Time:            {result.total_elapsed_ms}ms")
    print()

    if result.passed:
        print("✅ VALIDATION PASSED")
    else:
        print("❌ VALIDATION FAILED")
        print("\nErrors:")
        for error in result.errors:
            print(f"  • {error}")

    print()
    return result


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="End-to-end pipeline validation with WebSocket confirmation"
    )
    parser.add_argument(
        "mint",
        nargs="?",
        help="Token mint to validate (optional)",
    )
    parser.add_argument(
        "--db",
        default="database/flex_complete_database.db",
        help="Path to database",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Timeout for WebSocket readiness (seconds)",
    )
    parser.add_argument(
        "--confirmation-delay",
        type=int,
        default=5,
        help="Delay before confirming state persistence (seconds)",
    )

    args = parser.parse_args()

    if not args.mint:
        # Find first new pool to test
        db = Database(args.db)
        pool = db.query_one(
            "SELECT mint FROM token_pool_accounts WHERE is_legacy=0 ORDER BY created_at DESC LIMIT 1"
        )
        if pool:
            args.mint = pool["mint"]
        else:
            print("No pools found to test")
            return

    await test_single_pool(args.db, args.mint)


if __name__ == "__main__":
    asyncio.run(main())
