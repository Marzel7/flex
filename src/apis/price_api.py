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
        if time.time() - _metadata_cache_time.get(mint, 0) < 1800:
            return {
                'symbol': cached['symbol'],
                'name': cached['name'],
                'source': 'memory_cache',
                'is_fresh': True,
                'is_stale': False
            }

    # 2. Check SQLite cache
    sqlite_result = _get_metadata_from_sqlite(db_path, mint, max_age=1800)
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


@price_api.route('/<mint>', methods=['GET'])
def get_price(mint: str):
    """
    Get current price for a single token.
    
    Query params:
    - cache_type: 'hot' (10s), 'org' (30s), 'history' (5m). Default: 'hot'
    
    Example: GET /api/price/EPjFWaLb3odcccccccccccccccccccccccccccccccccc?cache_type=org
    """
    try:
        cache_type = request.args.get('cache_type', 'hot')
        service = get_price_service()
        
        price = service.get_token_price_sync(mint, cache_type)
        
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


@price_api.route('/health', methods=['GET'])
def health():
    """Health check - verify price service is working."""
    try:
        service = get_price_service()
        worker = get_price_worker()
        registry = PriceWorkerRegistry()

        return jsonify({
            'status': 'healthy',
            'cache_size': len(service.cache.cache),
            'worker_running': worker.running,
            'worker_stats': worker.get_stats(),
            'warm_up_stats': _warmup_stats.copy(),
            'timestamp': int(time.time())
        })
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


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

    Body: {"mints": ["mint1", "mint2", ...]}

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

        if not mints or not isinstance(mints, list):
            return jsonify({'error': 'mints must be a non-empty list'}), 400

        if len(mints) > 500:
            return jsonify({'error': 'Maximum 500 mints per request'}), 400

        registry = PriceWorkerRegistry()
        registered = 0

        for mint in mints:
            # Use MEDIUM priority (30s refresh) to avoid rate limiting with large token sets
            # This still provides frequent updates while respecting API rate limits
            if mint and registry.register_token(mint, priority_level='MEDIUM'):
                registered += 1

        # Phase 5: Enqueue warm-ups (non-blocking)
        queue = get_price_queue()
        queue_stats = queue.get_stats()

        warm_up_queued = 0
        warm_up_skipped = 0
        queue_depth_threshold = 50

        for mint in mints:
            # Always enqueue price warm-up (HIGH priority)
            try:
                task = FetchTask(
                    mint=mint,
                    priority='HIGH',
                    enqueued_at=time.time(),
                    callback=lambda m, p, t='price': _on_warmup_complete(m, p, t)
                )
                queue.enqueue(task)
                _warmup_stats['price_queued'] += 1
                warm_up_queued += 1
            except Exception as e:
                logger.debug(f"Failed to enqueue price warmup for {mint}: {e}")

        # Enqueue metadata warm-up only if queue not busy
        if queue_stats['queue_depth'] < queue_depth_threshold:
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
                f"Queue busy (depth={queue_stats['queue_depth']}), "
                f"skipping metadata warm-ups for {len(mints)} mints"
            )

        return jsonify({
            'registered': registered,
            'total': len(mints),
            'skipped': len(mints) - registered,
            'warm_up_queued': warm_up_queued,
            'warm_up_skipped': warm_up_skipped,
            'queue_depth': queue_stats['queue_depth']
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
