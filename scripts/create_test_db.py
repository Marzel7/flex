#!/usr/bin/env python3
"""
Create a persistent test database for Phase 9A audits.
Much smaller than load_simulate for quick plan audits.
"""

import sqlite3
from datetime import datetime, timedelta
import random

def create_test_db(db_path, num_networks=100, num_builds=10, num_alerts=500):
    """Create a smaller test database for query plan audits."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create schema
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS networks_release (
            network_name TEXT PRIMARY KEY,
            network_size INTEGER,
            network_risk_level TEXT,
            network_type TEXT,
            has_cex_funder INTEGER DEFAULT 0,
            has_infra_funder INTEGER DEFAULT 0,
            cex_funder_count INTEGER DEFAULT 0,
            infra_funder_count INTEGER DEFAULT 0,
            stability_state TEXT,
            build_version INTEGER,
            last_built_at TEXT
        )
    ''')

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

    # Populate networks and scores
    now = datetime.utcnow().isoformat()
    risk_bands = ['LOW', 'MODERATE', 'ELEVATED', 'CRITICAL']
    trend_dirs = ['UP', 'FLAT', 'DOWN']
    severities = ['low', 'medium', 'high']
    alert_types = ['SCORE_SPIKE', 'STABILITY_DROP', 'RISK_ACCELERATION_SPIKE', 'TREND_REVERSAL']

    for i in range(num_networks):
        network_name = f"test_net_{i:04d}"
        cursor.execute('''
            INSERT INTO networks_release
            (network_name, network_size, network_type, stability_state, build_version, last_built_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (network_name, random.randint(2, 1000), 'organic', 'stable', num_builds, now))

        # Insert scores
        cursor.execute('''
            INSERT INTO network_scores
            (network_name, score, smoothed_score, stability_coeff, stability_trend, trend_direction, risk_band, build_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (network_name, random.uniform(10, 90), random.uniform(10, 90), random.uniform(0, 1),
              random.uniform(-1, 1), random.choice(trend_dirs), random.choice(risk_bands), num_builds))

        # Insert history
        for build_ver in range(1, num_builds + 1):
            cursor.execute('''
                INSERT INTO network_score_history
                (network_name, score, smoothed_score, stability_coeff, stability_trend, trend_direction, risk_band, build_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (network_name, random.uniform(10, 90), random.uniform(10, 90), random.uniform(0, 1),
                  random.uniform(-1, 1), random.choice(trend_dirs), random.choice(risk_bands), build_ver))

    # Populate alerts
    for i in range(num_alerts):
        alert_id = f"test_alert_{i:05d}"
        network_name = f"test_net_{random.randint(0, num_networks-1):04d}"
        created_at = (datetime.utcnow() - timedelta(hours=random.randint(0, 168))).isoformat()
        acknowledged = 1 if random.random() < 0.05 else 0
        is_escalated = 1 if random.random() < 0.08 else 0

        cursor.execute('''
            INSERT INTO network_alerts
            (alert_id, network_name, alert_type, severity, message, created_at, acknowledged, is_escalated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (alert_id, network_name, random.choice(alert_types), random.choice(severities),
              f"Test alert in {network_name}", created_at, acknowledged, is_escalated))

    conn.commit()
    conn.close()

    print(f"✅ Created test database: {db_path}")
    print(f"   Networks: {num_networks}")
    print(f"   Builds: {num_builds}")
    print(f"   Alerts: {num_alerts}")

if __name__ == '__main__':
    create_test_db('.phase9a_test.db', num_networks=100, num_builds=10, num_alerts=500)
