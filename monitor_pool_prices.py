#!/usr/bin/env python3
"""
Monitor pool pricing in real-time.

Shows:
- WebSocket connection status
- Pool subscriptions active
- Price updates as they happen
- Event rates and latency

Usage:
    python monitor_pool_prices.py              # Single check
    python monitor_pool_prices.py --watch      # Continuous monitoring
    python monitor_pool_prices.py --watch --interval 5  # Check every 5s
"""

import requests
import sqlite3
import time
import argparse
from datetime import datetime
from collections import defaultdict

class PoolPriceMonitor:
    def __init__(self):
        self.api_url = 'http://localhost:5002/api/price'
        self.health_url = f'{self.api_url}/health'
        self.last_ws_stats = None

    def get_health(self):
        """Get health status from price API."""
        try:
            resp = requests.get(self.health_url, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return None

    def get_pools_from_db(self):
        """Get registered pools from database."""
        try:
            conn = sqlite3.connect('database/flex_complete_database.db')
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mint, base_account, quote_account, created_at
                FROM token_pool_accounts
                ORDER BY created_at DESC LIMIT 20
            """)
            pools = cursor.fetchall()
            conn.close()
            return pools
        except:
            return []

    def get_price(self, mint):
        """Get current price for a token."""
        try:
            resp = requests.get(f'{self.api_url}/{mint}/full?cache_type=hot', timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return None

    def format_health(self, health):
        """Format health status for display."""
        pool_stats = health.get('pool_stats', {})
        ws_stats = pool_stats.get('ws', {})

        print("\n📊 WebSocket Status")
        print("-" * 80)

        connected = ws_stats.get('connected', False)
        status = "🟢 CONNECTED" if connected else "🔴 DISCONNECTED"
        print(f"Connection: {status}")

        if connected:
            events_received = ws_stats.get('events_received', 0)
            events_decoded = ws_stats.get('events_decoded', 0)
            dedup = ws_stats.get('events_deduplicated', 0)
            is_stale = ws_stats.get('is_stale', True)

            print(f"\nEvents:")
            print(f"  Received: {events_received}")
            print(f"  Decoded: {events_decoded}")
            print(f"  Deduplicated (same slot): {dedup}")

            stale_status = "🔴 STALE (>2min no events)" if is_stale else "🟢 LIVE (recent events)"
            print(f"\nStatus: {stale_status}")

            subscriptions = ws_stats.get('active_subscriptions', 0)
            print(f"Active subscriptions: {subscriptions}")

    def format_pools(self, pools):
        """Format pool information."""
        print("\n🏊 Registered Pools")
        print("-" * 80)

        if not pools:
            print("No pools registered yet")
            return

        print(f"Total pools: {len(pools)}\n")

        for i, (mint, base, quote, created_at) in enumerate(pools[:5], 1):
            print(f"{i}. {mint[:16]}...")
            print(f"   Base:  {base[:16]}...")
            print(f"   Quote: {quote[:16]}...")

            # Try to get price
            price_data = self.get_price(mint)
            if price_data:
                price = price_data.get('price_usd', 0)
                source = price_data.get('source', 'unknown')
                timestamp = price_data.get('timestamp', 0)

                # Check if recent
                now = time.time()
                age = int(now - timestamp)
                age_indicator = "🟢" if age < 30 else "🟡" if age < 60 else "🔴"

                print(f"   Price: ${price:.8f}")
                print(f"   Source: {source}")
                print(f"   Age: {age}s {age_indicator}")
            else:
                print(f"   (No price data)")

            print()

    def check(self):
        """Run a single check."""
        print(f"\n{datetime.now().strftime('%H:%M:%S')} - Pool Price Status Check")
        print("=" * 80)

        health = self.get_health()
        if not health:
            print("❌ Could not connect to price API (http://localhost:5002)")
            return False

        self.format_health(health)
        pools = self.get_pools_from_db()
        self.format_pools(pools)

        return True

    def watch(self, interval=10):
        """Continuously monitor."""
        try:
            while True:
                self.check()

                # Show event rate changes
                health = self.get_health()
                if health and self.last_ws_stats:
                    new_stats = health.get('pool_stats', {}).get('ws', {})
                    old_received = self.last_ws_stats.get('events_received', 0)
                    new_received = new_stats.get('events_received', 0)

                    if new_received > old_received:
                        delta = new_received - old_received
                        print(f"\n📈 {delta} new events since last check")

                self.last_ws_stats = health.get('pool_stats', {}).get('ws', {}) if health else None

                print(f"\nWaiting {interval}s... (Ctrl+C to exit)")
                print("=" * 80)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped.")

def main():
    parser = argparse.ArgumentParser(
        description='Monitor pool pricing and WebSocket updates'
    )
    parser.add_argument(
        '--watch',
        action='store_true',
        help='Continuous monitoring mode'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=10,
        help='Check interval in seconds (for --watch)'
    )

    args = parser.parse_args()

    monitor = PoolPriceMonitor()

    if args.watch:
        print("📡 Monitoring pool prices (WebSocket updates)...")
        print("Press Ctrl+C to stop\n")
        monitor.watch(args.interval)
    else:
        monitor.check()

if __name__ == '__main__':
    main()
