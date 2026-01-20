#!/usr/bin/env python3
"""
View and manage the rug creator block list.

Shows all creators who have launched rugs, organized by rug count.
Reads from database for real-time updates.
"""

import sqlite3
import json
import sys
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"


def view_blocklist():
    """Display the rug creator block list from database"""

    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Get all blocked creators
        cursor.execute(
            "SELECT creator_address, rug_count, reputation, rugged_tokens, first_rug_detected_at, last_rug_detected_at "
            "FROM creator_blocklist ORDER BY rug_count DESC"
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("[BLOCKLIST] No creators in block list yet")
            print("[BLOCKLIST] Block list will be created when first rug is detected")
            return

        print(f"\n{'═' * 100}")
        print(f"RUG CREATOR BLOCK LIST (from database)")
        print(f"{'═' * 100}\n")

        print(f"Total creators tracked: {len(rows)}")
        print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        print(f"{'─' * 100}\n")

        for rank, row in enumerate(rows, 1):
            creator, rug_count, reputation, rugged_tokens_json, first_detected, last_rug = row

            try:
                rugged_tokens = json.loads(rugged_tokens_json) if rugged_tokens_json else []
            except:
                rugged_tokens = []

            # Color code by risk
            if rug_count >= 3:
                risk_emoji = "🚨"
                risk_label = "CRITICAL"
            elif rug_count >= 2:
                risk_emoji = "⚠️"
                risk_label = "HIGH"
            else:
                risk_emoji = "📝"
                risk_label = "WATCH"

            print(f"[{rank}] {risk_emoji} {risk_label} - {creator}")
            print(f"    Rugs: {rug_count} | Reputation: {reputation}")
            print(f"    First detected: {first_detected}")
            print(f"    Last rug: {last_rug}")
            print(f"    Rugged tokens:")
            for token in rugged_tokens[:3]:  # Show first 3
                print(f"      • {token[:8]}...")
            if len(rugged_tokens) > 3:
                print(f"      • ... and {len(rugged_tokens) - 3} more")
            print()

        print(f"{'─' * 100}\n")

        # Summary stats
        total_rugs = sum(row[1] for row in rows)
        critical = sum(1 for row in rows if row[1] >= 3)
        high = sum(1 for row in rows if row[1] == 2)
        watch = sum(1 for row in rows if row[1] == 1)

        print(f"SUMMARY:")
        print(f"  Total rugs across all creators: {total_rugs}")
        print(f"  🚨 CRITICAL (3+ rugs): {critical}")
        print(f"  ⚠️  HIGH (2 rugs): {high}")
        print(f"  📝 WATCH (1 rug): {watch}")
        print()

    except Exception as e:
        print(f"[ERROR] Failed to read block list: {e}")
        sys.exit(1)


if __name__ == "__main__":
    view_blocklist()
