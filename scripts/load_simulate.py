#!/usr/bin/env python3
"""
Phase 9A: Load Simulation Dataset Generator

Creates a synthetic SQLite database with realistic data volumes:
- 10,000 networks in networks_release + network_scores
- 1,000,000 rows in network_score_history
- 50,000 rows in network_alerts
- Realistic distributions for risk_band, trend_direction, severity, flags

Usage:
    python3 scripts/load_simulate.py

Output:
    .test_load.db - Temporary test database

Uses a context manager to clean up on exit.
"""

import sqlite3
import tempfile
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

class LoadSimulator:
    """Generate and populate synthetic test database."""

    def __init__(self):
        self.temp_db = None
        self.conn = None

    def __enter__(self):
        """Create temp database on entry."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        self.conn = sqlite3.connect(self.db_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up database on exit."""
        if self.conn:
            self.conn.close()
        if self.db_path and Path(self.db_path).exists():
            Path(self.db_path).unlink()

    def create_schema(self):
        """Create all required tables with Phase 9A schema."""
        cursor = self.conn.cursor()

        # networks_release table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS networks_release (
                network_name TEXT PRIMARY KEY,
                network_size INTEGER,
                network_risk_level TEXT,
                network_type TEXT,
                has_cex_funder INTEGER,
                has_infra_funder INTEGER,
                cex_funder_count INTEGER,
                infra_funder_count INTEGER,
                stability_state TEXT,
                build_version INTEGER,
                last_built_at TEXT
            )
        ''')

        # network_scores table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS network_scores (
                network_name TEXT PRIMARY KEY,
                score REAL,
                smoothed_score REAL,
                stability_coeff REAL,
                stability_trend REAL,
                trend_direction TEXT,
                risk_band TEXT,
                build_version INTEGER
            )
        ''')

        # network_score_history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS network_score_history (
                network_name TEXT,
                score REAL,
                smoothed_score REAL,
                stability_coeff REAL,
                stability_trend REAL,
                trend_direction TEXT,
                risk_band TEXT,
                build_version INTEGER,
                PRIMARY KEY (network_name, build_version)
            )
        ''')

        # network_alerts table with Phase 8B fields
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS network_alerts (
                alert_id TEXT PRIMARY KEY,
                network_name TEXT,
                alert_type TEXT,
                severity TEXT,
                message TEXT,
                created_at TEXT,
                acknowledged INTEGER DEFAULT 0,
                acknowledged_at TEXT,
                acknowledged_by TEXT,
                suppressed_until TEXT,
                suppressed_at TEXT,
                suppression_reason TEXT,
                is_escalated INTEGER DEFAULT 0,
                escalated_at TEXT,
                escalation_rule TEXT,
                escalation_reason TEXT
            )
        ''')

        # network_evidence table (Phase F)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS network_evidence (
                network_name TEXT PRIMARY KEY,
                total_edges INTEGER,
                average_confidence REAL,
                evidence_risk_score REAL,
                evidence_version INTEGER,
                last_changed_at TEXT
            )
        ''')

        self.conn.commit()

    def random_network_name(self):
        """Generate a random network name."""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))

    def populate_networks_and_scores(self, num_networks=10000):
        """Populate networks_release and network_scores."""
        cursor = self.conn.cursor()
        risk_bands = ['LOW', 'MODERATE', 'ELEVATED', 'CRITICAL']
        risk_dist = [0.70, 0.20, 0.08, 0.02]  # 70% LOW, 20% MODERATE, etc.
        trend_dirs = ['UP', 'FLAT', 'DOWN']
        trend_dist = [0.25, 0.60, 0.15]  # 60% FLAT, 25% UP, 15% DOWN
        network_types = ['cex_connected', 'infra_connected', 'cex_and_infra_connected', 'organic']
        stability_states = ['new', 'stable', 'growing', 'shrinking']

        networks = []
        now = datetime.utcnow().isoformat()

        for i in range(num_networks):
            network_name = f"network_{i:06d}"
            risk_band = random.choices(risk_bands, weights=risk_dist)[0]
            trend_dir = random.choices(trend_dirs, weights=trend_dist)[0]
            score = random.uniform(10, 95)
            smoothed_score = score + random.uniform(-5, 5)
            stability_coeff = random.uniform(0, 1)
            stability_trend = random.uniform(-1, 1)
            network_size = random.randint(2, 5000)

            networks.append((
                network_name,
                network_size,
                risk_band,
                network_types[i % len(network_types)],
                1 if random.random() < 0.3 else 0,
                1 if random.random() < 0.2 else 0,
                random.randint(0, 50),
                random.randint(0, 30),
                stability_states[i % len(stability_states)],
                1,
                now
            ))

        # Bulk insert networks_release
        cursor.executemany('''
            INSERT INTO networks_release (
                network_name, network_size, network_risk_level, network_type,
                has_cex_funder, has_infra_funder, cex_funder_count, infra_funder_count,
                stability_state, build_version, last_built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', networks)

        # Populate network_scores with same networks
        scores = []
        for i, (network_name, _, risk_band, _, _, _, _, _, _, _, _) in enumerate(networks):
            score = random.uniform(10, 95)
            smoothed_score = score + random.uniform(-5, 5)
            stability_coeff = random.uniform(0, 1)
            stability_trend = random.uniform(-1, 1)
            trend_dir = random.choices(trend_dirs, weights=trend_dist)[0]

            scores.append((
                network_name,
                score,
                smoothed_score,
                stability_coeff,
                stability_trend,
                trend_dir,
                risk_band,
                1
            ))

        cursor.executemany('''
            INSERT INTO network_scores (
                network_name, score, smoothed_score, stability_coeff,
                stability_trend, trend_direction, risk_band, build_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', scores)

        self.conn.commit()
        print(f"✅ Populated {num_networks} networks and scores")
        return [net[0] for net in networks]

    def populate_score_history(self, network_names, num_builds=100):
        """Populate network_score_history with multiple builds."""
        cursor = self.conn.cursor()
        risk_bands = ['LOW', 'MODERATE', 'ELEVATED', 'CRITICAL']
        risk_dist = [0.70, 0.20, 0.08, 0.02]
        trend_dirs = ['UP', 'FLAT', 'DOWN']
        trend_dist = [0.25, 0.60, 0.15]

        history_rows = []
        for build_ver in range(1, num_builds + 1):
            for network_name in network_names:
                score = random.uniform(10, 95)
                smoothed_score = score + random.uniform(-5, 5)
                stability_coeff = random.uniform(0, 1)
                stability_trend = random.uniform(-1, 1)
                risk_band = random.choices(risk_bands, weights=risk_dist)[0]
                trend_dir = random.choices(trend_dirs, weights=trend_dist)[0]

                history_rows.append((
                    network_name,
                    score,
                    smoothed_score,
                    stability_coeff,
                    stability_trend,
                    trend_dir,
                    risk_band,
                    build_ver
                ))

        cursor.executemany('''
            INSERT INTO network_score_history (
                network_name, score, smoothed_score, stability_coeff,
                stability_trend, trend_direction, risk_band, build_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', history_rows)

        self.conn.commit()
        print(f"✅ Populated {len(history_rows)} score history rows ({len(network_names)} nets × {num_builds} builds)")

    def populate_alerts(self, network_names, num_alerts=50000):
        """Populate network_alerts with realistic alert lifecycle state."""
        cursor = self.conn.cursor()
        severities = ['low', 'medium', 'high']
        severity_dist = [0.50, 0.35, 0.15]
        alert_types = [
            'SCORE_SPIKE', 'RISK_ACCELERATION_SPIKE', 'STABILITY_DROP',
            'TREND_REVERSAL', 'NETWORK_GROWTH', 'NETWORK_SHRINKAGE'
        ]

        now = datetime.utcnow()
        alerts = []

        for i in range(num_alerts):
            alert_id = f"alert_{i:08d}"
            network_name = random.choice(network_names)
            alert_type = random.choice(alert_types)
            severity = random.choices(severities, weights=severity_dist)[0]
            created_at = (now - timedelta(hours=random.randint(0, 168))).isoformat()

            # Sparse alert lifecycle state (90% unacknowledged/unsuppressed)
            acknowledged = 1 if random.random() < 0.05 else 0
            acknowledged_at = now.isoformat() if acknowledged else None
            acknowledged_by = 'operator' if acknowledged else None

            suppressed_until = None
            if random.random() < 0.05:  # 5% suppressed
                suppressed_until = (now + timedelta(hours=random.randint(1, 168))).isoformat()

            is_escalated = 1 if random.random() < 0.08 else 0  # 8% escalated
            escalated_at = now.isoformat() if is_escalated else None
            escalation_rule = random.choice(['R1_CRITICAL_HIGH', 'R2_STABILITY_REPEATED', 'R3_ACCEL_UNSTABLE']) if is_escalated else None

            alerts.append((
                alert_id,
                network_name,
                alert_type,
                severity,
                f"{alert_type} in {network_name}",
                created_at,
                acknowledged,
                acknowledged_at,
                acknowledged_by,
                suppressed_until,
                None,  # suppressed_at
                None,  # suppression_reason
                is_escalated,
                escalated_at,
                escalation_rule,
                None  # escalation_reason
            ))

        cursor.executemany('''
            INSERT INTO network_alerts (
                alert_id, network_name, alert_type, severity, message, created_at,
                acknowledged, acknowledged_at, acknowledged_by,
                suppressed_until, suppressed_at, suppression_reason,
                is_escalated, escalated_at, escalation_rule, escalation_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', alerts)

        self.conn.commit()
        print(f"✅ Populated {num_alerts} alerts")

    def populate_evidence(self, network_names):
        """Populate network_evidence."""
        cursor = self.conn.cursor()
        evidence_rows = []
        now = datetime.utcnow().isoformat()

        for network_name in network_names:
            total_edges = random.randint(0, 500)
            average_confidence = random.uniform(40, 100) if total_edges > 0 else 0

            evidence_rows.append((
                network_name,
                total_edges,
                average_confidence,
                random.uniform(10, 95),  # evidence_risk_score
                1,  # evidence_version
                now
            ))

        cursor.executemany('''
            INSERT INTO network_evidence (
                network_name, total_edges, average_confidence,
                evidence_risk_score, evidence_version, last_changed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', evidence_rows)

        self.conn.commit()
        print(f"✅ Populated {len(evidence_rows)} evidence rows")

    def generate(self):
        """Generate full synthetic database."""
        print("🔨 Creating schema...")
        self.create_schema()

        print("📊 Populating networks and scores...")
        network_names = self.populate_networks_and_scores(num_networks=10000)

        print("📈 Populating score history (1M rows)...")
        # 10k networks × 100 builds = 1M rows
        self.populate_score_history(network_names, num_builds=100)

        print("🚨 Populating alerts (50k rows)...")
        self.populate_alerts(network_names, num_alerts=50000)

        print("📋 Populating evidence...")
        self.populate_evidence(network_names)

        return self.db_path

def main():
    """Main entry point."""
    with LoadSimulator() as simulator:
        db_path = simulator.generate()
        print(f"\n✅ Synthetic database created at: {db_path}")
        print(f"\nDatabase stats:")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for table in ['networks_release', 'network_scores', 'network_score_history', 'network_alerts', 'network_evidence']:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"  {table}: {count:,} rows")

        conn.close()

        # Keep database open for external use
        print(f"\nTo use this database with benchmark_queries.py:")
        print(f"  export TEST_DB='{db_path}'")
        print(f"  python3 scripts/benchmark_queries.py")

if __name__ == '__main__':
    main()
