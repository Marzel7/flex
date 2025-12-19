#!/usr/bin/env python3
import queue
import time

# This is a hack to test the UI - we'll directly access the broadcast queue
# Since the WebSocket isn't working properly with Flask dev server

test_pools = [
    {
        'amm_id': 'GnLz6H6eXUwb1wbcJQsUKcpyC41uSp3BuJb8ER6AyrN2',
        'name': 'ORE Token',
        'symbol': 'ORE',
        'image': 'https://ipfs.io/ipfs/QmQEaKXNYw7QyX8R2rHksdFbkX2LnB2w4JsGnndJZirtaN',
        'base_mint': '2Wo1Rt3HmEVvtuKqkGxgYiBoihGsFAPNUzaBcf1GGhg2',
        'liquidity': 1000000,
        'price': 0.000000820,
        'signature': 'test_sig_1',
        'dex': 'Meteora',
        'first_seen': int(time.time() * 1000),
        'creation_price': 0.000000820030141081,
        'current_price': 0.000000820030141081,
        'is_depleted': False,
        'depletion_reason': None,
        'sol_usd_price': 128.95,
        'price_change_percent': 0
    },
    {
        'amm_id': 'Ag4ywdS756GZQXfGJZ1So4k6GcuUuUaMjgFTWwAYu7kd',
        'name': 'Test Moon Coin',
        'symbol': 'MOON',
        'image': 'https://ipfs.io/ipfs/Qmtest',
        'base_mint': 'BDoi9S3r9z9FuznGcSPdnnj1K5uVZMELzZLjSvQH4JdKK',
        'liquidity': 500000,
        'price': 0.0000005,
        'signature': 'test_sig_2',
        'dex': 'Meteora',
        'first_seen': int(time.time() * 1000) - 60000,
        'creation_price': 0.0000005,
        'current_price': 0.0000006,
        'is_depleted': False,
        'depletion_reason': None,
        'sol_usd_price': 128.95,
        'price_change_percent': 20
    },
    {
        'amm_id': 'H6rAVgUaUtkfGBdefwoC768nJWbeuY9g6aXFNsMUo8xr',
        'name': 'New Token Launch',
        'symbol': 'NEW',
        'image': 'https://ipfs.io/ipfs/QmNew',
        'base_mint': 'ELVWduBV95Hvt9Jf6n8rS5j3K2VT9zQR6pL5nY2qW3t',
        'liquidity': 2000000,
        'price': 0.000001,
        'signature': 'test_sig_3',
        'dex': 'Meteora',
        'first_seen': int(time.time() * 1000) - 120000,
        'creation_price': 0.000001,
        'current_price': 0.00000095,
        'is_depleted': False,
        'depletion_reason': None,
        'sol_usd_price': 128.95,
        'price_change_percent': -5
    }
]

# Import main module to get the broadcast queue
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
from main import pool_broadcast_queue

print(f"Adding {len(test_pools)} test pools to broadcast queue...")
for pool in test_pools:
    pool_broadcast_queue.put(pool)
    print(f"  ✓ Added: {pool['name']} ({pool['symbol']})")

print(f"\nBroadcast queue size: {pool_broadcast_queue.qsize()}")
print("Pools should appear in UI within a few seconds...")
