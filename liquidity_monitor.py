#!/usr/bin/env python3
"""
Liquidity Removal Event Monitor

Monitors newly launched tokens and detects when liquidity removal events occur.
A liquidity removal is detected when:
1. On-chain price drops significantly (indicating price was set by vault balances)
2. Vault balances change dramatically
3. Pool transitions from "normal" to "depleted" state

Usage:
  python liquidity_monitor.py  # Monitor all new pools
  python liquidity_monitor.py --pool <address> # Monitor specific pool
  python liquidity_monitor.py --token <mint>   # Monitor token's pool
"""

import sqlite3
import json
import time
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import requests

# Import from main.py
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
from main import RaydiumDatabase, RaydiumMonitor
from meteora_price_fetcher_v2 import fetch_price

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class LiquidityMonitor:
    def __init__(self):
        self.db = RaydiumDatabase()
        self.monitor = RaydiumMonitor()
        self.tracked_pools = {}  # pool_address -> pool_state
        self.price_history = {}   # pool_address -> [(timestamp, price), ...]
        self.vault_history = {}   # pool_address -> [(timestamp, vaults), ...]
        self.events_log = []      # List of detected events

    def get_pool_vault_balances(self, pool_address: str) -> Optional[Dict]:
        """Get current vault balances for a pool"""
        try:
            # Use the monitor's method to get vault data
            result = self.monitor.extract_pool_price_from_transaction(
                pool_address,
                None  # Will fetch creation tx internally
            )
            return result
        except Exception as e:
            print(f"Error fetching vault balances: {e}")
            return None

    def get_price_for_pool(self, pool_address: str) -> Optional[float]:
        """Get current on-chain price for a pool"""
        try:
            result = fetch_price(pool_address, verbose=False)
            return result.get('on_chain_price') if result else None
        except Exception as e:
            print(f"Error fetching price: {e}")
            return None

    def check_liquidity_removal(self, pool_address: str) -> Optional[Dict]:
        """
        Check if a pool shows signs of liquidity removal

        Returns: {
            'event_type': 'LIQUIDITY_REMOVAL' | 'PRICE_COLLAPSE' | 'VAULT_DRAIN',
            'severity': 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL',
            'details': {...},
            'timestamp': datetime
        }
        """
        event = None

        try:
            # Get current price
            current_price = self.get_price_for_pool(pool_address)
            if not current_price:
                return None

            # Check price history
            if pool_address in self.price_history:
                history = self.price_history[pool_address]
                if len(history) >= 2:
                    prev_price = history[-1][1]
                    time_diff = (history[-1][0] - history[-2][0]).total_seconds()

                    # Detect sudden price changes
                    if prev_price > 0:
                        price_change_pct = abs(current_price - prev_price) / prev_price * 100

                        # Large price drop = potential liquidity removal
                        if current_price < prev_price * 0.5:  # > 50% drop
                            event = {
                                'event_type': 'PRICE_COLLAPSE',
                                'severity': 'CRITICAL' if price_change_pct > 90 else 'HIGH',
                                'details': {
                                    'previous_price': prev_price,
                                    'current_price': current_price,
                                    'change_pct': -price_change_pct,
                                    'time_window_seconds': time_diff,
                                },
                                'timestamp': datetime.now()
                            }

            # Get pool from database to check status
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT is_depleted, price, created_at
                FROM pools
                WHERE pool_address = ?
            """, (pool_address,))

            row = cursor.fetchone()
            conn.close()

            if row:
                is_depleted = row[0]
                last_known_price = row[1]

                # If pool just became depleted
                if is_depleted and pool_address in self.tracked_pools:
                    prev_state = self.tracked_pools[pool_address]
                    if not prev_state.get('was_depleted'):
                        event = {
                            'event_type': 'LIQUIDITY_REMOVAL',
                            'severity': 'HIGH',
                            'details': {
                                'transition': 'NORMAL -> DEPLETED',
                                'last_price': last_known_price,
                                'current_price': current_price,
                                'pool_created': row[2],
                            },
                            'timestamp': datetime.now()
                        }

        except Exception as e:
            print(f"Error checking liquidity removal: {e}")

        return event

    def track_pool(self, pool_address: str, pool_data: Dict) -> None:
        """Start tracking a pool for liquidity removal"""
        self.tracked_pools[pool_address] = {
            'created_at': datetime.now(),
            'initial_price': pool_data.get('price'),
            'token_name': pool_data.get('name'),
            'symbol': pool_data.get('symbol'),
            'was_depleted': False,
            'check_count': 0,
        }

        self.price_history[pool_address] = [
            (datetime.now(), pool_data.get('price'))
        ]

        print(f"{Colors.OKBLUE}[TRACKING]{Colors.ENDC} {pool_address[:16]}... "
              f"({pool_data.get('symbol', 'UNKNOWN')}) "
              f"Price: ${pool_data.get('price', 0):.10f}")

    def update_pool_tracking(self, pool_address: str) -> None:
        """Update tracking data for a pool"""
        if pool_address not in self.tracked_pools:
            return

        current_price = self.get_price_for_pool(pool_address)
        if current_price:
            if pool_address not in self.price_history:
                self.price_history[pool_address] = []

            self.price_history[pool_address].append(
                (datetime.now(), current_price)
            )

            # Keep only last 100 price points (5+ hours of 2-min checks)
            if len(self.price_history[pool_address]) > 100:
                self.price_history[pool_address] = self.price_history[pool_address][-100:]

            self.tracked_pools[pool_address]['check_count'] += 1

    def log_event(self, event: Dict) -> None:
        """Log a detected event"""
        self.events_log.append(event)

        severity_colors = {
            'LOW': Colors.OKGREEN,
            'MEDIUM': Colors.WARNING,
            'HIGH': Colors.FAIL,
            'CRITICAL': Colors.FAIL + Colors.BOLD,
        }

        color = severity_colors.get(event['severity'], Colors.ENDC)
        severity = event['severity']
        event_type = event['event_type']
        details = event['details']

        print(f"\n{color}[{severity}]{Colors.ENDC} {Colors.BOLD}{event_type}{Colors.ENDC}")
        print(f"  Timestamp: {event['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")

        for key, value in details.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.10f}")
            else:
                print(f"  {key}: {value}")

    def monitor_continuous(self, check_interval: int = 120, max_age_hours: int = 24) -> None:
        """
        Continuously monitor pools

        Args:
            check_interval: Seconds between checks
            max_age_hours: Only monitor pools created in last N hours
        """
        print(f"{Colors.HEADER}{Colors.BOLD}Liquidity Removal Event Monitor{Colors.ENDC}")
        print(f"Check interval: {check_interval}s | Max pool age: {max_age_hours}h\n")

        try:
            while True:
                cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

                # Get recent pools from database
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pool_address, name, symbol, price, created_at, is_depleted
                    FROM pools
                    WHERE created_at > ?
                    ORDER BY created_at DESC
                """, (cutoff_time.isoformat(),))

                recent_pools = cursor.fetchall()
                conn.close()

                # Track new pools
                for pool_addr, name, symbol, price, created_at, is_depleted in recent_pools:
                    if pool_addr not in self.tracked_pools:
                        self.track_pool(pool_addr, {
                            'name': name,
                            'symbol': symbol,
                            'price': price,
                        })

                # Update existing pools and check for events
                print(f"\n{datetime.now().strftime('%H:%M:%S')} - Checking {len(self.tracked_pools)} pools...")

                for pool_addr in list(self.tracked_pools.keys()):
                    self.update_pool_tracking(pool_addr)

                    # Check for liquidity removal
                    event = self.check_liquidity_removal(pool_addr)
                    if event:
                        self.log_event(event)

                        # Update tracked state
                        if event['event_type'] in ['LIQUIDITY_REMOVAL', 'PRICE_COLLAPSE']:
                            self.tracked_pools[pool_addr]['was_depleted'] = True

                # Print current tracked pools summary
                active_count = len([p for p in self.tracked_pools.values()
                                   if not p.get('was_depleted')])
                depleted_count = len([p for p in self.tracked_pools.values()
                                     if p.get('was_depleted')])

                print(f"  Active pools: {active_count} | Depleted: {depleted_count} | "
                      f"Total: {len(self.tracked_pools)}")

                time.sleep(check_interval)

        except KeyboardInterrupt:
            print(f"\n\n{Colors.WARNING}Monitor stopped{Colors.ENDC}")
            self.print_summary()

    def monitor_specific_pool(self, pool_address: str, check_interval: int = 30) -> None:
        """Monitor a specific pool continuously"""
        print(f"{Colors.HEADER}{Colors.BOLD}Monitoring Pool: {pool_address}{Colors.ENDC}\n")

        try:
            check_count = 0
            while True:
                check_count += 1
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                price = self.get_price_for_pool(pool_address)
                if price:
                    print(f"[{timestamp}] Check #{check_count}: Price = ${price:.18f}")

                    # Check for liquidity removal signs
                    event = self.check_liquidity_removal(pool_address)
                    if event:
                        self.log_event(event)
                else:
                    print(f"[{timestamp}] Failed to fetch price")

                # Update tracking
                self.update_pool_tracking(pool_address)

                time.sleep(check_interval)

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}Monitoring stopped{Colors.ENDC}")

    def print_summary(self) -> None:
        """Print summary of detected events"""
        if not self.events_log:
            print("\nNo events detected")
            return

        print(f"\n{Colors.HEADER}{Colors.BOLD}EVENT SUMMARY{Colors.ENDC}")
        print(f"Total events: {len(self.events_log)}\n")

        # Group by severity
        by_severity = {}
        for event in self.events_log:
            severity = event['severity']
            if severity not in by_severity:
                by_severity[severity] = []
            by_severity[severity].append(event)

        for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
            events = by_severity.get(severity, [])
            if events:
                print(f"{severity}: {len(events)} events")
                for event in events[:3]:  # Show first 3
                    print(f"  - {event['event_type']} at {event['timestamp'].strftime('%H:%M:%S')}")
                if len(events) > 3:
                    print(f"  ... and {len(events) - 3} more")


def main():
    monitor = LiquidityMonitor()

    if len(sys.argv) > 1:
        if sys.argv[1] == '--pool' and len(sys.argv) > 2:
            # Monitor specific pool
            pool_address = sys.argv[2]
            monitor.monitor_specific_pool(pool_address)
        elif sys.argv[1] == '--token' and len(sys.argv) > 2:
            # Find and monitor pool for token
            token_mint = sys.argv[2]
            print(f"Looking for pool for token {token_mint}...")
            # TODO: Implement token -> pool lookup
            print("Not yet implemented")
        else:
            print(f"Usage:")
            print(f"  python liquidity_monitor.py                    # Monitor all new pools")
            print(f"  python liquidity_monitor.py --pool <address>   # Monitor specific pool")
            print(f"  python liquidity_monitor.py --token <mint>     # Monitor token's pool")
    else:
        # Monitor all new pools
        monitor.monitor_continuous(check_interval=120, max_age_hours=24)


if __name__ == "__main__":
    main()
