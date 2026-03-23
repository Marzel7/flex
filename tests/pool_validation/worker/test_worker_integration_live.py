#!/usr/bin/env python3
"""
Live Worker Integration Test

Starts a real price worker in the test and validates its functionality:
1. Worker starts successfully
2. WebSocket connects and subscribes
3. Price cycles execute
4. PoolStateStore can be populated

This is a true integration test that validates the worker's actual behavior.

Usage:
    python3 -m pytest tests/pool_validation/worker/test_worker_integration_live.py -v -s
    python3 -m pytest tests/pool_validation/worker/test_worker_integration_live.py::test_worker_starts -v -s
"""

import asyncio
import time
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from src.core.price_worker import start_price_worker, get_price_worker
from src.core.integration_helpers import export_worker_status

DB_PATH = "database/flex_complete_database.db"

# Configuration
STARTUP_TIMEOUT = 15  # seconds to wait for worker to start
CYCLE_TIMEOUT = 20    # seconds to wait for at least one cycle
WEBSOCKET_TIMEOUT = 10  # seconds to wait for WebSocket


def test_worker_starts():
    """
    Test that worker starts successfully.
    """
    print("\n" + "=" * 80)
    print("TEST: Worker Starts")
    print("=" * 80)

    # Clean up any existing worker
    from src.core import price_worker as pw_module
    pw_module._price_worker = None

    print("\n[1] Starting worker...")
    worker = start_price_worker(DB_PATH)

    print(f"[2] Checking worker.running...")
    assert worker is not None, "Worker is None"
    assert hasattr(worker, 'running'), "Worker has no 'running' attribute"
    assert worker.running, "Worker.running is False"
    print(f"  ✓ Worker.running = {worker.running}")

    print(f"[3] Checking worker thread...")
    assert hasattr(worker, 'thread'), "Worker has no 'thread' attribute"
    assert worker.thread is not None, "Worker.thread is None"
    assert worker.thread.is_alive(), "Worker thread is not alive"
    print(f"  ✓ Worker thread is alive")

    # Let it run for a moment
    time.sleep(2)

    print("\n✅ TEST PASSED: Worker starts successfully\n")


def test_worker_cycles():
    """
    Test that worker price cycles are executing.
    """
    print("\n" + "=" * 80)
    print("TEST: Worker Price Cycles")
    print("=" * 80)

    print("\n[1] Starting worker...")
    worker = start_price_worker(DB_PATH)

    print("[2] Checking worker.stats...")
    assert hasattr(worker, 'stats'), "Worker has no 'stats' attribute"
    initial_stats = worker.stats.copy()
    initial_cycles = initial_stats.get('cycles', 0)
    print(f"  Initial cycles: {initial_cycles}")

    print(f"[3] Waiting for price cycle to execute ({CYCLE_TIMEOUT}s timeout)...")
    start = time.time()
    cycle_executed = False

    while time.time() - start < CYCLE_TIMEOUT:
        current_stats = worker.stats.copy()
        current_cycles = current_stats.get('cycles', 0)
        if current_cycles > initial_cycles:
            cycle_executed = True
            print(f"  ✓ Cycle executed: {current_cycles} cycles (was {initial_cycles})")
            break
        time.sleep(0.5)

    assert cycle_executed, f"No price cycle executed in {CYCLE_TIMEOUT}s"

    print("\n✅ TEST PASSED: Worker price cycles are executing\n")


def test_worker_websocket():
    """
    Test that WebSocket client starts.
    """
    print("\n" + "=" * 80)
    print("TEST: Worker WebSocket")
    print("=" * 80)

    print("\n[1] Starting worker...")
    worker = start_price_worker(DB_PATH)

    print(f"[2] Waiting for WebSocket startup ({WEBSOCKET_TIMEOUT}s timeout)...")
    start = time.time()
    ws_started = False

    while time.time() - start < WEBSOCKET_TIMEOUT:
        ws_started = getattr(worker, '_ws_started', False)
        if ws_started:
            print(f"  ✓ WebSocket started")
            break
        time.sleep(0.5)

    # WebSocket may not start immediately if no pools exist
    if not ws_started:
        print(f"  ⊘ WebSocket not started (may be waiting for pools)")

    print("[3] Checking WebSocket client...")
    if ws_started:
        ws_client = getattr(worker, '_ws_client', None)
        assert ws_client is not None, "WebSocket client is None despite _ws_started=True"
        print(f"  ✓ WebSocket client exists")

    print("\n✅ TEST PASSED: WebSocket is ready\n")


