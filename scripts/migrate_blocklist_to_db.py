#!/usr/bin/env python3
"""
Migrate existing creator block list data to database.

This script loads blocklist data from creator_block_list.json
and populates the database creator_blocklist table.
"""

import json
import sqlite3
import sys

DB_PATH = "pumpswap_tokens.db"
BLOCKLIST_FILE = "creator_block_list.json"


def migrate_blocklist():
    """Load blocklist from JSON file and insert into database"""

    try:
        # Load blocklist from file
        with open(BLOCKLIST_FILE, 'r') as f:
            blocklist = json.load(f)

        malicious = blocklist.get("malicious_creators", [])
        suspicious = blocklist.get("suspicious_creators", [])

        print(f"[MIGRATE] Loading blocklist from {BLOCKLIST_FILE}")
        print(f"[MIGRATE] Found {len(malicious)} malicious creators")
        print(f"[MIGRATE] Found {len(suspicious)} suspicious creators")

        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Insert malicious creators
        for creator in malicious:
            cursor.execute(
                """INSERT OR IGNORE INTO creator_blocklist
                   (creator_address, rug_count, reputation, first_rug_detected_at, last_rug_detected_at)
                   VALUES (?, 2, 'MALICIOUS', datetime('now'), datetime('now'))""",
                (creator,)
            )

        # Insert suspicious creators
        for creator in suspicious:
            cursor.execute(
                """INSERT OR IGNORE INTO creator_blocklist
                   (creator_address, rug_count, reputation, first_rug_detected_at, last_rug_detected_at)
                   VALUES (?, 1, 'SUSPICIOUS', datetime('now'), datetime('now'))""",
                (creator,)
            )

        conn.commit()
        conn.close()

        print(f"\n[MIGRATE] ✅ Migration complete!")
        print(f"[MIGRATE] {len(malicious) + len(suspicious)} creators loaded to database")

    except FileNotFoundError:
        print(f"[ERROR] Block list file not found: {BLOCKLIST_FILE}")
        print(f"[ERROR] Run 'python3 scripts/analyze_creator_patterns.py' first")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    migrate_blocklist()
