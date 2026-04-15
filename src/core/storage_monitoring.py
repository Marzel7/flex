"""
FLEX Phase 3.2 Storage Monitoring

Comprehensive monitoring for transfer_index storage, including:
- Metrics collection (DB size, WAL, row counts, growth)
- Query latency tracking
- Capacity projections
- Alert thresholds and notifications
"""

import sqlite3
import time
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class StorageMetrics:
    """Storage metrics snapshot."""
    db_size_mb: float
    wal_size_mb: float
    row_count: int
    daily_growth_mb: float
    days_to_capacity: float
    last_cleanup_ago_hours: float
    last_cleanup_freed_mb: float


class StorageMonitor:
    """Monitor storage growth and cleanup performance."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.metrics_table = "storage_metrics"

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_metrics_table(self) -> None:
        """Create metrics table if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.metrics_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                db_size_mb REAL NOT NULL,
                wal_size_mb REAL NOT NULL,
                row_count INTEGER NOT NULL,
                daily_growth_mb REAL,
                query_latency_ms REAL
            )
        """)

        # Index for efficient queries
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{self.metrics_table}_timestamp
            ON {self.metrics_table}(timestamp DESC)
        """)

        conn.commit()
        conn.close()

    def collect_metrics(self) -> StorageMetrics:
        """Collect current storage metrics."""
        # File sizes
        db_size_mb = os.path.getsize(self.db_path) / (1024 ** 2)
        wal_path = self.db_path + '-wal'
        wal_size_mb = os.path.getsize(wal_path) / (1024 ** 2) if os.path.exists(wal_path) else 0

        # Database stats
        conn = self._get_conn()
        cursor = conn.cursor()

        # Row count
        cursor.execute("SELECT COUNT(*) FROM transfer_index")
        row_count = cursor.fetchone()[0]

        # Last cleanup metrics
        cursor.execute("""
            SELECT freed_mb, cleanup_timestamp FROM cleanup_log
            WHERE status = 'success'
            ORDER BY cleanup_timestamp DESC
            LIMIT 1
        """)
        last_cleanup = cursor.fetchone()

        if last_cleanup:
            last_cleanup_freed_mb, last_cleanup_ts = last_cleanup
            last_cleanup_ago_hours = (time.time() - last_cleanup_ts) / 3600
        else:
            last_cleanup_freed_mb = 0.0
            last_cleanup_ago_hours = float('inf')

        # Daily growth (estimate from cleanup log)
        yesterday = time.time() - 86400
        cursor.execute("""
            SELECT SUM(freed_mb) FROM cleanup_log
            WHERE cleanup_timestamp > ? AND status = 'success'
        """, (yesterday,))
        growth_result = cursor.fetchone()
        daily_growth_mb = growth_result[0] if growth_result and growth_result[0] else 0.0

        conn.close()

        # Capacity projection
        if daily_growth_mb > 0:
            # Assuming 500 GB capacity warning
            days_to_capacity = (500_000 - db_size_mb) / daily_growth_mb
        else:
            days_to_capacity = float('inf')

        return StorageMetrics(
            db_size_mb=db_size_mb,
            wal_size_mb=wal_size_mb,
            row_count=row_count,
            daily_growth_mb=daily_growth_mb,
            days_to_capacity=days_to_capacity,
            last_cleanup_ago_hours=last_cleanup_ago_hours,
            last_cleanup_freed_mb=last_cleanup_freed_mb
        )

    def record_metrics(self, metrics: StorageMetrics, query_latency_ms: float = 0.0) -> None:
        """Record metrics to database."""
        self._ensure_metrics_table()

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            INSERT INTO {self.metrics_table}
            (timestamp, db_size_mb, wal_size_mb, row_count, daily_growth_mb, query_latency_ms)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            time.time(),
            metrics.db_size_mb,
            metrics.wal_size_mb,
            metrics.row_count,
            metrics.daily_growth_mb,
            query_latency_ms
        ))

        conn.commit()
        conn.close()

    def get_metrics_summary(self, lookback_hours: int = 24) -> Dict:
        """Get metrics summary over time period."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cutoff_ts = time.time() - (lookback_hours * 3600)

        cursor.execute(f"""
            SELECT
                MIN(db_size_mb) as min_size_mb,
                MAX(db_size_mb) as max_size_mb,
                AVG(db_size_mb) as avg_size_mb,
                AVG(query_latency_ms) as avg_query_latency_ms,
                COUNT(*) as samples
            FROM {self.metrics_table}
            WHERE timestamp > ?
        """, (cutoff_ts,))

        row = cursor.fetchone()
        conn.close()

        if not row or row[4] == 0:
            return {
                'error': 'No metrics collected',
                'lookback_hours': lookback_hours
            }

        return {
            'lookback_hours': lookback_hours,
            'samples': row[4],
            'min_size_mb': row[0],
            'max_size_mb': row[1],
            'avg_size_mb': row[2],
            'avg_query_latency_ms': row[3],
            'growth_mb': row[1] - row[0] if row[0] else 0
        }

    def print_dashboard(self) -> None:
        """Print storage metrics dashboard."""
        metrics = self.collect_metrics()
        summary_24h = self.get_metrics_summary(24)
        summary_7d = self.get_metrics_summary(168)

        print("\n" + "=" * 80)
        print("FLEX STORAGE METRICS DASHBOARD")
        print("=" * 80)

        print(f"\n📊 CURRENT STATE (as of {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        print(f"  Database size:          {metrics.db_size_mb:,.1f} MB ({metrics.db_size_mb/1024:.1f} GB)")
        print(f"  WAL size:               {metrics.wal_size_mb:,.1f} MB")
        print(f"  Row count:              {metrics.row_count:,}")
        print(f"  Daily growth:           {metrics.daily_growth_mb:,.1f} MB")
        print(f"  Last cleanup:           {metrics.last_cleanup_ago_hours:.1f} hours ago")
        print(f"  Last cleanup freed:     {metrics.last_cleanup_freed_mb:,.1f} MB")

        if metrics.days_to_capacity != float('inf') and metrics.days_to_capacity > 0:
            print(f"\n⚠️  CAPACITY PROJECTION")
            print(f"  Days to 500 GB:         {metrics.days_to_capacity:.0f} days ({metrics.days_to_capacity/30:.1f} months)")
        else:
            print(f"\n✅ CAPACITY")
            print(f"  Well-managed (growth rate <1 MB/day)")

        if summary_24h.get('samples', 0) > 0:
            print(f"\n📈 24-HOUR TREND")
            print(f"  Size change:            {summary_24h.get('growth_mb', 0):+.1f} MB")
            print(f"  Avg query latency:      {summary_24h.get('avg_query_latency_ms', 0):.1f} ms")
            print(f"  Samples:                {summary_24h.get('samples', 0)}")

        if summary_7d.get('samples', 0) > 0:
            print(f"\n📊 7-DAY TREND")
            print(f"  Size change:            {summary_7d.get('growth_mb', 0):+.1f} MB")
            print(f"  Avg query latency:      {summary_7d.get('avg_query_latency_ms', 0):.1f} ms")

        print("\n" + "=" * 80 + "\n")


class QueryLatencyMonitor:
    """Monitor query performance over time."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def measure_key_queries(self) -> Dict[str, float]:
        """Measure latency of critical queries."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        latencies = {}

        # Query 1: Get funders for destination (90-day window)
        start = time.time()
        cursor = conn.execute("""
            SELECT source, COUNT(*) as count, SUM(amount_sol) as total
            FROM transfer_index
            WHERE destination = ? AND block_time >= ?
            GROUP BY source
            ORDER BY total DESC
            LIMIT 100
        """, ('test_address_' + 'x' * 30, int(time.time()) - 90 * 86400))
        cursor.fetchall()
        latencies['get_funders_90d'] = (time.time() - start) * 1000

        # Query 2: Count transfers in retention window
        start = time.time()
        cursor = conn.execute("""
            SELECT COUNT(*) FROM transfer_index
            WHERE block_time >= ?
        """, (int(time.time()) - 90 * 86400,))
        cursor.fetchone()
        latencies['count_retention_window'] = (time.time() - start) * 1000

        # Query 3: Find newest transfers (index scan)
        start = time.time()
        cursor = conn.execute("""
            SELECT signature, destination, amount_sol FROM transfer_index
            ORDER BY block_time DESC
            LIMIT 1000
        """)
        cursor.fetchall()
        latencies['recent_transfers'] = (time.time() - start) * 1000

        conn.close()
        return latencies


class StorageAlerts:
    """Alert thresholds and conditions."""

    # Alert thresholds
    THRESHOLDS = {
        'db_size_mb': 250_000,              # Alert at 250 GB
        'cleanup_duration_ms': 5_000,       # Alert if cleanup >5 seconds
        'query_latency_ms': 100,            # Alert if queries >100ms
        'wal_size_mb': 100,                 # Alert if WAL >100 MB
        'daily_growth_mb': 3_000,           # Alert if growing >3 GB/day
        'rows_deleted_per_cleanup': 100_000, # Alert if <100k deleted
        'invalid_row_percentage': 10,       # Alert if >10% invalid
    }

    @staticmethod
    def check_alerts(metrics: StorageMetrics, cleanup_result: Dict, query_latencies: Dict) -> List[str]:
        """Check metrics against thresholds and return active alerts."""
        alerts = []

        # Database size alert
        if metrics.db_size_mb > StorageAlerts.THRESHOLDS['db_size_mb']:
            alerts.append(
                f"🔴 DATABASE SIZE: {metrics.db_size_mb/1024:.0f} GB "
                f"(threshold: {StorageAlerts.THRESHOLDS['db_size_mb']/1024:.0f} GB)"
            )

        # Cleanup duration alert
        if cleanup_result.get('duration_ms', 0) > StorageAlerts.THRESHOLDS['cleanup_duration_ms']:
            alerts.append(
                f"🟡 CLEANUP SLOW: {cleanup_result['duration_ms']:.0f}ms "
                f"(threshold: {StorageAlerts.THRESHOLDS['cleanup_duration_ms']}ms)"
            )

        # Query latency alert
        for query_name, latency in query_latencies.items():
            if latency > StorageAlerts.THRESHOLDS['query_latency_ms']:
                alerts.append(
                    f"🟡 SLOW QUERY: {query_name} = {latency:.1f}ms "
                    f"(threshold: {StorageAlerts.THRESHOLDS['query_latency_ms']}ms)"
                )

        # WAL size alert
        if metrics.wal_size_mb > StorageAlerts.THRESHOLDS['wal_size_mb']:
            alerts.append(
                f"🟡 WAL FILE LARGE: {metrics.wal_size_mb:.1f} MB "
                f"(threshold: {StorageAlerts.THRESHOLDS['wal_size_mb']} MB)"
            )

        # Daily growth alert
        if metrics.daily_growth_mb > StorageAlerts.THRESHOLDS['daily_growth_mb']:
            alerts.append(
                f"🟡 RAPID GROWTH: {metrics.daily_growth_mb:.0f} MB/day "
                f"(threshold: {StorageAlerts.THRESHOLDS['daily_growth_mb']} MB/day)"
            )

        # Rows deleted alert
        if cleanup_result.get('deleted', 0) < StorageAlerts.THRESHOLDS['rows_deleted_per_cleanup']:
            alerts.append(
                f"🟡 LOW CLEANUP: {cleanup_result.get('deleted', 0):,} rows deleted "
                f"(threshold: {StorageAlerts.THRESHOLDS['rows_deleted_per_cleanup']:,})"
            )

        return alerts

    @staticmethod
    def send_alerts(alerts: List[str], channel: str = 'log') -> None:
        """Send alerts to appropriate channel."""
        if not alerts:
            return

        message = "FLEX STORAGE ALERTS:\n" + "\n".join(alerts)

        if channel == 'log':
            logger.error(message)
        elif channel == 'slack':
            # Would integrate with Slack API
            pass
        elif channel == 'email':
            # Would integrate with email service
            pass