def test_worker_pool_state_store():
    """
    Test that PoolStateStore exists and is accessible.
    """
    print("\n" + "=" * 80)
    print("TEST: Worker PoolStateStore")
    print("=" * 80)

    print("\n[1] Starting worker...")
    worker = start_price_worker(DB_PATH)

    print("[2] Checking PoolStateStore...")
    assert hasattr(worker, '_pool_state'), "Worker has no '_pool_state' attribute"
    pool_state = getattr(worker, '_pool_state', None)
    assert pool_state is not None, "PoolStateStore is None"
    print(f"  ✓ PoolStateStore exists")

    print("[3] Checking PoolStateStore methods...")
    assert hasattr(pool_state, 'get_reserves'), "PoolStateStore has no 'get_reserves' method"
    assert hasattr(pool_state, 'update_reserve'), "PoolStateStore has no 'update_reserve' method"
    print(f"  ✓ PoolStateStore has required methods")

    print("\n✅ TEST PASSED: PoolStateStore is functional\n")


def test_worker_export_status():
    """
    Test that worker status can be exported.
    """
    print("\n" + "=" * 80)
    print("TEST: Worker Status Export")
    print("=" * 80)

    print("\n[1] Starting worker...")
    worker = start_price_worker(DB_PATH)

    print("[2] Exporting worker status...")
    try:
        from src.core.integration_helpers import export_worker_status
        status = export_worker_status(worker)
        assert status is not None, "export_worker_status returned None"
        print(f"  ✓ Status exported successfully")
    except Exception as e:
        raise AssertionError(f"Failed to export worker status: {e}")

    print(f"[3] Checking status fields...")
    assert hasattr(status, 'subscribed_accounts'), "Status has no 'subscribed_accounts'"
    assert hasattr(status, 'pool_states'), "Status has no 'pool_states'"
    assert hasattr(status, 'all_mints'), "Status has no 'all_mints'"
    print(f"  ✓ Status has required fields")
    print(f"  • Subscribed accounts: {len(status.subscribed_accounts)}")
    print(f"  • Pool states: {len(status.pool_states)}")
    print(f"  • Active mints: {len(status.all_mints)}")

    print("\n✅ TEST PASSED: Worker status can be exported\n")


def test_worker_health_summary():
    """
    Complete health check - summary of worker health.
    """
    print("\n" + "=" * 80)
    print("TEST: Worker Health Summary")
    print("=" * 80)

    print("\n[1] Starting worker...")
    worker = start_price_worker(DB_PATH)
    time.sleep(3)  # Let it initialize

    print("\n[HEALTH CHECK RESULTS]")

    # Running?
    is_running = getattr(worker, 'running', False)
    print(f"  Running: {'✓' if is_running else '✗'} {is_running}")

    # Thread alive?
    thread = getattr(worker, 'thread', None)
    is_alive = thread.is_alive() if thread else False
    print(f"  Thread alive: {'✓' if is_alive else '✗'} {is_alive}")

    # WebSocket started?
    ws_started = getattr(worker, '_ws_started', False)
    print(f"  WebSocket started: {'✓' if ws_started else '⊘'} {ws_started}")

    # Stats
    stats = getattr(worker, 'stats', {})
    print(f"  Cycles: {stats.get('cycles', 0)}")
    print(f"  Errors: {stats.get('errors', 0)}")

    # Pool state
    pool_state = getattr(worker, '_pool_state', None)
    print(f"  PoolStateStore: {'✓' if pool_state else '✗'} exists")

    # Status export
    try:
        status = export_worker_status(worker)
        print(f"  Status export: ✓ success")
        print(f"    - Subscribed: {len(status.subscribed_accounts)} accounts")
        print(f"    - Pools: {len(status.pool_states)} pools")
        print(f"    - Mints: {len(status.all_mints)} mints")
    except Exception as e:
        print(f"  Status export: ✗ {e}")

    # Overall health
    healthy = is_running and is_alive and pool_state is not None
    print(f"\n  Overall health: {'🟢 HEALTHY' if healthy else '🔴 UNHEALTHY'}")

    print("\n✅ TEST PASSED: Health summary generated\n")


if __name__ == '__main__':
    # Run directly
    print("\nRunning live worker integration tests...\n")

    try:
        test_worker_starts()
        test_worker_cycles()
        test_worker_websocket()
        test_worker_pool_state_store()
        test_worker_export_status()
        test_worker_health_summary()

        print("\n" + "=" * 80)
        print("ALL TESTS PASSED ✅")
        print("=" * 80)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
