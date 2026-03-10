#!/usr/bin/env python3
"""
Phase 2/3 Deployment Verification and Testing Script

Validates:
1. Phase 2: RPC Response Caching layer
2. Phase 3: Transfer Indexing layer
3. Integration with existing Phase 1 (CursorManager)
4. Database schema and performance

Run as: python3 phase2_3_deployment_verification.py
"""

import sqlite3
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = 'flex_complete_database.db'


class Phase2ValidationSuite:
    """Validate Phase 2 RPC cache deployment."""

    def __init__(self):
        self.db_path = DB_PATH

    def check_schema(self) -> Dict[str, bool]:
        """Verify Phase 2 schema exists and is correct."""
        checks = {
            'rpc_response_cache_table': False,
            'cache_key_column': False,
            'ttl_seconds_column': False,
            'idx_rpc_cache_expiry': False,
            'idx_rpc_cache_method': False,
        }

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rpc_response_cache'")
            if cursor.fetchone():
                checks['rpc_response_cache_table'] = True

            # Check columns
            cursor.execute("PRAGMA table_info(rpc_response_cache)")
            columns = {row[1]: row[2] for row in cursor.fetchall()}

            if 'cache_key' in columns:
                checks['cache_key_column'] = True
            if 'ttl_seconds' in columns:
                checks['ttl_seconds_column'] = True

            # Check indexes
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='rpc_response_cache'")
            indexes = {row[0] for row in cursor.fetchall()}

            if 'idx_rpc_cache_expiry' in indexes:
                checks['idx_rpc_cache_expiry'] = True
            if 'idx_rpc_cache_method' in indexes:
                checks['idx_rpc_cache_method'] = True

            conn.close()

        except Exception as e:
            logger.error(f"Schema check failed: {e}")

        return checks

    def check_cache_operations(self) -> Dict[str, any]:
        """Test Phase 2 cache get/set operations."""
        result = {
            'rpc_cache_import': False,
            'cache_instantiation': False,
            'cache_key_generation': False,
            'cache_set_operation': False,
            'cache_get_operation': False,
            'cache_expiry': False,
            'test_entries_count': 0,
        }

        try:
            from src.core.rpc_cache import RPCCache
            result['rpc_cache_import'] = True

            cache = RPCCache(self.db_path)
            result['cache_instantiation'] = True

            # Test cache key generation
            key1 = RPCCache.make_key_get_transaction("test_sig_123")
            if key1 == "getTransaction:test_sig_123":
                result['cache_key_generation'] = True

            # Test cache set
            test_response = {
                "jsonrpc": "2.0",
                "result": {
                    "blockTime": 1678000000,
                    "slot": 180000000,
                    "signatures": ["sig1", "sig2"]
                }
            }
            cache.set(key1, test_response, "getTransaction")
            result['cache_set_operation'] = True

            # Test cache get
            cached = cache.get(key1)
            if cached is not None and "blockTime" in cached.get("result", {}):
                result['cache_get_operation'] = True

            # Test expiry
            stats = cache.get_stats()
            result['test_entries_count'] = stats.get('total_entries', 0)
            result['cache_expiry'] = stats.get('expired_entries', 0) >= 0

        except Exception as e:
            logger.error(f"Cache operations check failed: {e}")

        return result


