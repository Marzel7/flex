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
  8. SecondHopExpansionBuilder   → funder_upstream_links, upstream_network_bridge, creator_second_hop
  9. NetworksReleaseBuilder      → networks_release (includes second-hop bridge counts)
 10. RiskScoringBuilder          → creator_risk_scores, network_risk_scores, risk_score_history

Each analyzer result is logged to analyzer_runs table.
Safe to run repeatedly. Exits nonzero if any analyzer failed.

Cron example (every 10 minutes):
  */10 * * * * cd /path/to/flex && python scripts/run_graph_analyzers.py >> logs/graph_analyzers_cron.log 2>&1
"""

import sys
import os
import time
import fcntl
import logging
import logging.handlers
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
        logging.handlers.RotatingFileHandler(
            _log_dir / 'graph_analyzers.log',
            maxBytes=20 * 1024 * 1024,
            backupCount=2,
            encoding='utf-8',
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def _lock_path() -> Path:
    raw = os.environ.get('FLEX_GRAPH_ANALYZER_LOCK')
    if raw:
        return Path(raw)
    return _REPO_ROOT / 'logs' / 'graph_analyzers.lock'


def _acquire_runner_lock():
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open('a+', encoding='utf-8')
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        holder = fh.read().strip()
        detail = f" ({holder})" if holder else ""
        logger.warning(f"Graph Analyzer Runner already active{detail}; skipping this run.")
        fh.close()
        return None

    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} started_at={datetime.now(timezone.utc).isoformat()}\n")
    fh.flush()
    os.fsync(fh.fileno())
    return fh


def _release_runner_lock(fh) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _resolve_db_path() -> str:
    env_path = os.environ.get('DB_PATH')
    candidate = Path(env_path).expanduser() if env_path else _REPO_ROOT / 'database' / 'flex_complete_database.db'
    if not candidate.is_absolute():
        candidate = (_REPO_ROOT / candidate).resolve()
    if candidate.exists():
        canonical = (_REPO_ROOT / 'database' / 'flex_complete_database.db').resolve()
        resolved = candidate.resolve()
        if resolved != canonical:
            raise RuntimeError(
                f"Refusing to run graph analyzers against non-canonical DB: {resolved}. "
                f"Expected: {canonical}"
            )
        return str(resolved)
    raise FileNotFoundError(
        f"Database not found. Expected: {_REPO_ROOT / 'database' / 'flex_complete_database.db'}"
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
    for attempt in range(6):
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO analyzer_runs (analyzer_name, started_at, status, created_at) VALUES (?, ?, 'running', ?)",
                (analyzer_name, time.time(), time.time())
            )
            run_id = cur.lastrowid
            conn.commit()
            conn.close()
            return run_id
        except sqlite3.OperationalError as e:
            if attempt < 5:
                logger.warning(f"[_log_run_start] DB locked, retrying in 10s ({attempt+1}/6): {e}")
                time.sleep(10)
            else:
                logger.error(f"[_log_run_start] DB still locked after 6 attempts, skipping run log: {e}")
                return -1


def _log_run_finish(db_path: str, run_id: int, started_at: float, status: str,
                    error_message: str | None, rows_written: int) -> None:
    if run_id < 0:
        return  # logging was skipped due to DB lock at start
    import sqlite3
    finished_at = time.time()
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("""
            UPDATE analyzer_runs
            SET finished_at=?, duration_seconds=?, status=?, error_message=?, rows_written=?
            WHERE id=?
        """, (finished_at, finished_at - started_at, status, error_message, rows_written, run_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[_log_run_finish] Could not write run finish: {e}")


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
            from src.utils.infra_mapping import sync_infra_wallets
            # Phase 1: compute results into a temp table (read-only, no write lock held)
            conn = _sqlite3.connect(db_path, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")
            sync_infra_wallets(conn)
            conn.execute("DROP TABLE IF EXISTS _coordinated_edges_tmp")
            conn.execute("""
                CREATE TEMP TABLE _coordinated_edges_tmp AS
                SELECT cf1.creator_address AS creator_a,
                       cf2.creator_address AS creator_b,
                       cf1.funder_address  AS bridge_funder,
                       MIN(1.0, COUNT(*) * 0.25) AS confidence
                FROM creator_funders cf1
                JOIN creator_funders cf2 ON cf1.funder_address = cf2.funder_address
                  AND cf1.creator_address < cf2.creator_address
                WHERE cf1.is_cex = 0 AND cf2.is_cex = 0
                  AND cf1.funder_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY cf1.creator_address, cf2.creator_address, cf1.funder_address
            """)
            # Phase 2: fast swap — exclusive lock held only for DELETE + INSERT from temp
            conn.execute("DELETE FROM coordinated_creator_edges")
            cur = conn.execute("""
                INSERT OR IGNORE INTO coordinated_creator_edges (creator_a, creator_b, bridge_funder, confidence)
                SELECT creator_a, creator_b, bridge_funder, confidence FROM _coordinated_edges_tmp
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

        elif name == 'SecondHopLiteWorker':
            from src.core.second_hop_lite_worker import SecondHopLiteWorker
            result = SecondHopLiteWorker(db_path).run()
            result.setdefault('status', 'success')
            result['edges_written'] = result.get('links_written', 0)

        elif name == 'SecondHopExpansionBuilder':
            from src.core.second_hop_builder import SecondHopExpansionBuilder
            result = SecondHopExpansionBuilder(db_path).build()
            result.setdefault('status', 'success')
            # Map output key for _rows_written_from_result
            result['edges_written'] = result.get('bridges_written', 0)

        elif name == 'UpstreamExpansionBuilder':
            from src.core.upstream_expansion_builder import UpstreamExpansionBuilder
            result = UpstreamExpansionBuilder(db_path).run()
            result.setdefault('status', 'success')
            result['edges_written'] = result.get('funders_enqueued', 0)

        elif name == 'NetworksReleaseBuilder':
            from src.utils.build_networks_release import build_networks_release
            result = build_networks_release(db_path)
            # Normalise key for _rows_written_from_result
            result.setdefault('status', 'success')
            result['networks_processed'] = result.get('networks_processed', 0)

        elif name == 'RiskScoringBuilder':
            from src.core.risk_scoring_builder import RiskScoringBuilder
            result = RiskScoringBuilder(db_path).run()
            result.setdefault('status', 'success')
            result['networks_processed'] = result.get('networks_scored', 0)

        elif name == 'TokenPredictionBuilder':
            from src.core.token_prediction_builder import TokenPredictionBuilder
            result = TokenPredictionBuilder(db_path).run()
            result.setdefault('status', 'success')
            result['edges_written'] = result.get('tokens_scored', 0)

        elif name == 'IntelligenceRefreshCandidateBuilder':
            from src.core.intelligence_refresh import (
                IntelligenceRefreshCandidateBuilder, apply_migration
            )
            apply_migration(db_path)
            result = IntelligenceRefreshCandidateBuilder(db_path).run()

        elif name == 'CreatorOutboundBuilder':
            from src.core.creator_outbound_builder import CreatorOutboundBuilder
            result = CreatorOutboundBuilder(db_path).run()
            result['edges_written'] = result.get('classifications_written', 0)

        elif name == 'CreatorOutboundWorker':
            from src.core.creator_outbound_worker import CreatorOutboundWorker
            result = CreatorOutboundWorker(db_path).run()
            result.setdefault('status', 'success')
            result['edges_written'] = result.get('transfers_written', 0)

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
    'CreatorOutboundBuilder',                # DB-only: classify creator_outgoing_transfers
    'IntelligenceRefreshCandidateBuilder',   # scores include outbound signals; auto-approves
    # RPC workers removed — run as daemons in main.py start_background_workers()
    # 'CreatorOutboundWorker'  — daemon: loops every 5 min
    # 'SecondHopLiteWorker'    — daemon: loops every 5 min
    'SecondHopExpansionBuilder',             # DB-only: builds 2H bridges from existing upstream links
    'UpstreamExpansionBuilder',              # DB-only: enqueues funders around significant hubs
    'NetworksReleaseBuilder',
    'RiskScoringBuilder',
    'TokenPredictionBuilder',
]


