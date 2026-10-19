#!/usr/bin/env python3
"""
Phase 3 Integration Module

Shows how to integrate TransferIndexer into existing Phase 1/2 extraction pipeline.

This module demonstrates:
1. Wrapping existing extraction to index transfers
2. Replacing RPC-based queries with SQL queries
3. Running parallel RPC + SQL for validation
4. Metrics tracking for Phase 3 rollout

Usage:
    # In realtime_creator_funding_extractor.py:
    from src.core.phase3_integration import Phase3ExtractorWrapper

    extractor = RealTimeCreatorFundingExtractor()
    phase3 = Phase3ExtractorWrapper(extractor)

    # Use phase3.extract_for_creator() instead of extractor.extract_for_creator()
    # It wraps the result and indexes transfers automatically
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from src.core.transfer_indexer import TransferIndexer
from src.metrics.rpc_metrics_recorder import record_request

logger = logging.getLogger(__name__)

DB_PATH = 'flex_complete_database.db'


class Phase3ExtractorWrapper:
    """
    Wraps existing Phase 1/2 extractor to add Phase 3 transfer indexing.

    This is a non-invasive wrapper that:
    1. Calls existing extraction logic
    2. Indexes transfers from parsed transactions
    3. Tracks metrics for Phase 3 impact
    4. Enables gradual migration from RPC to SQL queries
    """

    def __init__(self, extractor):
        """
        Initialize Phase 3 wrapper.

        Args:
            extractor: RealTimeCreatorFundingExtractor instance
        """
        self.extractor = extractor
        self.transfer_indexer = TransferIndexer(DB_PATH)
        self.metrics = {
            'transactions_processed': 0,
            'transfers_indexed': 0,
            'indexing_errors': 0,
            'phase3_queries_made': 0,
            'rpc_calls_saved': 0,
        }

    async def extract_for_creator(self, creator: str) -> Dict:
        """
        Extract creator funding, then index transfers.

        Wraps existing extraction and adds Phase 3 transfer indexing.
        """
        try:
            # Step 1: Call existing Phase 1/2 extraction
            result = await self.extractor.extract_for_creator(creator)

            # Step 2: Index transfers from processed transactions
            if result and result.get('status') != 'cached':
                transfers_indexed = await self._index_transfers_from_result(result)
                self.metrics['transfers_indexed'] += transfers_indexed

            self.metrics['transactions_processed'] += 1

            return result

        except Exception as e:
            logger.error(f"[PHASE3] Extraction failed for {creator}: {e}")
            self.metrics['indexing_errors'] += 1
            return {'status': 'error', 'error': str(e)}

    async def _index_transfers_from_result(self, extraction_result: Dict) -> int:
        """
        Index transfers from extraction result.

        Extracts transaction data from result and indexes transfers.
        """
        indexed_count = 0

        try:
            # Get list of transactions that were processed
            transfers = extraction_result.get('transfers', [])

            # If result has raw transactions, extract and index
            if 'transactions' in extraction_result:
                for tx in extraction_result['transactions']:
                    indexed_count += self.transfer_indexer.index_transaction(tx)

            return indexed_count

        except Exception as e:
            logger.warning(f"[PHASE3] Failed to index transfers: {e}")
            self.metrics['indexing_errors'] += 1
            return 0

    # ===== PHASE 3: SQL-BASED QUERIES (Replace RPC) =====

    async def get_creator_funders_sql(self, creator: str) -> List[str]:
        """
        Get creator's funders using transfer_index (Phase 3).

        This replaces Phase 2's RPC-based approach:
        - Phase 2: getSignaturesForAddress + getTransaction × N [1000+ credits]
        - Phase 3: Single SQL query [0 credits]

        Returns list of source addresses that funded creator.
        """
        try:
            funders = self.transfer_indexer.get_funders(creator)

            # Track metrics
            self.metrics['phase3_queries_made'] += 1
            self.metrics['rpc_calls_saved'] += 10  # Saved at least 1 RPC call

            logger.info(f"[PHASE3] Found {len(funders)} funders for {creator} via SQL")

            return funders

        except Exception as e:
            logger.error(f"[PHASE3] get_creator_funders_sql failed: {e}")
            return []

    async def get_funder_activity_sql(self, funder: str, min_amount_sol: float = 0) -> List[Tuple]:
        """
        Get all creators funded by a wallet using transfer_index (Phase 3).

        Returns list of (creator, num_transfers, total_sol) tuples.
        """
        try:
            activity = self.transfer_indexer.get_funded_creators(funder, min_amount_sol=min_amount_sol)

            self.metrics['phase3_queries_made'] += 1
            self.metrics['rpc_calls_saved'] += len(activity) * 10  # Saved RPC per funder

            logger.info(f"[PHASE3] Found {len(activity)} creators funded by {funder}")

            return activity

        except Exception as e:
            logger.error(f"[PHASE3] get_funder_activity_sql failed: {e}")
            return []

    # ===== VALIDATION: PARALLEL RPC + SQL =====

    async def validate_extraction_parallel(
        self,
        creator: str,
        use_rpc: bool = True,
        use_sql: bool = True
    ) -> Dict:
        """
        Run extraction using both RPC (Phase 2) and SQL (Phase 3) in parallel.

        Compares results to validate Phase 3 correctness before full rollout.

        Returns:
        {
            'rpc_funders': [...],
            'sql_funders': [...],
            'match': bool,
            'rpc_time_ms': float,
            'sql_time_ms': float,
            'rpc_cost': int (estimated credits)
        }
        """
        import time

        results = {
            'creator': creator,
            'rpc_funders': [],
            'sql_funders': [],
            'match': False,
            'rpc_time_ms': 0,
            'sql_time_ms': 0,
            'rpc_cost': 0,
        }

        # Get RPC-based result (Phase 2)
        if use_rpc:
            try:
                start = time.time()

                # Call existing Phase 1/2 extraction
                extraction_result = await self.extractor.extract_for_creator(creator)

                rpc_time = (time.time() - start) * 1000
                results['rpc_time_ms'] = rpc_time

                # Extract funders from transactions
                funders_from_txs = set()
                if extraction_result and 'transfers' in extraction_result:
                    for transfer_list in extraction_result['transfers'].values():
                        for transfer in transfer_list:
                            funders_from_txs.add(transfer.get('source'))

                results['rpc_funders'] = list(funders_from_txs)
                results['rpc_cost'] = len(results['rpc_funders']) * 10  # Estimate

            except Exception as e:
                logger.error(f"[PHASE3] RPC validation failed: {e}")
                results['rpc_error'] = str(e)

        # Get SQL-based result (Phase 3)
        if use_sql:
            try:
                start = time.time()

                sql_funders = self.transfer_indexer.get_funders(creator)

                sql_time = (time.time() - start) * 1000
                results['sql_time_ms'] = sql_time
                results['sql_funders'] = sql_funders

            except Exception as e:
                logger.error(f"[PHASE3] SQL validation failed: {e}")
                results['sql_error'] = str(e)

        # Compare results
        if results['rpc_funders'] and results['sql_funders']:
            rpc_set = set(results['rpc_funders'])
            sql_set = set(results['sql_funders'])

            # Check if results match
            results['match'] = rpc_set == sql_set
            results['overlap_count'] = len(rpc_set & sql_set)
            results['rpc_only'] = list(rpc_set - sql_set)
            results['sql_only'] = list(sql_set - rpc_set)

            # Log validation result
            if results['match']:
                logger.info(f"[PHASE3] ✅ Validation PASSED for {creator}")
                logger.info(f"  RPC time: {results['rpc_time_ms']:.1f}ms")
                logger.info(f"  SQL time: {results['sql_time_ms']:.1f}ms")
                logger.info(f"  Speedup: {results['rpc_time_ms'] / max(results['sql_time_ms'], 1):.0f}x")
            else:
                logger.warning(f"[PHASE3] ⚠️ Validation MISMATCH for {creator}")
                logger.warning(f"  RPC found: {len(results['rpc_funders'])}")
                logger.warning(f"  SQL found: {len(results['sql_funders'])}")
                if results['rpc_only']:
                    logger.warning(f"  RPC only: {results['rpc_only'][:5]}")
                if results['sql_only']:
                    logger.warning(f"  SQL only: {results['sql_only'][:5]}")

        return results

    # ===== MONITORING =====

    def get_phase3_status(self) -> Dict:
        """Get Phase 3 deployment status and metrics."""
        stats = self.transfer_indexer.get_stats()

        return {
            'transfer_index_stats': stats,
            'metrics': self.metrics,
            'phase3_ready': stats.get('total_transfers', 0) > 100,
            'estimated_rpc_calls_saved': self.metrics['rpc_calls_saved'],
            'estimated_credits_saved': self.metrics['rpc_calls_saved'],
        }

    def print_status(self) -> None:
        """Print Phase 3 status to console."""
        status = self.get_phase3_status()
        stats = status['transfer_index_stats']

        print("\n" + "=" * 100)
        print("PHASE 3: TRANSFER INDEXING STATUS")
        print("=" * 100)
        print(f"Transfers indexed: {stats.get('total_transfers', 0):,}")
        print(f"Valid transfers: {stats.get('valid_transfers', 0):,}")
        print(f"Storage (MB): {stats.get('approx_size_mb', 0):.1f}")
        print(f"Ingestion rate: {stats.get('avg_transfers_per_day', 0):.0f} per day")
        print(f"Latest block time: {stats.get('latest_block_time', 0)}")
        print()
        print(f"Phase 3 queries made: {self.metrics['phase3_queries_made']}")
        print(f"Estimated RPC calls saved: {self.metrics['rpc_calls_saved']}")
        print(f"Estimated credits saved: {self.metrics['rpc_calls_saved']}")
        print()
        print("=" * 100 + "\n")


class Phase3ValidationRunner:
    """
    Validation suite for Phase 3 rollout.

    Tests Phase 3 correctness by comparing RPC vs SQL results
    across a sample of creators.
    """

    def __init__(self, extractor_wrapper: Phase3ExtractorWrapper):
        """Initialize validation runner."""
        self.wrapper = extractor_wrapper
        self.results = []

    async def validate_batch(self, creator_list: List[str]) -> Dict:
        """
        Validate Phase 3 across a batch of creators.

        Returns summary of validation results.
        """
        print(f"\n[PHASE3] Starting validation of {len(creator_list)} creators...")

        validated = 0
        passed = 0
        failed = 0

        for creator in creator_list:
            result = await self.wrapper.validate_extraction_parallel(creator)
            self.results.append(result)

            if result.get('match'):
                passed += 1
            else:
                failed += 1

            validated += 1

            if validated % 100 == 0:
                print(f"[PHASE3] Validated {validated}/{len(creator_list)} creators...")

        summary = {
            'total_validated': validated,
            'passed': passed,
            'failed': failed,
            'pass_rate': (passed / max(validated, 1)) * 100,
            'validation_results': self.results,
        }

        print(f"\n[PHASE3] Validation complete:")
        print(f"  Passed: {passed}/{validated} ({summary['pass_rate']:.1f}%)")
        print(f"  Failed: {failed}/{validated}")

        return summary


# ===== EXAMPLE INTEGRATION CODE =====

async def phase3_example():
    """
    Example: How to use Phase 3 integration in existing code.
    """
    from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor

    # Create extractor
    extractor = RealTimeCreatorFundingExtractor()
    await extractor.init_session()

    # Wrap with Phase 3
    phase3 = Phase3ExtractorWrapper(extractor)

    # Use Phase 3-enabled extraction
    creator = 'example_creator_address'

    # This now indexes transfers automatically
    result = await phase3.extract_for_creator(creator)

    # Also available: SQL-based queries (no RPC calls)
    funders = await phase3.get_creator_funders_sql(creator)
    print(f"Funders (SQL): {funders}")

    # Validation: compare RPC vs SQL
    validation = await phase3.validate_extraction_parallel(creator)
    print(f"Validation: {validation['match']}")

    # Status
    phase3.print_status()


if __name__ == "__main__":
    # Run example
    import sys
    sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

    # asyncio.run(phase3_example())
    print("Phase 3 Integration Module - Ready to integrate")