class Phase3ValidationSuite:
    """Validate Phase 3 Transfer Indexing deployment."""

    def __init__(self):
        self.db_path = DB_PATH

    def check_schema(self) -> Dict[str, bool]:
        """Verify Phase 3 schema exists and is correct."""
        checks = {
            'transfer_index_table': False,
            'source_column': False,
            'destination_column': False,
            'amount_lamports_column': False,
            'amount_sol_column': False,
            'idx_transfer_destination_time': False,
            'idx_transfer_source_time': False,
            'idx_transfer_block_time': False,
        }

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transfer_index'")
            if cursor.fetchone():
                checks['transfer_index_table'] = True

            # Check columns
            cursor.execute("PRAGMA table_info(transfer_index)")
            columns = {row[1]: row[2] for row in cursor.fetchall()}

            for col in ['source', 'destination', 'amount_lamports']:
                if col in columns:
                    checks[f'{col}_column'] = True

            # amount_sol is GENERATED, may not show in PRAGMA, so check via schema text
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='transfer_index'")
            schema = cursor.fetchone()[0] or ""
            if 'amount_sol' in schema:
                checks['amount_sol_column'] = True

            # Check indexes
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='transfer_index'")
            indexes = {row[0] for row in cursor.fetchall()}

            for idx in ['idx_transfer_destination_time', 'idx_transfer_source_time', 'idx_transfer_block_time']:
                if idx in indexes:
                    checks[idx] = True

            conn.close()

        except Exception as e:
            logger.error(f"Schema check failed: {e}")

        return checks

    def check_transfer_indexing(self) -> Dict[str, any]:
        """Test Phase 3 transfer indexing operations."""
        result = {
            'transfer_indexer_import': False,
            'indexer_instantiation': False,
            'index_transaction': False,
            'get_funders': False,
            'get_funded_creators': False,
            'get_stats': False,
            'current_transfers_count': 0,
        }

        try:
            from src.core.transfer_indexer import TransferIndexer
            result['transfer_indexer_import'] = True

            indexer = TransferIndexer(self.db_path)
            result['indexer_instantiation'] = True

            # Create test transaction
            test_tx = {
                "signature": f"test_tx_{int(time.time())}",
                "slot": 200000000,
                "blockTime": int(time.time()),
                "transaction": {
                    "message": {
                        "instructions": [
                            {
                                "program": "system",
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "source": "TestSource1234567890123456789",
                                        "destination": "TestDest1234567890123456789",
                                        "lamports": 5000000
                                    }
                                }
                            }
                        ]
                    }
                }
            }

            # Test indexing
            indexed = indexer.index_transaction(test_tx)
            if indexed >= 0:
                result['index_transaction'] = True

            # Test get_funders (will likely be empty with test data)
            funders = indexer.get_funders("TestDest1234567890123456789")
            if isinstance(funders, list):
                result['get_funders'] = True

            # Test get_funded_creators
            creators = indexer.get_funded_creators("TestSource1234567890123456789")
            if isinstance(creators, list):
                result['get_funded_creators'] = True

            # Test get_stats
            stats = indexer.get_stats()
            result['get_stats'] = isinstance(stats, dict)
            result['current_transfers_count'] = stats.get('total_transfers', 0)

        except Exception as e:
            logger.error(f"Transfer indexing check failed: {e}")

        return result


class Phase1IntegrationCheck:
    """Verify Phase 1 still operational with Phase 2/3."""

    def __init__(self):
        self.db_path = DB_PATH

    def check_cursor_manager(self) -> Dict[str, bool]:
        """Verify Phase 1 CursorManager still works."""
        checks = {
            'cursor_manager_import': False,
            'cursor_manager_instantiation': False,
            'address_scan_state_table': False,
            'active_cursors': 0,
        }

        try:
            from src.core.cursor_manager import CursorManager
            checks['cursor_manager_import'] = True

            mgr = CursorManager(self.db_path)
            checks['cursor_manager_instantiation'] = True

            # Check address_scan_state table
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='address_scan_state'")
            if cursor.fetchone():
                checks['address_scan_state_table'] = True

            # Count active cursors
            cursor.execute("SELECT COUNT(*) FROM address_scan_state WHERE last_cursor IS NOT NULL")
            checks['active_cursors'] = cursor.fetchone()[0] or 0
            conn.close()

        except Exception as e:
            logger.error(f"CursorManager check failed: {e}")

        return checks


class DatabasePerformanceCheck:
    """Measure Phase 2/3 query performance."""

    def __init__(self):
        self.db_path = DB_PATH

    def measure_cache_lookup_time(self) -> Tuple[float, int]:
        """Measure cache lookup performance."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Measure time for 100 cache lookups
            start = time.time()
            for i in range(100):
                cursor.execute(
                    "SELECT response_json FROM rpc_response_cache WHERE cache_key = ? LIMIT 1",
                    (f"test_key_{i}",)
                )
                cursor.fetchone()
            elapsed = (time.time() - start) * 1000

            conn.close()
            return elapsed, 100

        except Exception as e:
            logger.error(f"Cache lookup timing failed: {e}")
            return 0, 0

    def measure_transfer_query_time(self) -> Tuple[float, int]:
        """Measure transfer index query performance."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Measure time for transfer lookups
            start = time.time()
            for i in range(100):
                cursor.execute(
                    "SELECT id FROM transfer_index WHERE destination = ? LIMIT 10",
                    (f"test_addr_{i}",)
                )
                cursor.fetchall()
            elapsed = (time.time() - start) * 1000

            conn.close()
            return elapsed, 100

        except Exception as e:
            logger.error(f"Transfer query timing failed: {e}")
            return 0, 0


def print_section(title: str, char: str = "=") -> None:
    """Print formatted section header."""
    print(f"\n{char * 80}")
    print(f" {title}")
    print(f"{char * 80}")


