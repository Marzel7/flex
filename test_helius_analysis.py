#!/usr/bin/env python3
"""
Quick test script to verify Helius analysis works in isolation.

This script tests the Helius API fetch without running the full listener.

Usage:
    python test_helius_analysis.py <creator_address>

Example:
    python test_helius_analysis.py 49nSpmxwnTTyXujNm3zHqoin1mg1y1rKd1THXwJjYdLa
"""

import sys
import os
from pathlib import Path

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

# Import the analysis functions
from analyze_creator_wallet import (
    fetch_helius_transactions,
    analyze_sol_transfers,
    store_creator_wallet_data
)

def test_helius_analysis(creator_address):
    """Test Helius analysis for a specific creator"""

    print(f"\n{'='*100}")
    print(f"HELIUS ANALYSIS TEST for {creator_address}")
    print(f"{'='*100}\n")

    # Check API key
    api_key = os.getenv('HELIUS_API_KEY')
    if not api_key:
        print("❌ HELIUS_API_KEY not found in environment!")
        print("   Make sure .env file exists and contains HELIUS_API_KEY=...")
        return False

    print(f"✓ Found HELIUS_API_KEY in environment (first 8 chars: {api_key[:8]}...)\n")

    # Step 1: Fetch Helius transactions
    print(f"[1/4] Fetching Helius transaction history...")
    transactions = fetch_helius_transactions(creator_address, fetch_all=False)

    if not transactions:
        print(f"❌ No transactions fetched!")
        print(f"   This could mean:")
        print(f"   - Creator address is invalid")
        print(f"   - Creator has no transfer-type transactions")
        print(f"   - API error or rate limit")
        return False

    print(f"✓ Fetched {len(transactions)} transactions\n")

    # Step 2: Analyze SOL transfers
    print(f"[2/4] Analyzing SOL transfers...")
    sol_transfers = analyze_sol_transfers(transactions, creator_address)

    print(f"✓ Analysis complete:")
    print(f"  - Incoming SOL: {sol_transfers['total_in']:.4f} from {len(sol_transfers['sol_in'])} transfers")
    print(f"  - Outgoing SOL: {sol_transfers['total_out']:.4f} to {len(sol_transfers['sol_out'])} destinations")
    print(f"  - Net position: {sol_transfers['total_in'] - sol_transfers['total_out']:.4f} SOL\n")

    # Step 3: Display transfer details
    print(f"[3/4] Transfer details:")

    if sol_transfers['sol_in']:
        print(f"\n  Incoming transfers:")
        for transfer in sol_transfers['sol_in'][:5]:  # Show first 5
            print(f"    - {transfer['source'][:16]}... → {transfer['amount']:.4f} SOL")
        if len(sol_transfers['sol_in']) > 5:
            print(f"    ... and {len(sol_transfers['sol_in']) - 5} more")

    if sol_transfers['sol_out']:
        print(f"\n  Outgoing transfers:")
        for transfer in sol_transfers['sol_out'][:5]:  # Show first 5
            print(f"    - {transfer['destination'][:16]}... ← {transfer['amount']:.4f} SOL")
        if len(sol_transfers['sol_out']) > 5:
            print(f"    ... and {len(sol_transfers['sol_out']) - 5} more")

    print()

    # Step 4: Store to database
    print(f"[4/4] Storing analysis results to database...")

    wallet_stats = {
        'account_age_days': 0,
        'first_tx_timestamp': None,
        'total_transactions': len(transactions),
        'swap_count': 0,
        'transfer_count': len(sol_transfers['sol_in']) + len(sol_transfers['sol_out']),
        'total_sol_in': sol_transfers['total_in'],
        'total_sol_out': sol_transfers['total_out'],
        'net_sol_position': sol_transfers['total_in'] - sol_transfers['total_out'],
        'unique_wallet_interactions': 0
    }

    success = store_creator_wallet_data(creator_address, wallet_stats, sol_transfers)

    if success:
        print(f"✓ Successfully stored {len(sol_transfers['sol_in']) + len(sol_transfers['sol_out'])} transfer records to database\n")
        print(f"{'='*100}")
        print(f"✅ HELIUS ANALYSIS SUCCESSFUL")
        print(f"{'='*100}\n")
        return True
    else:
        print(f"❌ Failed to store analysis results to database")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python test_helius_analysis.py <creator_address>")
        print("\nExample: python test_helius_analysis.py 49nSpmxwnTTyXujNm3zHqoin1mg1y1rKd1THXwJjYdLa")
        sys.exit(1)

    creator = sys.argv[1]
    success = test_helius_analysis(creator)
    sys.exit(0 if success else 1)
