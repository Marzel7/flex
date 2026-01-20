#!/usr/bin/env python3
"""
Check if token_creator and pool_address are shared across multiple tokens.

This identifies COORDINATED RUG NETWORKS:
- If same token_creator appears in multiple tokens → Same person controls multiple tokens
- If same pool_address appears in multiple tokens → Same extraction point
- If same creator_address appears in multiple tokens → Same owner/authority

This is THE smoking gun for coordinated rug operations.
"""

import sqlite3
from datetime import datetime
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"


def analyze_authority_sharing():
    """Analyze which authorities appear in multiple tokens"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        print("\n" + "=" * 80)
        print("AUTHORITY SHARING ANALYSIS - Coordinated Rug Detection")
        print("=" * 80 + "\n")

        # 1. Token Creator Sharing
        print("[1] TOKEN CREATOR (Mint Authority) SHARING")
        print("-" * 80)

        cursor.execute("""
            SELECT
                token_creator,
                COUNT(*) as token_count,
                GROUP_CONCAT(SUBSTR(mint, 1, 8), ', ') as sample_tokens
            FROM token_analysis
            WHERE token_creator IS NOT NULL AND token_creator != ''
            GROUP BY token_creator
            HAVING COUNT(*) > 1
            ORDER BY token_count DESC
        """)

        token_creator_sharing = cursor.fetchall()
        if token_creator_sharing:
            print(f"Found {len(token_creator_sharing)} token creators controlling MULTIPLE tokens:\n")
            for creator, count, samples in token_creator_sharing:
                creator_short = f"{creator[:8]}...{creator[-4:]}"
                print(f"  🚨 {creator_short}: Controls {count} tokens")
                print(f"     Samples: {samples}")

                # Get details of all tokens controlled by this creator
                cursor.execute("""
                    SELECT mint, earliest_tx_creator, risk_level, rug_indicator
                    FROM token_analysis
                    WHERE token_creator = ?
                    ORDER BY created_at DESC
                """, (creator,))
                tokens = cursor.fetchall()
                for mint, orig_creator, risk, rug in tokens:
                    orig_short = f"{orig_creator[:8]}...{orig_creator[-4:]}" if orig_creator else "???"
                    rug_status = "🚩 RUG" if rug else "✓ OK"
                    print(f"       • {mint[:16]}... | Creator: {orig_short} | Risk: {risk} | {rug_status}")
                print()
        else:
            print("✓ No token creators controlling multiple tokens\n")

        # 2. Creator Address Sharing
        print("[2] CREATOR ADDRESS (Token Owner) SHARING")
        print("-" * 80)

        cursor.execute("""
            SELECT
                creator_address,
                COUNT(*) as token_count,
                GROUP_CONCAT(SUBSTR(mint, 1, 8), ', ') as sample_tokens
            FROM token_analysis
            WHERE creator_address IS NOT NULL AND creator_address != ''
            GROUP BY creator_address
            HAVING COUNT(*) > 1
            ORDER BY token_count DESC
        """)

        creator_address_sharing = cursor.fetchall()
        if creator_address_sharing:
            print(f"Found {len(creator_address_sharing)} creator addresses controlling MULTIPLE tokens:\n")
            for address, count, samples in creator_address_sharing:
                address_short = f"{address[:8]}...{address[-4:]}"
                print(f"  🚨 {address_short}: Owns {count} tokens")
                print(f"     Samples: {samples}")

                cursor.execute("""
                    SELECT mint, earliest_tx_creator, risk_level, rug_indicator
                    FROM token_analysis
                    WHERE creator_address = ?
                    ORDER BY created_at DESC
                """, (address,))
                tokens = cursor.fetchall()
                for mint, orig_creator, risk, rug in tokens:
                    orig_short = f"{orig_creator[:8]}...{orig_creator[-4:]}" if orig_creator else "???"
                    rug_status = "🚩 RUG" if rug else "✓ OK"
                    print(f"       • {mint[:16]}... | Creator: {orig_short} | Risk: {risk} | {rug_status}")
                print()
        else:
            print("✓ No creator addresses controlling multiple tokens\n")

        # 3. Pool Address Sharing
        print("[3] POOL ADDRESS (Liquidity Authority) SHARING")
        print("-" * 80)

        cursor.execute("""
            SELECT
                pool_address,
                COUNT(*) as token_count,
                GROUP_CONCAT(SUBSTR(mint, 1, 8), ', ') as sample_tokens
            FROM token_analysis
            WHERE pool_address IS NOT NULL AND pool_address != ''
            GROUP BY pool_address
            HAVING COUNT(*) > 1
            ORDER BY token_count DESC
        """)

        pool_sharing = cursor.fetchall()
        if pool_sharing:
            print(f"Found {len(pool_sharing)} pool addresses used in MULTIPLE tokens:\n")
            for pool, count, samples in pool_sharing:
                pool_short = f"{pool[:8]}...{pool[-4:]}"
                print(f"  🚨 {pool_short}: Used in {count} tokens")
                print(f"     Samples: {samples}")

                cursor.execute("""
                    SELECT mint, earliest_tx_creator, risk_level, rug_indicator
                    FROM token_analysis
                    WHERE pool_address = ?
                    ORDER BY created_at DESC
                """, (pool,))
                tokens = cursor.fetchall()
                for mint, orig_creator, risk, rug in tokens:
                    orig_short = f"{orig_creator[:8]}...{orig_creator[-4:]}" if orig_creator else "???"
                    rug_status = "🚩 RUG" if rug else "✓ OK"
                    print(f"       • {mint[:16]}... | Creator: {orig_short} | Risk: {risk} | {rug_status}")
                print()
        else:
            print("✓ No pool addresses used in multiple tokens\n")

        # 4. Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)

        total_coordinated_creators = len(token_creator_sharing) if token_creator_sharing else 0
        total_coordinated_owners = len(creator_address_sharing) if creator_address_sharing else 0
        total_coordinated_pools = len(pool_sharing) if pool_sharing else 0

        print(f"\n✓ Token creators in multiple tokens: {total_coordinated_creators}")
        print(f"✓ Creator addresses in multiple tokens: {total_coordinated_owners}")
        print(f"✓ Pool addresses in multiple tokens: {total_coordinated_pools}")

        if total_coordinated_creators > 0 or total_coordinated_owners > 0 or total_coordinated_pools > 0:
            print("\n🚨 COORDINATED RUG NETWORK DETECTED!")
            print("   Multiple tokens share authorities → Single operator controlling multiple rugs")
        else:
            print("\n✓ No obvious coordinated networks found (only 16 tokens have full data)")
            print("   Need to extract authorities for remaining 87 tokens to get complete picture")

        conn.close()

    except Exception as e:
        print(f"[ERROR] {e}")


if __name__ == "__main__":
    print(f"Authority Sharing Analysis at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    analyze_authority_sharing()