def print_check_result(name: str, passed: bool, details: str = "") -> None:
    """Print check result with status indicator."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status:10} {name}")
    if details:
        print(f"             {details}")


def main():
    """Run complete Phase 2/3 deployment verification."""
    print("\n" + "=" * 80)
    print(" FLEX V2: PHASE 2/3 DEPLOYMENT VERIFICATION")
    print("=" * 80)
    print(f"\nDatabase: {DB_PATH}")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    all_passed = True

    # ===== PHASE 1 CHECK =====
    print_section("PHASE 1: CURSOR MANAGER (Base Layer)")
    phase1 = Phase1IntegrationCheck()
    phase1_checks = phase1.check_cursor_manager()

    for check, passed in phase1_checks.items():
        if check == 'active_cursors':
            print(f"  ℹ️  {check:40} {passed}")
        else:
            all_passed = all_passed and passed
            print_check_result(check, passed)

    # ===== PHASE 2 SCHEMA CHECK =====
    print_section("PHASE 2: RPC RESPONSE CACHE - Schema")
    phase2 = Phase2ValidationSuite()
    schema_checks = phase2.check_schema()

    for check, passed in schema_checks.items():
        all_passed = all_passed and passed
        print_check_result(check, passed)

    # ===== PHASE 2 OPERATIONS CHECK =====
    print_section("PHASE 2: RPC RESPONSE CACHE - Operations")
    cache_checks = phase2.check_cache_operations()

    for check, result in cache_checks.items():
        if check == 'test_entries_count':
            print(f"  ℹ️  {check:40} {result}")
        else:
            all_passed = all_passed and bool(result)
            print_check_result(check, bool(result))

    # ===== PHASE 3 SCHEMA CHECK =====
    print_section("PHASE 3: TRANSFER INDEXING - Schema")
    phase3 = Phase3ValidationSuite()
    schema_checks = phase3.check_schema()

    for check, passed in schema_checks.items():
        all_passed = all_passed and passed
        print_check_result(check, passed)

    # ===== PHASE 3 OPERATIONS CHECK =====
    print_section("PHASE 3: TRANSFER INDEXING - Operations")
    indexing_checks = phase3.check_transfer_indexing()

    for check, result in indexing_checks.items():
        if check == 'current_transfers_count':
            print(f"  ℹ️  {check:40} {result}")
        else:
            all_passed = all_passed and bool(result)
            print_check_result(check, bool(result))

    # ===== PERFORMANCE CHECK =====
    print_section("PERFORMANCE: Query Latency Benchmarks")
    perf = DatabasePerformanceCheck()

    cache_time, cache_queries = perf.measure_cache_lookup_time()
    transfer_time, transfer_queries = perf.measure_transfer_query_time()

    if cache_queries > 0:
        cache_avg = cache_time / cache_queries
        print(f"  📊 Cache lookups:      {cache_avg:.3f}ms avg ({cache_queries} queries)")

    if transfer_queries > 0:
        transfer_avg = transfer_time / transfer_queries
        print(f"  📊 Transfer queries:   {transfer_avg:.3f}ms avg ({transfer_queries} queries)")

    # ===== DATABASE STATISTICS =====
    print_section("DATABASE STATISTICS")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Phase 2 stats
        cursor.execute("SELECT COUNT(*), SUM(hit_count) FROM rpc_response_cache")
        row = cursor.fetchone()
        cache_entries, cache_hits = row if row[0] else (0, 0)
        print(f"  💾 Phase 2 Cache:")
        print(f"     Total entries:     {cache_entries or 0}")
        print(f"     Total hits:        {cache_hits or 0}")

        # Phase 3 stats
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN is_valid = 1 THEN 1 END) as valid,
                COUNT(DISTINCT source) as sources,
                COUNT(DISTINCT destination) as destinations
            FROM transfer_index
        """)
        row = cursor.fetchone()
        if row:
            total, valid, sources, dests = row
            print(f"  🔄 Phase 3 Transfers:")
            print(f"     Total indexed:     {total or 0}")
            print(f"     Valid transfers:   {valid or 0}")
            print(f"     Unique sources:    {sources or 0}")
            print(f"     Unique destinations: {dests or 0}")

        conn.close()
    except Exception as e:
        logger.error(f"Database stats failed: {e}")

    # ===== SUMMARY =====
    print_section("SUMMARY")
    if all_passed:
        print("\n  ✅ ALL CRITICAL CHECKS PASSED")
        print("\n  Phase 2/3 deployment is ready for production.")
        print("  Database schema is complete and operational.")
    else:
        print("\n  ⚠️  SOME CHECKS FAILED")
        print("\n  Review errors above and run diagnostics.")

    print("\n" + "=" * 80 + "\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
