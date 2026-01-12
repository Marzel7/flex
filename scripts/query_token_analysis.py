#!/usr/bin/env python3
"""
Query stored token analysis data for purchase strategy decisions.

Usage:
    python3 query_token_analysis.py                    # Show all analyzed tokens
    python3 query_token_analysis.py <MINT>            # Show analysis for specific token
    python3 query_token_analysis.py --risk-only       # Show only high-risk tokens
    python3 query_token_analysis.py --safe-only       # Show only low-risk tokens
    python3 query_token_analysis.py --sort-by-rug     # Sort by rug probability (highest first)
"""

import sqlite3
import sys
from datetime import datetime
from typing import List, Dict, Optional

DB_PATH = "pumpswap_tokens.db"


class TokenAnalysisQuery:
    """Query and display token analysis data"""

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path

    def get_all_analysis(self) -> List[Dict]:
        """Fetch all analyzed tokens"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM token_analysis ORDER BY analyzed_at DESC")
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error querying database: {e}", flush=True)
            return []

    def get_token_analysis(self, mint: str) -> Optional[Dict]:
        """Fetch analysis for specific token"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM token_analysis WHERE mint = ?", (mint,))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            print(f"❌ Error querying database: {e}", flush=True)
            return None

    def get_by_risk_level(self, risk_level: str) -> List[Dict]:
        """Fetch tokens by risk level"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM token_analysis WHERE amm_risk_level = ? ORDER BY amm_rug_probability DESC",
                (risk_level,)
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error querying database: {e}", flush=True)
            return []

    def get_high_risk(self) -> List[Dict]:
        """Fetch all high-risk tokens (75%+ rug probability)"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM token_analysis WHERE amm_rug_probability >= 0.75 ORDER BY amm_rug_probability DESC"
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error querying database: {e}", flush=True)
            return []

    def get_safe_tokens(self) -> List[Dict]:
        """Fetch all safe tokens (25%- rug probability)"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM token_analysis WHERE amm_rug_probability <= 0.25 ORDER BY amm_rug_probability ASC"
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error querying database: {e}", flush=True)
            return []

    def format_timestamp(self, ts: float) -> str:
        """Format Unix timestamp to readable format"""
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    def print_token_summary(self, data: Dict):
        """Print formatted summary of a single token"""
        print(f"\n{'='*80}")
        print(f"📊 TOKEN: {data['mint']}")
        print(f"{'='*80}")

        print(f"\n⏰ Analyzed: {self.format_timestamp(data['analyzed_at'])}")

        print(f"\n🔴 RISK ASSESSMENT:")
        print(f"  • AMM Risk Level: {data['amm_risk_level']}")
        print(f"  • Rug Probability: {data['amm_rug_probability']:.2%}")

        print(f"\n💰 BONDING CURVE METRICS ({data['events_parsed']} events):")
        print(f"  • Mint Concentration: {data['mint_concentration']:.3f}")
        print(f"  • Unique Minters Ratio: {data['unique_minters_ratio']:.3f}")
        print(f"  • Sell Suppression: {data['sell_suppression_ratio']:.3f}")
        print(f"  • Creator Activity Ratio: {data['creator_activity_ratio']:.3f}")

        print(f"\n📈 ACTIVITY METRICS:")
        print(f"  • Mint Velocity: {data['mint_velocity_sec']:.2f} mints/sec")
        print(f"  • Buy Size Variance: {data['buy_size_variance']:.0f}")
        print(f"  • Sell Volume Concentration: {data['sell_volume_concentration']:.3f}")

        print(f"\n🎯 PRE-MIGRATION RISK: {data['risk_level']}")
        print(f"  • Pre-Migration Rug Probability: {data['rug_probability']:.2%}")

    def print_table(self, tokens: List[Dict]):
        """Print tokens in table format"""
        if not tokens:
            print("No tokens found.")
            return

        # Header
        print(f"\n{'MINT':<45} {'RISK':<12} {'RUG %':<8} {'ANALYZED':<20}")
        print("-" * 85)

        # Rows
        for token in tokens:
            mint_short = token['mint'][:40] + "..." if len(token['mint']) > 40 else token['mint']
            risk = token['amm_risk_level']
            rug_pct = f"{token['amm_rug_probability']:.1%}"
            analyzed = self.format_timestamp(token['analyzed_at'])[-10:]  # Just time

            print(f"{mint_short:<45} {risk:<12} {rug_pct:<8} {analyzed:<20}")

    def print_purchase_strategy(self, data: Dict):
        """Print purchase strategy recommendations based on analysis"""
        rug_prob = data['amm_rug_probability']
        mint_conc = data['mint_concentration']
        sell_supp = data['sell_suppression_ratio']
        unique_ratio = data['unique_minters_ratio']

        print(f"\n🎯 PURCHASE STRATEGY FOR {data['mint'][:16]}...")
        print("=" * 80)

        # Risk tier
        if rug_prob <= 0.25:
            print(f"✅ SAFE - Low rug probability ({rug_prob:.1%})")
            print("   Recommendation: SAFE TO BUY during AMM migration")
            confidence = "HIGH"
        elif rug_prob <= 0.50:
            print(f"⚠️  CAUTION - Medium rug probability ({rug_prob:.1%})")
            print("   Recommendation: MODERATE risk - Use position sizing")
            confidence = "MEDIUM"
        elif rug_prob <= 0.75:
            print(f"🔴 HIGH RISK - High rug probability ({rug_prob:.1%})")
            print("   Recommendation: AVOID or minimal position size only")
            confidence = "LOW"
        else:
            print(f"☠️  CRITICAL - Very high rug probability ({rug_prob:.1%})")
            print("   Recommendation: DO NOT BUY - Likely rug")
            confidence = "CRITICAL"

        # Key factors
        print(f"\n📌 KEY RISK FACTORS:")
        if mint_conc > 0.6:
            print(f"   ⚠️  High mint concentration ({mint_conc:.1%}) - Top wallets control supply")
        if sell_supp > 0.8:
            print(f"   ⚠️  High sell suppression ({sell_supp:.1%}) - Few sell opportunities")
        if unique_ratio < 0.3:
            print(f"   ⚠️  Low unique minter ratio ({unique_ratio:.1%}) - Limited participation")

        print(f"\n💡 CONFIDENCE LEVEL: {confidence}")
        print(f"   Based on {data['events_parsed']} bonding curve events")


def main():
    query = TokenAnalysisQuery()

    if len(sys.argv) < 2:
        # Show all tokens
        tokens = query.get_all_analysis()
        if tokens:
            print(f"\n📊 ANALYZED TOKENS ({len(tokens)} total)")
            query.print_table(tokens)
            print(f"\nFor details: python3 query_token_analysis.py <MINT>")
        else:
            print("No analyzed tokens yet.")
        return

    arg = sys.argv[1]

    if arg == "--help" or arg == "-h":
        print(__doc__)
        return

    if arg == "--risk-only":
        # Show only high-risk tokens
        tokens = query.get_high_risk()
        print(f"\n🔴 HIGH-RISK TOKENS ({len(tokens)} total)")
        query.print_table(tokens)
        for token in tokens:
            query.print_purchase_strategy(token)
        return

    if arg == "--safe-only":
        # Show only safe tokens
        tokens = query.get_safe_tokens()
        print(f"\n✅ SAFE TOKENS ({len(tokens)} total)")
        query.print_table(tokens)
        for token in tokens[:5]:  # Show first 5 in detail
            query.print_purchase_strategy(token)
        return

    if arg == "--sort-by-rug":
        # Sort by rug probability
        tokens = query.get_all_analysis()
        tokens.sort(key=lambda x: x['amm_rug_probability'], reverse=True)
        print(f"\n📊 TOKENS SORTED BY RUG PROBABILITY")
        query.print_table(tokens)
        return

    # Specific token query
    token = query.get_token_analysis(arg)
    if token:
        query.print_token_summary(token)
        query.print_purchase_strategy(token)
    else:
        print(f"❌ Token {arg} not found in analysis database.")
        print(f"   Make sure the listener has analyzed it (can take several minutes)")


if __name__ == "__main__":
    main()
