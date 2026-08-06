#!/usr/bin/env python3
"""
Address tags utility module for persistent address metadata.

Works like INFRA_ACCOUNTS and CEX_ACCOUNTS, but stores discovered tags
(domains, patterns, etc.) in the database for reuse across the system.
"""

import sqlite3
from typing import Dict, List, Optional, Set

DB_PATH = "flex_complete_database.db"


def get_address_tags(address: str) -> Dict[str, List[str]]:
    """
    Get all tags for an address, grouped by tag_type.
    Returns: {tag_type: [tag_value1, tag_value2, ...]}
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tag_type, tag_value
            FROM address_tags
            WHERE address = ?
            ORDER BY tag_type, tag_value
        """, (address,))

        tags: Dict[str, List[str]] = {}
        for tag_type, tag_value in cursor.fetchall():
            if tag_type not in tags:
                tags[tag_type] = []
            tags[tag_type].append(tag_value)

        conn.close()
        return tags
    except Exception as e:
        return {}


def get_domain_tag(address: str) -> Optional[str]:
    """Get the SNS domain for an address (tag_type='domain')"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tag_value
            FROM address_tags
            WHERE address = ? AND tag_type = 'domain'
            LIMIT 1
        """, (address,))

        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        return None


def has_tag(address: str, tag_type: str, tag_value: Optional[str] = None) -> bool:
    """
    Check if an address has a specific tag.
    If tag_value is None, checks if any tag of that type exists.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        if tag_value:
            cursor.execute("""
                SELECT 1 FROM address_tags
                WHERE address = ? AND tag_type = ? AND tag_value = ?
                LIMIT 1
            """, (address, tag_type, tag_value))
        else:
            cursor.execute("""
                SELECT 1 FROM address_tags
                WHERE address = ? AND tag_type = ?
                LIMIT 1
            """, (address, tag_type))

        result = cursor.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        return False


def add_tag(address: str, tag_type: str, tag_value: str, source: str = "manual") -> bool:
    """Add a tag to an address"""
    # X78.0 -- conn.close() was only reached on success; called from
    # domain_extraction.resolve_domains_for_addresses_async, itself called
    # from inside creator_funding_worker's extraction hot path (event-loop
    # thread) -- an exception here left the connection, and its write
    # lease, open for the rest of that thread's life.
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO address_tags
            (address, tag_type, tag_value, source, first_seen_at)
            VALUES (?, ?, ?, ?, ?)
        """, (address, tag_type, tag_value, source, int(__import__('time').time())))
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_addresses_by_tag(tag_type: str, tag_value: Optional[str] = None) -> Set[str]:
    """
    Get all addresses with a specific tag type.
    If tag_value is specified, only return addresses with that specific value.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        if tag_value:
            cursor.execute("""
                SELECT DISTINCT address
                FROM address_tags
                WHERE tag_type = ? AND tag_value = ?
            """, (tag_type, tag_value))
        else:
            cursor.execute("""
                SELECT DISTINCT address
                FROM address_tags
                WHERE tag_type = ?
            """, (tag_type,))

        addresses = {row[0] for row in cursor.fetchall()}
        conn.close()
        return addresses
    except Exception as e:
        return set()


def get_all_addresses_with_domains() -> Dict[str, str]:
    """Get all addresses that have SNS domains mapped"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT address, tag_value
            FROM address_tags
            WHERE tag_type = 'domain'
        """)

        domains = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return domains
    except Exception as e:
        return {}


def format_address_with_tags(address: str, max_tags: int = 3) -> str:
    """
    Format an address for display with its top tags.
    Example: "6LY1JzAF... [kraken.sol] [domain]"
    """
    tags = get_address_tags(address)

    if not tags:
        return f"{address[:14]}..."

    # Prioritize domain tags
    tag_list = []
    if 'domain' in tags:
        for domain in tags['domain'][:max_tags]:
            tag_list.append(f"[{domain}]")

    # Add other tags
    for tag_type in sorted(tags.keys()):
        if tag_type == 'domain':
            continue
        for tag_value in tags[tag_type][:max_tags]:
            tag_list.append(f"[{tag_type}]")

    tags_str = " ".join(tag_list[:max_tags])
    return f"{address[:14]}... {tags_str}" if tags_str else f"{address[:14]}..."


if __name__ == "__main__":
    print("Address Tags Utility Module")
    print("=" * 80)

    # Test - show stats
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Count domains
        cursor.execute("SELECT COUNT(DISTINCT address) FROM address_tags WHERE tag_type = 'domain'")
        domain_count = cursor.fetchone()[0]

        # Count all tags
        cursor.execute("SELECT COUNT(*) FROM address_tags")
        total_tags = cursor.fetchone()[0]

        # Count tag types
        cursor.execute("SELECT COUNT(DISTINCT tag_type) FROM address_tags")
        tag_types = cursor.fetchone()[0]

        conn.close()

        print(f"Total tags: {total_tags}")
        print(f"Unique addresses: {domain_count} with domains")
        print(f"Tag types: {tag_types}")
    except Exception as e:
        print(f"Error: {e}")
