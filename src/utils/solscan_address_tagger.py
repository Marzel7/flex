#!/usr/bin/env python3
"""
Automated address discovery and tagging system.

Instead of relying on external APIs, this system:
1. Analyzes recipient/funder patterns in your own transaction data
2. Tags creators with addresses they interact with
3. Maintains a local database of known addresses
4. Auto-discovers high-value recipients (likely fee vaults, routers)
"""

import sqlite3
from typing import Optional, Dict, Set
import time

DB_PATH = "flex_complete_database.db"

# In-memory cache for recently looked up addresses
LABEL_CACHE: Dict[str, tuple] = {}  # {address: (label_name, category, timestamp)}
CACHE_TTL = 3600  # 1 hour cache


def get_address_label_from_db(address: str) -> Optional[Dict]:
    """
    Look up an address label from local database.

    Returns dict with:
    - label_name: e.g. "GMGN Fees Vault 5"
    - category: e.g. "feevault", "router", "cex"
    - description: description from database

    Returns None if no label found.
    """
    if not isinstance(address, str) or len(address) < 30:
        return None

    # Check memory cache first
    if address in LABEL_CACHE:
        cached_label, cached_category, cached_time = LABEL_CACHE[address]
        if time.time() - cached_time < CACHE_TTL:
            return {
                "label_name": cached_label,
                "category": cached_category,
                "source": "cache"
            } if cached_label else None

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

        # Look up address in database
        cursor.execute("""
            SELECT label_name, category, description
            FROM address_labels
            WHERE address = ?
        """, (address,))

        result = cursor.fetchone()
        conn.close()

        if result:
            label_name, category, description = result
            # Cache it
            LABEL_CACHE[address] = (label_name, category, time.time())
            return {
                "label_name": label_name,
                "category": category,
                "description": description,
                "source": "database"
            }
        else:
            # Not found - negative cache
            LABEL_CACHE[address] = (None, None, time.time())
            return None

    except Exception as e:
        print(f"[LABEL_LOOKUP] ⚠ Error looking up {address[:16]}...: {e}")
        return None


def save_address_label(address: str, label_name: str, category: str = "", description: str = "", source: str = "manual"):
    """
    Save an address label to the database.

    Creates or updates address_labels table entry.
    """
    if not address or not label_name:
        return False

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
                risk_level TEXT,
                tags TEXT,
                source TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            INSERT OR REPLACE INTO address_labels
            (address, label_name, category, description, source)
            VALUES (?, ?, ?, ?, ?)
        """, (address, label_name, category, description, source))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"[SOLSCAN_TAG] ⚠ Error saving label: {e}")
        return False


def get_address_label(address: str) -> Optional[Dict]:
    """
    Get a saved label for an address from database.

    Returns dict with label_name, category, etc., or None if not found.
    """
    if not address:
        return None

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT label_name, category, description, source
            FROM address_labels
            WHERE address = ?
        """, (address,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "label_name": row[0],
                "category": row[1],
                "description": row[2],
                "source": row[3]
            }
        return None

    except Exception as e:
        print(f"[SOLSCAN_TAG] ⚠ Error getting label: {e}")
        return None


def tag_address_if_needed(address: str) -> Optional[Dict]:
    """
    Look up an address label from the local database.

    Returns the label info dict, or None if no label found.
    """
    if not address:
        return None

    # Check if already in database
    existing = get_address_label_from_db(address)
    return existing


def format_address_with_label(address: str, label: Optional[Dict] = None) -> str:
    """
    Format an address for display with its label if available.

    Examples:
    - "8iBa3q2N... (GMGN Fees Vault 5)"
    - "5g7yNHy... (Binance Hot Wallet)"
    - "RandomAddr..."
    """
    if not address:
        return "Unknown"

    short_addr = address[:8] + "..." if len(address) > 16 else address

    if label and label.get("label_name"):
        return f"{short_addr} ({label['label_name']})"

    return short_addr


# ============================================================================
# Integration with funding extractor
# ============================================================================

def tag_funder_if_labeled(funder_address: str) -> Optional[Dict]:
    """
    Look up and tag a funder address if it has a label.

    Returns label info if found, None otherwise.
    """
    return tag_address_if_needed(funder_address)


