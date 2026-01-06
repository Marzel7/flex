#!/usr/bin/env python3
"""
Backfill missing Helius analysis for tokens that were detected but
didn't get their SOL transfer data analyzed.

This script finds all tokens with creators but NO creator_sol_transfers records,
and runs the Helius analysis to populate the missing data.

This is critical because:
1. WebSocket handler might fail to extract creator during detection
2. PumpFun API lookups can fail or timeout
3. Tokens detected early may not have had Helius analysis implemented
4. Risk assessment requires SOL transfer data for accurate coordination detection

Usage:
    python backfill_missing_helius_analysis.py

    Optional: Process only tokens detected in last N hours:
    python backfill_missing_helius_analysis.py --hours 1

    Optional: Process with full transaction history:
    python backfill_missing_helius_analysis.py --full
"""

import sqlite3
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# Load environment variables
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()


def backfill_missing_helius_analysis(hours=None, fetch_all=False):
    """
    Analyze creators that have tokens but no SOL transfer records.

    Args:
        hours: Only process tokens detected in last N hours (None = all)
        fetch_all: If True, fetch all transactions; if False, fetch last 100
    """

    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Error: Database not found")
        return False

    try:
        # Import analysis functions
        sys.path.insert(0, str(Path(__file__).parent))
        from analyze_creator_wallet import (
            fetch_helius_transactions,
            analyze_sol_transfers,
            store_creator_wallet_data
        )

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        c = conn.cursor()

        # Find creators with tokens but no Helius data
        query = '''
            SELECT DISTINCT p.pumpfun_creator, COUNT(p.base_mint) as token_count,
                   MAX(p.first_seen) as latest_token
            FROM pools p
            WHERE p.pumpfun_creator IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM creator_sol_transfers cst
                WHERE cst.creator_address = p.pumpfun_creator
            )
        '''

        params = []

        # Optional time filter
        if hours:
            cutoff = datetime.now() - timedelta(hours=hours)
            query += ' AND p.first_seen > ?'
            params.append(cutoff.isoformat())

        query += ' GROUP BY p.pumpfun_creator ORDER BY p.first_seen DESC'

        c.execute(query, params)
        creators = c.fetchall()

        print("\n" + "="*100)
        print(f"BACKFILL: Missing Helius Analysis")
        if hours:
            print(f"Processing tokens from last {hours} hour(s)")
        print(f"Found {len(creators)} creator(s) with missing SOL transfer data")
        print("="*100)

        analyzed = 0
        stored = 0
        skipped = 0
        errors = 0

        for i, (creator, token_count, latest) in enumerate(creators, 1):
            if not creator:
                skipped += 1
                continue

            print(f"\n[{i}/{len(creators)}] Creator: {creator[:16]}... ({token_count} token(s))")

            try:
                # Fetch Helius transactions
                print(f"  ├─ Fetching Helius data...", end=" ", flush=True)
                transactions = fetch_helius_transactions(creator, fetch_all=fetch_all)

                if not transactions:
                    print(f"⚠ No transactions found")
                    analyzed += 1
                    continue

                print(f"✓ {len(transactions)} transactions")

                # Analyze SOL transfers
                print(f"  ├─ Analyzing transfers...", end=" ", flush=True)
                sol_transfers = analyze_sol_transfers(transactions, creator)
                transfer_count = len(sol_transfers['sol_in']) + len(sol_transfers['sol_out'])
                print(f"✓ {transfer_count} transfers")

                # Store to database
                print(f"  └─ Storing data...", end=" ", flush=True)
                wallet_stats = {
                    'account_age_days': 0,
                    'first_tx_timestamp': None,
                    'total_transactions': len(transactions),
                    'swap_count': 0,
                    'transfer_count': transfer_count,
                    'total_sol_in': sol_transfers['total_in'],
                    'total_sol_out': sol_transfers['total_out'],
                    'net_sol_position': sol_transfers['total_in'] - sol_transfers['total_out'],
                    'unique_wallet_interactions': 0
                }

                if store_creator_wallet_data(creator, wallet_stats, sol_transfers):
                    print(f"✓")
                    if transfer_count > 0:
                        print(f"     ✓ Stored {transfer_count} transfer records")
                        print(f"       - In:  {sol_transfers['total_in']:.4f} SOL")
                        print(f"       - Out: {sol_transfers['total_out']:.4f} SOL")
                    stored += 1
                else:
                    print(f"⚠ Could not store")
                    errors += 1

                analyzed += 1

            except Exception as e:
                print(f"❌ Error: {str(e)[:60]}")
                errors += 1

        # Update risk levels for creators with now-populated data
        print(f"\n" + "="*100)
        print(f"Updating risk assessments for newly analyzed creators...")

        # Re-import to get fresh analysis
        from analyze_creator_wallet import analyze_creator_with_funding_reuse

        for creator, _, _ in creators[:analyzed]:
            if creator:
                try:
                    analysis = analyze_creator_with_funding_reuse(creator)
                    if analysis:
                        risk_level = analysis['overall_risk']
                        pattern = analysis['coordination_pattern']
                    else:
                        risk_level = 'LOW'
                        pattern = 'INDEPENDENT_CREATOR'

                    c.execute('''
                        UPDATE pools
                        SET funding_risk_level = ?, funding_risk_pattern = ?, funding_check_timestamp = ?
                        WHERE pumpfun_creator = ?
                    ''', (risk_level, pattern, datetime.now(), creator))
                    conn.commit()
                except:
                    pass

        # Summary
        print(f"\n" + "="*100)
        print(f"BACKFILL COMPLETE")
        print(f"="*100)
        print(f"Creators analyzed: {analyzed}/{len(creators)}")
        print(f"Helius data stored: {stored}")
        print(f"Errors: {errors}")

        # Stats
        c.execute('SELECT COUNT(*) FROM creator_sol_transfers')
        total_records = c.fetchone()[0]
        print(f"\nTotal creator_sol_transfers records: {total_records}")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    hours = None
    fetch_all = False

    # Parse arguments
    for arg in sys.argv[1:]:
        if arg == '--full' or arg == '-f':
            fetch_all = True
            print("📋 FULL MODE: Fetching ALL transactions per creator")
        elif arg.startswith('--hours='):
            hours = int(arg.split('=')[1])
            print(f"⏱️  TIME FILTER: Only processing tokens from last {hours} hour(s)")
        elif arg.startswith('--hours'):
            # Next argument is the hours value
            try:
                hours = int(sys.argv[sys.argv.index(arg) + 1])
                print(f"⏱️  TIME FILTER: Only processing tokens from last {hours} hour(s)")
            except:
                pass

    if not fetch_all and not hours:
        print("📋 QUICK MODE: Fetching last 100 transactions per creator")
        print("   Use --full to fetch all transactions")
        print("   Use --hours N to process only recent tokens")

    success = backfill_missing_helius_analysis(hours=hours, fetch_all=fetch_all)
    sys.exit(0 if success else 1)
