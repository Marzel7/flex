"""
Token Price API Routes

Exposes the token price service via Flask REST API.
Includes price confidence scoring and launch outcome tracking.
"""

import logging
import time
import sqlite3
from flask import Blueprint, request, jsonify
from src.core.price_service import get_price_service, TokenPrice
from src.core.price_confidence import get_confidence_scorer
from src.core.launch_outcome_tracker import get_outcome_tracker
from src.core.price_worker import get_price_worker, PriceWorkerRegistry
from src.core.price_aggregation import get_price_aggregator
from src.core.price_anomaly_detection import get_anomaly_detector
from src.core.liquidity_intelligence import get_liquidity_intelligence
from src.core.liquidity_worker import get_liquidity_worker

logger = logging.getLogger(__name__)

price_api = Blueprint('price_api', __name__, url_prefix='/api/price')

# Simple in-memory cache for token metadata with TTL
_metadata_cache = {}
_metadata_cache_time = {}

# Database path (will be initialized when register_price_api is called)
_db_path = 'database/flex_complete_database.db'

import os as _os
_WS_STATS_PATH = _os.path.normpath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'logs', 'ws_stats.json')
)
_PUMPFUN_PREMIG_LOG_PATH = _os.path.normpath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'logs', 'premigration.log')
)
_PUMPFUN_LISTENER_LOG_PATH = _os.path.normpath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'listener.log')
)

def _read_ws_stats() -> dict:
    """Read real WS counters written by the listener process. Returns empty dict on any error."""
    try:
        import json as _json
        with open(_WS_STATS_PATH) as _f:
            return _json.load(_f)
    except Exception:
        return {}


def _listener_log_activity(now: int) -> dict:
    """Best-effort listener liveness from log mtimes."""
    freshest = 0
    premig_mtime = 0
    listener_mtime = 0
    try:
        if _os.path.exists(_PUMPFUN_PREMIG_LOG_PATH):
            premig_mtime = int(_os.path.getmtime(_PUMPFUN_PREMIG_LOG_PATH))
            freshest = max(freshest, premig_mtime)
    except Exception:
        premig_mtime = 0
    try:
        if _os.path.exists(_PUMPFUN_LISTENER_LOG_PATH):
            listener_mtime = int(_os.path.getmtime(_PUMPFUN_LISTENER_LOG_PATH))
            freshest = max(freshest, listener_mtime)
    except Exception:
        listener_mtime = 0

    age = (now - freshest) if freshest else None
    return {
        'premig_log_mtime': premig_mtime or None,
        'listener_log_mtime': listener_mtime or None,
        'last_activity_at': freshest or None,
        'age_secs': age,
        'active': age is not None and age <= 30,
        'recent': age is not None and age <= 120,
    }


def _configure_sqlite_wal(db_path: str) -> None:
    """Enable WAL mode for safer concurrent access."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        conn.commit()
        conn.close()
        logger.info("SQLite WAL mode enabled for metadata cache")
    except Exception as e:
        logger.warning(f"Failed to enable WAL mode: {e}")


def _ensure_metadata_cache_table(db_path: str) -> None:
    """Create metadata_cache table if not exists."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata_cache (
                mint TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                cached_at INTEGER NOT NULL,
                cached_source TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info("metadata_cache table ensured")
    except Exception as e:
        logger.error(f"Failed to ensure metadata_cache table: {e}")