def _pending_queue_count(db_path: str) -> int:
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        n = conn.execute("SELECT COUNT(*) FROM second_hop_lite_queue WHERE status='pending'").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def _critical_queue_status(db_path: str) -> dict:
    """Return hot-path work that must outrank graph analyzers."""
    import sqlite3
    status = {
        'creator_resolution_ready': 0,
        'creator_funding_ready': 0,
        'creator_funding_running': 0,
        'oldest_ready_age_seconds': 0,
    }
    now = int(time.time())
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout=3000")

        try:
            row = conn.execute("""
                SELECT
                    COUNT(*) AS ready,
                    MIN(next_attempt_at) AS oldest_ready_at
                FROM creator_resolution_queue
                WHERE status IN ('pending', 'retry')
                  AND COALESCE(locked_until, 0) < ?
                  AND COALESCE(next_attempt_at, 0) <= ?
            """, (now, now)).fetchone()
            if row:
                status['creator_resolution_ready'] = int(row['ready'] or 0)
                oldest = int(row['oldest_ready_at'] or 0)
                if oldest:
                    status['oldest_ready_age_seconds'] = max(status['oldest_ready_age_seconds'], now - oldest)
        except sqlite3.OperationalError:
            pass

        try:
            row = conn.execute("""
                SELECT
                    SUM(CASE WHEN status IN ('pending', 'retry')
                              AND COALESCE(locked_until, 0) < ?
                              AND COALESCE(next_attempt_at, 0) <= ?
                             THEN 1 ELSE 0 END) AS ready,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                    MIN(CASE WHEN status IN ('pending', 'retry')
                              AND COALESCE(locked_until, 0) < ?
                              AND COALESCE(next_attempt_at, 0) <= ?
                             THEN next_attempt_at END) AS oldest_ready_at
                FROM creator_funding_queue
            """, (now, now, now, now)).fetchone()
            if row:
                status['creator_funding_ready'] = int(row['ready'] or 0)
                status['creator_funding_running'] = int(row['running'] or 0)
                oldest = int(row['oldest_ready_at'] or 0)
                if oldest:
                    status['oldest_ready_age_seconds'] = max(status['oldest_ready_age_seconds'], now - oldest)
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception as exc:
        status['error'] = str(exc)
    return status


