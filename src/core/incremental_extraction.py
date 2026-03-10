#!/usr/bin/env python3
"""
Incremental Extraction Utilities for FLEX V2.

Provides helper functions to integrate CursorManager into existing extraction code.
Enables cursor-based extraction in parallel with old code for safe validation.

Usage in realtime_creator_funding_extractor.py:
    from src.core.incremental_extraction import extract_with_cursor

    # Old way (full scan) - keeps working
    all_signatures = await rpc.get_signatures(creator)

    # New way (cursor-based) - runs in parallel for validation
    cursor_sigs = await extract_with_cursor(
        creator,
        rpc_client,
        cursor_mgr,
        db_path
    )

    # Compare results (should be identical)
    # Once validated for 1 week, switch to cursor-based extraction only
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from .cursor_manager import CursorManager

logger = logging.getLogger(__name__)


async def extract_with_cursor(
    address: str,
    rpc_client,
    cursor_mgr: CursorManager,
    db_path: str,
    max_signatures: int = 100,
    enable_activity_tracking: bool = True
) -> Dict[str, Any]:
    """
    Extract signatures for an address using cursor-based incremental fetching.

    This is a wrapper that:
    1. Loads cursor from DB (where we left off)
    2. Fetches only NEW signatures (not all)
    3. Updates cursor for next time
    4. Returns results in standardized format

    Args:
        address: Solana address to scan
        rpc_client: RPC client (Helius or public)
        cursor_mgr: CursorManager instance
        db_path: Database path for state tracking
        max_signatures: Number of signatures to fetch (default 100)
        enable_activity_tracking: Track activity for due-time scheduling

    Returns:
        Dict with:
        - 'address': The address scanned
        - 'new_signatures': List of new signatures found
        - 'signature_count': Count of new signatures
        - 'cursor_status': 'first_time' | 'incremental' | 'no_change'
        - 'next_scan_time': Recommended time for next scan
        - 'error': Optional error message
    """
    try:
        # Step 1: Load cursor
        cursor = cursor_mgr.get_cursor(address)

        if cursor is None:
            cursor_status = "first_time"
            before_sig = None
            logger.info(f"First-time scan for {address}")
        else:
            cursor_status = "incremental"
            before_sig = cursor.last_signature
            logger.debug(
                f"Resuming {address} from signature {before_sig[:8]}..."
            )

        # Step 2: Fetch signatures
        # When before_sig is set, RPC only returns signatures AFTER this one
        # (i.e., newer signatures not yet processed)
        try:
            signatures = await rpc_client.get_signatures(
                address=address,
                before=before_sig,
                limit=max_signatures
            )
        except Exception as e:
            logger.error(f"RPC error fetching signatures for {address}: {e}")
            return {
                'address': address,
                'error': str(e),
                'signature_count': 0,
                'cursor_status': 'error'
            }

        # Step 3: Check if we found any new signatures
        if not signatures:
            cursor_status = "no_change"
            logger.debug(f"No new signatures for {address}")

            # Update cursor with same signature (for timestamp update)
            if cursor and cursor.last_signature:
                cursor_mgr.update_cursor(
                    address,
                    cursor.last_signature,
                    activity_count=0
                )

            return {
                'address': address,
                'new_signatures': [],
                'signature_count': 0,
                'cursor_status': cursor_status,
                'next_scan_time': cursor.next_scan_at if cursor else None
            }

        # Step 4: Update cursor
        # Save the most recent signature for next time
        most_recent_sig = signatures[0].signature if hasattr(signatures[0], 'signature') else signatures[0]['signature']

        cursor_mgr.update_cursor(
            address,
            most_recent_sig,
            activity_count=len(signatures)
        )

        logger.info(
            f"Updated cursor for {address}: "
            f"found {len(signatures)} new signatures, "
            f"most recent={most_recent_sig[:8]}..."
        )

        # Step 5: Return results
        return {
            'address': address,
            'new_signatures': signatures,
            'signature_count': len(signatures),
            'cursor_status': cursor_status,
            'next_scan_time': _calculate_next_scan_time(len(signatures))
        }

    except Exception as e:
        logger.error(f"Unexpected error in extract_with_cursor: {e}")
        cursor_mgr.mark_failed(address, str(e))

        return {
            'address': address,
            'error': str(e),
            'signature_count': 0,
            'cursor_status': 'error'
        }


def _calculate_next_scan_time(activity_count: int) -> str:
    """Calculate human-readable next scan time based on activity."""
    now = datetime.utcnow()

    if activity_count == 0:
        next_time = now + __import__('datetime').timedelta(hours=24)
        return f"{next_time.isoformat()} (24h later)"
    elif activity_count <= 2:
        next_time = now + __import__('datetime').timedelta(hours=6)
        return f"{next_time.isoformat()} (6h later)"
    elif activity_count <= 5:
        next_time = now + __import__('datetime').timedelta(hours=1)
        return f"{next_time.isoformat()} (1h later)"
    else:
        next_time = now + __import__('datetime').timedelta(minutes=15)
        return f"{next_time.isoformat()} (15m later)"


def compare_extraction_results(
    old_result: Dict[str, Any],
    new_result: Dict[str, Any],
    address: str
) -> Dict[str, Any]:
    """
    Compare old (full scan) vs new (cursor-based) extraction results.

    Used during validation phase to ensure cursor implementation is correct.

    Args:
        old_result: Full scan results (old way)
        new_result: Cursor-based results (new way)
        address: Address being compared

    Returns:
        Comparison report with any discrepancies
    """
    old_count = old_result.get('signature_count', 0)
    new_count = new_result.get('signature_count', 0)

    # During first scan, cursor and old method should return same signatures
    # If cursor_status is 'incremental', new method should return < old method
    cursor_status = new_result.get('cursor_status', 'unknown')

    if cursor_status == 'first_time':
        # Both should be identical on first scan
        match = old_count == new_count
        recommendation = "PASS" if match else "FAIL"
    elif cursor_status == 'incremental':
        # New way should return fewer (only new ones)
        match = new_count <= old_count
        recommendation = "PASS" if match else "FAIL"
    else:
        recommendation = "SKIP"
        match = True

    return {
        'address': address,
        'old_count': old_count,
        'new_count': new_count,
        'cursor_status': cursor_status,
        'match': match,
        'recommendation': recommendation,
        'message': (
            f"Old: {old_count} sigs, New: {new_count} sigs, "
            f"Status: {cursor_status}, Match: {match}"
        )
    }


class CursorValidationRunner:
    """
    Validation utility to run old and new extraction in parallel.

    Usage:
        validator = CursorValidationRunner(cursor_mgr, rpc_client)
        results = await validator.validate_batch(creator_addresses)

        # Filter to problematic comparisons
        failures = [r for r in results if not r['match']]
    """

    def __init__(self, cursor_mgr: CursorManager, rpc_client):
        self.cursor_mgr = cursor_mgr
        self.rpc = rpc_client
        self.comparisons = []

    async def validate_address(self, address: str) -> Dict[str, Any]:
        """
        Validate cursor extraction for a single address.

        Runs both old and new methods, compares results.
        """
        try:
            # Old way: full scan
            old_sigs = await self.rpc.get_signatures(address=address, limit=100)
            old_result = {'signature_count': len(old_sigs) if old_sigs else 0}

            # New way: cursor-based
            new_result = await extract_with_cursor(
                address,
                self.rpc,
                self.cursor_mgr,
                max_signatures=100
            )

            # Compare
            comparison = compare_extraction_results(old_result, new_result, address)
            self.comparisons.append(comparison)

            return comparison

        except Exception as e:
            logger.error(f"Validation error for {address}: {e}")
            return {
                'address': address,
                'error': str(e),
                'recommendation': 'SKIP'
            }

    async def validate_batch(self, addresses: List[str], batch_size: int = 5) -> List[Dict]:
        """
        Validate multiple addresses in parallel batches.

        Args:
            addresses: List of addresses to validate
            batch_size: Number of addresses to process in parallel

        Returns:
            List of comparison results
        """
        import asyncio

        results = []

        for i in range(0, len(addresses), batch_size):
            batch = addresses[i:i + batch_size]
            batch_results = await asyncio.gather(
                *[self.validate_address(addr) for addr in batch],
                return_exceptions=True
            )
            results.extend(batch_results)

        return results

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all validations."""
        passes = sum(1 for c in self.comparisons if c.get('match'))
        fails = sum(1 for c in self.comparisons if not c.get('match'))
        skips = sum(1 for c in self.comparisons if c.get('recommendation') == 'SKIP')

        return {
            'total': len(self.comparisons),
            'passes': passes,
            'fails': fails,
            'skips': skips,
            'pass_rate': (passes / len(self.comparisons) * 100) if self.comparisons else 0,
            'ready_to_switch': fails == 0 and passes > 0
        }
