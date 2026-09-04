import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_broad_price_worker_start_is_permanently_suppressed():
    from src.core import price_worker

    assert price_worker.BROAD_PRICE_TRACKING_RUNTIME_ENABLED is False
    worker = object.__new__(price_worker.BackgroundPriceWorker)
    worker.running = False
    worker.start()
    assert worker.running is False


def test_listener_cannot_reenable_broad_worker_with_environment_flag():
    source = (ROOT / "src/core/pumpfun_curve_listener.py").read_text()
    ast.parse(source)
    assert "BROAD_PRICE_TRACKING_RUNTIME_ENABLED" in source
    assert 'environ.get("LISTENER_PRICE_WORKER_ENABLED", "0")' in source
    assert "Broad price tracking decommissioned" in source


def test_flask_start_has_no_broad_worker_or_population_sync():
    source = (ROOT / "src/core/main.py").read_text()
    start = source.index("def start_background_workers():")
    end = source.index("\n\n", source.index("[LIQUIDITY_WORKER]", start))
    block = source[start:end]
    assert "start_price_worker" not in block
    assert "_sync_validated_tokens_to_tracker()" not in block
    assert "start_liquidity_worker" in block


def test_mission_control_omits_retired_price_capability_and_incidents():
    from src.ops import mission_control_capabilities as capabilities

    assert "price_tracking" not in capabilities.CAPABILITY_NAMES
    assert "price_tracking" not in capabilities._COMPUTE_FN
    result = capabilities.compute_capabilities({"price_worker": {"status": "DOWN"}})
    assert "price_tracking" not in result
    assert all(
        item.get("capability") != "price_tracking"
        for item in capabilities.compute_incidents(result)
    )


def test_compact_price_and_owned_liquidity_paths_remain():
    price_service = (ROOT / "src/core/price_service.py").read_text()
    liquidity = (ROOT / "src/core/liquidity_worker.py").read_text()
    listener = (ROOT / "src/core/pumpfun_curve_listener.py").read_text()
    assert "token_price_snapshots" not in price_service
    assert "token_market_cap_peaks" in price_service
    assert "get_token_price_sync" in price_service
    assert "start_liquidity_worker" in liquidity
    assert "liquidity_removed" in listener
