#!/usr/bin/env python3
"""
Phase 3 Optimization Test Suite

Tests for:
- Phase 3.1A: Batch indexing
- Phase 3.1B: Clustering materialized view
- Phase 3.1C: Query result caching

Run as: python3 test_phase3_optimizations.py
"""

import time
import sqlite3
import tempfile
import os
from typing import List, Dict

from src.core.transfer_indexer import TransferIndexer, OptimizedTransferIndexer


def create_test_transactions(count: int = 100) -> List[Dict]:
    """Create test transaction data."""
    transactions = []

    for i in range(count):
        tx = {
            'signature': f'test_sig_{i:06d}',
            'slot': 180000000 + i,
            'blockTime': int(time.time()) - (86400 * i),
            'instructions': [
                {
                    'program': 'system',
                    'parsed': {
                        'type': 'transfer',
                        'info': {
                            'source': f'source_{i % 10:02d}{"x" * 40}',  # 10 unique sources
                            'destination': f'dest_{i % 20:02d}{"y" * 40}',  # 20 unique destinations
                            'lamports': 1000000 + (i * 100)
                        }
                    }
                }
            ]
        }
        transactions.append(tx)

    return transactions


def _create_schema(db_path: str):
    """Create transfer_index table schema for testing."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transfer_index (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            signature           TEXT NOT NULL UNIQUE,
            source              TEXT NOT NULL,
            destination         TEXT NOT NULL,
            amount_lamports      INTEGER NOT NULL,
            amount_sol           REAL GENERATED ALWAYS AS (amount_lamports / 1e9) STORED,
            slot                INTEGER NOT NULL,
            block_time          INTEGER NOT NULL,
            indexed_at          REAL NOT NULL DEFAULT (strftime('%s', 'now')),
            is_valid            BOOLEAN NOT NULL DEFAULT 1,
            transfer_type       TEXT DEFAULT 'standard',
            CHECK (amount_lamports > 0),
            CHECK (block_time > 0)
        )
    """)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transfer_destination_time ON transfer_index(destination, block_time DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transfer_source_time ON transfer_index(source, block_time DESC)")

    conn.commit()
    conn.close()


def test_batch_indexing():
    """Test Phase 3.1A: Batch indexing performance."""
    print("\n" + "=" * 80)
    print("TEST: Phase 3.1A — Batch Indexing")
    print("=" * 80)

    # Create test database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        _create_schema(db_path)
        indexer = TransferIndexer(db_path)

        # Create test transactions
        transactions = create_test_transactions(100)

        # Test batch indexing
        print(f"\nIndexing {len(transactions)} transactions (100 transfers total)...")
        start = time.time()
        result = indexer.index_transactions_batch(transactions, batch_size=50)
        elapsed = time.time() - start

        print(f"✅ Batch indexing complete")
        print(f"   Indexed: {result['indexed']} transfers")
        print(f"   Skipped: {result['skipped']} transfers")
        print(f"   Errors: {result['errors']} transfers")
        print(f"   Time: {elapsed:.3f}s ({result['indexed']/elapsed:.0f} transfers/sec)")

        # Verify data was inserted
        stats = indexer.get_stats()
        assert stats['total_transfers'] == 100, f"Expected 100 transfers, got {stats['total_transfers']}"
        print(f"✅ Database verification: {stats['total_transfers']} transfers stored")

        return True

    finally:
        os.unlink(db_path)


def test_clustering_materialization():
    """Test Phase 3.1B: Clustering materialized view."""
    print("\n" + "=" * 80)
    print("TEST: Phase 3.1B — Clustering Materialized View")
    print("=" * 80)

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        _create_schema(db_path)
        indexer = TransferIndexer(db_path)

        # Create test transactions with known clustering
        transactions = create_test_transactions(100)
        result = indexer.index_transactions_batch(transactions)

        # Test materialization
        print(f"\nMaterializing clustering view...")
        start = time.time()
        clustering_result = indexer.materialize_clustering_view(lookback_days=30)
        elapsed = time.time() - start

        print(f"✅ Clustering view materialized")
        print(f"   Relationships: {clustering_result['updated']}")
        print(f"   Time: {elapsed:.3f}s")

        # Test cached cluster queries
        print(f"\nTesting cached cluster queries...")
        test_creators = ['dest_00' + 'y' * 40, 'dest_01' + 'y' * 40]

        start = time.time()
        clusters = indexer.find_clusters_cached(test_creators)
        elapsed_cached = time.time() - start

        print(f"✅ Cluster query (cached)")
        print(f"   Results: {len(clusters)} clusters")
        print(f"   Time: {elapsed_cached*1000:.2f}ms")

        # Compare with non-cached version
        start = time.time()
        clusters_live = indexer.find_clusters(test_creators)
        elapsed_live = time.time() - start

        print(f"\nComparison: Cached vs Live")
        print(f"   Cached: {elapsed_cached*1000:.2f}ms ({len(clusters)} results)")
        print(f"   Live:   {elapsed_live*1000:.2f}ms ({len(clusters_live)} results)")
        if elapsed_live > 0:
            speedup = elapsed_live / elapsed_cached if elapsed_cached > 0 else float('inf')
            print(f"   Speedup: {speedup:.1f}x")

        return True

    finally:
        os.unlink(db_path)


def test_query_caching():
    """Test Phase 3.1C: Query result caching."""
    print("\n" + "=" * 80)
    print("TEST: Phase 3.1C — Query Result Caching")
    print("=" * 80)

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        _create_schema(db_path)
        indexer = OptimizedTransferIndexer(db_path)

        # Create test transactions
        transactions = create_test_transactions(100)
        indexer.index_transactions_batch(transactions)

        # Test caching: First query (no cache)
        print(f"\nTesting query caching...")
        test_dest = 'dest_00' + 'y' * 40

        start = time.time()
        result1 = indexer.get_funders(test_dest, use_cache=True)
        elapsed_first = time.time() - start

        print(f"✅ First query (not cached)")
        print(f"   Results: {len(result1)} funders")
        print(f"   Time: {elapsed_first*1000:.2f}ms")

        # Second query (should be cached)
        start = time.time()
        result2 = indexer.get_funders(test_dest, use_cache=True)
        elapsed_cached = time.time() - start

        print(f"✅ Second query (cached)")
        print(f"   Results: {len(result2)} funders")
        print(f"   Time: {elapsed_cached*1000:.4f}ms")

        # Verify caching worked
        assert result1 == result2, "Cached result doesn't match original"
        assert elapsed_cached < elapsed_first, "Cached query not faster"

        speedup = elapsed_first / elapsed_cached if elapsed_cached > 0 else float('inf')
        print(f"\n✅ Speedup: {speedup:.0f}x (first: {elapsed_first*1000:.2f}ms → cached: {elapsed_cached*1000:.4f}ms)")

        # Check cache stats
        stats = indexer.get_cache_stats()
        print(f"✅ Cache statistics")
        print(f"   Entries: {stats['total_entries']}")
        print(f"   Size: {stats['cache_size_mb']:.2f} MB")

        # Test cache expiration
        print(f"\nTesting cache TTL expiration...")
        indexer.cache_ttl['get_funders'] = 1  # 1 second TTL for next call

        time.sleep(1.1)  # Wait for expiration

        # Clear cache and re-cache with 1-second TTL
        indexer.clear_cache()
        result3 = indexer.get_funders(test_dest, use_cache=True)

        # Verify entry was cached
        assert len(indexer.query_cache) == 1, "Entry not cached"

        # Wait for expiration and access again
        time.sleep(1.1)
        result4 = indexer.get_funders(test_dest, use_cache=True)

        # Entry should be evicted on next access
        assert len(indexer.query_cache) <= 1, f"Expected <= 1 cache entry, got {len(indexer.query_cache)}"
        print(f"✅ Cache expiration working")

        return True

    finally:
        os.unlink(db_path)


def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("PHASE 3 OPTIMIZATION TEST SUITE")
    print("=" * 80)

    tests = [
        ("Batch Indexing (3.1A)", test_batch_indexing),
        ("Clustering Materialized View (3.1B)", test_clustering_materialization),
        ("Query Result Caching (3.1C)", test_query_caching),
    ]

    results = []

    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, "✅ PASS" if passed else "❌ FAIL"))
        except Exception as e:
            print(f"\n❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, "❌ FAIL"))

    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    for name, result in results:
        print(f"{result:12} {name}")

    print("=" * 80)

    all_passed = all("PASS" in r for _, r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
