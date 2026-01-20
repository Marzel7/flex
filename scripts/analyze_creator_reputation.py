#!/usr/bin/env python3
"""
Analyze creator reputation based on their token performance.
Uses ONLY token_creator (actual creators from Metaplex metadata).
creator_address is just the migration processor - NOT the creator.
"""

import sqlite3
import sys
from pathlib import Path
from collections import Counter, defaultdict

DB_PATH = Path(__file__).parent.parent / "pumpswap_tokens.db"

def analyze_creator_reputation():
    """Analyze creators and assign reputation"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        
        # Get ONLY tokens with actual token_creator (NOT creator_address)
        # creator_address is just the Pump.Fun migration processor wallet
        cursor.execute("""
            SELECT mint, 
                   token_creator,
                   rug_indicator, risk_level, rug_probability
            FROM token_analysis
            WHERE token_creator IS NOT NULL
            ORDER BY token_creator
        """)
        
        tokens = cursor.fetchall()
        
        if not tokens:
            print("No tokens with token_creator data found")
            conn.close()
            return False
        
        # Group by actual token creator
        creators = defaultdict(list)
        for mint, token_creator, rug_flag, risk_level, rug_prob in tokens:
            creators[token_creator].append({
                'mint': mint,
                'rug_flag': rug_flag,
                'risk_level': risk_level,
                'rug_prob': rug_prob if rug_prob else 0
            })
        
        # Analyze each creator
        creator_reputations = {}
        
        for token_creator, tokens_list in creators.items():
            token_count = len(tokens_list)
            
            # Count rug tokens
            rug_count = sum(1 for t in tokens_list if t['rug_flag'] == 'quick_peak_low_mc')
            rug_rate = rug_count / token_count if token_count > 0 else 0
            
            # Average risk metrics
            avg_rug_prob = sum(t['rug_prob'] for t in tokens_list) / token_count if token_count > 0 else 0
            high_risk_count = sum(1 for t in tokens_list if t['risk_level'] == 'HIGH')
            
            # Determine reputation based on actual creator behavior
            if token_count >= 2 and rug_rate >= 0.40:  # 2+ tokens, 40%+ rug rate
                reputation = "MALICIOUS"
                reason = f"{token_count} tokens, {rug_rate*100:.0f}% rug rate"
            elif token_count >= 2 and rug_rate >= 0.50:  # Even 1 token: 50%+ rug prob
                reputation = "MALICIOUS"
                reason = f"{token_count} tokens, {rug_rate*100:.0f}% rug rate"
            elif rug_rate == 1.0:  # Single token that's 100% rug
                reputation = "MALICIOUS"
                reason = "Single token, 100% rug"
            else:
                reputation = None  # Unknown or clean
                reason = None
            
            creator_reputations[token_creator] = {
                'reputation': reputation,
                'token_count': token_count,
                'rug_count': rug_count,
                'rug_rate': rug_rate,
                'avg_rug_prob': avg_rug_prob,
                'high_risk_count': high_risk_count,
                'reason': reason
            }
        
        # Update database - ONLY for tokens with token_creator
        updates = 0
        for token_creator, rep_info in creator_reputations.items():
            if rep_info['reputation']:
                cursor.execute("""
                    UPDATE token_analysis
                    SET creator_reputation = ?
                    WHERE token_creator = ?
                """, (rep_info['reputation'], token_creator))
                updates += cursor.rowcount
        
        # Clear reputation for other tokens (those without token_creator)
        cursor.execute("""
            UPDATE token_analysis
            SET creator_reputation = NULL
            WHERE token_creator IS NULL
        """)
        
        conn.commit()
        conn.close()
        
        # Print summary
        print("\n=== Creator Reputation Analysis (Based on Actual Creators) ===\n")
        print("NOTE: creator_address = Migration Processor (NOT the creator)")
        print("      token_creator = Actual creator from Metaplex metadata\n")
        
        malicious_count = 0
        for token_creator in sorted(creator_reputations.keys()):
            rep = creator_reputations[token_creator]
            print(f"Creator: {token_creator}")
            print(f"  Tokens launched: {rep['token_count']}")
            print(f"  Rug tokens: {rep['rug_count']} ({rep['rug_rate']*100:.1f}%)")
            print(f"  Avg rug probability: {rep['avg_rug_prob']*100:.1f}%")
            print(f"  High risk tokens: {rep['high_risk_count']}")
            
            if rep['reputation']:
                print(f"  🏷️  REPUTATION: {rep['reputation']} 🚨")
                if rep['reason']:
                    print(f"     Reason: {rep['reason']}")
                malicious_count += 1
            else:
                print(f"  🏷️  REPUTATION: Clean/Legitimate ✓")
            print()
        
        print(f"\n=== Summary ===")
        print(f"✓ Total creators with token_creator data: {len(creator_reputations)}")
        print(f"🚨 Malicious creators flagged: {malicious_count}")
        print(f"✓ Updated {updates} tokens with creator reputations")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = analyze_creator_reputation()
    sys.exit(0 if success else 1)
