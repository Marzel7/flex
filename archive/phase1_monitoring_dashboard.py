#!/usr/bin/env python3
"""
Phase 1 Monitoring Dashboard

Real-time monitoring of Phase 1 deployment status.
Tracks:
- Cursor coverage (how many creators have cursors)
- Cursor activity (new cursors being created)
- RPC cost trending
- Signature count validation
"""

import sqlite3
import time
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

DB_PATH = 'flex_complete_database.db'

class Phase1Dashboard:
    def __init__(self):
        self.db_path = DB_PATH

    def get_cursor_stats(self) -> Dict:
        """Get current cursor statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Total cursors
            cursor.execute("SELECT COUNT(*) FROM address_scan_state")
            total_cursors = cursor.fetchone()[0]

            # Cursors with signatures (has been scanned)
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE last_signature IS NOT NULL
            """)
            cursors_with_sigs = cursor.fetchone()[0]

            # Active cursors (next_scan_at <= now)
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE next_scan_at <= CURRENT_TIMESTAMP
                AND status = 'active'
            """)
            due_for_scan = cursor.fetchone()[0]

            # Recently updated (last 24 hours)
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE datetime(last_scan_at) >= datetime('now', '-1 day')
            """)
            updated_last_24h = cursor.fetchone()[0]

            conn.close()

            return {
                'total_cursors': total_cursors,
                'with_signatures': cursors_with_sigs,
                'coverage_percent': (cursors_with_sigs / total_cursors * 100) if total_cursors > 0 else 0,
                'due_for_scan': due_for_scan,
                'updated_last_24h': updated_last_24h,
            }
        except Exception as e:
            return {'error': str(e)}

    def get_creator_stats(self) -> Dict:
        """Get creator funding statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM creator_funders")
            total_creators = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT creator_address) FROM creator_funders")
            unique_creators = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT funder_address) FROM creator_funders")
            unique_funders = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(amount_sol) FROM creator_funders")
            total_sol = cursor.fetchone()[0] or 0

            conn.close()

            return {
                'total_creator_relationships': total_creators,
                'unique_creators': unique_creators,
                'unique_funders': unique_funders,
                'total_sol_tracked': total_sol,
            }
        except Exception as e:
            return {'error': str(e)}

    def get_rpc_metrics(self) -> Dict:
        """Get RPC cost metrics (if tracking is enabled)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Try to get RPC metrics if table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rpc_request_log'")
            if not cursor.fetchone():
                return {'status': 'RPC metrics table not found (tracking may not be enabled)'}

            # Count RPC calls
            cursor.execute("SELECT COUNT(*) FROM rpc_request_log")
            total_calls = cursor.fetchone()[0]

            # Calls in last 24 hours
            cursor.execute("""
                SELECT COUNT(*) FROM rpc_request_log
                WHERE timestamp >= datetime('now', '-1 day')
            """)
            calls_last_24h = cursor.fetchone()[0]

            # Calls per method
            cursor.execute("""
                SELECT method, COUNT(*) as count
                FROM rpc_request_log
                WHERE timestamp >= datetime('now', '-1 day')
                GROUP BY method
                ORDER BY count DESC
                LIMIT 5
            """)
            top_methods = cursor.fetchall()

            conn.close()

            return {
                'total_calls_all_time': total_calls,
                'calls_last_24h': calls_last_24h,
                'calls_per_day_avg': total_calls / 30 if total_calls > 0 else 0,  # Rough estimate
                'top_methods_last_24h': [{'method': m, 'count': c} for m, c in top_methods],
            }
        except Exception as e:
            return {'error': str(e)}

    def print_dashboard(self):
        """Print formatted dashboard."""
        cursor_stats = self.get_cursor_stats()
        creator_stats = self.get_creator_stats()
        rpc_stats = self.get_rpc_metrics()

        print("\n" + "=" * 80)
        print("FLEX V2 PHASE 1 MONITORING DASHBOARD")
        print("=" * 80)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()

        # Cursor Statistics
        print("📍 CURSOR COVERAGE (Phase 1)")
        print("-" * 80)
        if 'error' in cursor_stats:
            print(f"  ⚠ Error: {cursor_stats['error']}")
        else:
            print(f"  Total cursors created:        {cursor_stats['total_cursors']}")
            print(f"  Cursors with signatures:      {cursor_stats['with_signatures']}")
            print(f"  Coverage:                     {cursor_stats['coverage_percent']:.1f}%")
            print(f"  Due for next scan:            {cursor_stats['due_for_scan']}")
            print(f"  Updated in last 24h:          {cursor_stats['updated_last_24h']}")

            # Color-coded status
            if cursor_stats['total_cursors'] == 0:
                print("  Status: 🟡 WAITING - No extractions yet")
            elif cursor_stats['coverage_percent'] < 10:
                print("  Status: 🟡 WARMING UP - Building cursor coverage")
            elif cursor_stats['coverage_percent'] < 50:
                print("  Status: 🟠 IN PROGRESS - 25-50% coverage")
            else:
                print("  Status: 🟢 ACTIVE - Good cursor coverage")

        print()

        # Creator Funding Statistics
        print("👥 CREATOR FUNDING DATA")
        print("-" * 80)
        if 'error' in creator_stats:
            print(f"  ⚠ Error: {creator_stats['error']}")
        else:
            print(f"  Creator-funder relationships: {creator_stats['total_creator_relationships']}")
            print(f"  Unique creators tracked:      {creator_stats['unique_creators']}")
            print(f"  Unique funders found:         {creator_stats['unique_funders']}")
            print(f"  Total SOL tracked:            {creator_stats['total_sol_tracked']:.2f} SOL")

        print()

        # RPC Metrics
        print("📊 RPC COST TRACKING")
        print("-" * 80)
        if 'error' in rpc_stats:
            print(f"  {rpc_stats.get('error', 'Unable to retrieve RPC metrics')}")
        elif 'status' in rpc_stats:
            print(f"  {rpc_stats['status']}")
        else:
            print(f"  Total RPC calls (all-time):   {rpc_stats['total_calls_all_time']}")
            print(f"  RPC calls (last 24h):         {rpc_stats['calls_last_24h']}")
            print(f"  Avg calls/day:                {rpc_stats['calls_per_day_avg']:.0f}")

            if rpc_stats['top_methods_last_24h']:
                print("\n  Top RPC methods (last 24h):")
                for method_info in rpc_stats['top_methods_last_24h']:
                    print(f"    - {method_info['method']}: {method_info['count']} calls")

        print()

        # Phase 1 Impact Projection
        print("💰 PHASE 1 IMPACT PROJECTION")
        print("-" * 80)
        print("  Target RPC reduction:     60%")
        print("  Target cost/day:          $20 (from $50)")
        print("  Annual savings:           ~$10,950")
        print()
        print("  Status: Monitoring active - wait 7 days for conclusive metrics")
        print()

        print("=" * 80)
        print()

    def run_continuous(self, interval_seconds: int = 60):
        """Run dashboard continuously with refresh interval."""
        print("Starting Phase 1 monitoring (Ctrl+C to stop)...")
        try:
            while True:
                self.print_dashboard()
                print(f"Next refresh in {interval_seconds} seconds... (Ctrl+C to stop)")
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped.")
            sys.exit(0)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Phase 1 Monitoring Dashboard")
    parser.add_argument(
        '--interval',
        type=int,
        default=60,
        help='Refresh interval in seconds (default: 60)'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Print dashboard once and exit'
    )

    args = parser.parse_args()

    dashboard = Phase1Dashboard()

    if args.once:
        dashboard.print_dashboard()
    else:
        dashboard.run_continuous(args.interval)
