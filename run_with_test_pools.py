#!/usr/bin/env python3
"""
Run the Raydium monitor with automatic test pool injection after startup.
This helps test if the UI rendering works without waiting for real pools.

Usage: python run_with_test_pools.py
"""

import main
import time
from datetime import datetime
import threading

# Test pools to inject
TEST_POOLS = [
    {
        'amm_id': 'test-1',
        'name': '🧪 Test Token Alpha',
        'symbol': 'TEST',
        'image': 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWaLb3cwQB5Bhk91DdGJF6RqHSadP3j6gYLxQXpKJ/logo.png',
        'base_mint': 'EPjFWaLb3cwQB5Bhk91DdGJF6RqHSadP3j6gYLxQXpKJ',
        'liquidity': 5000.0,
        'price': 0.00001,
        'signature': 'test-sig-1',
        'dex': 'Test (Mock)',
        'first_seen': datetime.now().isoformat()
    },
    {
        'amm_id': 'test-2',
        'name': '🧪 Bonk Token',
        'symbol': 'BONK',
        'image': 'https://arweave.net/hQiPv0S3EBgkmpN3M0IC3rb6qvPJGBrDatG4yppstDE',
        'base_mint': 'DezXAZ8z7PnrnRJjz3wXBoRgixVqXaSJ1shNorWHcVLj',
        'liquidity': 50000.0,
        'price': 0.000025,
        'signature': 'test-sig-2',
        'dex': 'Test (Mock)',
        'first_seen': datetime.now().isoformat()
    }
]

def inject_test_pools():
    """Inject test pools after a delay to allow app to start"""
    # Wait 3 seconds for app to fully start
    print("\n[TEST] Waiting for app to start...")
    time.sleep(3)

    print("\n" + "="*70)
    print("  INJECTING TEST POOLS")
    print("="*70)

    for pool in TEST_POOLS:
        print(f"\n✓ Injecting: {pool['name']} ({pool['symbol']})")
        main.pool_broadcast_queue.put(pool)
        print(f"  Queue size: {main.pool_broadcast_queue.qsize()}")

    print("\n" + "="*70)
    print("TEST POOLS INJECTED!")
    print("="*70)
    print("""
Check your browser:
1. Open: http://localhost:5002
2. You should see the test pools appear within 1-2 seconds
3. Open DevTools (F12) → Console to see [POLL] and [RENDER] logs

If pools appear:
  ✓ UI rendering works
  ✗ Problem is backend WebSocket detection

If pools DON'T appear:
  ✗ UI rendering is broken
  ✓ Check browser console for errors

Press Ctrl+C to stop the app
""")

if __name__ == "__main__":
    print("="*70)
    print("  RAYDIUM MONITOR - WITH TEST POOL INJECTION")
    print("="*70)
    print(f"Starting application with test pool injection...")
    print(f"Web UI: http://localhost:5002")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Start test pool injection in background thread
    injector_thread = threading.Thread(target=inject_test_pools, daemon=True)
    injector_thread.start()

    # Start the main app
    main.main()
