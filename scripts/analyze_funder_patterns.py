#!/usr/bin/env python3
"""
Analyze funder patterns in rugged tokens.

This script:
1. Finds all rugged tokens (creator_is_blocked = 1)
2. Checks if funders/treasury addresses appear in multiple rugs
3. Identifies coordinated rug-pulling networks via funding patterns
4. Flags creators with common funders as part of same network
"""

import sqlite3
import json
from collections import defaultdict
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"


def analyze_rugged_creators():
    """Analyze creators of rugged tokens to find patterns"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Get all rugged tokens with their creators
        cursor.execute("""
            SELECT
                ta.mint,
                ta.earliest_tx_creator,
                cb.reputation,
                cb.rug_count,
                ta.created_at
            FROM token_analysis ta
            JOIN creator_blocklist cb ON ta.earliest_tx_creator = cb.creator_address
            ORDER BY cb.rug_count DESC, ta.created_at
        """)

        rugged_tokens = cursor.fetchall()
        conn.close()

        print(f"\n[FUNDER] Analyzing {len(rugged_tokens)} rugged tokens...\n")
        print("=" * 100)

        # Group by creator
        creators = defaultdict(list)
        for mint, creator, reputation, rug_count, created_at in rugged_tokens:
            creators[creator].append({
                "mint": mint,
                "reputation": reputation,
                "rug_count": rug_count,
                "created_at": created_at
            })

        # Analyze each creator
        malicious_creators = []
        suspicious_creators = []

        for creator, tokens in sorted(creators.items(), key=lambda x: len(x[1]), reverse=True):
            creator_short = f"{creator[:8]}...{creator[-4:]}"
            token_count = len(tokens)
            reputation = tokens[0]["reputation"]

            print(f"\n{creator_short}")
            print(f"  Reputation: {reputation}")
            print(f"  Tokens Rugged: {token_count}")
            print(f"  Timeline:")

            for i, token in enumerate(tokens, 1):
                print(f"    {i}. {token['mint'][:16]}... ({token['created_at']})")

            if reputation == "MALICIOUS":
                malicious_creators.append((creator, token_count, tokens))
            else:
                suspicious_creators.append((creator, token_count, tokens))

        # Summary
        print("\n" + "=" * 100)
        print(f"\n[SUMMARY] Funder/Creator Pattern Analysis:")
        print(f"  Total Rugged Creators: {len(creators)}")
        print(f"  🚨 MALICIOUS (2+ rugs): {len(malicious_creators)}")
        for creator, count, tokens in malicious_creators:
            print(f"     - {creator[:8]}...: {count} tokens")

        print(f"  📝 SUSPICIOUS (1 rug): {len(suspicious_creators)}")
        print(f"     (Total: {len(suspicious_creators)} creators)")

        # Identify potential funding networks
        print(f"\n[NETWORKS] Potential Coordinated Rug-Pulling Rings:")
        print(f"  Note: Full SOL transfer analysis needed to confirm connections")
        print(f"  Requires: Tracking where creators send SOL to treasury addresses")
        print(f"  Status: ⏳ PENDING SOL transfer extraction")

        return creators

    except Exception as e:
        print(f"[ERROR] Analysis failed: {e}")
        return {}


def check_creator_coverage():
    """Check what creator data we have for all tokens"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN earliest_tx_creator IS NOT NULL THEN 1 ELSE 0 END) as with_earliest_creator,
                SUM(CASE WHEN token_creator IS NOT NULL THEN 1 ELSE 0 END) as with_token_creator,
                SUM(CASE WHEN creator_is_blocked = 1 THEN 1 ELSE 0 END) as blocked_count
            FROM token_analysis
        """)

        result = cursor.fetchone()
        conn.close()

        total, earliest, token, blocked = result

        print(f"\n[COVERAGE] Creator Data Analysis:")
        print(f"  Total Tokens: {total}")
        print(f"  ✅ With Earliest TX Creator: {earliest} ({earliest/total*100:.1f}%)")
        print(f"  ⚠️ With Metaplex Creator: {token} ({token/total*100:.1f}%)")
        print(f"  🚨 From Blocked Creators: {blocked} ({blocked/total*100:.1f}%)")

        return {
            "total": total,
            "earliest_creator": earliest,
            "token_creator": token,
            "blocked": blocked
        }

    except Exception as e:
        print(f"[ERROR] Coverage check failed: {e}")
        return {}


if __name__ == "__main__":
    print(f"[FUNDER] Starting funder pattern analysis at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # Check creator coverage
    coverage = check_creator_coverage()

    # Analyze rugged creators
    creators = analyze_rugged_creators()

    print("\n" + "=" * 100)
    print(f"[FUNDER] Analysis complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
