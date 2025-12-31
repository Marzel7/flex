#!/usr/bin/env python3
"""
PumpFun Integration Module

PumpFun is a token launchpad where creators can launch tokens on Solana.
Key points:
- Uses Raydium V4 for liquidity trading
- Tokens have associated bonding curves during initial phase
- Once Raydium pool created, tokens can be traded freely
- All PumpFun pools are discoverable via Raydium monitoring

This module provides:
1. Detection of newly launched PumpFun tokens
2. Price monitoring during bonding curve phase
3. Identification when migrated to Raydium
"""

import requests
import json
from typing import Optional, Dict, List
from datetime import datetime
import time

class PumpFunMonitor:
    """Monitor and detect PumpFun token launches"""

    # Known PumpFun program ID (the bonding curve program)
    PUMPFUN_PROGRAM = "6EF8rQNi3ECMFzvZR2Yy5z2kh2eCMJQEt1g5TQPVe1fq"

    # PumpFun API endpoints
    API_BASE = "https://api.pump.fun"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def test_connection(self) -> bool:
        """Test connection to PumpFun API"""
        try:
            print("[PUMPFUN] Testing API connection...")
            response = self.session.get(f"{self.API_BASE}/health", timeout=5)
            if response.status_code == 200:
                print("[PUMPFUN] ✓ Connected successfully")
                return True
            print(f"[PUMPFUN] Status: {response.status_code}")
            return False
        except Exception as e:
            print(f"[PUMPFUN] Connection error: {e}")
            return False

    def get_homepage_coins(self, limit: int = 10) -> Optional[List[Dict]]:
        """Get coins from PumpFun homepage/trending

        Args:
            limit: Number of coins to fetch

        Returns:
            List of coin objects
        """
        try:
            print(f"[PUMPFUN] Fetching {limit} homepage coins...")

            # PumpFun returns coins as JSON data
            response = self.session.get(
                f"{self.API_BASE}/coins",
                params={"limit": limit},
                timeout=10
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"[PUMPFUN] ✓ Fetched {len(data)} coins")
                    return data
                except json.JSONDecodeError:
                    print("[PUMPFUN] Response is not JSON")
                    return None
            else:
                print(f"[PUMPFUN] Error: {response.status_code}")
                return None

        except Exception as e:
            print(f"[PUMPFUN] Error fetching coins: {e}")
            return None

    def get_coin_details(self, mint: str) -> Optional[Dict]:
        """Get detailed information about a specific coin

        Args:
            mint: Token mint address

        Returns:
            Coin details
        """
        try:
            print(f"[PUMPFUN] Fetching details for {mint[:8]}...")

            response = self.session.get(
                f"{self.API_BASE}/coins/{mint}",
                timeout=10
            )

            if response.status_code == 200:
                coin = response.json()
                print(f"[PUMPFUN] ✓ Fetched: {coin.get('name', 'Unknown')}")
                return coin
            else:
                print(f"[PUMPFUN] Error: {response.status_code}")
                return None

        except Exception as e:
            print(f"[PUMPFUN] Error fetching details: {e}")
            return None

    def is_pumpfun_token(self, token_data: Dict) -> bool:
        """Check if a token was launched on PumpFun

        Args:
            token_data: Token information

        Returns:
            True if token is from PumpFun
        """
        # PumpFun tokens have specific attributes:
        # - bonding_curve: indicates it's on PumpFun bonding curve
        # - Has website/twitter for creator verification
        # - Created via PumpFun program

        has_bonding_curve = "bonding_curve" in token_data
        has_creator_info = any(token_data.get(k) for k in ["website", "twitter"])

        return has_bonding_curve and has_creator_info

    def get_raydium_pool_for_token(self, token_data: Dict) -> Optional[str]:
        """Get associated Raydium pool if token migrated from bonding curve

        Args:
            token_data: Token information

        Returns:
            Raydium pool address or None
        """
        # When PumpFun bonding curve is complete, a Raydium V4 pool is created
        # This is the next stage after bonding curve

        raydium_pool = token_data.get("raydium_pool")
        if raydium_pool:
            print(f"[PUMPFUN] Token migrated to Raydium: {raydium_pool[:16]}...")
            return raydium_pool

        return None

    def print_coin_summary(self, coin: Dict) -> None:
        """Print summary of coin information"""
        print(f"\n{'='*80}")
        print(f"PumpFun Token")
        print(f"{'='*80}")
        print(f"Name:              {coin.get('name', 'N/A')}")
        print(f"Symbol:            {coin.get('symbol', 'N/A')}")
        print(f"Mint:              {coin.get('mint', 'N/A')}")
        print(f"Description:       {coin.get('description', 'N/A')[:60]}...")
        print(f"Website:           {coin.get('website', 'N/A')}")
        print(f"Twitter:           {coin.get('twitter', 'N/A')}")
        print(f"Discord:           {coin.get('discord', 'N/A')}")
        print(f"Image:             {coin.get('image_uri', 'N/A')[:50]}...")
        print(f"Bonding Curve:     {coin.get('bonding_curve', 'N/A')[:16]}...")
        print(f"Raydium Pool:      {coin.get('raydium_pool', 'Not migrated')[:16]}...")
        print(f"Created:           {coin.get('created_timestamp', 'N/A')}")
        print(f"Migrated:          {coin.get('raydium_migrate_timestamp', 'Not yet')}")
        print()

def main():
    """Test PumpFun monitor"""
    print("="*80)
    print("PumpFun Integration Test")
    print("="*80 + "\n")

    monitor = PumpFunMonitor()

    # Test 1: Connection
    print("Test 1: API Connection")
    print("-" * 40)
    if not monitor.test_connection():
        print("\n[ERROR] Could not connect to PumpFun API")
        print("[INFO] PumpFun may be down or rate-limiting")
        print("[INFO] In production, use WebSocket for real-time updates\n")
        return

    # Test 2: Fetch coins
    print("\nTest 2: Fetch Trending Coins")
    print("-" * 40)
    coins = monitor.get_homepage_coins(limit=5)

    if coins:
        print(f"\nFetched {len(coins)} coins\n")
        for i, coin in enumerate(coins[:3], 1):
            print(f"{i}. {coin.get('name')} ({coin.get('symbol')})")
            print(f"   Mint: {coin.get('mint', 'N/A')[:16]}...")
            print(f"   Raydium Pool: {'✓ Migrated' if coin.get('raydium_pool') else '✗ Still bonding'}")
            print()

    # Test 3: Detailed info
    print("\nTest 3: Get Coin Details")
    print("-" * 40)
    if coins:
        first_coin = coins[0]
        mint = first_coin.get('mint')
        if mint:
            details = monitor.get_coin_details(mint)
            if details:
                monitor.print_coin_summary(details)

    print("="*80)
    print("Integration Test Complete")
    print("="*80)
    print("""
Next Steps to Integrate with Main App:
1. Add PumpFun token detection to existing Raydium monitor
2. Track bonding curve phase separately from Raydium trading
3. Notify when tokens migrate to Raydium
4. Monitor prices using vault balances once on Raydium
5. Store PumpFun-specific metadata (creator, launch time, etc.)
    """)

if __name__ == "__main__":
    main()
