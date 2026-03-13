"""
Token Price API Routes

Exposes the token price service via Flask REST API.
Includes price confidence scoring and launch outcome tracking.
"""

import logging
import time
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


@price_api.route('/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """
    Get token symbol and name via proxy to Dexscreener.
    Avoids CORS issues and rate limiting.

    Returns: {symbol, name}
    """
    import requests

    try:
        # Check cache validity (5 minute TTL)
        cache_ttl = 300
        now = time.time()
        if mint in _metadata_cache and (now - _metadata_cache_time.get(mint, 0)) < cache_ttl:
            return jsonify(_metadata_cache[mint])

        # Always try to fetch fresh data from Dexscreener
        resp = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{mint}',
            timeout=5
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get('pairs') and len(data['pairs']) > 0:
                base_token = data['pairs'][0].get('baseToken', {})
                result = {
                    'symbol': base_token.get('symbol', mint[:8].upper()),
                    'name': base_token.get('name', 'Token')
                }
                _metadata_cache[mint] = result
                _metadata_cache_time[mint] = now
                return jsonify(result)

        # Fallback: use cached value if available, or default
        if mint in _metadata_cache:
            return jsonify(_metadata_cache[mint])

        result = {'symbol': mint[:8].upper(), 'name': 'Token'}
        _metadata_cache[mint] = result
        _metadata_cache_time[mint] = now
        return jsonify(result), 200

    except Exception as e:
        logger.debug(f"Error fetching metadata for {mint}: {e}")
        # Use cached value if available on error
        if mint in _metadata_cache:
            return jsonify(_metadata_cache[mint])
        result = {'symbol': mint[:8].upper(), 'name': 'Token'}
        _metadata_cache[mint] = result
        _metadata_cache_time[mint] = time.time()
        return jsonify(result), 200


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

    Body: {"mints": ["mint1", "mint2", ...]}

    Returns: {"registered": count, "total": count}
    """
    try:
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

        return jsonify({
            'registered': registered,
            'total': len(mints),
            'skipped': len(mints) - registered
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
    app.register_blueprint(price_api)
    logger.info("Price API routes registered (extended with liquidity intelligence)")
