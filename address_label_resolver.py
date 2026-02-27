#!/usr/bin/env python3
"""
Address Label Resolver

Provides label lookup for addresses against our synced database.
Used to skip infrastructure/known accounts during domain extraction.
"""

import sqlite3
from typing import Optional, Dict, List
from functools import lru_cache

DB_PATH = "flex_complete_database.db"


@lru_cache(maxsize=10000)
def get_address_label(address: str) -> Optional[Dict]:
    """
    Get label information for an address from database.
    
    Returns dict with: label_name, category, risk_level, source, tags
    Or None if address not found.
    
    Cached for performance.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT label_name, category, risk_level, source, tags
            FROM address_labels
            WHERE address = ?
            LIMIT 1
        """, (address,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "label_name": row["label_name"],
                "category": row["category"],
                "risk_level": row["risk_level"],
                "source": row["source"],
                "tags": row["tags"],
            }
    
    except Exception as e:
        print(f"[LABEL] Error looking up {address}: {e}")
    
    return None


def is_infrastructure(address: str) -> bool:
    """Check if address is a known infrastructure account."""
    label = get_address_label(address)
    return label and label["category"] in ("system", "program", "automation", "bridge", "validator")


def is_user_wallet(address: str) -> bool:
    """Check if address is likely a user wallet (not infrastructure)."""
    return not is_infrastructure(address)


def format_address_with_label(address: str, short: bool = False) -> str:
    """
    Format address with its label if available.
    
    Examples:
      - With label: "Jupiter Aggregator (JUP6...)"
      - Without: "4cKLM5...v3n"
    """
    label = get_address_label(address)
    
    if label:
        label_name = label["label_name"]
        if short:
            return f"{label_name} ({address[:8]}...)"
        return label_name
    
    # No label, return shortened address
    if short:
        return address[:16] + "..."
    return address


def get_labels_by_category(category: str) -> Dict[str, Dict]:
    """Get all addresses in a category."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT address, label_name, category, risk_level, source
            FROM address_labels
            WHERE category = ?
        """, (category,))
        
        result = {}
        for row in cursor.fetchall():
            result[row["address"]] = {
                "label_name": row["label_name"],
                "category": row["category"],
                "risk_level": row["risk_level"],
                "source": row["source"],
            }
        
        conn.close()
        return result
    
    except Exception:
        return {}


def get_infrastructure_addresses() -> set:
    """Get all known infrastructure addresses."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT address FROM address_labels
            WHERE category IN ('system', 'program', 'automation', 'bridge', 'validator')
        """)
        
        addresses = {row[0] for row in cursor.fetchall()}
        conn.close()
        return addresses
    
    except Exception:
        return set()


if __name__ == "__main__":
    # Test the label resolver
    print("Address Label Resolver Test")
    print("=" * 80)
    
    test_addresses = [
        "JUP6LkbZbjS1jKKwapdHyR353L1x6za6YEDSAXUJXU5",  # Jupiter
        "675kPX9MHTjS2zt1qrXjVnYYtYFZNKLw5xaKq5QBuvV",  # Raydium
        "AVUCZyuT35YSuj4RH7fwiyPu82Djn2Hfg7y2ND2XcnZH",  # Photon
        "RandomAddress1234567890123456789012345",  # Unknown
    ]
    
    for addr in test_addresses:
        label = get_address_label(addr)
        is_infra = is_infrastructure(addr)
        formatted = format_address_with_label(addr, short=True)
        
        print(f"\nAddress: {addr[:20]}...")
        print(f"  Label: {label.get('label_name') if label else 'Unknown'}")
        print(f"  Infrastructure: {is_infra}")
        print(f"  Formatted: {formatted}")
    
    print("\n" + "=" * 80)
