"""
Test fixtures and utilities for Phase 6A testing.

Provides:
- Temporary SQLite database creation
- Schema initialization
- Synthetic data seeding
- Transaction context management
"""

import sqlite3
import tempfile
import os
from pathlib import Path
from contextlib import contextmanager
import pytest
from datetime import datetime


@contextmanager
def db_transaction(db_path):
    """Context manager for safe database transactions."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def create_test_db():
    """Create a temporary SQLite test database with minimal schema."""
    # Create temp file
    fd, path = tempfile.mkstemp(suffix='.db', prefix='test_flex_')
    os.close(fd)

    with db_transaction(path) as db:
        # Create networks_release table (minimal schema for testing)
        db.execute('''
            CREATE TABLE IF NOT EXISTS networks_release (
                network_name TEXT PRIMARY KEY,
                network_size INTEGER DEFAULT 0,
                network_type TEXT DEFAULT 'organic',
                stability_state TEXT DEFAULT 'stable',
                build_version INTEGER DEFAULT 1,
                last_built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # Create network_evidence table
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_evidence (
                network_name TEXT PRIMARY KEY,
                total_edges INTEGER DEFAULT 0,
                high_confidence_edges INTEGER DEFAULT 0,
                medium_confidence_edges INTEGER DEFAULT 0,
                low_confidence_edges INTEGER DEFAULT 0,
                evidence_version INTEGER DEFAULT 1,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(network_name) REFERENCES networks_release(network_name)
            );
        ''')

        # Create network_scores table
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_scores (
                network_name TEXT PRIMARY KEY,
                score INTEGER NOT NULL DEFAULT 0,
                score_version INTEGER NOT NULL DEFAULT 1,
                score_components_json TEXT,
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
            );
        ''')

        # Create network_score_history table
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_score_history (
                network_name TEXT NOT NULL,
                build_version INTEGER NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                score_version INTEGER NOT NULL DEFAULT 1,
                components_json TEXT,
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (network_name, build_version),
                FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
            );
        ''')

        # Create network_alerts table
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                network_name TEXT NOT NULL,
                build_version INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (network_name) REFERENCES networks_release(network_name),
                UNIQUE (network_name, build_version, alert_type)
            );
        ''')

        # Create indexes
        db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_score ON network_scores(score DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_nsh_computed_at ON network_score_history(computed_at DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON network_alerts(created_at DESC);')

    return path


@pytest.fixture
def test_db():
    """Pytest fixture: Create and clean up temporary test database."""
    db_path = create_test_db()
    yield db_path
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


def seed_network(db, network_name: str, network_type: str = 'organic',
                 stability_state: str = 'stable', build_version: int = 1):
    """Insert a synthetic network into networks_release."""
    db.execute('''
        INSERT OR IGNORE INTO networks_release
        (network_name, network_type, stability_state, build_version, network_size)
        VALUES (?, ?, ?, ?, ?)
    ''', (network_name, network_type, stability_state, build_version, 0))


def seed_network_evidence(db, network_name: str, total_edges: int = 10,
                         high_confidence: int = 10, medium_confidence: int = 0,
                         low_confidence: int = 0):
    """Insert synthetic network_evidence."""
    db.execute('''
        INSERT OR REPLACE INTO network_evidence
        (network_name, total_edges, high_confidence_edges, medium_confidence_edges, low_confidence_edges)
        VALUES (?, ?, ?, ?, ?)
    ''', (network_name, total_edges, high_confidence, medium_confidence, low_confidence))


def seed_network_score(db, network_name: str, score: int = 50, score_version: int = 2,
                      components_json: str = None):
    """Insert a synthetic network_score."""
    if components_json is None:
        components_json = '{"connectivity": 25, "lifecycle": 20, "evidence": 5, "model": "v2"}'

    db.execute('''
        INSERT OR REPLACE INTO network_scores
        (network_name, score, score_version, score_components_json)
        VALUES (?, ?, ?, ?)
    ''', (network_name, score, score_version, components_json))


def seed_score_history(db, network_name: str, build_version: int, score: int,
                       score_version: int = 2, components_json: str = None):
    """Insert a synthetic score history entry."""
    if components_json is None:
        components_json = '{"connectivity": 25, "lifecycle": 20, "evidence": 5, "model": "v2"}'

    db.execute('''
        INSERT OR IGNORE INTO network_score_history
        (network_name, build_version, score, score_version, components_json)
        VALUES (?, ?, ?, ?, ?)
    ''', (network_name, build_version, score, score_version, components_json))


def insert_alert(db, network_name: str, build_version: int, alert_type: str,
                severity: str, message: str, details_json: str = None):
    """Insert a test alert."""
    db.execute('''
        INSERT OR IGNORE INTO network_alerts
        (network_name, build_version, alert_type, severity, message, details_json)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (network_name, build_version, alert_type, severity, message, details_json))


def get_alert_count(db, alert_type: str = None) -> int:
    """Get count of alerts, optionally filtered by type."""
    if alert_type:
        result = db.execute(
            'SELECT COUNT(*) as cnt FROM network_alerts WHERE alert_type = ?',
            (alert_type,)
        ).fetchone()
    else:
        result = db.execute('SELECT COUNT(*) as cnt FROM network_alerts').fetchone()
    return result['cnt']


def get_alerts(db, alert_type: str = None, network_name: str = None):
    """Get alerts from database."""
    query = 'SELECT * FROM network_alerts'
    params = []

    conditions = []
    if alert_type:
        conditions.append('alert_type = ?')
        params.append(alert_type)
    if network_name:
        conditions.append('network_name = ?')
        params.append(network_name)

    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)

    return db.execute(query, params).fetchall()


def get_score(db, network_name: str):
    """Get a network's score."""
    result = db.execute(
        'SELECT * FROM network_scores WHERE network_name = ?',
        (network_name,)
    ).fetchone()
    return result


@pytest.fixture
def test_db_with_networks(test_db):
    """Pytest fixture: Test DB with pre-seeded synthetic networks."""
    with db_transaction(test_db) as db:
        # Seed some networks for testing
        seed_network(db, 'TestNetwork1', 'organic', 'stable', 1)
        seed_network(db, 'TestNetwork2', 'cex_connected', 'growing', 1)
        seed_network(db, 'TestNetwork3', 'cex_and_infra_connected', 'new', 1)

        # Seed evidence
        seed_network_evidence(db, 'TestNetwork1', total_edges=10, high_confidence=10)
        seed_network_evidence(db, 'TestNetwork2', total_edges=10, high_confidence=0, medium_confidence=10)
        seed_network_evidence(db, 'TestNetwork3', total_edges=10, high_confidence=0, low_confidence=10)

    return test_db
