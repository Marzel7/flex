#!/usr/bin/env python3
"""
Analyze creator rug patterns and build reputation.

Once creators are extracted via earliest_tx_creator, this script:
1. Counts tokens per creator
2. Calculates rug rate per creator
3. Identifies serial ruggers
4. Generates reputation scores
5. Creates block list for trading bot
"""

import sqlite3
import json
from collections import defaultdict
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"


def analyze_creators():
    """Analyze all creators and their rug patterns"""

    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all tokens with creators
        cursor.execute("""
            SELECT
                earliest_tx_creator,
                mint,
                rug_indicator,
                rug_probability,
                risk_level,
                created_at
            FROM token_analysis
            WHERE earliest_tx_creator IS NOT NULL
            ORDER BY earliest_tx_creator, created_at
        """)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("[ANALYZE] No tokens with creators found")
            return

        # Group by creator
        creators = defaultdict(list)
        for row in rows:
            creator = row["earliest_tx_creator"]
            creators[creator].append({
                "mint": row["mint"],
                "rug_indicator": row["rug_indicator"],
                "rug_probability": row["rug_probability"],
                "risk_level": row["risk_level"],
                "created_at": row["created_at"]
            })

        # Calculate reputation for each creator
        malicious_creators = []
        clean_creators = []
        suspicious_creators = []

        print(f"\n[ANALYZE] Analyzing {len(creators)} creators...")
        print("=" * 100)

        for creator, tokens in sorted(creators.items(), key=lambda x: len(x[1]), reverse=True):
            token_count = len(tokens)
            rugged_count = sum(1 for t in tokens if t["rug_indicator"] == "quick_peak_low_mc" or t["rug_probability"] > 0.7)
            rug_rate = rugged_count / token_count if token_count > 0 else 0

            reputation = "UNKNOWN"
            if token_count >= 2 and rug_rate > 0.40:
                reputation = "🔴 MALICIOUS"
                malicious_creators.append((creator, token_count, rugged_count, rug_rate))
            elif token_count == 1 and rug_rate == 1.0:
                reputation = "🔴 SUSPICIOUS"
                suspicious_creators.append((creator, token_count, rugged_count, rug_rate))
            else:
                reputation = "🟢 CLEAN"
                clean_creators.append((creator, token_count, rugged_count, rug_rate))

            # Show detail
            print(f"\n{reputation} {creator}")
            print(f"   Tokens: {token_count} | Rugged: {rugged_count} | Rug Rate: {rug_rate*100:.0f}%")

            # List tokens by this creator
            for token in tokens:
                status = "🔴" if token["rug_indicator"] == "quick_peak_low_mc" or token["rug_probability"] > 0.7 else "🟢"
                print(f"     {status} {token['mint'][:8]}... (risk: {token['risk_level']}, prob: {token['rug_probability']:.1%})")

        # Summary
        print("\n" + "=" * 100)
        print(f"\n[SUMMARY] Creator Analysis Results:")
        print(f"  Total creators: {len(creators)}")
        print(f"  🔴 MALICIOUS (2+ tokens, >40% rug rate): {len(malicious_creators)}")
        for creator, token_count, rugged_count, rug_rate in malicious_creators:
            print(f"     - {creator}: {token_count} tokens, {rugged_count} rugged ({rug_rate*100:.0f}%)")

        print(f"  🟡 SUSPICIOUS (1 token, 100% rug rate): {len(suspicious_creators)}")
        for creator, token_count, rugged_count, rug_rate in suspicious_creators:
            print(f"     - {creator}: {token_count} tokens, {rugged_count} rugged ({rug_rate*100:.0f}%)")

        print(f"  🟢 CLEAN (2+ tokens, <40% rug rate): {len(clean_creators)}")
        print(f"     (showing top 5)")
        for creator, token_count, rugged_count, rug_rate in sorted(clean_creators, key=lambda x: x[1], reverse=True)[:5]:
            print(f"     - {creator}: {token_count} tokens, {rugged_count} rugged ({rug_rate*100:.0f}%)")

        # Generate block list for trading bot
        block_list = [creator for creator, _, _, _ in malicious_creators]
        block_list.extend([creator for creator, _, _, _ in suspicious_creators])

        print(f"\n[BLOCK_LIST] {len(block_list)} creators to block:")
        for creator in sorted(block_list):
            print(f"  - {creator}")

        # Save block list to file
        block_list_file = "creator_block_list.json"
        with open(block_list_file, "w") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "malicious_creators": [creator for creator, _, _, _ in malicious_creators],
                "suspicious_creators": [creator for creator, _, _, _ in suspicious_creators],
                "all_blocked": block_list
            }, f, indent=2)

        print(f"\n[SAVED] Block list saved to {block_list_file}")

    except Exception as e:
        print(f"[ERROR] Analysis failed: {e}")


if __name__ == "__main__":
    print(f"[ANALYZE] Starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    analyze_creators()
    print(f"[ANALYZE] Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
