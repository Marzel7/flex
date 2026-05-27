#!/usr/bin/env python3
"""
Graph Analyzer API — trigger and status endpoints.

Routes registered:
  POST /api/run-graph-analyzers     — start analyzers in background, returns 202
  GET  /api/graph-analyzer-status   — latest run status + output table counts
"""

import os
import sys
import time
import threading
import sqlite3
from pathlib import Path
from flask import jsonify, request

# ── concurrency guard ─────────────────────────────────────────────────────────
_run_lock = threading.Lock()
_running = False
_current_analyzer: str | None = None   # name of the analyzer currently executing
_suite_started_at: float | None = None

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_db_path(db_path: str) -> str:
    if os.path.exists(db_path):
        return db_path
    fallback = str(_REPO_ROOT / 'flex_complete_database.db')
    if os.path.exists(fallback):
        return fallback
    raise FileNotFoundError(f"Database not found: {db_path}")


# ── runner (reuses logic from scripts/run_graph_analyzers.py) ─────────────────

def _ensure_analyzer_runs_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyzer_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            analyzer_name    TEXT NOT NULL,
            started_at       REAL NOT NULL,
            finished_at      REAL,
            duration_seconds REAL,
            status           TEXT NOT NULL DEFAULT 'running',
            error_message    TEXT,
            rows_written     INTEGER DEFAULT 0,
            created_at       REAL NOT NULL DEFAULT (unixepoch())
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analyzer_runs_name_started ON analyzer_runs(analyzer_name, started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analyzer_runs_started ON analyzer_runs(started_at DESC)")
    conn.commit()
    conn.close()


def _log_run_start(db_path: str, analyzer_name: str) -> int:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO analyzer_runs (analyzer_name, started_at, status, created_at) VALUES (?, ?, 'running', ?)",
        (analyzer_name, time.time(), time.time())
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def _log_run_finish(db_path: str, run_id: int, started_at: float, status: str,
                    error_message, rows_written: int) -> None:
    finished_at = time.time()
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        UPDATE analyzer_runs
        SET finished_at=?, duration_seconds=?, status=?, error_message=?, rows_written=?
        WHERE id=?
    """, (finished_at, finished_at - started_at, status, error_message, rows_written, run_id))
    conn.commit()
    conn.close()


def _rows_written_from_result(result: dict) -> int:
    for key in ('clusters_found', 'overlaps_stored', 'farms_identified',
                'farm_members_stored', 'reputations_updated',
                'edges_stored', 'edges_written', 'memberships_written', 'networks_processed'):
        if key in result and isinstance(result[key], int):
            return result[key]
    return 0


def _run_suite_background(db_path: str) -> None:
    """Run all analyzers in a daemon thread. Releases _run_lock when done."""
    global _running, _current_analyzer, _suite_started_at
    try:
        _ensure_analyzer_runs_table(db_path)
        for name in ANALYZERS:
            _current_analyzer = name
            _run_single(name, db_path)
    finally:
        _running = False
        _current_analyzer = None
        _suite_started_at = None
        _run_lock.release()


def _run_single(name: str, db_path: str) -> dict:
    started_at = time.time()
    run_id = _log_run_start(db_path, name)
    try:
        if name == 'WalletClusteringEngine':
            from src.core.wallet_clustering import WalletClusteringEngine
            result = WalletClusteringEngine(db_path).detect_and_store()
        elif name == 'DevReputationUpdater':
            from src.core.wallet_clustering import DevReputationUpdater
            result = DevReputationUpdater(db_path).run()
        elif name == 'FunderOverlapAnalyzer':
            from src.core.funder_overlap_analysis import FunderOverlapAnalyzer
            result = FunderOverlapAnalyzer(db_path).analyze_and_store()
        elif name == 'GraphDevFarmDetectionEngine':
            from src.core.graph_dev_farm_detection import GraphDevFarmDetectionEngine
            result = GraphDevFarmDetectionEngine(db_path).detect_and_store()
        elif name == 'CoordinatedEdgesBuilder':
            from src.utils.infra_mapping import sync_infra_wallets
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            sync_infra_wallets(conn)
            conn.execute("DELETE FROM coordinated_creator_edges")
            cur = conn.execute("""
                INSERT OR IGNORE INTO coordinated_creator_edges (creator_a, creator_b, bridge_funder, confidence)
                SELECT cf1.creator_address, cf2.creator_address, cf1.funder_address,
                       MIN(1.0, COUNT(*) * 0.25)
                FROM creator_funders cf1
                JOIN creator_funders cf2 ON cf1.funder_address = cf2.funder_address
                  AND cf1.creator_address < cf2.creator_address
                WHERE cf1.is_cex = 0 AND cf2.is_cex = 0
                  AND cf1.funder_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY cf1.creator_address, cf2.creator_address, cf1.funder_address
            """)
            rows_inserted = cur.rowcount
            conn.commit()
            conn.close()
            result = {'status': 'success', 'edges_stored': rows_inserted}
        elif name == 'CexCoordinationDetector':
            from src.core.cex_coordination_detector import CexCoordinationDetector
            result = CexCoordinationDetector(db_path).detect_and_store()
        elif name == 'C2CEdgeBuilder':
            from src.core.c2c_edge_builder import C2CEdgeBuilder
            result = C2CEdgeBuilder(db_path).build()
        elif name == 'NetworkMembershipBuilder':
            from src.core.network_membership_builder import NetworkMembershipBuilder
            result = NetworkMembershipBuilder(db_path).build()
            result.setdefault('status', 'success')
        elif name == 'NetworksReleaseBuilder':
            from src.utils.build_networks_release import build_networks_release
            result = build_networks_release(db_path)
            result.setdefault('status', 'success')
            result['networks_processed'] = result.get('networks_processed', 0)
        else:
            raise ValueError(f"Unknown analyzer: {name}")

        rows = _rows_written_from_result(result)
        status = result.get('status', 'success')
        error = result.get('message') if status != 'success' else None
        _log_run_finish(db_path, run_id, started_at, status, error, rows)

        return {
            'analyzer': name,
            'status': status,
            'started_at': started_at,
            'finished_at': time.time(),
            'duration_seconds': round(time.time() - started_at, 2),
            'rows_written': rows,
            'error': error,
        }
    except Exception as exc:
        error_msg = str(exc)
        _log_run_finish(db_path, run_id, started_at, 'failed', error_msg, 0)
        return {
            'analyzer': name,
            'status': 'failed',
            'started_at': started_at,
            'finished_at': time.time(),
            'duration_seconds': round(time.time() - started_at, 2),
            'rows_written': 0,
            'error': error_msg,
        }


ANALYZERS = [
    'WalletClusteringEngine',
    # DevReputationUpdater omitted — rescores 17k creators against large WAL, takes 45+ min and blocks everything
    'FunderOverlapAnalyzer',
    'GraphDevFarmDetectionEngine',
    'CoordinatedEdgesBuilder',
    'CexCoordinationDetector',
    'C2CEdgeBuilder',
    'NetworkMembershipBuilder',
    'NetworksReleaseBuilder',
]

OUTPUT_TABLES = [
    'transfer_index',
    'wallet_clusters',
    'farm_clusters',
    'farm_cluster_members',
    'farm_cluster_edges',
    'funder_overlap',
    'funder_network_map',
    'networks_release',
    'network_membership',
    'coordinated_creator_edges',
    'creator_c2c_edges',
    'cex_coordinated_groups',
]


def _table_counts(db_path: str) -> dict:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA query_only=ON")
        cur = conn.cursor()
        counts = {}
        for table in OUTPUT_TABLES:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
            except Exception:
                counts[table] = None
        conn.close()
        return counts
    except Exception:
        return {t: None for t in OUTPUT_TABLES}


def _latest_runs(db_path: str) -> dict:
    """Return the latest run row per analyzer, plus the latest overall run."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        cur = conn.cursor()

        per_analyzer = {}
        for name in ANALYZERS:
            cur.execute("""
                SELECT * FROM analyzer_runs
                WHERE analyzer_name = ?
                ORDER BY started_at DESC LIMIT 1
            """, (name,))
            row = cur.fetchone()
            per_analyzer[name] = dict(row) if row else None

        # Latest finished run across all analyzers
        cur.execute("""
            SELECT * FROM analyzer_runs
            WHERE status != 'running'
            ORDER BY started_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        latest_overall = dict(row) if row else None

        conn.close()
        return {'per_analyzer': per_analyzer, 'latest_overall': latest_overall}
    except Exception:
        return {'per_analyzer': {n: None for n in ANALYZERS}, 'latest_overall': None}


def _freshness_badge(latest_overall: dict | None) -> str:
    if not latest_overall:
        return 'unknown'
    if latest_overall.get('status') == 'failed':
        return 'failed'
    finished = latest_overall.get('finished_at') or latest_overall.get('started_at')
    if not finished:
        return 'unknown'
    age_minutes = (time.time() - finished) / 60
    if age_minutes < 15:
        return 'fresh'
    if age_minutes < 60:
        return 'stale'
    return 'old'


# ── Flask route handlers ───────────────────────────────────────────────────────

def _make_run_handler(db_path: str):
    def handle_run():
        global _running, _suite_started_at
        if not _run_lock.acquire(blocking=False):
            return jsonify({
                'status': 'busy',
                'error': 'Analyzers are already running',
                'started_at': _suite_started_at,
            }), 409

        _running = True
        _suite_started_at = time.time()
        t = threading.Thread(target=_run_suite_background, args=(db_path,), daemon=True)
        t.start()

        return jsonify({
            'status': 'started',
            'started_at': _suite_started_at,
            'message': 'Analyzers running in background. Poll /api/graph-analyzer-status for progress.',
        }), 202
    handle_run.__name__ = 'api_run_graph_analyzers'
    return handle_run


def _make_status_handler(db_path: str):
    def handle_status():
        try:
            _ensure_analyzer_runs_table(db_path)
        except Exception:
            pass

        runs = _latest_runs(db_path)
        counts = _table_counts(db_path)
        latest = runs['latest_overall']
        badge = _freshness_badge(latest)

        age_seconds = None
        if latest:
            finished = latest.get('finished_at') or latest.get('started_at')
            if finished:
                age_seconds = round(time.time() - finished)

        # Extra freshness fields for networks
        networks_meta = {}
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute("SELECT MAX(last_built_at) FROM networks_release").fetchone()
            last_built = row[0] if row else None
            networks_meta = {
                'last_built_at': last_built,
                'fresh': last_built is not None,
            }
            conn.close()
        except Exception:
            pass

        return jsonify({
            'is_running': _running,
            'current_analyzer': _current_analyzer,
            'suite_started_at': _suite_started_at,
            'freshness': badge,
            'age_seconds': age_seconds,
            'latest_overall': latest,
            'per_analyzer': runs['per_analyzer'],
            'table_counts': counts,
            'networks_release': networks_meta,
        })
    handle_status.__name__ = 'api_graph_analyzer_status'
    return handle_status


# ── registration ──────────────────────────────────────────────────────────────

def register_graph_analyzer_api(app, db_path: str) -> None:
    app.add_url_rule(
        '/api/run-graph-analyzers',
        view_func=_make_run_handler(db_path),
        methods=['POST'],
    )
    app.add_url_rule(
        '/api/graph-analyzer-status',
        view_func=_make_status_handler(db_path),
        methods=['GET'],
    )
