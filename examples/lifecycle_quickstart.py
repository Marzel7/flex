#!/usr/bin/env python3
"""
Token Lifecycle System - Quick Start Example

Shows how to integrate and use the lifecycle system with minimal code.

Run this to:
1. Initialize tables
2. Populate with sample data
3. Classify tokens
4. Run analytics
"""

import os
import sys
import time
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.token_lifecycle import (
    TokenLifecycleManager,
    TokenSnapshot,
    LifecycleMonitoringWorker,
    LifecycleConfig
)
from src.core.lifecycle_analytics import LifecycleAnalytics

# =============================================================================
# CONFIGURATION
# =============================================================================

DB_PATH = os.environ.get('DB_PATH', 'database/flex_complete_database.db')

# Sample tokens for demonstration
SAMPLE_TOKENS = [
    # Fast rug
    {
        'mint': 'rug_fast_001',
        'cluster_id': 'cluster_1',
        'creator': 'creator_1',
        'price_points': [
            (0, 0.00001),      # Start
            (60, 0.00005),     # Small rise
            (120, 0.00010),    # Peak at 2 min
            (180, 0.00001),    # Collapse
            (240, 0.000001),   # Rug
        ]
    },
    # Successful token
    {
        'mint': 'success_001',
        'cluster_id': 'cluster_good',
        'creator': 'creator_good',
        'price_points': [
            (0, 0.001),
            (300, 0.005),      # Peak at 5 min
            (600, 0.004),      # Slight decline
            (1800, 0.003),     # Stable
            (3600, 0.0035),    # Holding value
        ]
    },
    # Slow rug
    {
        'mint': 'slowrug_001',
        'cluster_id': 'cluster_bad',
        'creator': 'creator_bad',
        'price_points': [
            (0, 0.001),
            (600, 0.01),       # Strong growth
            (1200, 0.015),     # Peak
            (2400, 0.005),     # Decline
            (3600, 0.001),     # Further down
            (5400, 0.0001),    # Slow decay
        ]
    },
]


# =============================================================================
# EXAMPLE 1: Initialize and Set Up
# =============================================================================

def example_1_initialize():
    """Initialize the lifecycle system"""
    print("\n" + "="*70)
    print("EXAMPLE 1: Initialize Lifecycle System")
    print("="*70)

    manager = TokenLifecycleManager(DB_PATH)
    print("✅ Tables created (if they didn't exist)")
    print(f"   Database: {DB_PATH}")
    return manager


# =============================================================================
# EXAMPLE 2: Start Monitoring Tokens
# =============================================================================

def example_2_start_monitoring(manager):
    """Start monitoring new tokens"""
    print("\n" + "="*70)
    print("EXAMPLE 2: Start Monitoring Tokens")
    print("="*70)

    for token_info in SAMPLE_TOKENS:
        manager.start_monitoring(
            mint=token_info['mint'],
            cluster_id=token_info['cluster_id'],
            creator=token_info['creator']
        )
        print(f"✅ Started monitoring {token_info['mint']}")


# =============================================================================
# EXAMPLE 3: Record Price Snapshots (Simulate Price Feed)
# =============================================================================

def example_3_record_snapshots(manager):
    """Record simulated price data"""
    print("\n" + "="*70)
    print("EXAMPLE 3: Record Price Snapshots")
    print("="*70)

    now = int(datetime.now().timestamp())

    for token_info in SAMPLE_TOKENS:
        mint = token_info['mint']
        cluster_id = token_info['cluster_id']

        print(f"\nToken: {mint}")

        # Process each price point
        for time_offset, price in token_info['price_points']:
            # Calculate market cap (assume 1M supply)
            market_cap = price * 1_000_000

            # Create snapshot
            snapshot = TokenSnapshot(
                mint=mint,
                timestamp=now + time_offset,
                price_usd=price,
                market_cap_usd=market_cap,
                liquidity_usd=market_cap * 0.5,
                volume_24h=market_cap * 0.1,
                price_source="simulation",
                cluster_id=cluster_id,
                creator=token_info['creator']
            )

            # Record it
            manager.record_snapshot(snapshot)
            print(f"  +{time_offset}s: ${price:.8f} (MC: ${market_cap:,.0f})")

        print(f"  ✅ Recorded {len(token_info['price_points'])} snapshots")


# =============================================================================
# EXAMPLE 4: Run Monitoring Cycle (Classify Tokens)
# =============================================================================

def example_4_run_monitoring(manager):
    """Simulate running the monitoring loop"""
    print("\n" + "="*70)
    print("EXAMPLE 4: Run Monitoring Cycle (Classify Tokens)")
    print("="*70)

    worker = LifecycleMonitoringWorker(DB_PATH)

    # Run one cycle
    print("\nRunning monitoring cycle...")
    results = worker.run_cycle()

    print(f"✅ Results:")
    print(f"   Tokens checked: {results['mints_checked']}")
    print(f"   Tokens stopped: {results['mints_stopped']}")
    print(f"   Tokens classified: {results['mints_classified']}")

    if results['errors']:
        print(f"⚠️  Errors: {results['errors']}")

    # Show what was classified
    print("\n📊 Outcomes:")
    recent = manager.get_outcome_summary(limit=10)
    for outcome in recent:
        print(f"  {outcome['mint'][:20]}... → {outcome['outcome']}")
        print(f"     Peak: ${outcome['peak_mc']:,.0f}, "
              f"Final: ${outcome['final_mc']:,.0f}, "
              f"Drawdown: {outcome['max_drawdown_pct']:.1f}%")


