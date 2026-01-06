#!/usr/bin/env python3
"""
Backfill risk assessment for tokens that haven't been analyzed yet.

This script finds all tokens with funding_risk_level = 'UNKNOWN' and 
runs the funding risk analysis on them, updating the database with 
the results.

Usage:
    python backfill_risk_assessment.py
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime

def backfill_risk_assessment():
    """Analyze all tokens with UNKNOWN risk status"""
    
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'
    
    if not db_path.exists():
        print("Error: Database not found")
        return False
    
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    c = conn.cursor()
    
    # Get all tokens with UNKNOWN risk
    c.execute('''
        SELECT base_mint, pumpfun_creator, symbol
        FROM pools
        WHERE funding_risk_level = 'UNKNOWN'
        ORDER BY first_seen DESC
    ''')
    
    unanalyzed = c.fetchall()
    
    if not unanalyzed:
        print("✅ All tokens already assessed!")
        conn.close()
        return True
    
    print(f"\nBackfilling risk assessment for {len(unanalyzed)} token(s)")
    print("="*100)
    
    try:
        # Import the analysis function
        sys.path.insert(0, str(Path(__file__).parent))
        from analyze_creator_wallet import analyze_creator_with_funding_reuse
        
        updated = 0
        
        for mint, creator, symbol in unanalyzed:
            symbol_display = symbol or mint[:6]
            print(f"\n{symbol_display}: Analyzing {creator[:16]}...", end=" ")
            
            # Get the funding analysis
            analysis = analyze_creator_with_funding_reuse(creator)
            
            if analysis:
                risk_level = analysis['overall_risk']
                pattern = analysis['coordination_pattern']
            else:
                # Default to LOW if no analysis
                risk_level = 'LOW'
                pattern = 'INDEPENDENT_CREATOR'
            
            # Update the pool record with risk assessment
            c.execute('''
                UPDATE pools
                SET funding_risk_level = ?, funding_risk_pattern = ?, funding_check_timestamp = ?
                WHERE base_mint = ?
            ''', (risk_level, pattern, datetime.now(), mint))
            
            print(f"✓ {risk_level}")
            updated += 1
        
        conn.commit()
        
        print(f"\n" + "="*100)
        print(f"✅ Backfilled {updated} token(s)!")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        conn.close()


if __name__ == '__main__':
    success = backfill_risk_assessment()
    sys.exit(0 if success else 1)
