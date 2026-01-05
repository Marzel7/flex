#!/usr/bin/env python3
"""
Analyze creator's wallet transaction history and on-chain behavior.

Examines:
- Total transactions and activity volume
- Fund flows (incoming vs outgoing)
- Token holdings and distribution patterns
- Trading behavior across multiple tokens
- Wallet age and activity timeline
- Suspicious patterns (rapid fund movements, multiple wallets, etc.)
"""

import sqlite3
from pathlib import Path
import sys
import os

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def get_creator_info(creator_address):
    """Get creator info from database"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return None

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                COUNT(*) as token_count,
                SUM(CASE WHEN trade_status = 'sold' THEN 1 ELSE 0 END) as sold_count,
                SUM(CASE WHEN buy_price_usd IS NOT NULL THEN quantity_bought ELSE 0 END) as total_bought,
                SUM(CASE WHEN sell_price_usd IS NOT NULL THEN quantity_sold ELSE 0 END) as total_sold,
                SUM(profit_loss_usd) as total_profit_usd,
                AVG(profit_loss_percent) as avg_profit_pct,
                MIN(first_seen) as first_token_date,
                MAX(first_seen) as latest_token_date
            FROM pools
            WHERE pumpfun_creator = ?
        ''', (creator_address,))

        stats = cursor.fetchone()
        conn.close()

        return {
            'token_count': stats[0] or 0,
            'sold_count': stats[1] or 0,
            'total_bought': stats[2] or 0,
            'total_sold': stats[3] or 0,
            'total_profit_usd': stats[4] or 0,
            'avg_profit_pct': stats[5] or 0,
            'first_token_date': stats[6],
            'latest_token_date': stats[7]
        }

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def fetch_solscan_transactions(wallet_address, api_key=None):
    """Fetch transaction history from Solscan API"""
    if not HAS_REQUESTS:
        print("⚠️  requests library not installed. Install with: pip install requests")
        return None

    # Try to get API key from environment
    if not api_key:
        api_key = os.getenv('SOLSCAN_API_KEY')

    if not api_key:
        print("⚠️  SOLSCAN_API_KEY environment variable not set")
        print("    Set it with: export SOLSCAN_API_KEY=your_api_key")
        print("    Or get a free key from: https://solscan.io/")
        return None

    try:
        url = "https://api.solscan.io/v2/account/transactions"
        headers = {"token": api_key}
        params = {
            "address": wallet_address,
            "limit": 100
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code == 200:
            return response.json()
        else:
            print(f"⚠️  Solscan API error: {response.status_code}")
            print(f"    Response: {response.text[:200]}")
            return None

    except Exception as e:
        print(f"❌ API Error: {e}")
        return None


def fetch_magic_eden_activity(creator_address):
    """Fetch NFT/token activity from Magic Eden or similar"""
    print("⚠️  Magic Eden integration coming soon...")
    return None


def analyze_creator_wallet(creator_address):
    """Analyze creator's wallet and transaction patterns"""
    creator_short = f"{creator_address[:8]}...{creator_address[-4:]}"

    print(f"\n{'='*160}")
    print(f"CREATOR WALLET ANALYSIS: {creator_short}")
    print(f"Full Address: {creator_address}")
    print(f"{'='*160}\n")

    # Get database info
    db_info = get_creator_info(creator_address)

    if not db_info:
        print("❌ Creator not found in database")
        return

    print("DATABASE STATISTICS (from tracked tokens)")
    print("-" * 160)
    print(f"  Tokens launched: {db_info['token_count']}")
    print(f"  Tokens sold: {db_info['sold_count']} ({db_info['sold_count']/db_info['token_count']*100:.1f}% exit rate)")
    print(f"  Total quantity bought: {db_info['total_bought']:.2e}")
    print(f"  Total quantity sold: {db_info['total_sold']:.2e}")
    print(f"  Total realized profit: ${db_info['total_profit_usd']:.2f}")
    print(f"  Average profit %: {db_info['avg_profit_pct']:+.2f}%")
    print()

    # Get on-chain transaction data
    print("ON-CHAIN TRANSACTION DATA")
    print("-" * 160)

    tx_data = fetch_solscan_transactions(creator_address)

    if tx_data:
        print(f"✓ Successfully fetched transaction history")
        print(f"  Total transactions (last 100): {len(tx_data.get('data', []))}")

        # Analyze transaction types
        print()
        print("TRANSACTION ANALYSIS")
        print("-" * 160)

        transactions = tx_data.get('data', [])

        if transactions:
            # Categorize transactions
            swap_count = sum(1 for tx in transactions if 'swap' in str(tx).lower())
            transfer_count = sum(1 for tx in transactions if 'transfer' in str(tx).lower())
            token_mint = sum(1 for tx in transactions if 'initializeMint' in str(tx).lower() or 'createToken' in str(tx).lower())

            print(f"  Swaps: {swap_count}")
            print(f"  Transfers: {transfer_count}")
            print(f"  Token creations: {token_mint}")
            print()

            # Show recent transactions
            print("RECENT TRANSACTIONS")
            print("-" * 160)

            for i, tx in enumerate(transactions[:10], 1):
                sig = tx.get('txHash', 'unknown')
                ts = tx.get('timestamp', 'unknown')
                tx_type = tx.get('type', 'unknown')
                status = tx.get('status', 'unknown')

                print(f"  {i}. [{status}] {tx_type} - {sig[:16]}... ({ts})")
    else:
        print("⚠️  Could not fetch on-chain transactions")
        print()
        print("SETUP GUIDE:")
        print("-" * 160)
        print("  1. Get free Solscan API key: https://solscan.io/")
        print("  2. Set environment variable:")
        print("     export SOLSCAN_API_KEY=your_api_key_here")
        print("  3. Run this script again")
        print()
        print("ALTERNATIVE DATA SOURCES:")
        print("  • Solscan.io - Manual inspection of wallet")
        print("  • MagicEden - Check NFT/token activity")
        print("  • Dune Analytics - Complex on-chain queries")
        print()

    # Risk assessment based on available data
    print("RISK ASSESSMENT & PATTERNS")
    print("-" * 160)

    risk_factors = []

    # Check exit rate
    if db_info['token_count'] > 0:
        exit_rate = db_info['sold_count'] / db_info['token_count']
        if exit_rate < 0.3:
            risk_factors.append(f"⚠️  Low exit rate ({exit_rate*100:.1f}%) - may be holding bags or testing")
        elif exit_rate > 0.8:
            risk_factors.append(f"✓ High exit rate ({exit_rate*100:.1f}%) - actively trading")

    # Check profitability
    if db_info['avg_profit_pct'] < -50:
        risk_factors.append("⚠️  Negative average returns - possible poor timing or risk management")
    elif db_info['avg_profit_pct'] > 100:
        risk_factors.append("✓ Strong profitability - skilled trader")

    # Check profit
    if db_info['total_profit_usd'] < 0:
        risk_factors.append("⚠️  Net loss on trades - be cautious")
    elif db_info['total_profit_usd'] > 1000:
        risk_factors.append("✓ Significant profits generated")

    if not risk_factors:
        risk_factors.append("✓ No major red flags detected in database records")

    for factor in risk_factors:
        print(f"  {factor}")

    print()

    # What to look for in on-chain data
    print("WHAT TO VERIFY ON-CHAIN:")
    print("-" * 160)
    print("  When you analyze the wallet on Solscan:")
    print("  ✓ Transaction frequency - how active is this wallet?")
    print("  ✓ Fund sources - where do their SOL/funds come from?")
    print("  ✓ Multi-wallet activity - do they control other wallets?")
    print("  ✓ Timing patterns - when do they buy/sell relative to token launches?")
    print("  ✓ Profit extraction - do they cash out immediately or hold?")
    print("  ✓ Suspicious patterns:")
    print("     • Multiple transfers to different wallets (potential funds splitting)")
    print("     • Rapid rapid in/out flows (market making or wash trading)")
    print("     • Large SOL movements before token launches (potential insider funding)")
    print()

    print("DIRECT WALLET LINK:")
    print("-" * 160)
    print(f"  Solscan: https://solscan.io/address/{creator_address}")
    print(f"  Magic Eden: https://magiceden.io/marketplace?search={creator_address}")
    print()

    print(f"{'='*160}\n")


if __name__ == '__main__':
    # Analyze the duplicate creator by default
    creator_to_analyze = "6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA"

    if len(sys.argv) > 1:
        creator_to_analyze = sys.argv[1]

    analyze_creator_wallet(creator_to_analyze)
