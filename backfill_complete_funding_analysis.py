#!/usr/bin/env python3
"""
Complete Funding Analysis Backfill - Comprehensive Treasury & Coordination Detection

This script performs the COMPLETE funding analysis pipeline for all tokens:

1. For each creator:
   - Fetch their SOL transfer history from Helius API
   - Analyze treasury/funding accounts (accounts that sent SOL to creator)
   - Store creator_sol_transfers records

2. Check for funding account reuse:
   - Find treasuries used by multiple creators
   - Detect coordination patterns
   - Calculate risk levels (Level 1 + Level 2)

3. Update database:
   - funding_risk_level (LOW/MEDIUM/HIGH/CRITICAL)
   - funding_risk_pattern (coordination pattern)
   - creator_sol_transfers table (complete treasury data)
   - creator_wallets table (wallet statistics)

Usage:
    python backfill_complete_funding_analysis.py

    Optional: Fetch ALL transactions (slower, more comprehensive):
    python backfill_complete_funding_analysis.py --full
"""

import sqlite3
import sys
import os
from pathlib import Path
from datetime import datetime

# Load environment variables
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()


def backfill_complete_funding_analysis(fetch_all=False):
    """
    Complete funding analysis for all tokens with creators.

    Performs:
    1. Helius API fetch for creator's SOL transfer history
    2. Treasury/funding account identification
    3. Coordination detection (Level 1 + Level 2)
    4. Risk assessment
    5. Database updates
    """

    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Error: Database not found")
        return False

    try:
        # Import analysis functions
        sys.path.insert(0, str(Path(__file__).parent))
        from analyze_creator_wallet import (
            analyze_creator_with_funding_reuse,
            fetch_helius_transactions,
            analyze_sol_transfers,
            store_creator_wallet_data
        )

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        c = conn.cursor()

        # Get all unique creators
        c.execute('''
            SELECT DISTINCT pumpfun_creator FROM pools
            WHERE pumpfun_creator IS NOT NULL
            ORDER BY pumpfun_creator
        ''')

        creators = [row[0] for row in c.fetchall()]

        print("\n" + "=" * 100)
        print(f"COMPLETE FUNDING ANALYSIS BACKFILL")
        print(f"Analyzing {len(creators)} creator(s)")
        print("=" * 100)

        analyzed = 0
        skipped = 0
        errors = 0

        for i, creator in enumerate(creators, 1):
            # Get creator's token count
            c.execute('SELECT COUNT(*) FROM pools WHERE pumpfun_creator = ?', (creator,))
            token_count = c.fetchone()[0]

            print(f"\n[{i}/{len(creators)}] Creator: {creator[:20]}... ({token_count} token(s))")

            try:
                # Step 1: Fetch Helius transaction history
                print(f"   └─ Fetching Helius data...", end=" ", flush=True)
                transactions = fetch_helius_transactions(creator, fetch_all=fetch_all)

                if not transactions:
                    print("⚠️  No transactions found")
                    # Still analyze (will get LOW risk)
                else:
                    print(f"✓ {len(transactions)} transactions")

                    # Step 2: Analyze SOL transfers
                    print(f"   └─ Analyzing SOL transfers...", end=" ", flush=True)
                    sol_transfers = analyze_sol_transfers(transactions, creator)

                    # Step 3: Store creator wallet data and treasury records
                    print(f"✓ {len(sol_transfers['sol_in'])} in, {len(sol_transfers['sol_out'])} out")

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

                    print(f"   └─ Storing treasury data...", end=" ", flush=True)
                    if store_creator_wallet_data(creator, wallet_stats, sol_transfers):
                        print(f"✓ SOL in: {sol_transfers['total_in']:.4f}, out: {sol_transfers['total_out']:.4f}")
                    else:
                        print("⚠️  Could not store wallet data")

                # Step 4: Analyze funding reuse/coordination
                print(f"   └─ Analyzing coordination...", end=" ", flush=True)
                analysis = analyze_creator_with_funding_reuse(creator)

                if analysis:
                    risk_level = analysis['overall_risk']
                    pattern = analysis['coordination_pattern']
                    print(f"✓ {risk_level} ({pattern})")
                else:
                    risk_level = 'LOW'
                    pattern = 'INDEPENDENT_CREATOR'
                    print("✓ LOW (default - no funding data)")

                # Step 5: Update database
                print(f"   └─ Updating risk level...", end=" ", flush=True)
                c.execute('''
                    UPDATE pools
                    SET funding_risk_level = ?, funding_risk_pattern = ?, funding_check_timestamp = ?
                    WHERE pumpfun_creator = ?
                ''', (risk_level, pattern, datetime.now(), creator))
                conn.commit()
                print(f"✓")

                # Show reuse if detected
                if analysis and analysis['overall_risk'] in ['MEDIUM', 'HIGH', 'CRITICAL']:
                    high_risk = sum(1 for f in analysis['funding_sources'] if f['reused_token_count'] > 0)
                    print(f"   ⚠️  {high_risk} funding source(s) shared with other creators")

                analyzed += 1

            except Exception as e:
                print(f"❌ Error: {str(e)[:60]}")
                errors += 1

        # Summary
        c.execute('SELECT COUNT(*) FROM pools WHERE funding_risk_level IS NOT NULL AND funding_risk_level != "UNKNOWN"')
        assessed = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM pools')
        total = c.fetchone()[0]

        # Risk distribution
        c.execute('''
            SELECT funding_risk_level, COUNT(*)
            FROM pools
            WHERE funding_risk_level IS NOT NULL
            GROUP BY funding_risk_level
            ORDER BY funding_risk_level
        ''')

        distribution = c.fetchall()

        print("\n" + "=" * 100)
        print(f"BACKFILL COMPLETE")
        print("=" * 100)
        print(f"\nResults:")
        print(f"  Creators analyzed: {analyzed}/{len(creators)}")
        print(f"  Tokens assessed:   {assessed}/{total}")
        print(f"  Errors:            {errors}")

        if distribution:
            print(f"\nRisk Distribution:")
            for risk_level, count in distribution:
                pct = count * 100 // total
                print(f"  {risk_level:10} {count:3} tokens ({pct:3}%)")

        # Treasury data status
        c.execute('SELECT COUNT(*) FROM creator_sol_transfers WHERE transfer_type = "incoming"')
        treasury_records = c.fetchone()[0]

        c.execute('SELECT COUNT(DISTINCT creator_address) FROM creator_sol_transfers')
        creators_with_treasury = c.fetchone()[0]

        print(f"\nTreasury Data:")
        print(f"  Treasury records:    {treasury_records}")
        print(f"  Creators with data:  {creators_with_treasury}/{len(creators)}")

        conn.close()
        return True

    except Exception as e:
        print(f"\n❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    fetch_all = '--full' in sys.argv or '-f' in sys.argv

    if fetch_all:
        print("\n⚠️  FULL MODE: Fetching ALL transactions (slower but comprehensive)")
    else:
        print("\n📋 QUICK MODE: Fetching last 100 transactions per creator")
        print("   Use --full or -f to fetch all transactions")

    success = backfill_complete_funding_analysis(fetch_all=fetch_all)
    sys.exit(0 if success else 1)