def _get_metadata_from_sqlite(db_path: str, mint: str, max_age: int = 300):
    """Get metadata from SQLite if fresh."""
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, name, cached_at, cached_source
            FROM metadata_cache
            WHERE mint = ?
        """, (mint,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        symbol, name, cached_at, source = row
        age = int(time.time()) - cached_at

        if age <= max_age:
            return {
                'symbol': symbol,
                'name': name,
                'cached_at': cached_at,
                'source': source,
                'age': age
            }

        return None  # Stale

    except Exception as e:
        logger.debug(f"SQLite metadata lookup error for {mint}: {e}")
        return None


def _store_metadata_to_sqlite(db_path: str, mint: str, symbol: str, name: str, source: str):
    """Store metadata in SQLite cache."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO metadata_cache
            (mint, symbol, name, cached_at, cached_source)
            VALUES (?, ?, ?, ?, ?)
        """, (mint, symbol, name, int(time.time()), source))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to store metadata for {mint}: {e}")


def _fetch_symbol_from_dexscreener(mint: str):
    """Fetch token symbol and name from Dexscreener API."""
    import requests

    resp = requests.get(
        f'https://api.dexscreener.com/latest/dex/tokens/{mint}',
        timeout=5
    )

    if resp.status_code == 200:
        data = resp.json()
        if data.get('pairs') and len(data['pairs']) > 0:
            base_token = data['pairs'][0].get('baseToken', {})
            return (
                base_token.get('symbol', mint[:8].upper()),
                base_token.get('name', 'Token')
            )

    raise Exception(f"Failed to fetch metadata from Dexscreener: {resp.status_code}")


def get_token_symbol_cached(db_path: str, mint: str) -> dict:
    """
    Get token symbol/name with multi-level caching.

    Lookup order:
    1. In-memory cache (fresh)
    2. SQLite cache (fresh)
    3. Upstream fetch
    4. Stale SQLite cache
    5. Default

    Never returns 404. Always returns valid symbol/name.
    """
    # 1. Check in-memory cache
    if mint in _metadata_cache:
        cached = _metadata_cache[mint]
        if time.time() - _metadata_cache_time.get(mint, 0) < 3600:
            return {
                'symbol': cached['symbol'],
                'name': cached['name'],
                'source': 'memory_cache',
                'is_fresh': True,
                'is_stale': False
            }

    # 2. Check SQLite cache
    sqlite_result = _get_metadata_from_sqlite(db_path, mint, max_age=3600)
    if sqlite_result:
        # Hydrate memory cache
        _metadata_cache[mint] = {
            'symbol': sqlite_result['symbol'],
            'name': sqlite_result['name']
        }
        _metadata_cache_time[mint] = time.time()
        return {
            'symbol': sqlite_result['symbol'],
            'name': sqlite_result['name'],
            'source': 'sqlite_cache',
            'is_fresh': True,
            'is_stale': False
        }

    # 2b. Check token_analysis + tracked_tokens (available immediately after detection)
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        row = conn.execute("""
            SELECT COALESCE(tt.symbol, ta.symbol) AS symbol,
                   ta.name
            FROM (SELECT ? AS mint) m
            LEFT JOIN tracked_tokens tt ON tt.mint = m.mint
            LEFT JOIN token_analysis ta ON ta.mint = m.mint
        """, (mint,)).fetchone()
        conn.close()
        if row and row[0] and row[0] not in ('UNKNOWN', ''):
            sym, nm = row[0], row[1] or row[0]
            _metadata_cache[mint] = {'symbol': sym, 'name': nm}
            _metadata_cache_time[mint] = time.time()
            return {'symbol': sym, 'name': nm, 'source': 'db_analysis', 'is_fresh': True, 'is_stale': False}
    except Exception:
        pass

    # 3. Try upstream fetch
    try:
        symbol, name = _fetch_symbol_from_dexscreener(mint)

        # Store in both caches
        now = time.time()
        _metadata_cache[mint] = {
            'symbol': symbol,
            'name': name
        }
        _metadata_cache_time[mint] = now
        _store_metadata_to_sqlite(db_path, mint, symbol, name, 'dexscreener')

        return {
            'symbol': symbol,
            'name': name,
            'source': 'dexscreener',
            'is_fresh': True,
            'is_stale': False
        }

    except Exception as e:
        logger.debug(f"Upstream fetch failed for {mint}: {e}")

    # 4. Fall back to stale SQLite cache
    stale_sqlite = _get_metadata_from_sqlite(db_path, mint, max_age=999999)
    if stale_sqlite:
        return {
            'symbol': stale_sqlite['symbol'],
            'name': stale_sqlite['name'],
            'source': 'stale_sqlite',
            'is_fresh': False,
            'is_stale': True
        }

    # 5. Default (never 404)
    return {
        'symbol': 'UNKNOWN',
        'name': 'Unknown Token',
        'source': 'default',
        'is_fresh': False,
        'is_stale': True
    }


# Phase 5: Warm-up metrics tracking
_warmup_stats = {
    'price_queued': 0,
    'price_completed': 0,
    'price_failed': 0,
    'metadata_queued': 0,
    'metadata_completed': 0,
    'metadata_failed': 0,
    'skipped_due_to_queue': 0,
    'skipped_due_to_timeout': 0,
}


def _on_warmup_complete(mint: str, price, task_type: str) -> None:
    """Track warm-up completion."""
    key = f'{task_type}'
    if price:
        _warmup_stats[f'{key}_completed'] = _warmup_stats.get(f'{key}_completed', 0) + 1
    else:
        _warmup_stats[f'{key}_failed'] = _warmup_stats.get(f'{key}_failed', 0) + 1


def _persist_warmup_price(mint: str, price) -> None:
    """Persist successful warm-up fetches so first-price warm-ups are real, not just metrics."""
    try:
        if not price or getattr(price, 'source', None) == 'unavailable':
            return
        worker = get_price_worker()
        if worker:
            worker._on_price_fetched(mint, price)
    except Exception as e:
        logger.debug(f"Warm-up price persist skipped for {mint}: {e}")


def _register_pool_accounts(pool_accounts: list) -> int:
    """Upsert pool account registrations into token_pool_accounts. Returns count inserted."""
    now = int(time.time())
    count = 0
    try:
        with sqlite3.connect(_db_path, timeout=5) as conn:
            for pa in pool_accounts:
                conn.execute("""
                    INSERT OR REPLACE INTO token_pool_accounts
                    (mint, base_account, quote_account, pool_program,
                     base_token, quote_token, base_decimals, quote_decimals,
                     is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, COALESCE(
                        (SELECT created_at FROM token_pool_accounts
                         WHERE mint=? AND base_account=?), ?
                    ), ?)
                """, (
                    pa['mint'], pa['base_account'], pa['quote_account'],
                    pa.get('pool_program', 'raydium_amm'),
                    pa.get('base_token', pa['mint']),
                    pa.get('quote_token', 'So11111111111111111111111111111111111111112'),
                    pa.get('base_decimals', 6), pa.get('quote_decimals', 9),
                    pa['mint'], pa['base_account'], now, now,
                ))
                count += 1
            conn.commit()
        logger.info(f"Registered {count} pool accounts")
    except Exception as e:
        logger.error(f"Error registering pool accounts: {e}")
    return count


def _count_active_pools() -> int:
    """Count active pool registrations."""
    try:
        with sqlite3.connect(_db_path, timeout=5) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM token_pool_accounts WHERE is_active=1")
            return cursor.fetchone()[0]
    except Exception:
        return 0


@price_api.route('/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """
    Get token symbol and name with multi-level caching.

    Never returns 404. Uses persistent SQLite cache + in-memory cache.

    Returns: {symbol, name, source, is_fresh, is_stale}
    """
    try:
        result = get_token_symbol_cached(_db_path, mint)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error in get_token_symbol for {mint}: {e}")
        # Return safe default
        return jsonify({
            'symbol': 'UNKNOWN',
            'name': 'Unknown Token',
            'source': 'error',
            'is_fresh': False,
            'is_stale': True
        }), 200


@price_api.route('/diagnostics', methods=['GET'])
def diagnostics():
    """Process identity + path diagnostics. Safe to call at any time."""
    import os
    try:
        from src.core.ws_snapshot_logger import _LOG_PATH as _ws_log_path
        ws_log_abs = os.path.abspath(_ws_log_path)
        ws_log_exists = os.path.isfile(ws_log_abs)
        ws_log_size = os.path.getsize(ws_log_abs) if ws_log_exists else 0
    except Exception as ex:
        ws_log_abs = f'(error: {ex})'
        ws_log_exists = False
        ws_log_size = 0

    db_abs = os.path.abspath(_db_path)
    worker_info = {}
    try:
        worker = get_price_worker()
        worker_info = {
            'worker_id': hex(id(worker)),
            'running': worker.running,
            'cycles': worker.stats.get('cycles', 0),
            'ws_disabled': getattr(worker, '_ws_disabled', None),
            'ws_started': getattr(worker, '_ws_started', None),
            'ws_bootstrap': getattr(worker, '_ws_bootstrap', None),
        }
    except Exception:
        pass

    return jsonify({
        'role': 'flask',
        'pid': os.getpid(),
        'cwd': os.getcwd(),
        'db_path': db_abs,
        'db_exists': os.path.isfile(db_abs),
        'ws_snapshot_log': ws_log_abs,
        'ws_snapshot_log_exists': ws_log_exists,
        'ws_snapshot_log_bytes': ws_log_size,
        'flex_ws_disabled': os.environ.get('FLEX_WS_DISABLED', '0'),
        'worker': worker_info,
        'timestamp': int(time.time()),
    })


@price_api.route('/<mint>', methods=['GET'])
def get_price(mint: str):
    """
    Get current price for a single token.

    Query params:
    - cache_type: 'snapshot' (30s, no upstream), 'hot' (10s), 'org' (30s), 'history' (5m). Default: 'snapshot'

    Dashboard should use default 'snapshot' to avoid triggering upstream calls.
    Example: GET /api/price/EPjFWaLb3odcccccccccccccccccccccccccccccccccc?cache_type=hot
    """
    try:
        cache_type = request.args.get('cache_type', 'snapshot')
        service = get_price_service()
        
        price = service.get_token_price_sync(mint, cache_type)
        
        return jsonify({
            'mint': price.mint,
            'price_usd': price.price_usd,
            'price_sol': price.price_sol,
            'liquidity_usd': price.liquidity_usd,
            'volume_24h': price.volume_24h,
            'market_cap': price.market_cap,
            'peak_market_cap': price.peak_market_cap,
            'peak_market_cap_at': price.peak_market_cap_at,
            'source': price.source,
            'pair_address': price.pair_address,
            'timestamp': price.timestamp,
            'is_stale': price.is_stale,
            'freshness': 'live' if price.source != 'cached' else 'stale'
        })
    except Exception as e:
        logger.error(f"Error getting price for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/batch', methods=['POST'])
def get_prices_batch():
    """
    Get prices for multiple tokens in one request.
    
    Body: {"mints": ["mint1", "mint2", ...], "cache_type": "hot"}
    
    Returns: {"mint1": {...}, "mint2": {...}, ...}
    """
    try:
        data = request.get_json()
        mints = data.get('mints', [])
        cache_type = data.get('cache_type', 'hot')
        
        if not mints or not isinstance(mints, list):
            return jsonify({'error': 'mints must be a non-empty list'}), 400
        
        if len(mints) > 100:
            return jsonify({'error': 'Maximum 100 mints per request'}), 400
        
        service = get_price_service()
        prices = service.get_token_prices_sync(mints, cache_type)
        
        result = {}
        for mint, price in prices.items():
            result[mint] = {
                'mint': price.mint,
                'price_usd': price.price_usd,
                'price_sol': price.price_sol,
                'liquidity_usd': price.liquidity_usd,
                'volume_24h': price.volume_24h,
                'market_cap': price.market_cap,
                'source': price.source,
                'pair_address': price.pair_address,
                'timestamp': price.timestamp,
                'is_stale': price.is_stale,
                'freshness': 'live' if price.source != 'cached' else 'stale'
            }
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting batch prices: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/history', methods=['GET'])
def get_price_history(mint: str):
    """
    Get historical price snapshots.
    
    Query params:
    - hours: Number of hours to look back. Default: 24
    
    Returns: List of snapshots with timestamps
    """
    try:
        hours = request.args.get('hours', 24, type=int)
        
        if hours < 1 or hours > 720:  # Max 30 days
            return jsonify({'error': 'hours must be between 1 and 720'}), 400
        
        service = get_price_service()
        history = service.get_price_history(mint, hours)
        
        return jsonify({
            'mint': mint,
            'hours': hours,
            'snapshots': history,
            'count': len(history)
        })
    except Exception as e:
        logger.error(f"Error getting price history for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


def _db_snapshot_cleanup(db_path: str) -> dict:
    """Read latest cleanup stats from snapshot_cleanup_log."""
    try:
        conn = sqlite3.connect(db_path, timeout=3)
        cur = conn.cursor()
        cur.execute(
            "SELECT ts, snapshots_deleted, tokens_deleted, snapshots_downsampled "
            "FROM snapshot_cleanup_log ORDER BY ts DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.execute(
            "SELECT SUM(snapshots_deleted), SUM(tokens_deleted), SUM(snapshots_downsampled) "
            "FROM snapshot_cleanup_log WHERE ts > strftime('%s','now','-24 hours')"
        )
        totals = cur.fetchone()
        conn.close()
        if row:
            return {
                'last_cleanup_at': row[0],
                'snapshots_deleted_24h': totals[0] or 0,
                'tokens_deleted_24h': totals[1] or 0,
                'snapshots_downsampled_24h': totals[2] or 0,
            }
    except Exception:
        pass
    return {}


def _db_health_signals(db_path: str, window_secs: int = 60) -> dict:
    """Query DB for cross-process activity signals. All metrics are DB-backed."""
    signals = {
        'last_snapshot_at': 0,
        'snapshots_in_window': 0,
        'tokens_priced_in_window': 0,
        'active_pools': 0,
        'snapshots_60s': 0,
        'unique_mints_60s': 0,
        'last_analysis_at': 0,
        'last_snapshot_count_update_at': 0,
        'active_writer': 'unknown',
    }
    try:
        conn = sqlite3.connect(db_path, timeout=3)
        cur = conn.cursor()

        cur.execute("SELECT MAX(captured_at) FROM token_price_snapshots")
        signals['last_snapshot_at'] = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*), COUNT(DISTINCT mint) FROM token_price_snapshots "
            "WHERE captured_at > strftime('%s','now',?)",
            (f'-{window_secs} seconds',)
        )
        row = cur.fetchone()
        signals['snapshots_in_window'] = row[0] or 0
        signals['snapshots_60s'] = row[0] or 0
        signals['unique_mints_60s'] = row[1] or 0

        cur.execute(
            "SELECT COUNT(*) FROM tracked_tokens "
            "WHERE last_price_update > strftime('%s','now',?)",
            (f'-{window_secs} seconds',)
        )
        signals['tokens_priced_in_window'] = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM token_pool_accounts "
            "WHERE is_active=1 AND vault_validation_status IN ('validated','pending')"
        )
        signals['active_pools'] = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT MAX(analyzed_at) FROM token_analysis "
            "WHERE analyzed_at > strftime('%s','now',?)",
            (f'-{window_secs} seconds',)
        )
        signals['last_analysis_at'] = cur.fetchone()[0] or 0

        cur.execute("SELECT MAX(last_updated) FROM token_snapshot_counts")
        signals['last_snapshot_count_update_at'] = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT source FROM token_price_snapshots "
            "WHERE captured_at > strftime('%s','now','-300 seconds') "
            "GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            src = row[0] or ''
            signals['active_writer'] = 'listener' if src == 'pool' else ('main' if src else 'fallback')

        conn.close()
    except Exception:
        pass
    return signals


@price_api.route('/health', methods=['GET'])
def health():
    """Health check — prioritize real-time liveness over batched history writes."""
    try:
        now = int(time.time())
        sig = _db_health_signals(_db_path, window_secs=60)
        snapshot_cleanup = _db_snapshot_cleanup(_db_path)
        listener_activity = _listener_log_activity(now)

        last_snapshot_at = sig['last_snapshot_at']
        last_count_update_at = sig['last_snapshot_count_update_at']

        # Use freshest DB-backed signal for service/worker liveness.
        # token_price_snapshots can lag because history writes are batched/conditional,
        # while token_snapshot_counts reflects the live snapshot stream.
        freshest_db_activity = max(last_snapshot_at or 0, last_count_update_at or 0)
        secs_since_activity = (now - freshest_db_activity) if freshest_db_activity else 9999
        secs_since_snapshot = (now - last_snapshot_at) if last_snapshot_at else None

        worker_alive = (
            secs_since_activity <= 60
            or sig['snapshots_in_window'] > 0
            or sig['tokens_priced_in_window'] > 0
            or listener_activity['active']
        )

        # WS liveness: prefer activity-count signal over stale timestamp
        ws_alive = (
            sig['snapshots_in_window'] > 0
            or sig['unique_mints_60s'] > 0
            or secs_since_activity <= 60
            or listener_activity['active']
        )

        if ws_alive and worker_alive:
            ws_status = 'CONNECTED'
            service_status = 'healthy'
        elif ws_alive or worker_alive or listener_activity['recent']:
            ws_status = 'DEGRADED'
            service_status = 'degraded'
        else:
            ws_status = 'DISCONNECTED'
            service_status = 'critical'

        # Real WS counters from listener process (written to disk each worker cycle)
        ws_file = _read_ws_stats()
        ws_file_age = (now - ws_file['written_at']) if ws_file.get('written_at') else None
        effective_last_event_at = max(
            int(ws_file.get('last_event_at') or 0),
            int(freshest_db_activity or 0),
            int(listener_activity.get('last_activity_at') or 0),
        ) or None
        effective_connected = bool(
            ws_file.get('connected')
            or ws_alive
            or listener_activity['active']
        )

        pool_stats = {
            'pools_registered': sig['active_pools'],
            'pool_prices_cached': sig['unique_mints_60s'],
            'pool_prices_fetched_last_cycle': sig['snapshots_60s'],
            'pool_attempted': sig['snapshots_60s'],
            'pool_success': sig['unique_mints_60s'],
            'pool_fail': 0,
            'ws': {
                'connected': effective_connected,
                'status': ws_status,
                'subscriptions': ws_file.get('subscriptions', sig['active_pools'] * 2),
                'events_received': ws_file.get('events_received', 0),
                'events_decoded': ws_file.get('events_decoded', 0),
                'events_deduplicated': ws_file.get('events_deduplicated', 0),
                'reconnects': ws_file.get('reconnects', 0),
                'last_event_at': effective_last_event_at,
                'stats_age_secs': ws_file_age,
                'multi_pool_enabled': True,
                'listener_log_age_secs': listener_activity.get('age_secs'),
                'listener_log_last_activity_at': listener_activity.get('last_activity_at'),
            },
            'detection': {
                'primary_success': sig['unique_mints_60s'],
                'fallback_used': 0,
                'total_attempted': sig['snapshots_60s'],
            }
        }

        local_diag = {}
        try:
            import os
            service = get_price_service()
            worker = get_price_worker()
            local_diag = {
                'note': 'Local Flask process only — listener process owns pricing',
                'pid': os.getpid(),
                'cache_size': len(service.cache.cache) if service else 0,
                'worker_cycles': worker.stats.get('cycles', 0) if worker else 0,
                'worker_running_flag': worker.running if worker else False,
                'rolling_window_stats': service.get_rolling_window_stats() if service else {},
            }
        except Exception:
            pass

        return jsonify({
            'status': service_status,
            'worker_running': worker_alive,
            'cache_size': local_diag.get('cache_size', 0),
            'worker_stats': {
                'registry': {'active': sig['active_pools'], 'total_tracked': sig['active_pools']},
                'worker': {
                    'cycles': sig['snapshots_in_window'],
                    'errors': 0,
                    'last_run': secs_since_activity if freshest_db_activity else None,
                    'pool_prices_fetched': sig['unique_mints_60s'],
                }
            },
            'warm_up_stats': _warmup_stats.copy(),
            'pool_stats': pool_stats,
            'rolling_window_stats': local_diag.get('rolling_window_stats', {}),
            'snapshot_cleanup': snapshot_cleanup,
            'db_signals': {
                'last_snapshot_at': last_snapshot_at or None,
                'last_snapshot_count_update_at': last_count_update_at or None,
                'seconds_since_last_update': secs_since_activity if freshest_db_activity else None,
                'seconds_since_last_history_snapshot': secs_since_snapshot,
                'snapshots_in_window': sig['snapshots_in_window'],
                'tokens_priced_in_window': sig['tokens_priced_in_window'],
                'active_pools': sig['active_pools'],
                'unique_mints_60s': sig['unique_mints_60s'],
                'last_analysis_at': sig['last_analysis_at'],
                'inferred_active_writer': sig['active_writer'],
                'listener_log_last_activity_at': listener_activity.get('last_activity_at'),
                'listener_log_age_secs': listener_activity.get('age_secs'),
            },
            'local_process_diagnostics': local_diag,
            'timestamp': now,
        })
    except Exception as e:
        logger.exception("Health endpoint failed")
        now = int(time.time())
        return jsonify({
            'status': 'critical',
            'worker_running': False,
            'cache_size': 0,
            'worker_stats': {
                'registry': {'active': 0, 'total_tracked': 0},
                'worker': {'cycles': 0, 'errors': 0, 'last_run': None, 'pool_prices_fetched': 0}
            },
            'warm_up_stats': {},
            'pool_stats': {
                'pools_registered': 0,
                'pool_prices_cached': 0,
                'pool_prices_fetched_last_cycle': 0,
                'pool_attempted': 0,
                'pool_success': 0,
                'pool_fail': 0,
                'ws': {
                    'connected': False,
                    'status': 'DISCONNECTED',
                    'subscriptions': 0,
                    'events_received': 0,
                    'events_decoded': 0,
                    'events_deduplicated': 0,
                    'reconnects': 0,
                    'last_event_at': None,
                    'stats_age_secs': None,
                    'multi_pool_enabled': False,
                },
                'detection': {
                    'primary_success': 0,
                    'fallback_used': 0,
                    'total_attempted': 0,
                }
            },
            'rolling_window_stats': {},
            'snapshot_cleanup': {},
            'db_signals': {
                'last_snapshot_at': None,
                'last_snapshot_count_update_at': None,
                'seconds_since_last_update': None,
                'seconds_since_last_history_snapshot': None,
                'snapshots_in_window': 0,
                'tokens_priced_in_window': 0,
                'active_pools': 0,
                'unique_mints_60s': 0,
                'last_analysis_at': None,
                'inferred_active_writer': 'unknown',
            },
            'local_process_diagnostics': {},
            'timestamp': now,
            'error': str(e),
        }), 200


@price_api.route('/pool/register', methods=['POST'])
def register_pool_accounts():
    """Register pool account mappings for pool-based pricing."""
    try:
        data = request.get_json(force=True) or {}
        pool_accounts = data.get('pool_accounts', [])
        
        if not pool_accounts:
            return jsonify({'error': 'pool_accounts required'}), 400
        
        count = _register_pool_accounts(pool_accounts)
        return jsonify({'registered': count, 'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Error registering pool accounts: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/confidence', methods=['GET'])
def get_price_confidence(mint: str):
    """
    Get price with confidence score.

    Query params:
    - cache_type: 'hot' (10s), 'org' (30s), 'history' (5m). Default: 'hot'

    Returns: TokenPrice + confidence band and component scores
    """
    try:
        cache_type = request.args.get('cache_type', 'hot')
        service = get_price_service()
        scorer = get_confidence_scorer()

        price = service.get_token_price_sync(mint, cache_type)
        confidence = scorer.compute_confidence(price)

        return jsonify({
            'mint': price.mint,
            'price_usd': price.price_usd,
            'price_sol': price.price_sol,
            'liquidity_usd': price.liquidity_usd,
            'volume_24h': price.volume_24h,
            'market_cap': price.market_cap,
            'source': price.source,
            'pair_address': price.pair_address,
            'timestamp': price.timestamp,
            'is_stale': price.is_stale,
            'freshness': 'live' if price.source != 'cached' else 'stale',
            'confidence': {
                'band': confidence.confidence_band,
                'score': confidence.confidence_score,
                'liquidity_score': confidence.liquidity_score,
                'volume_score': confidence.volume_score,
                'source_score': confidence.source_score,
                'stability_score': confidence.stability_score,
                'reasons': confidence.reasons
            }
        })
    except Exception as e:
        logger.error(f"Error getting price confidence for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/outcome', methods=['GET'])
def get_launch_outcome(mint: str):
    """
    Get launch outcome (post-launch performance) for a token.

    Returns: Launch price, current price, ATH, return multiple, rug flag
    """
    try:
        tracker = get_outcome_tracker()
        outcome = tracker.get_outcome(mint)

        if not outcome:
            return jsonify({'error': f'No outcome tracked for {mint}'}), 404

        return jsonify({
            'mint': outcome.mint,
            'organization_id': outcome.organization_id,
            'launch_price_usd': outcome.launch_price_usd,
            'current_price_usd': outcome.current_price_usd,
            'ath_price_usd': outcome.ath_price_usd,
            'return_multiple': outcome.return_multiple,
            'rug_flag': outcome.rug_flag,
            'launched_at': outcome.launched_at,
            'updated_at': outcome.updated_at
        })
    except Exception as e:
        logger.error(f"Error getting launch outcome for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/tracked/register', methods=['POST'])
def register_tracked_token():
    """
    Register a token for background price prefetching.

    Body: {
        "mint": "token_mint",
        "symbol": "symbol",
        "pair_address": "address",
        "priority_level": "HIGH|MEDIUM|LOW"
    }
    """
    try:
        data = request.get_json()
        mint = data.get('mint')
        symbol = data.get('symbol')
        pair_address = data.get('pair_address')
        priority = data.get('priority_level', 'MEDIUM')

        if not mint:
            return jsonify({'error': 'mint required'}), 400

        registry = PriceWorkerRegistry()
        success = registry.register_token(mint, symbol, pair_address, priority)

        if success:
            return jsonify({'status': 'registered', 'mint': mint}), 200
        else:
            return jsonify({'error': 'Failed to register token'}), 500
    except Exception as e:
        logger.error(f"Error registering tracked token: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/tracked/stats', methods=['GET'])
def get_tracked_stats():
    """Get statistics about tracked tokens and worker."""
    try:
        registry = PriceWorkerRegistry()
        worker = get_price_worker()

        return jsonify({
            'registry': registry.get_stats(),
            'worker': worker.get_stats()
        })
    except Exception as e:
        logger.error(f"Error getting tracked stats: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/worker/start', methods=['POST'])
def start_worker():
    """Start the background price worker."""
    try:
        worker = get_price_worker()
        if not worker.running:
            worker.start()

        return jsonify({
            'status': 'started' if worker.running else 'already_running',
            'running': worker.running
        })
    except Exception as e:
        logger.error(f"Error starting worker: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/worker/stop', methods=['POST'])
def stop_worker():
    """Stop the background price worker."""
    try:
        worker = get_price_worker()
        worker.stop()

        return jsonify({
            'status': 'stopped',
            'running': worker.running
        })
    except Exception as e:
        logger.error(f"Error stopping worker: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/aggregated', methods=['GET'])
def get_aggregated_price(mint: str):
    """
    Get aggregated price from multiple sources.

    Query params:
    - method: 'median' (default) or 'weighted'

    Returns: Consensus price with sources used and aggregation method
    """
    try:
        method = request.args.get('method', 'median')
        aggregator = get_price_aggregator()

        agg_price = aggregator.aggregate_price_sync(mint, method)

        if not agg_price:
            return jsonify({'error': f'Could not aggregate price for {mint}'}), 500

        return jsonify({
            'mint': agg_price.mint,
            'price_usd': agg_price.price_usd,
            'price_sol': agg_price.price_sol,
            'liquidity_usd': agg_price.liquidity_usd,
            'volume_24h': agg_price.volume_24h,
            'market_cap': agg_price.market_cap,
            'source': 'aggregated',
            'sources_used': agg_price.sources_used,
            'source_count': agg_price.source_count,
            'timestamp': agg_price.timestamp,
            'aggregation_method': agg_price.aggregation_method
        })
    except Exception as e:
        logger.error(f"Error getting aggregated price for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/anomaly', methods=['GET'])
def get_price_anomaly(mint: str):
    """
    Detect price anomalies.

    Returns: Anomaly detection result with score, type, and reasons
    """
    try:
        # Get current price
        service = get_price_service()
        price = service.get_token_price_sync(mint, cache_type='org')

        if price.source == 'unavailable':
            return jsonify({'error': 'No price data available'}), 404

        # Detect anomalies
        detector = get_anomaly_detector()
        result = detector.detect_anomaly(
            mint=mint,
            current_price=price.price_usd,
            current_liquidity=price.liquidity_usd,
            current_volume=price.volume_24h
        )

        return jsonify({
            'mint': result.mint,
            'is_anomaly': result.is_anomaly,
            'anomaly_type': result.anomaly_type,
            'anomaly_score': result.anomaly_score,
            'confidence': result.confidence,
            'reasons': result.reasons,
            'price_current': result.current_price,
            'price_previous': result.previous_price,
            'price_change_percent': result.price_change_percent,
            'liquidity': result.current_liquidity,
            'volume_to_liquidity_ratio': result.volume_to_liquidity_ratio
        })
    except Exception as e:
        logger.error(f"Error detecting anomaly for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/full', methods=['GET'])
def get_full_price_data(mint: str):
    """
    Get complete price data with all enhancements.

    Returns: price + confidence + aggregation + anomaly detection
    """
    try:
        cache_type = request.args.get('cache_type', 'hot')

        # Get base price
        service = get_price_service()
        price = service.get_token_price_sync(mint, cache_type)

        if price.source == 'unavailable':
            return jsonify({'error': 'No price data available'}), 404

        # Get confidence
        scorer = get_confidence_scorer()
        confidence = scorer.compute_confidence(price)

        # Get anomaly detection
        detector = get_anomaly_detector()
        anomaly = detector.detect_anomaly(
            mint=mint,
            current_price=price.price_usd,
            current_liquidity=price.liquidity_usd,
            current_volume=price.volume_24h
        )

        return jsonify({
            'mint': price.mint,
            'price_usd': price.price_usd,
            'price_sol': price.price_sol,
            'liquidity_usd': price.liquidity_usd,
            'volume_24h': price.volume_24h,
            'market_cap': price.market_cap,
            'source': price.source,
            'pair_address': price.pair_address,
            'timestamp': price.timestamp,
            'is_stale': price.is_stale,
            'freshness': 'live' if price.source != 'cached' else 'stale',
            'confidence': {
                'band': confidence.confidence_band,
                'score': confidence.confidence_score,
                'liquidity_score': confidence.liquidity_score,
                'volume_score': confidence.volume_score,
                'source_score': confidence.source_score,
                'stability_score': confidence.stability_score,
                'reasons': confidence.reasons
            },
            'anomaly': {
                'is_anomaly': anomaly.is_anomaly,
                'anomaly_type': anomaly.anomaly_type,
                'anomaly_score': anomaly.anomaly_score,
                'confidence': anomaly.confidence,
                'reasons': anomaly.reasons,
                'price_change_percent': anomaly.price_change_percent
            }
        })
    except Exception as e:
        logger.error(f"Error getting full price data for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/liquidity/health', methods=['GET'])
def get_liquidity_health(mint: str):
    """
    Get liquidity health score for a token.

    Returns: health band, score, trend, current liquidity
    """
    try:
        intelligence = get_liquidity_intelligence()
        health = intelligence.compute_health_score(mint)

        return jsonify({
            'mint': health.mint,
            'health_band': health.health_band,
            'health_score': health.health_score,
            'liquidity_level_score': health.liquidity_level_score,
            'liquidity_growth_score': health.liquidity_growth_score,
            'liquidity_stability_score': health.liquidity_stability_score,
            'current_liquidity': health.current_liquidity,
            'liquidity_trend': health.liquidity_trend,
            'reasons': health.reasons
        })
    except Exception as e:
        logger.error(f"Error getting liquidity health for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/liquidity/risk', methods=['GET'])
def get_liquidity_risk(mint: str):
    """
    Get liquidity-based risk assessment for a token.

    Returns: risk band, rug pull likelihood, warnings
    """
    try:
        intelligence = get_liquidity_intelligence()
        risk = intelligence.detect_rug_pull_risk(mint)

        return jsonify({
            'mint': risk.mint,
            'liquidity_risk': risk.liquidity_risk,
            'risk_score': risk.risk_score,
            'drop_percent_24h': risk.drop_percent_24h,
            'drop_percent_7d': risk.drop_percent_7d,
            'rug_pull_likelihood': risk.rug_pull_likelihood,
            'warning_reasons': risk.warning_reasons
        })
    except Exception as e:
        logger.error(f"Error getting liquidity risk for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/liquidity/history', methods=['GET'])
def get_liquidity_history(mint: str):
    """
    Get historical liquidity data for charting.

    Query params:
    - hours: Number of hours to look back (default 24)

    Returns: Array of liquidity snapshots with timestamps
    """
    try:
        hours = request.args.get('hours', 24, type=int)
        if hours < 1 or hours > 720:  # Max 30 days
            return jsonify({'error': 'hours must be between 1 and 720'}), 400

        intelligence = get_liquidity_intelligence()
        snapshots = intelligence.get_snapshot_history(mint, hours)

        return jsonify({
            'mint': mint,
            'hours': hours,
            'snapshots': [
                {
                    'liquidity_usd': s.liquidity_usd,
                    'liquidity_sol': s.liquidity_sol,
                    'captured_at': s.captured_at
                }
                for s in snapshots
            ],
            'count': len(snapshots)
        })
    except Exception as e:
        logger.error(f"Error getting liquidity history for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/liquidity/worker/start', methods=['POST'])
def start_liquidity_worker_endpoint():
    """Start the background liquidity worker."""
    try:
        worker = get_liquidity_worker()
        if not worker.running:
            worker.start()

        return jsonify({
            'status': 'started' if worker.running else 'already_running',
            'running': worker.running
        })
    except Exception as e:
        logger.error(f"Error starting liquidity worker: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/liquidity/worker/stop', methods=['POST'])
def stop_liquidity_worker_endpoint():
    """Stop the background liquidity worker."""
    try:
        worker = get_liquidity_worker()
        worker.stop()

        return jsonify({
            'status': 'stopped',
            'running': worker.running
        })
    except Exception as e:
        logger.error(f"Error stopping liquidity worker: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/liquidity/worker/stats', methods=['GET'])
def get_liquidity_worker_stats():
    """Get liquidity worker statistics."""
    try:
        worker = get_liquidity_worker()
        return jsonify({
            'running': worker.running,
            'stats': worker.get_stats()
        })
    except Exception as e:
        logger.error(f"Error getting liquidity worker stats: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/batch/register', methods=['POST'])
def register_tokens_batch():
    """
    Register multiple tokens for immediate price tracking.

    Phase 5: Also enqueues warm-ups:
    - Price warm-up: HIGH priority (always)
    - Metadata warm-up: LOW priority (if queue not busy)

    Body: {
        "mints": ["mint1", "mint2", ...],
        "priority_levels": {"mint1": "HIGH", "mint2": "MEDIUM"}  # Optional
    }

    If priority_levels not provided, defaults to MEDIUM for all.

    Returns: {
        "registered": count,
        "total": count,
        "warm_up_queued": count,
        "warm_up_skipped": count,
        "queue_depth": int
    }
    """
    try:
        from src.core.price_fetch_queue import FetchTask, get_price_queue

        data = request.get_json()
        mints = data.get('mints', [])
        priority_levels = data.get('priority_levels', {})  # Dict: {mint: priority_level}

        if not mints or not isinstance(mints, list):
            return jsonify({'error': 'mints must be a non-empty list'}), 400

        if len(mints) > 500:
            return jsonify({'error': 'Maximum 500 mints per request'}), 400

        registry = PriceWorkerRegistry()
        registered = 0

        for mint in mints:
            # Use priority_levels if provided, otherwise default to MEDIUM
            priority = priority_levels.get(mint, 'MEDIUM')
            if mint and registry.register_token(mint, priority_level=priority):
                registered += 1

        # Phase 5: Enqueue warm-ups (non-blocking)
        queue = get_price_queue()

        warm_up_queued = 0
        warm_up_skipped = 0

        for mint in mints:
            # Always enqueue price warm-up (HIGH priority)
            try:
                task = FetchTask(
                    mint=mint,
                    priority='HIGH',
                    enqueued_at=time.time(),
                    callback=lambda m, p, t='price': (_persist_warmup_price(m, p), _on_warmup_complete(m, p, t))
                )
                queue.enqueue(task)
                _warmup_stats['price_queued'] += 1
                warm_up_queued += 1
            except Exception as e:
                logger.debug(f"Failed to enqueue price warmup for {mint}: {e}")

        # Take fresh snapshot AFTER price warm-ups are enqueued
        queue_stats = queue.get_stats()

        QUEUE_WAIT_THRESHOLD_MS = 10_000  # ~38 tasks at 260ms average latency
        # Enqueue metadata warm-up only if queue wait estimate < threshold
        if queue_stats.get('queue_wait_estimate_ms', 0) < QUEUE_WAIT_THRESHOLD_MS:
            for mint in mints:
                try:
                    # Metadata warm-up: fetch symbol in background (LOW priority)
                    task = FetchTask(
                        mint=mint,
                        priority='LOW',
                        enqueued_at=time.time(),
                        callback=lambda m, p, t='metadata': _on_warmup_complete(m, p, t)
                    )
                    queue.enqueue(task)
                    _warmup_stats['metadata_queued'] += 1
                    warm_up_queued += 1
                except Exception as e:
                    logger.debug(f"Failed to enqueue metadata warmup for {mint}: {e}")
        else:
            warm_up_skipped = len(mints)
            _warmup_stats['skipped_due_to_queue'] += len(mints)
            logger.info(
                f"Queue busy (wait={queue_stats.get('queue_wait_estimate_ms', 0):.0f}ms), "
                f"skipping metadata warm-ups for {len(mints)} mints"
            )

        # Immediately fetch + broadcast price for each mint (bypasses worker delay)
        service = get_price_service()
        from src.core.launch_price_logger import record_first_price as _record_fp
        import requests as _req
        for mint in mints:
            try:
                price = service.get_token_price_sync(mint)
                if price and price.price_usd:
                    _record_fp(mint, price.price_usd, price.market_cap or 0, price.source or 'register_batch')
                    try:
                        _req.post('http://127.0.0.1:5002/api/internal/broadcast', json={
                            'type': 'price_update',
                            'mint': mint,
                            'price_usd': price.price_usd,
                            'market_cap': price.market_cap,
                            'source': price.source or 'register_batch',
                            'updated_at': int(time.time()),
                        }, timeout=1)
                    except Exception:
                        pass
            except Exception:
                pass

        return jsonify({
            'registered': registered,
            'total': len(mints),
            'skipped': len(mints) - registered,
            'warm_up_queued': warm_up_queued,
            'warm_up_skipped': warm_up_skipped,
            'queue_depth': queue_stats['queue_depth'],
            'queue_wait_estimate_ms': queue_stats.get('queue_wait_estimate_ms', 0)
        })
    except Exception as e:
        logger.error(f"Error registering batch tokens: {e}")
        return jsonify({'error': str(e)}), 500


@price_api.route('/<mint>/fetch-now', methods=['POST'])
def fetch_price_now(mint: str):
    """
    Immediately fetch and cache price for a token.
    Used for new token launches to get instant price data.
    """
    try:
        service = get_price_service()
        # Fetch price with 'hot' cache (5-15 seconds) to ensure fresh data
        price = service.get_token_price_sync(mint, cache_type='hot')

        if price.source == 'unavailable':
            return jsonify({'error': 'Could not fetch price'}), 404

        # Log first-price gap for new token launches
        try:
            from src.core.launch_price_logger import record_first_price as _record_fp
            _record_fp(mint, price.price_usd, price.market_cap or 0, price.source or 'fetch_now')
        except Exception:
            pass

        # Get confidence
        scorer = get_confidence_scorer()
        confidence = scorer.compute_confidence(price)

        # Get anomaly detection
        detector = get_anomaly_detector()
        anomaly = detector.detect_anomaly(
            mint=mint,
            current_price=price.price_usd,
            current_liquidity=price.liquidity_usd,
            current_volume=price.volume_24h
        )

        return jsonify({
            'mint': price.mint,
            'price_usd': price.price_usd,
            'price_sol': price.price_sol,
            'liquidity_usd': price.liquidity_usd,
            'volume_24h': price.volume_24h,
            'market_cap': price.market_cap,
            'source': price.source,
            'freshness': 'live',
            'confidence': {
                'band': confidence.confidence_band,
                'score': confidence.confidence_score
            }
        })
    except Exception as e:
        logger.error(f"Error fetching price for {mint}: {e}")
        return jsonify({'error': str(e)}), 500


def register_price_api(app):
    """Register price API with Flask app."""
    global _db_path
    
    # Initialize database from app config if available
    if hasattr(app, 'config') and 'DATABASE' in app.config:
        _db_path = app.config['DATABASE']
    
    # Configure SQLite WAL mode and create metadata cache table
    _configure_sqlite_wal(_db_path)
    _ensure_metadata_cache_table(_db_path)
    
    app.register_blueprint(price_api)
    logger.info("Price API routes registered (with Phase 4: Persistent metadata cache)")
