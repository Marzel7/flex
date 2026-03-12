#!/usr/bin/env python3
"""
Phase 1 Anomaly Detection System

Detects rollout problems early:
- RPC call spikes (cursors not loading or updating)
- Cursor growth stopping (extraction stalled)
- Creator backlog (overdue extractions accumulating)
- Cursor activity drops (extraction rates declining)
- Incremental extraction failure (spikes in full scans)

Use this to monitor Phase 1 validation (March 10-17) and catch issues before
they impact production.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from dataclasses import dataclass

DB_PATH = 'flex_complete_database.db'

# Alert thresholds (from feedback document)
ALERT_THRESHOLDS = {
    'rpc_spike_percent': 30,          # Alert if RPC >130% of baseline
    'rpc_per_hour_max': 150,          # Alert if >150 calls/hour
    'cursor_updates_per_hour_min': 5, # Alert if <5 updates/hour
    'overdue_creators_max': 100,      # Alert if >100 creators overdue
    'zero_activity_hours': 2,         # Alert if no activity for 2 hours
}

@dataclass
class AnomalyAlert:
    """Represents a detected anomaly."""
    name: str
    severity: str  # 'critical', 'warning', 'info'
    message: str
    metric: str
    current_value: float
    threshold: float
    timestamp: datetime

class Phase1AnomalyDetector:
    """Detects anomalies in Phase 1 deployment."""

    def __init__(self):
        self.db_path = DB_PATH
        self.alerts: List[AnomalyAlert] = []

    def detect_all(self) -> List[AnomalyAlert]:
        """Run all anomaly detection checks."""
        self.alerts = []

        # Run detection checks
        self._detect_rpc_spike()
        self._detect_cursor_growth_stall()
        self._detect_extraction_backlog()
        self._detect_cursor_activity_drop()
        self._detect_no_recent_activity()
        self._detect_failed_cursor_updates()

        return self.alerts

    def _detect_rpc_spike(self):
        """ANOMALY 1: RPC calls increased (cursors not working)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get RPC calls last 24 hours by day
            cursor.execute("""
                WITH daily AS (
                  SELECT
                    DATE(recorded_at) AS day,
                    COUNT(*) AS rpc_calls
                  FROM rpc_metrics
                  WHERE source_file = 'realtime_creator_funding_extractor'
                  AND recorded_at >= datetime('now', '-7 days')
                  GROUP BY DATE(recorded_at)
                  ORDER BY day DESC
                )
                SELECT day, rpc_calls FROM daily LIMIT 2;
            """)
            results = cursor.fetchall()
            conn.close()

            if len(results) >= 2:
                today_day, today_calls = results[0]
                yesterday_day, yesterday_calls = results[1]

                if yesterday_calls > 0:
                    percent_increase = ((today_calls - yesterday_calls) / yesterday_calls) * 100

                    if percent_increase > ALERT_THRESHOLDS['rpc_spike_percent']:
                        self.alerts.append(AnomalyAlert(
                            name='RPC_SPIKE',
                            severity='critical',
                            message=f'RPC calls spiked {percent_increase:.0f}% from {yesterday_calls} → {today_calls}. Cursors may not be loading.',
                            metric='rpc_calls_percent_change',
                            current_value=today_calls,
                            threshold=ALERT_THRESHOLDS['rpc_spike_percent'],
                            timestamp=datetime.now()
                        ))

        except Exception as e:
            self.alerts.append(AnomalyAlert(
                name='RPC_SPIKE_CHECK_ERROR',
                severity='warning',
                message=f'Could not check RPC spike: {e}',
                metric='rpc_spike_check',
                current_value=0,
                threshold=0,
                timestamp=datetime.now()
            ))

    def _detect_cursor_growth_stall(self):
        """ANOMALY 2: Cursor count not increasing (extraction stalled)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check cursor count by day
            cursor.execute("""
                SELECT
                  DATE(last_scan_at) AS scan_day,
                  COUNT(*) AS cursors_created
                FROM address_scan_state
                WHERE last_scan_at IS NOT NULL
                AND last_scan_at >= datetime('now', '-3 days')
                GROUP BY DATE(last_scan_at)
                ORDER BY scan_day DESC
                LIMIT 2;
            """)
            results = cursor.fetchall()
            conn.close()

            if len(results) >= 2:
                today_day, today_count = results[0]
                yesterday_day, yesterday_count = results[1]

                # Alert if no new cursors created
                if today_count == 0 and yesterday_count > 0:
                    self.alerts.append(AnomalyAlert(
                        name='CURSOR_GROWTH_STALL',
                        severity='critical',
                        message=f'No new cursors created today. Extraction may have stalled.',
                        metric='new_cursors_today',
                        current_value=today_count,
                        threshold=1,
                        timestamp=datetime.now()
                    ))

        except Exception as e:
            self.alerts.append(AnomalyAlert(
                name='CURSOR_GROWTH_CHECK_ERROR',
                severity='warning',
                message=f'Could not check cursor growth: {e}',
                metric='cursor_growth_check',
                current_value=0,
                threshold=0,
                timestamp=datetime.now()
            ))

    def _detect_extraction_backlog(self):
        """ANOMALY 3: Creator backlog growing (extraction falling behind)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Count creators due for scan but not yet scanned
            cursor.execute("""
                SELECT COUNT(*)
                FROM address_scan_state
                WHERE next_scan_at <= CURRENT_TIMESTAMP
                AND status = 'active'
                AND last_signature IS NULL;
            """)
            overdue_unscanned = cursor.fetchone()[0]

            # Count creators due for re-scan
            cursor.execute("""
                SELECT COUNT(*)
                FROM address_scan_state
                WHERE next_scan_at <= CURRENT_TIMESTAMP
                AND status = 'active'
                AND last_signature IS NOT NULL;
            """)
            overdue_rescan = cursor.fetchone()[0]

            conn.close()

            total_overdue = overdue_unscanned + overdue_rescan

            if total_overdue > ALERT_THRESHOLDS['overdue_creators_max']:
                self.alerts.append(AnomalyAlert(
                    name='EXTRACTION_BACKLOG',
                    severity='warning',
                    message=f'{total_overdue} creators overdue for extraction. Backlog growing.',
                    metric='overdue_creators',
                    current_value=total_overdue,
                    threshold=ALERT_THRESHOLDS['overdue_creators_max'],
                    timestamp=datetime.now()
                ))

        except Exception as e:
            self.alerts.append(AnomalyAlert(
                name='BACKLOG_CHECK_ERROR',
                severity='warning',
                message=f'Could not check extraction backlog: {e}',
                metric='backlog_check',
                current_value=0,
                threshold=0,
                timestamp=datetime.now()
            ))

    def _detect_cursor_activity_drop(self):
        """ANOMALY 4: Cursor update rate drops (extraction slowing)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Count cursor updates in last hour
            cursor.execute("""
                SELECT COUNT(*)
                FROM address_scan_state
                WHERE last_scan_at >= datetime('now', '-1 hour');
            """)
            updates_last_hour = cursor.fetchone()[0]

            conn.close()

            if updates_last_hour < ALERT_THRESHOLDS['cursor_updates_per_hour_min']:
                self.alerts.append(AnomalyAlert(
                    name='CURSOR_ACTIVITY_DROP',
                    severity='warning',
                    message=f'Only {updates_last_hour} cursor updates in last hour. Extraction rate declining.',
                    metric='cursor_updates_per_hour',
                    current_value=updates_last_hour,
                    threshold=ALERT_THRESHOLDS['cursor_updates_per_hour_min'],
                    timestamp=datetime.now()
                ))

        except Exception as e:
            self.alerts.append(AnomalyAlert(
                name='ACTIVITY_DROP_CHECK_ERROR',
                severity='warning',
                message=f'Could not check cursor activity: {e}',
                metric='activity_drop_check',
                current_value=0,
                threshold=0,
                timestamp=datetime.now()
            ))

    def _detect_no_recent_activity(self):
        """ANOMALY 5: No extraction activity for extended period"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get time of most recent cursor update
            cursor.execute("""
                SELECT MAX(last_scan_at) FROM address_scan_state;
            """)
            last_update = cursor.fetchone()[0]
            conn.close()

            if last_update:
                last_update_dt = datetime.fromisoformat(last_update)
                time_since_update = datetime.now() - last_update_dt
                hours_since = time_since_update.total_seconds() / 3600

                if hours_since > ALERT_THRESHOLDS['zero_activity_hours']:
                    self.alerts.append(AnomalyAlert(
                        name='NO_RECENT_ACTIVITY',
                        severity='critical',
                        message=f'No extraction activity for {hours_since:.1f} hours. Listener may be down.',
                        metric='hours_since_last_extraction',
                        current_value=hours_since,
                        threshold=ALERT_THRESHOLDS['zero_activity_hours'],
                        timestamp=datetime.now()
                    ))

        except Exception as e:
            self.alerts.append(AnomalyAlert(
                name='ACTIVITY_CHECK_ERROR',
                severity='warning',
                message=f'Could not check recent activity: {e}',
                metric='activity_check',
                current_value=0,
                threshold=0,
                timestamp=datetime.now()
            ))

    def _detect_failed_cursor_updates(self):
        """ANOMALY 6: Cursor update success rate dropping"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check ratio of cursors with signatures to total cursors
            cursor.execute("""
                SELECT
                  COUNT(*) as total,
                  COUNT(CASE WHEN last_signature IS NOT NULL THEN 1 END) as with_sig
                FROM address_scan_state;
            """)
            total, with_sig = cursor.fetchone()

            # Check how many cursors haven't been updated recently
            cursor.execute("""
                SELECT COUNT(*)
                FROM address_scan_state
                WHERE last_scan_at <= datetime('now', '-7 days')
                AND status = 'active';
            """)
            stale_cursors = cursor.fetchone()[0]

            conn.close()

            if total > 0:
                coverage = (with_sig / total) * 100

                # If coverage is very low and we're past day 2, alert
                if coverage < 20:
                    self.alerts.append(AnomalyAlert(
                        name='LOW_CURSOR_COVERAGE',
                        severity='warning',
                        message=f'Only {coverage:.1f}% cursor coverage. Below expected {20}% for day 2+.',
                        metric='cursor_coverage_percent',
                        current_value=coverage,
                        threshold=20,
                        timestamp=datetime.now()
                    ))

            if stale_cursors > (total * 0.5):
                self.alerts.append(AnomalyAlert(
                    name='STALE_CURSORS',
                    severity='warning',
                    message=f'{stale_cursors} cursors haven\'t been updated in 7 days.',
                    metric='stale_cursors',
                    current_value=stale_cursors,
                    threshold=total * 0.5,
                    timestamp=datetime.now()
                ))

        except Exception as e:
            self.alerts.append(AnomalyAlert(
                name='COVERAGE_CHECK_ERROR',
                severity='warning',
                message=f'Could not check cursor coverage: {e}',
                metric='coverage_check',
                current_value=0,
                threshold=0,
                timestamp=datetime.now()
            ))

    def print_alerts(self):
        """Print formatted alert summary."""
        if not self.alerts:
            print("✅ No anomalies detected")
            return

        # Group by severity
        critical = [a for a in self.alerts if a.severity == 'critical']
        warnings = [a for a in self.alerts if a.severity == 'warning']
        info = [a for a in self.alerts if a.severity == 'info']

        if critical:
            print("\n🚨 CRITICAL ALERTS")
            print("-" * 80)
            for alert in critical:
                print(f"  [{alert.name}] {alert.message}")
                print(f"    Current: {alert.current_value} | Threshold: {alert.threshold}")

        if warnings:
            print("\n⚠️  WARNING ALERTS")
            print("-" * 80)
            for alert in warnings:
                print(f"  [{alert.name}] {alert.message}")
                print(f"    Current: {alert.current_value} | Threshold: {alert.threshold}")

        if info:
            print("\nℹ️  INFO ALERTS")
            print("-" * 80)
            for alert in info:
                print(f"  [{alert.name}] {alert.message}")

        print()

    def health_status(self) -> str:
        """Return overall Phase 1 health status."""
        if not self.alerts:
            return "🟢 OK"

        critical_count = len([a for a in self.alerts if a.severity == 'critical'])
        warning_count = len([a for a in self.alerts if a.severity == 'warning'])

        if critical_count > 0:
            return f"🔴 CRITICAL ({critical_count} issues)"
        elif warning_count > 2:
            return f"🟠 WARNING ({warning_count} issues)"
        elif warning_count > 0:
            return f"🟡 CAUTION ({warning_count} issues)"
        else:
            return "🟢 OK"


if __name__ == "__main__":
    detector = Phase1AnomalyDetector()
    alerts = detector.detect_all()

    print("\n" + "=" * 80)
    print("PHASE 1 ANOMALY DETECTION REPORT")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Status: {detector.health_status()}")
    print("=" * 80)

    detector.print_alerts()

    print("\n" + "=" * 80)
    print("INTERPRETATION GUIDE")
    print("=" * 80)
    print("""
RPC_SPIKE: RPC calls increased >30%. Cursors may not be loading.
           → Check logs for "Loaded cursor" vs "No cursor found"
           → Verify cursor table has data

CURSOR_GROWTH_STALL: No new cursors created today.
                     → Check if extraction is running
                     → Verify Helius API is accessible

EXTRACTION_BACKLOG: >100 creators due for extraction.
                    → Check if extraction is keeping up
                    → May indicate slow extraction or listener down

CURSOR_ACTIVITY_DROP: <5 cursor updates per hour.
                      → Check extraction rate
                      → Verify system resources

NO_RECENT_ACTIVITY: No extractions for >2 hours.
                    → Listener may be down
                    → Check listener logs

LOW_CURSOR_COVERAGE: <20% coverage on day 2+.
                     → Early indicator of stalled extraction
                     → Verify CursorManager working

STALE_CURSORS: 50%+ cursors unchanged for 7 days.
               → Certain creators may have no activity
               → Or extraction queue is blocked
    """)

    print("=" * 80)
    print(f"Alert Summary: {len(alerts)} anomaly/anomalies detected")
    print("=" * 80)
