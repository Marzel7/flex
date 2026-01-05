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


def fetch_solscan_transactions(wallet_address):
    """Fetch transaction history from Solscan API (requires token)"""
    if not HAS_REQUESTS:
        print("⚠️  requests library not installed. Install with: pip install requests")
        return None

    # Try to get API token from environment
    api_token = os.getenv('SOLSCAN_API_TOKEN')

    if not api_token:
        # API requires authentication - return None and fall back to manual inspection
        return None

    try:
        # Pro API endpoint
        url = "https://pro-api.solscan.io/v2.0/account/detail"
        headers = {"token": api_token}
        params = {
            "address": wallet_address,
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            return data
        elif response.status_code == 429:
            print(f"⚠️  Rate limited by Solscan API. Try again in a moment.")
            return None
        else:
            return None

    except Exception as e:
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

    # Provide direct Solscan link for manual inspection
    print("🔗 DIRECT WALLET INSPECTION (Recommended):")
    print(f"   https://solscan.io/address/{creator_address}")
    print()

    # Try API if token is available
    tx_data = fetch_solscan_transactions(creator_address)

    if tx_data and isinstance(tx_data, dict) and 'data' in tx_data:
        print(f"✓ Successfully fetched transaction history from public API")

        transactions = tx_data.get('data', []) if isinstance(tx_data, dict) else tx_data

        if isinstance(transactions, list):
            print(f"  Total transactions (last 100): {len(transactions)}")

            # Analyze transaction types
            print()
            print("TRANSACTION ANALYSIS")
            print("-" * 160)

            # Categorize transactions
            swap_count = 0
            transfer_count = 0
            token_mint = 0
            successful_txs = 0

            for tx in transactions:
                tx_str = str(tx).lower() if tx else ""
                if 'swap' in tx_str or 'jupiteragg' in tx_str:
                    swap_count += 1
                if 'transfer' in tx_str or 'token 2022' in tx_str:
                    transfer_count += 1
                if 'initializemint' in tx_str or 'createtoken' in tx_str:
                    token_mint += 1
                if isinstance(tx, dict) and tx.get('status') == 'Success':
                    successful_txs += 1

            print(f"  Total transactions: {len(transactions)}")
            print(f"  Successful transactions: {successful_txs} ({successful_txs/len(transactions)*100:.1f}%)")
            print(f"  Swaps detected: {swap_count}")
            print(f"  Transfers detected: {transfer_count}")
            print(f"  Token creations detected: {token_mint}")
            print()

            # Show recent transactions
            print("RECENT TRANSACTIONS (Last 10)")
            print("-" * 160)

            for i, tx in enumerate(transactions[:10], 1):
                if isinstance(tx, dict):
                    sig = tx.get('txHash', tx.get('signature', 'unknown'))[:16]
                    ts = tx.get('timestamp', 'unknown')
                    tx_type = tx.get('type', 'unknown')
                    status = tx.get('status', 'unknown')

                    print(f"  {i}. [{status}] {sig}... - {tx_type} ({ts})")
                else:
                    print(f"  {i}. {str(tx)[:80]}...")
        else:
            print(f"⚠️  Unexpected response format from API")
    else:
        print("⚠️  API data not available (requires authentication token)")
        print()
        print("MANUAL WALLET INSPECTION CHECKLIST:")
        print("-" * 160)
        print()
        print("When you open the Solscan link above, look for these patterns:\n")

        print("📊 GENERAL METRICS:")
        print("  □ Wallet age - New wallet (suspicious) vs established (trustworthy)")
        print("  □ Total SOL balance - High balance vs low")
        print("  □ Total transactions - Active (100+) vs inactive (<10)")
        print("  □ Token holdings - How many different SPL tokens do they hold?")
        print()

        print("💰 FUND FLOWS:")
        print("  □ SOL inflows - Where does their funding come from?")
        print("    • From exchange wallets? (suspicious)")
        print("    • From other private wallets? (potential multi-wallet control)")
        print("    • Consistent funding source? (organized operation)")
        print("  □ SOL outflows - Where do profits go?")
        print("    • To same addresses repeatedly? (treasury/main wallet)")
        print("    • Dispersed to many wallets? (fund splitting/mixing)")
        print("  □ Frequency of movements - Daily, weekly, sporadic?")
        print()

        print("🔄 TRANSACTION PATTERNS:")
        print("  □ Swap activity - Are they swapping tokens before launches?")
        print("  □ Token creation - Do they create tokens themselves?")
        print("  □ Timing patterns - Buy before/after token launches?")
        print("    • Before launch = potential insider knowledge")
        print("    • After launch = following public signals")
        print("  □ Success rate - What % of swaps are successful?")
        print()

        print("⚠️  RED FLAGS TO WATCH FOR:")
        print("  ⚠️  Recent wallet (created <1 month ago)")
        print("  ⚠️  Rapid fund movements (many transactions per hour)")
        print("  ⚠️  Multiple wallet connections (they control other wallets)")
        print("  ⚠️  Large SOL deposits before token launches")
        print("  ⚠️  Immediate profit extraction (buys, then sells within minutes)")
        print("  ⚠️  Consistent pump & dump timing")
        print("  ⚠️  No holding period (never holds tokens long-term)")
        print("  ⚠️  Wash trading signatures (rapid buy-sell with same counterparty)")
        print()

        print("✓ POSITIVE INDICATORS:")
        print("  ✓ Wallet age >6 months")
        print("  ✓ Diverse token holdings (not just pumps)")
        print("  ✓ Holding periods (keeps tokens 1-7 days)")
        print("  ✓ Mixed results (some wins, some losses - shows random selection)")
        print("  ✓ Consistent SOL reserves (not depleting)")
        print("  ✓ Stable transaction patterns (predictable rhythm)")
        print()

        print("HOW TO USE SOLSCAN TOOLS:")
        print("-" * 160)
        print("  1. Click 'Token' tab to see all SPL tokens in wallet")
        print("  2. Click 'Transaction' tab to see full history")
        print("  3. Look for 'Swap' transactions - these show their trading activity")
        print("  4. Check transaction details - right-click → 'View on Explorer'")
        print("  5. Trace fund sources - click on incoming transaction sender")
        print()

        print("API SETUP (for automated analysis):")
        print("-" * 160)
        print("  1. Get Solscan Pro API token: https://solscan.io/")
        print("  2. Set environment variable:")
        print("     export SOLSCAN_API_TOKEN=your_token_here")
        print("  3. Re-run this script for automated wallet analysis")
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
