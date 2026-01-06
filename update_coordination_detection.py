#!/usr/bin/env python3
"""
Update coordination detection for all creators with newly populated SOL transfer data.

This script should be run AFTER backfill_missing_helius_analysis.py to:
1. Re-analyze all creators for funding account reuse patterns
2. Detect new coordination groups from SOL transfer data
3. Update the coordinated_accounts registry
4. Escalate risk levels for creators using shared funding accounts
5. Update funding_risk_level and funding_risk_pattern in database

This is the CRITICAL automation step that was missing:
- Helius analysis populates creator_sol_transfers ✓
- But coordination detection & registry update wasn't automatic ✗
- This script fills that gap

Usage:
    python update_coordination_detection.py

    Optional: Only update creators analyzed in last N hours:
    python update_coordination_detection.py --hours 2

    Optional: Force re-analysis of all creators:
    python update_coordination_detection.py --all
"""

import sqlite3
import sys
import os
import json
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


def update_coordination_detection(hours=None, all_creators=False):
    """
    Re-analyze creators for coordination patterns and update risk assessments.

    Args:
        hours: Only process creators analyzed in last N hours
        all_creators: If True, process ALL creators regardless of when analyzed
    """

    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Error: Database not found")
        return False

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from analyze_creator_wallet import analyze_creator_with_funding_reuse
        from coordinated_funding_registry import CoordinatedFundingRegistry

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        c = conn.cursor()

        # Get list of creators to process
        if all_creators:
            print("\n📋 Processing ALL creators...")
            c.execute('''
                SELECT DISTINCT pumpfun_creator FROM pools
                WHERE pumpfun_creator IS NOT NULL
                ORDER BY pumpfun_creator
            ''')
        else:
            # Get creators with SOL transfer data (recently analyzed)
            if hours:
                cutoff = datetime.now() - timedelta(hours=hours)
                print(f"\n⏱️  Processing creators analyzed in last {hours} hour(s)...")
                c.execute('''
                    SELECT DISTINCT p.pumpfun_creator
                    FROM pools p
                    WHERE p.pumpfun_creator IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM creator_sol_transfers cst
                        WHERE cst.creator_address = p.pumpfun_creator
                    )
                    AND p.first_seen > ?
                    ORDER BY p.pumpfun_creator
                ''', (cutoff.isoformat(),))
            else:
                print("\n📋 Processing all creators with SOL transfer data...")
                c.execute('''
                    SELECT DISTINCT p.pumpfun_creator
                    FROM pools p
                    WHERE p.pumpfun_creator IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM creator_sol_transfers cst
                        WHERE cst.creator_address = p.pumpfun_creator
                    )
                    ORDER BY p.pumpfun_creator
                ''')

        creators = [row[0] for row in c.fetchall()]

        print(f"\n" + "="*100)
        print(f"COORDINATION DETECTION UPDATE")
        print(f"Analyzing {len(creators)} creator(s) for funding account reuse")
        print("="*100)

        registry = CoordinatedFundingRegistry()
        updated = 0
        escalated_high = 0
        escalated_critical = 0
        errors = 0

        for i, creator in enumerate(creators, 1):
            try:
                # Analyze this creator for funding reuse
                analysis = analyze_creator_with_funding_reuse(creator)

                if analysis:
                    risk_level = analysis['overall_risk']
                    pattern = analysis['coordination_pattern']
                    funding_sources = analysis.get('funding_sources', [])

                    # Show result
                    if risk_level in ['HIGH', 'CRITICAL']:
                        marker = "⚠️ " if risk_level == 'HIGH' else "🚨"
                        print(f"\n[{i}/{len(creators)}] {marker} {creator[:16]}... → {risk_level}")

                        # Count shared funding accounts
                        shared = sum(1 for f in funding_sources if f.get('reused_token_count', 0) > 0)
                        if shared:
                            print(f"  ├─ {shared} shared funding account(s)")

                        # Register new coordinated accounts
                        for funding_source in funding_sources:
                            funding_account = funding_source.get('address')
                            reused_tokens = funding_source.get('reused_tokens', [])

                            if funding_account and len(reused_tokens) > 1:
                                # Get all creators funded by this account
                                c.execute('''
                                    SELECT DISTINCT creator_address FROM creator_sol_transfers
                                    WHERE counterparty_address = ? AND transfer_type = 'incoming'
                                ''', (funding_account,))

                                creators_funded = [row[0] for row in c.fetchall()]

                                if len(creators_funded) >= 2:
                                    if registry.add_account(funding_account, creators_funded):
                                        print(f"  ├─ ✓ Registered: {funding_account[:16]}... (funds {len(creators_funded)} creators)")

                        if risk_level == 'HIGH':
                            escalated_high += 1
                        else:
                            escalated_critical += 1
                    else:
                        # Low/Medium risk - just show as processed
                        if i % 5 == 0:  # Show every 5th to reduce noise
                            print(f"  [{i}/{len(creators)}] {creator[:16]}... → {risk_level}")

                    # Update database with risk assessment
                    c.execute('''
                        UPDATE pools
                        SET funding_risk_level = ?, funding_risk_pattern = ?, funding_check_timestamp = ?
                        WHERE pumpfun_creator = ?
                    ''', (risk_level, pattern, datetime.now(), creator))
                    conn.commit()

                    updated += 1
                else:
                    # No analysis result - set to LOW
                    c.execute('''
                        UPDATE pools
                        SET funding_risk_level = ?, funding_risk_pattern = ?, funding_check_timestamp = ?
                        WHERE pumpfun_creator = ?
                    ''', ('LOW', 'INDEPENDENT_CREATOR', datetime.now(), creator))
                    conn.commit()
                    updated += 1

            except Exception as e:
                print(f"  ❌ Error analyzing {creator[:16]}...: {str(e)[:60]}")
                errors += 1

        # Summary
        print(f"\n" + "="*100)
        print(f"COORDINATION DETECTION COMPLETE")
        print(f"="*100)
        print(f"\nResults:")
        print(f"  Creators analyzed: {updated}/{len(creators)}")
        print(f"  Escalated to HIGH: {escalated_high}")
        print(f"  Escalated to CRITICAL: {escalated_critical}")
        print(f"  Errors: {errors}")

        # Registry stats
        print(f"\nRegistry Status:")
        c.execute('SELECT COUNT(DISTINCT funding_account) FROM coordinated_funding_registry')
        registry_stats = registry.get_stats()
        print(f"  Registered coordinated accounts: {registry_stats['account_count']}")
        print(f"  Total creators in registry: {registry_stats['total_linked_creators']}")

        # Risk distribution
        c.execute('''
            SELECT funding_risk_level, COUNT(*) FROM pools
            WHERE pumpfun_creator IS NOT NULL
            GROUP BY funding_risk_level
            ORDER BY funding_risk_level
        ''')

        print(f"\nRisk Distribution (all creators):")
        total = 0
        for level, count in c.fetchall():
            total += count
            print(f"  {level:10} {count:3}")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    hours = None
    all_creators = False

    # Parse arguments
    for arg in sys.argv[1:]:
        if arg == '--all' or arg == '-a':
            all_creators = True
        elif arg.startswith('--hours='):
            hours = int(arg.split('=')[1])
        elif arg.startswith('--hours'):
            try:
                hours = int(sys.argv[sys.argv.index(arg) + 1])
            except:
                pass

    if not all_creators and not hours:
        print("📋 QUICK MODE: Processing creators with SOL transfer data")
        print("   Use --all to process ALL creators")
        print("   Use --hours N to process recently analyzed creators\n")

    success = update_coordination_detection(hours=hours, all_creators=all_creators)
    sys.exit(0 if success else 1)
