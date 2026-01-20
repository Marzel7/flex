#!/usr/bin/env python3
"""
Show probable creators from stored analysis metrics.

For tokens with high post_migration_creator_activity_ratio,
the creator was likely very active in the transactions.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "pumpswap_tokens.db"

def show_probable_creators():
    """Show probable creators for rugged tokens without token_creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        
        # Get rugged tokens without token_creator
        cursor.execute("""
            SELECT 
                mint,
                market_cap_highest,
                post_migration_creator_activity_ratio,
                risk_level,
                rug_indicator
            FROM token_analysis
            WHERE rug_indicator = 'quick_peak_low_mc'
              AND token_creator IS NULL
            ORDER BY post_migration_creator_activity_ratio DESC
        """)
        
        tokens = cursor.fetchall()
        conn.close()
        
        print("\n" + "="*80)
        print("RUGGED TOKENS WITHOUT VERIFIED CREATORS")
        print("Note: post_migration_creator_activity_ratio indicates creator involvement")
        print("="*80 + "\n")
        
        for i, (mint, peak_mc, creator_activity, risk_level, rug_flag) in enumerate(tokens, 1):
            creator_activity_pct = creator_activity * 100 if creator_activity else 0
            
            print(f"{i:2d}. {mint}")
            print(f"    Peak MC: ${peak_mc:,.0f}" if peak_mc else f"    Peak MC: Unknown")
            print(f"    Creator Activity: {creator_activity_pct:.1f}%")
            print(f"    Risk Level: {risk_level}")
            
            # Interpretation
            if creator_activity_pct > 30:
                print(f"    ⚠️  HIGH creator involvement - likely deliberate rug")
            elif creator_activity_pct > 10:
                print(f"    ⚠️  MEDIUM creator involvement - suspicious")
            else:
                print(f"    ✓ Low creator involvement - likely exit scam or abandonment")
            
            print()
        
        print(f"\nTotal rugged tokens without creator metadata: {len(tokens)}")
        
        # Recommendation
        print("\n" + "="*80)
        print("RECOMMENDATIONS:")
        print("="*80)
        print("""
1. HIGH CREATOR ACTIVITY tokens (>30%): 
   - Likely intentional rugs by creator
   - Creator may be planning next rug
   - BLOCK if they launch again

2. MEDIUM CREATOR ACTIVITY tokens (10-30%):
   - Mixed signal - could be rug or abandonment
   - Requires manual verification

3. LOW CREATOR ACTIVITY tokens (<10%):
   - Creator abandoned project
   - Exit scam by early investors/holders
   - Less likely to repeat creator behavior

To identify actual creator wallets:
- Use Solscan.io to view token transfers
- Check first significant buyer on bonding curve
- Look for patterns in creator's other launches
        """)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    show_probable_creators()
