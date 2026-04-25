#!/usr/bin/env python3
"""
Graph Analyzer Runner — runs all three graph analyzers in sequence.

Runs:
  1. WalletClusteringEngine      → wallet_clusters (skips if no new transfer_index rows)
  2. DevReputationUpdater        → dev_reputation (always runs)
  3. FunderOverlapAnalyzer       → funder_overlap
  4. GraphDevFarmDetectionEngine → farm_clusters, farm_cluster_members, farm_cluster_edges
  5. CoordinatedEdgesBuilder     → coordinated_creator_edges
  6. C2CEdgeBuilder              → creator_c2c_edges (direct creator→creator transfers only)
  7. NetworkMembershipBuilder    → network_membership, funder_network_map
  8. NetworksReleaseBuilder      → networks_release

Each analyzer result is logged to analyzer_runs table.
Safe to run repeatedly. Exits nonzero if any analyzer failed.

Cron example (every 10 minutes):
  */10 * * * * cd /path/to/flex && python scripts/run_graph_analyzers.py >> logs/graph_analyzers_cron.log 2>&1
"""

import sys
import os
import time
import logging
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Logging — fall back to local logs/ if /var/log/flex inaccessible
try:
    _log_dir = Path('/var/log/flex')
    _log_dir.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError):
    _log_dir = _REPO_ROOT / 'logs'
    _log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(_log_dir / 'graph_analyzers.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def _resolve_db_path() -> str:
    for candidate in [
        _REPO_ROOT / 'database' / 'flex_complete_database.db',
        _REPO_ROOT / 'flex_complete_database.db',
    ]:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        f"Database not found. Tried: database/flex_complete_database.db and flex_complete_database.db"
    )


def _ensure_analyzer_runs_table(db_path: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyzer_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            analyzer_name   TEXT NOT NULL,
            started_at      REAL NOT NULL,
            finished_at     REAL,
            duration_seconds REAL,
            status          TEXT NOT NULL DEFAULT 'running',
            error_message   TEXT,
            rows_written    INTEGER DEFAULT 0,
            created_at      REAL NOT NULL DEFAULT (unixepoch())
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analyzer_runs_name_started ON analyzer_runs(analyzer_name, started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analyzer_runs_started ON analyzer_runs(started_at DESC)")
    conn.commit()
    conn.close()


def _log_run_start(db_path: str, analyzer_name: str) -> int:
    import sqlite3
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
                    error_message: str | None, rows_written: int) -> None:
    import sqlite3
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
    """Extract meaningful row-count from an analyzer result dict."""
    for key in ('clusters_found', 'overlaps_stored', 'farms_identified',
                'farm_members_stored', 'reputations_updated',
                'edges_stored', 'edges_written', 'memberships_written', 'networks_processed'):
        if key in result and isinstance(result[key], int):
            return result[key]
    return 0


def run_analyzer(name: str, db_path: str) -> dict:
    """
    Run a single named analyzer. Returns:
        {analyzer, status, started_at, finished_at, duration_seconds, rows_written, error, result}
    """
    from src.utils.db_locking import db_connect  # noqa — ensures WAL helpers are initialised

    logger.info(f"[{name}] Starting")
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
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("DELETE FROM coordinated_creator_edges")
            cur = conn.execute("""
                INSERT OR IGNORE INTO coordinated_creator_edges (creator_a, creator_b, bridge_funder, confidence)
                SELECT cf1.creator_address, cf2.creator_address, cf1.funder_address,
                       MIN(1.0, COUNT(*) * 0.25)
                FROM creator_funders cf1
                JOIN creator_funders cf2 ON cf1.funder_address = cf2.funder_address
                  AND cf1.creator_address < cf2.creator_address
                WHERE cf1.is_cex = 0 AND cf2.is_cex = 0
                GROUP BY cf1.creator_address, cf2.creator_address, cf1.funder_address
            """)
            rows_inserted = cur.rowcount
            conn.commit()
            conn.close()
            result = {'status': 'success', 'edges_stored': rows_inserted}

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
            # Normalise key for _rows_written_from_result
            result.setdefault('status', 'success')
            result['networks_processed'] = result.get('networks_processed', 0)

        else:
            raise ValueError(f"Unknown analyzer: {name}")

        rows = _rows_written_from_result(result)
        status = result.get('status', 'success')
        error = result.get('message') if status != 'success' else None

        _log_run_finish(db_path, run_id, started_at, status, error, rows)
        duration = time.time() - started_at
        logger.info(f"[{name}] Done — status={status} rows={rows} duration={duration:.1f}s")

        return {
            'analyzer': name,
            'status': status,
            'started_at': started_at,
            'finished_at': time.time(),
            'duration_seconds': duration,
            'rows_written': rows,
            'error': error,
            'result': result,
        }

    except Exception as exc:
        duration = time.time() - started_at
        error_msg = str(exc)
        _log_run_finish(db_path, run_id, started_at, 'failed', error_msg, 0)
        logger.error(f"[{name}] FAILED after {duration:.1f}s: {error_msg}", exc_info=True)
        return {
            'analyzer': name,
            'status': 'failed',
            'started_at': started_at,
            'finished_at': time.time(),
            'duration_seconds': duration,
            'rows_written': 0,
            'error': error_msg,
            'result': {},
        }


ANALYZERS = [
    'WalletClusteringEngine',
    'DevReputationUpdater',
    'FunderOverlapAnalyzer',
    'GraphDevFarmDetectionEngine',
    'CoordinatedEdgesBuilder',
    'C2CEdgeBuilder',
    'NetworkMembershipBuilder',
    'NetworksReleaseBuilder',
]


def main() -> int:
    logger.info("=" * 60)
    logger.info("Graph Analyzer Runner — START")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")

    try:
        db_path = _resolve_db_path()
        logger.info(f"Database: {db_path}")
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    _ensure_analyzer_runs_table(db_path)

    suite_start = time.time()
    results = []
    for name in ANALYZERS:
        r = run_analyzer(name, db_path)
        results.append(r)

    suite_duration = time.time() - suite_start
    failed = [r for r in results if r['status'] != 'success']

    logger.info("=" * 60)
    logger.info(f"SUMMARY  total={len(results)}  failed={len(failed)}  duration={suite_duration:.1f}s")
    for r in results:
        mark = "✓" if r['status'] == 'success' else "✗"
        logger.info(f"  {mark} {r['analyzer']:<35} status={r['status']}  rows={r['rows_written']}  {r['duration_seconds']:.1f}s")
        if r['error']:
            logger.info(f"      error: {r['error']}")
    logger.info("=" * 60)

    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