def tag_recipient_if_labeled(recipient_address: str) -> Optional[Dict]:
    """
    Look up and tag a recipient address if it has a label.

    Useful for tracking fee vault, router, and other protocol addresses.
    """
    return tag_address_if_needed(recipient_address)


def extract_service_names_from_description(description: str) -> set:
    """
    Extract service/account names mentioned in Helius transaction descriptions.

    Helius includes human-readable labels like:
    - "Transfer to Axiom Trading"
    - "Swap via GMGN"
    - "Fee to GMGN Fees Vault 5"
    - "Instruction: Jupiter"
    - "Program: Magic Eden"

    Returns set of service names found (e.g., {"Axiom Trading", "GMGN"}).
    """
    import re

    services = set()

    if not description or not isinstance(description, str):
        return services

    # Pattern 1: "to/from/via [Service Name]"
    pattern1 = r'(?:to|from|via|mint|with|through)\s+([A-Z][A-Za-z0-9\s\-\.()]+?)(?:\s+(?:for|Hot|Wallet|Deposit|Custody|\()|,|$)'

    # Pattern 2: "Program: [Name]" or "Instruction: [Name]"
    pattern2 = r'(?:Program|Instruction|Account|Service):\s*([A-Z][A-Za-z0-9\s\-\.()]+?)(?:\s*(?:\(|\)|,)|$)'

    # Pattern 3: Capitalized phrases that are likely service names
    pattern3 = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+[A-Z][a-z]+)?)\b'

    for pattern in [pattern1, pattern2]:
        for match in re.finditer(pattern, description):
            service_name = match.group(1).strip()

            # Filter out false positives
            if service_name and len(service_name) > 2:
                # Skip common false positives
                if service_name.upper() not in ["SOL", "USD", "TOKEN", "TRANSFER", "SWAP", "FEE"]:
                    # Only keep if has at least one letter
                    if any(c.isalpha() for c in service_name):
                        # Normalize: remove trailing/leading spaces and extra whitespace
                        service_name = ' '.join(service_name.split())
                        if len(service_name) > 2 and len(service_name) < 100:
                            services.add(service_name)

    return services


def tag_creator_with_services(creator: str, service_names: set) -> int:
    """
    Tag a creator with service names found in their transactions.

    Returns number of new tags added.
    """
    if not creator or not service_names:
        return 0

    tags_added = 0

    # X78.0 -- conn was only closed on the success path; any exception
    # between connect() and commit()/close() (e.g. this thread's write
    # lease already held by an earlier leak elsewhere in the same reused
    # asyncio.to_thread/event-loop worker thread) left this connection
    # open, extending whatever lease it managed to acquire indefinitely.
    # This function is called synchronously (no await) from
    # extract_for_creator, on the event-loop's own thread -- the same
    # thread creator_funding_worker's extraction runs on -- so a leak here
    # contributes directly to the self-perpetuating stall documented in
    # docs/audits/x78_0_creator_funding_concurrency.md. conn is declared
    # before the try so finally can safely check `is not None` regardless
    # of which line raised.
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_tags (
                creator_address TEXT NOT NULL,
                tag TEXT NOT NULL,
                description TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(creator_address, tag)
            )
        """)

        for service_name in service_names:
            # FILTER: Skip wallet addresses (Solana addresses are 44 chars or end with a period)
            # and other invalid tag patterns
            if len(service_name) > 40 or service_name.endswith('.') or '.' in service_name:
                continue

            # Skip if already tagged
            cursor.execute(
                "SELECT 1 FROM creator_tags WHERE creator_address = ? AND tag = ?",
                (creator, service_name)
            )

            if not cursor.fetchone():
                # Add new tag
                cursor.execute("""
                    INSERT INTO creator_tags
                    (creator_address, tag, description)
                    VALUES (?, ?, ?)
                """, (creator, service_name, f"Creator interacted with {service_name}"))
                tags_added += 1

        conn.commit()

    except Exception as e:
        print(f"[SERVICE_TAGS] ⚠ Error tagging creator: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return tags_added


def auto_discover_recipient_addresses() -> Dict[str, Dict]:
    """
    Auto-discover recipient addresses by analyzing patterns in creator_receivers.

    Returns dict of discovered addresses: {address: {label_name, sol_count, creator_count}}

    High-value recipients received SOL from many creators = likely fee vaults/routers.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Find recipients that:
        # - Appeared with 5+ creators (coordinated)
        # - Had 10+ transfers total (popular address)
        # - Received 0.1-1.0 SOL per transfer (fee-like amounts)
        cursor.execute("""
            SELECT
                receiver_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as transfer_count,
                SUM(amount_sol) as total_sol,
                AVG(amount_sol) as avg_sol
            FROM creator_receivers
            GROUP BY receiver_address
            HAVING
                creator_count >= 5
                AND transfer_count >= 10
                AND avg_sol >= 0.0005
                AND avg_sol <= 5.0
            ORDER BY creator_count DESC
        """)

        discovered = {}
        for row in cursor.fetchall():
            address, creator_count, transfer_count, total_sol, avg_sol = row

            # Skip if already labeled
            existing = get_address_label_from_db(address)
            if existing:
                continue

            # Create auto-generated label based on patterns
            if creator_count >= 50:
                label_name = f"Popular Fee Address #{address[:8]}"
                category = "feevault"
            elif creator_count >= 20:
                label_name = f"Protocol Router #{address[:8]}"
                category = "router"
            else:
                label_name = f"Recipient Address #{address[:8]}"
                category = "recipient"

            discovered[address] = {
                "label_name": label_name,
                "category": category,
                "creator_count": creator_count,
                "transfer_count": transfer_count,
                "total_sol": total_sol
            }

        conn.close()
        return discovered

    except Exception as e:
        print(f"[AUTO_DISCOVER] ⚠ Error discovering addresses: {e}")
        return {}