# =============================================================================
# EXAMPLE 5: Run Analytics
# =============================================================================

def example_5_analytics():
    """Run analytical queries"""
    print("\n" + "="*70)
    print("EXAMPLE 5: Analytics & Reporting")
    print("="*70)

    analytics = LifecycleAnalytics(DB_PATH)

    # Overall stats
    print("\n📈 Overall Statistics:")
    stats = analytics.overall_stats()
    if stats:
        print(f"   Total classified: {stats['total_classified']}")
        print(f"   Success rate: {stats['success_rate']:.1%}")
        print(f"   Rug rate: {stats['rug_rate']:.1%}")
        print(f"   Avg peak MC: ${stats['avg_peak_mc']:,.0f}")
        print(f"   Avg drawdown: {stats['avg_drawdown_pct']:.1f}%")

    # Cluster consistency
    print("\n🏢 Cluster Health:")
    consistency = analytics.cluster_consistency()
    for cluster in consistency[:5]:
        print(f"   {cluster['cluster_name']}: "
              f"{cluster['consistency_label']} "
              f"(rug: {cluster['rug_rate']:.1%}, "
              f"success: {cluster['success_rate']:.1%})")

    # Recent outcomes
    print("\n📋 Recently Classified:")
    recent = analytics.recently_classified(limit=5)
    for token in recent:
        print(f"   {token['mint'][:20]}... → {token['outcome']}")

    # Peak distribution
    print("\n💰 Market Cap Distribution:")
    dist = analytics.peak_market_cap_distribution()
    for row in dist[:5]:
        print(f"   {row['market_cap_range']}: {row['count']} tokens ({row['percentage']:.1f}%)")


# =============================================================================
# EXAMPLE 6: Custom Query
# =============================================================================

def example_6_custom_queries(analytics):
    """Run custom SQL queries"""
    print("\n" + "="*70)
    print("EXAMPLE 6: Custom Queries")
    print("="*70)

    # Fastest rugs
    print("\n⚡ Fastest Rugs:")
    fastest = analytics.fastest_rugs(limit=5)
    for token in fastest:
        print(f"   {token['mint'][:20]}... peaked in {token['time_to_peak_minutes']}min")

    # Biggest winners
    print("\n🏆 Biggest Winners:")
    winners = analytics.biggest_winners(limit=5)
    for token in winners:
        print(f"   {token['mint'][:20]}... → ${token['peak_market_cap']:,.0f}")

    # Worst clusters
    print("\n❌ Worst Clusters:")
    worst = analytics.worst_performing_clusters(limit=5)
    for cluster in worst:
        print(f"   {cluster.cluster_name}: {cluster.rug_rate:.1%} rug rate")

    # Best clusters
    print("\n✅ Best Clusters:")
    best = analytics.best_performing_clusters(limit=5)
    for cluster in best:
        print(f"   {cluster.cluster_name}: {cluster.success_rate:.1%} success rate")


# =============================================================================
# EXAMPLE 7: Compute Cluster Stats
# =============================================================================

def example_7_cluster_stats(manager):
    """Compute and show cluster aggregates"""
    print("\n" + "="*70)
    print("EXAMPLE 7: Compute Cluster Statistics")
    print("="*70)

    # Compute for all clusters
    print("\nComputing cluster statistics...")
    manager.compute_cluster_stats()
    print("✅ Cluster stats computed")

    # Show them (would query cluster_outcome_stats table)


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Run all examples"""
    print("\n" + "="*70)
    print("TOKEN LIFECYCLE SYSTEM - QUICK START")
    print("="*70)

    try:
        # Example 1: Initialize
        manager = example_1_initialize()

        # Example 2: Start monitoring
        example_2_start_monitoring(manager)

        # Example 3: Record snapshots
        example_3_record_snapshots(manager)

        # Example 4: Run monitoring cycle (classify)
        example_4_run_monitoring(manager)

        # Example 5: Analytics
        analytics = LifecycleAnalytics(DB_PATH)
        example_5_analytics()

        # Example 6: Custom queries
        example_6_custom_queries(analytics)

        # Example 7: Cluster stats
        example_7_cluster_stats(manager)

        # Summary
        print("\n" + "="*70)
        print("✅ QUICK START COMPLETE!")
        print("="*70)
        print("\nNext steps:")
        print("1. Review TOKEN_LIFECYCLE_INTEGRATION_GUIDE.md")
        print("2. Integrate with your price feed")
        print("3. Add background monitoring loop")
        print("4. Validate on historical data")
        print("5. Deploy to production")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
