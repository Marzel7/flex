#!/usr/bin/env python3
"""
Extract probable token creators from bonding curve trading activity.

For tokens without Metaplex metadata (token_creator), identify the likely creator
by finding the wallet with the highest trading activity on the bonding curve.
This is a strong proxy because creators typically have the most transactions.
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "pumpswap_tokens.db"

# Import analyzer
sys.path.insert(0, str(Path(__file__).parent.parent))
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer

async def extract_creators_from_activity():
    """Extract creators from bonding curve activity"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        
        # Get tokens WITHOUT token_creator (need creator extraction from curve data)
        cursor.execute("""
            SELECT mint FROM token_analysis
            WHERE token_creator IS NULL
            AND rug_indicator = 'quick_peak_low_mc'
            ORDER BY market_cap_highest DESC
        """)
        
        rugged_tokens = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        print(f"Analyzing {len(rugged_tokens)} rugged tokens to extract creators from bonding curve activity...\n")
        
        creators_map = {}
        
        for i, mint in enumerate(rugged_tokens, 1):
            try:
                print(f"[{i}/{len(rugged_tokens)}] Analyzing {mint[:30]}...")
                
                # Analyze the token to get bonding curve events
                analyzer = PostMigrationAnalyzer(mint)
                await analyzer.fetch_curve_activity_async()
                
                if analyzer.events:
                    # Find top buyer/trader (likely creator)
                    all_wallets = Counter(e["wallet"] for e in analyzer.events)
                    if all_wallets:
                        top_wallet, activity_count = all_wallets.most_common(1)[0]
                        activity_ratio = activity_count / len(analyzer.events) * 100
                        
                        creators_map[mint] = {
                            'probable_creator': top_wallet,
                            'activity_count': activity_count,
                            'activity_ratio': activity_ratio,
                            'total_events': len(analyzer.events)
                        }
                        
                        print(f"  ├─ Top wallet: {top_wallet[:20]}...")
                        print(f"  ├─ Activity: {activity_count}/{len(analyzer.events)} ({activity_ratio:.1f}%)")
                        print(f"  └─ Status: Extracted ✓\n")
                else:
                    print(f"  └─ No events found\n")
                
                # Rate limiting
                await asyncio.sleep(0.2)
                
            except Exception as e:
                print(f"  └─ Error: {str(e)[:80]}\n")
        
        # Print summary
        print("\n" + "="*70)
        print("PROBABLE CREATORS FROM BONDING CURVE ACTIVITY")
        print("="*70 + "\n")
        
        # Group by creator
        creator_tokens = {}
        for mint, info in creators_map.items():
            creator = info['probable_creator']
            if creator not in creator_tokens:
                creator_tokens[creator] = []
            creator_tokens[creator].append({
                'mint': mint,
                'activity_ratio': info['activity_ratio']
            })
        
        # Print creators with multiple tokens (likely repeat rug creators)
        print("CREATORS WITH MULTIPLE RUG TOKENS:\n")
        multi_token_creators = {k: v for k, v in creator_tokens.items() if len(v) > 1}
        
        if multi_token_creators:
            for creator, tokens in sorted(multi_token_creators.items(), key=lambda x: len(x[1]), reverse=True):
                print(f"Creator: {creator}")
                print(f"  Rugs launched: {len(tokens)}")
                for token_info in tokens:
                    print(f"  ├─ {token_info['mint'][:30]}... (activity: {token_info['activity_ratio']:.1f}%)")
                print()
        else:
            print("No creators with multiple rug tokens found\n")
        
        print("\n" + "="*70)
        print(f"SUMMARY: Extracted probable creators for {len(creators_map)}/{len(rugged_tokens)} tokens")
        print("="*70)
        
        # Print as wallet list for blocking
        print("\nWALLETS TO POTENTIALLY BLOCK:")
        for creator in sorted(multi_token_creators.keys()):
            print(f"  {creator}")
        
        if not multi_token_creators:
            print("  (No repeat creators found - likely one-off rugs)")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(extract_creators_from_activity())
    sys.exit(0 if success else 1)
