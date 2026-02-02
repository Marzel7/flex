#!/usr/bin/env python3
"""
Initialize known address labels in the database.

This script adds addresses for known fee vaults, routers, and infrastructure
that you want to automatically tag when they appear in creator transactions.
"""

import sqlite3

DB_PATH = "pumpswap_tokens.db"

# Known addresses to add
KNOWN_ADDRESSES = {
    # GMGN Fee Vaults
    "5g7yNHyNQZhgKmJvG3K7xK8hN9pQ5rR2sT3uV4wX5yZ": {
        "label_name": "GMGN Fees Vault 5",
        "category": "feevault",
        "description": "GMGN fee aggregator for trading interface"
    },
    # Add more as you discover them
    # Format: "address": {"label_name": "...", "category": "...", "description": "..."}
}


def init_addresses():
    """Initialize known addresses in the database."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS address_labels (
                address TEXT PRIMARY KEY,
                label_name TEXT,
                category TEXT,
                description TEXT,
                source TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        added = 0
        for address, info in KNOWN_ADDRESSES.items():
            cursor.execute("""
                INSERT OR REPLACE INTO address_labels
                (address, label_name, category, description, source, synced_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                address,
                info["label_name"],
                info.get("category", "unknown"),
                info.get("description", ""),
                "manual"
            ))
            added += 1
            print(f"✓ Added: {info['label_name']} ({address[:16]}...)")

        conn.commit()
        conn.close()

        print(f"\n✓ Initialized {added} known addresses")
        return added

    except Exception as e:
        print(f"✗ Error: {e}")
        return 0


if __name__ == "__main__":
    init_addresses()