def save_discovered_addresses(discovered: Dict[str, Dict]) -> int:
    """
    Save auto-discovered addresses to the database.

    Returns number of addresses saved.
    """
    if not discovered:
        return 0

    saved_count = 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        for address, info in discovered.items():
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO address_labels
                    (address, label_name, category, description, source, synced_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    address,
                    info["label_name"],
                    info["category"],
                    f"Auto-discovered: {info['creator_count']} creators, {info['transfer_count']} transfers",
                    "autodiscovery"
                ))
                saved_count += 1
            except Exception as e:
                print(f"[AUTO_DISCOVER] ⚠ Error saving {address[:16]}...: {e}")

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"[AUTO_DISCOVER] ⚠ Error: {e}")

    return saved_count


if __name__ == "__main__":
    # Test script
    print("Testing address discovery and tagging system...\n")

    # Test 1: Try to get a label for a known address
    print("Test 1: Looking up known addresses")
    test_addr = "11111111111111111111111111111111"
    label = tag_address_if_needed(test_addr)
    if label:
        print(f"  ✓ {test_addr[:16]}... → {label['label_name']}")
    else:
        print(f"  - {test_addr[:16]}... → No label yet")

    # Test 2: Auto-discover high-value recipients
    print("\nTest 2: Auto-discovering recipient addresses")
    discovered = auto_discover_recipient_addresses()
    if discovered:
        print(f"  Found {len(discovered)} candidate addresses:")
        for addr, info in list(discovered.items())[:3]:
            print(f"    - {addr[:16]}... ({info['creator_count']} creators, {info['transfer_count']} transfers)")

        # Save them
        saved = save_discovered_addresses(discovered)
        print(f"  Saved {saved} addresses to database")
    else:
        print("  No high-value recipients discovered yet")

    # Test 3: Manually add a known address
    print("\nTest 3: Adding a known address manually")
    save_address_label(
        "5g7yNHyNQZhgKmJvG3K7xK8hN9pQ5rR2sT3uV4wX5yZ",
        "GMGN Fees Vault 5",
        "feevault",
        "GMGN fee aggregator for trading interface",
        "manual"
    )
    print("  ✓ Added GMGN Fees Vault 5")

    # Test 4: Verify lookup works
    print("\nTest 4: Verifying label lookup")
    label = tag_address_if_needed("5g7yNHyNQZhgKmJvG3K7xK8hN9pQ5rR2sT3uV4wX5yZ")
    if label:
        print(f"  ✓ Found: {label['label_name']} ({label['category']})")
    else:
        print("  ✗ Not found")