def _has_critical_queue_work(db_path: str) -> tuple[bool, dict]:
    status = _critical_queue_status(db_path)
    ready = (
        int(status.get('creator_resolution_ready') or 0)
        + int(status.get('creator_funding_ready') or 0)
    )
    return ready > 0, status


def _critical_queue_guard_enabled() -> bool:
    return os.environ.get('FLEX_ANALYZER_IGNORE_CRITICAL_QUEUES', '').strip().lower() not in {'1', 'true', 'yes'}


def _main_unlocked() -> int:
    logger.info("=" * 60)
    logger.info("Graph Analyzer Runner — START")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")

    try:
        db_path = _resolve_db_path()
        logger.info(f"Database: {db_path}")
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    if _critical_queue_guard_enabled():
        has_work, queue_status = _has_critical_queue_work(db_path)
        if has_work:
            logger.warning(
                "[QUEUE_GUARD] Skipping graph analyzers: hot-path work is ready "
                f"(creator_resolution_ready={queue_status.get('creator_resolution_ready', 0)} "
                f"creator_funding_ready={queue_status.get('creator_funding_ready', 0)} "
                f"creator_funding_running={queue_status.get('creator_funding_running', 0)} "
                f"oldest_ready_age={queue_status.get('oldest_ready_age_seconds', 0)}s)"
            )
            return 0

    _ensure_analyzer_runs_table(db_path)

    suite_start = time.time()
    results = []
    for name in ANALYZERS:
        if _critical_queue_guard_enabled():
            has_work, queue_status = _has_critical_queue_work(db_path)
            if has_work:
                logger.warning(
                    "[QUEUE_GUARD] Stopping graph analyzer before "
                    f"{name}: hot-path work appeared "
                    f"(creator_resolution_ready={queue_status.get('creator_resolution_ready', 0)} "
                    f"creator_funding_ready={queue_status.get('creator_funding_ready', 0)} "
                    f"creator_funding_running={queue_status.get('creator_funding_running', 0)} "
                    f"oldest_ready_age={queue_status.get('oldest_ready_age_seconds', 0)}s)"
                )
                break

        r = run_analyzer(name, db_path)
        results.append(r)

        # Passive checkpoint after each analyzer so WAL doesn't balloon
        try:
            import sqlite3 as _sq
            _ck = _sq.connect(db_path, timeout=5)
            _ck.execute("PRAGMA wal_checkpoint(PASSIVE)")
            _ck.close()
        except Exception:
            pass

        # Yield to the listener between analyzers — gives it write windows
        # so price updates, migrations, and funding jobs aren't blocked for the
        # entire suite duration.
        time.sleep(10)

        # Pipeline boundary logging: IRC → Phase 2
        if name == 'IntelligenceRefreshCandidateBuilder':
            irc_result = r.get('result', {})
            enqueued = irc_result.get('auto_approved', 0)
            pending = _pending_queue_count(db_path)
            logger.info(f"[PIPELINE] IRC completed: {enqueued} auto-approved  |  {pending} pending in Phase 2 queue")

        elif name == 'SecondHopLiteWorker':
            shl_result = r.get('result', {})
            scanned = shl_result.get('funders_scanned', shl_result.get('funders', 0))
            remaining = _pending_queue_count(db_path)
            logger.info(f"[PIPELINE] Phase2 worker completed: {scanned} scanned  |  {remaining} still pending")

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


def main() -> int:
    lock_fh = _acquire_runner_lock()
    if lock_fh is None:
        return 0
    try:
        return _main_unlocked()
    finally:
        _release_runner_lock(lock_fh)


if __name__ == '__main__':
    sys.exit(main())
