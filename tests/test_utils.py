"""Test utilities for Phase 6A and 6B tests."""

import sqlite3
import tempfile
import os
from contextlib import contextmanager


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
        # Create networks_release table (full schema matching build_networks_release)
        db.execute('''
            CREATE TABLE IF NOT EXISTS networks_release (
                network_name TEXT PRIMARY KEY,
                network_size INTEGER DEFAULT 0,
                network_type TEXT DEFAULT 'organic',
                network_risk_level TEXT DEFAULT 'MEDIUM',
                stability_state TEXT DEFAULT 'stable',
                has_cex_funder INTEGER DEFAULT 0,
                cex_funder_count INTEGER DEFAULT 0,
                cex_funder_addresses TEXT DEFAULT '[]',
                has_infra_funder INTEGER DEFAULT 0,
                infra_funder_count INTEGER DEFAULT 0,
                infra_funder_addresses TEXT DEFAULT '[]',
                build_version INTEGER DEFAULT 1,
                last_built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # Create network_membership table (required input for build)
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_membership (
                network_name TEXT NOT NULL,
                creator_address TEXT NOT NULL,
                PRIMARY KEY (network_name, creator_address)
            );
        ''')

        # Create creator_networks table (required input for build)
        db.execute('''
            CREATE TABLE IF NOT EXISTS creator_networks (
                network_name TEXT PRIMARY KEY,
                network_risk_level TEXT DEFAULT 'MEDIUM'
            );
        ''')

        # Create creator_funders table (required input for build)
        db.execute('''
            CREATE TABLE IF NOT EXISTS creator_funders (
                creator_address TEXT NOT NULL,
                funder_address TEXT NOT NULL,
                amount_sol REAL DEFAULT 0,
                transaction_signature TEXT,
                PRIMARY KEY (creator_address, funder_address)
            );
        ''')

        # Create cex_wallets table (required input for build)
        db.execute('''
            CREATE TABLE IF NOT EXISTS cex_wallets (
                cex_address TEXT PRIMARY KEY
            );
        ''')

        # Create infra_funders_observed table (optional input for build)
        db.execute('''
            CREATE TABLE IF NOT EXISTS infra_funders_observed (
                funder_address TEXT PRIMARY KEY
            );
        ''')

        # Create coordinated_creator_edges table (optional input for build)
        db.execute('''
            CREATE TABLE IF NOT EXISTS coordinated_creator_edges (
                creator_a TEXT NOT NULL,
                creator_b TEXT NOT NULL,
                bridge_funder TEXT,
                confidence INTEGER DEFAULT 50,
                evidence_tx TEXT,
                first_seen_block_time INTEGER,
                PRIMARY KEY (creator_a, creator_b)
            );
        ''')

        # Create network_evidence table
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_evidence (
                network_name TEXT PRIMARY KEY,
                total_edges INTEGER DEFAULT 0,
                total_evidence_txs INTEGER DEFAULT 0,
                average_confidence REAL DEFAULT 0.0,
                high_confidence_edges INTEGER DEFAULT 0,
                medium_confidence_edges INTEGER DEFAULT 0,
                low_confidence_edges INTEGER DEFAULT 0,
                earliest_evidence_time INTEGER,
                latest_evidence_time INTEGER,
                evidence_span_days INTEGER DEFAULT 0,
                unique_bridge_funders INTEGER DEFAULT 0,
                bridge_funder_list TEXT DEFAULT '[]',
                evidence_risk_score REAL DEFAULT 0.0,
                evidence_version INTEGER DEFAULT 1,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_network ON network_alerts(network_name);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_build ON network_alerts(build_version);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_history_network ON network_score_history(network_name);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_nm_network ON network_membership(network_name);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_cf_creator ON creator_funders(creator_address);')

    return path


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
