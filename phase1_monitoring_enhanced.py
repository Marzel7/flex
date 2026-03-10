#!/usr/bin/env python3
"""
Enhanced Phase 1 Monitoring Dashboard with Anomaly Detection

Combines real-time metrics with anomaly detection to catch Phase 1 issues early.

Usage:
    python3 phase1_monitoring_enhanced.py --once          # Single report
    python3 phase1_monitoring_enhanced.py --interval 60   # Auto-refresh
    python3 phase1_monitoring_enhanced.py --alerts-only   # Just anomalies
"""

import sqlite3
import time
import sys
import argparse
from datetime import datetime, timedelta
from typing import Dict, List
from phase1_anomaly_detection import Phase1AnomalyDetector, ALERT_THRESHOLDS

DB_PATH = 'flex_complete_database.db'

class EnhancedPhase1Dashboard:
    def __init__(self):
        self.db_path = DB_PATH
        self.detector = Phase1AnomalyDetector()

    def get_cursor_stats(self) -> Dict:
        """Get current cursor statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Total cursors
            cursor.execute("SELECT COUNT(*) FROM address_scan_state")
            total_cursors = cursor.fetchone()[0]

            # Cursors with signatures
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE last_signature IS NOT NULL
            """)
            cursors_with_sigs = cursor.fetchone()[0]

            # Cursors updated today
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE DATE(last_scan_at) = DATE('now')
            """)
            updated_today = cursor.fetchone()[0]

            # Cursors due for scan
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE next_scan_at <= CURRENT_TIMESTAMP AND status = 'active'
            """)
            due_for_scan = cursor.fetchone()[0]

            conn.close()

            return {
                'total_cursors': total_cursors,
                'with_signatures': cursors_with_sigs,
                'coverage_percent': (cursors_with_sigs / total_cursors * 100) if total_cursors > 0 else 0,
                'updated_today': updated_today,
                'due_for_scan': due_for_scan,
            }
        except Exception as e:
            return {'error': str(e)}

    def get_rpc_stats(self) -> Dict:
        """Get RPC call statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # RPC calls last hour
            cursor.execute("""
                SELECT COUNT(*) FROM rpc_metrics
                WHERE recorded_at >= datetime('now', '-1 hour')
                AND source_file = 'realtime_creator_funding_extractor'
            """)
            calls_last_hour = cursor.fetchone()[0]

            # RPC calls last 24 hours
            cursor.execute("""
                SELECT COUNT(*) FROM rpc_metrics
                WHERE recorded_at >= datetime('now', '-1 day')
                AND source_file = 'realtime_creator_funding_extractor'
            """)
            calls_last_24h = cursor.fetchone()[0]

            # Average per hour
            avg_per_hour = calls_last_24h / 24 if calls_last_24h > 0 else 0

            conn.close()

            return {
                'calls_last_hour': calls_last_hour,
                'calls_last_24h': calls_last_24h,
                'avg_per_hour': avg_per_hour,
                'alert_threshold': ALERT_THRESHOLDS['rpc_per_hour_max'],
            }
        except Exception as e:
            return {'error': str(e)}

    def get_extraction_health(self) -> Dict:
        """Get extraction pipeline health."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Count overdue creators
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE next_scan_at <= CURRENT_TIMESTAMP AND status = 'active'
            """)
            overdue = cursor.fetchone()[0]

            # Time since last cursor update
            cursor.execute("""
                SELECT MAX(last_scan_at) FROM address_scan_state
            """)
            last_update = cursor.fetchone()[0]

            # Cursor update rate (updates per hour)
            cursor.execute("""
                SELECT COUNT(*) FROM address_scan_state
                WHERE last_scan_at >= datetime('now', '-1 hour')
            """)
            updates_last_hour = cursor.fetchone()[0]

            conn.close()

            time_since_update = None
            if last_update:
                last_update_dt = datetime.fromisoformat(last_update)
                time_since_update = (datetime.now() - last_update_dt).total_seconds() / 60

            return {
                'overdue_creators': overdue,
                'overdue_threshold': ALERT_THRESHOLDS['overdue_creators_max'],
                'updates_per_hour': updates_last_hour,
                'updates_threshold': ALERT_THRESHOLDS['cursor_updates_per_hour_min'],
                'minutes_since_last_update': time_since_update,
                'activity_threshold_hours': ALERT_THRESHOLDS['zero_activity_hours'],
            }
        except Exception as e:
            return {'error': str(e)}

    def print_dashboard(self, alerts_only=False):
        """Print formatted enhanced dashboard."""
        cursor_stats = self.get_cursor_stats()
        rpc_stats = self.get_rpc_stats()
        extraction_health = self.get_extraction_health()

        # Run anomaly detection
        anomalies = self.detector.detect_all()

        print("\n" + "=" * 100)
        print("PHASE 1 ENHANCED MONITORING DASHBOARD")
        print("=" * 100)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Health Status: {self.detector.health_status()}")
        print()

        if alerts_only:
            self._print_anomalies_section(anomalies)
            return

        # Cursor Statistics Section
        print("📍 CURSOR COVERAGE")
        print("-" * 100)
        if 'error' in cursor_stats:
            print(f"  ⚠ Error: {cursor_stats['error']}")
        else:
            coverage = cursor_stats['coverage_percent']
            coverage_color = "🟢" if coverage >= 60 else "🟠" if coverage >= 30 else "🟡"

            print(f"  {coverage_color} Coverage:            {coverage:.1f}% ({cursor_stats['with_signatures']}/{cursor_stats['total_cursors']})")
            print(f"     Updated today:       {cursor_stats['updated_today']} cursors")
            print(f"     Due for scan:        {cursor_stats['due_for_scan']} creators")

            # Expected coverage for day
            self._print_coverage_expectation(coverage)

        print()

        # RPC Statistics Section
        print("📊 RPC CALL TRACKING")
        print("-" * 100)
        if 'error' in rpc_stats:
            print(f"  ⚠ Error: {rpc_stats['error']}")
        else:
            calls_hour = rpc_stats['calls_last_hour']
            calls_24h = rpc_stats['calls_last_24h']
            alert_threshold = rpc_stats['alert_threshold']

            hour_color = "🟢" if calls_hour < alert_threshold else "🔴" if calls_hour > alert_threshold * 1.3 else "🟡"

            print(f"  {hour_color} Last hour:           {calls_hour} calls ({calls_hour}/{alert_threshold} threshold)")
            print(f"     Last 24 hours:       {calls_24h} calls")
            print(f"     Avg per hour:        {rpc_stats['avg_per_hour']:.1f} calls/hour")

            if calls_hour > alert_threshold:
                print(f"     ⚠ WARNING: Approaching alert threshold ({alert_threshold} calls/hour)")

        print()

        # Extraction Health Section
        print("⚙️  EXTRACTION PIPELINE HEALTH")
        print("-" * 100)
        if 'error' in extraction_health:
            print(f"  ⚠ Error: {extraction_health['error']}")
        else:
            overdue = extraction_health['overdue_creators']
            updates = extraction_health['updates_per_hour']
            minutes_since = extraction_health['minutes_since_last_update']

            overdue_color = "🟢" if overdue < 10 else "🟠" if overdue < extraction_health['overdue_threshold'] else "🔴"
            updates_color = "🟢" if updates >= extraction_health['updates_threshold'] else "🟡" if updates > 0 else "🔴"

            print(f"  {overdue_color} Overdue creators:    {overdue} (threshold: {extraction_health['overdue_threshold']})")
            print(f"  {updates_color} Updates per hour:    {updates} (threshold: {extraction_health['updates_threshold']})")

            if minutes_since is not None:
                hours_since = minutes_since / 60
                activity_color = "🟢" if hours_since < 1 else "🟡" if hours_since < extraction_health['activity_threshold_hours'] else "🔴"
                print(f"  {activity_color} Last update:         {hours_since:.2f} hours ago")

        print()

        # Anomalies Section
        self._print_anomalies_section(anomalies)

        print()

        # Expected Progress Section
        print("🎯 EXPECTED PHASE 1 PROGRESSION")
        print("-" * 100)
        print("""
  Day 1-2:  0-15%  cursor coverage (initial ramp-up)
  Day 3-4:  20-40% cursor coverage (steady growth)
  Day 5-6:  40-60% cursor coverage (good progress)
  Day 7:    60%+   cursor coverage (validation goal ✅)
        """)

        print("=" * 100)

    def _print_coverage_expectation(self, coverage):
        """Print expected coverage progress."""
        if coverage == 0:
            print(f"     Status: 🟡 STARTING - Awaiting first extractions")
        elif coverage < 15:
            print(f"     Status: 🟡 WARMING UP (Day 1-2 expected)")
        elif coverage < 40:
            print(f"     Status: 🟠 BUILDING (Day 3-4 expected)")
        elif coverage < 60:
            print(f"     Status: 🟡 PROGRESSING (Day 5-6 expected)")
        else:
            print(f"     Status: 🟢 VALIDATION GOAL REACHED ✅")

    def _print_anomalies_section(self, anomalies):
        """Print anomalies section of dashboard."""
        print("🔔 ANOMALY DETECTION")
        print("-" * 100)

        if not anomalies:
            print("  ✅ No anomalies detected")
            return

        # Group by severity
        critical = [a for a in anomalies if a.severity == 'critical']
        warnings = [a for a in anomalies if a.severity == 'warning']

        if critical:
            print(f"\n  🚨 CRITICAL ({len(critical)})")
            for alert in critical:
                print(f"    • [{alert.name}] {alert.message}")

        if warnings:
            print(f"\n  ⚠️  WARNING ({len(warnings)})")
            for alert in warnings:
                print(f"    • [{alert.name}] {alert.message}")

        if critical:
            print(f"\n  ACTION REQUIRED: {len(critical)} critical issue(s) detected!")

    def run_continuous(self, interval_seconds=60, alerts_only=False):
        """Run dashboard continuously with refresh interval."""
        print("Starting enhanced Phase 1 monitoring (Ctrl+C to stop)...")
        try:
            while True:
                self.print_dashboard(alerts_only=alerts_only)
                print(f"\nNext refresh in {interval_seconds} seconds... (Ctrl+C to stop)")
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped.")
            sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enhanced Phase 1 Monitoring with Anomaly Detection")
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
    parser.add_argument(
        '--alerts-only',
        action='store_true',
        help='Show only anomaly alerts'
    )

    args = parser.parse_args()

    dashboard = EnhancedPhase1Dashboard()

    if args.once:
        dashboard.print_dashboard(alerts_only=args.alerts_only)
    else:
        dashboard.run_continuous(args.interval, alerts_only=args.alerts_only)
