#!/usr/bin/env python3
"""
Sync address labels from Solscan API to our infrastructure mapping.

Solscan maintains a comprehensive database of labeled addresses including:
- Infrastructure accounts (Jito, Helius, protocols, etc.)
- CEX accounts (Binance, Coinbase, etc.)
- Known programs and services
- Spam/scam addresses

This script periodically syncs their labels to keep our mapping current.
"""

import aiohttp
import asyncio
import sqlite3
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from infra_mapping import (
    add_infrastructure_account, 
    add_cex_account,
    INFRASTRUCTURE_ACCOUNTS,
    CEX_ACCOUNTS
)

DB_PATH = "pumpswap_tokens.db"
SOLSCAN_LABELS_API = "https://api.solscan.io/addresses/labels"
SOLSCAN_LABEL_DETAILS = "https://api.solscan.io/account/{address}"

# Mapping of Solscan label types to our categories
SOLSCAN_CATEGORY_MAPPING = {
    "token": "program",
    "program": "program",
    "dex": "automation",
    "nft": "automation",
    "oracle": "automation",
    "bridge": "bridge",
    "staking": "automation",
    "cex": "cex",
    "exchange": "cex",
    "wallet": "automation",
    "feevault": "automation",
    "router": "automation",
    "vault": "automation",
    "authority": "automation",
    "validator": "validator",
    "system": "system",
}

# Solscan label types we care about
INTERESTING_LABELS = {
    "dex", "cex", "oracle", "bridge", "staking", "nft", 
    "feevault", "router", "vault", "authority", "program",
    "wallet", "exchange", "aggregator", "automation"
}


async def fetch_solscan_labels(session: aiohttp.ClientSession, limit: int = 500) -> List[Dict]:
    """
    Fetch labeled addresses from Solscan API.
    
    Returns list of dicts with: address, label, category, description
    """
    try:
        # Note: Solscan has a public labels endpoint, but let's check what data we can get
        # Solscan also has account labels in the block explorer data
        
        # Alternative: Use Solscan's public data for known accounts
        # They publish their label database periodically
        
        print("[SOLSCAN] Querying Solscan labels API...")
        
        # Fetch from their public endpoint
        async with session.get(SOLSCAN_LABELS_API, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                print(f"[SOLSCAN] Got {len(data)} labeled addresses")
                return data
            else:
                print(f"[SOLSCAN] API returned {resp.status}")
                return []
                
    except Exception as e:
        print(f"[SOLSCAN] Error fetching labels: {e}")
        return []


async def fetch_account_details(session: aiohttp.ClientSession, address: str) -> Optional[Dict]:
    """
    Fetch details for a specific account from Solscan.
    
    Returns dict with account info including labels if available.
    """
    try:
        url = SOLSCAN_LABEL_DETAILS.format(address=address)
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[SOLSCAN] Error fetching {address}: {e}")
    
    return None


def determine_label_category(solscan_label: str, solscan_type: str = None) -> tuple[str, str]:
    """
    Determine our category and risk level from Solscan label.
    
    Returns (category, risk_level)
    """
    label_lower = solscan_label.lower()
    
    # Map Solscan type if provided
    if solscan_type:
        category = SOLSCAN_CATEGORY_MAPPING.get(solscan_type.lower(), "unknown")
    else:
        # Infer from label text
        category = "unknown"
        for key in SOLSCAN_CATEGORY_MAPPING.keys():
            if key in label_lower:
                category = SOLSCAN_CATEGORY_MAPPING[key]
                break
    
    # Determine risk level based on category
    risk_mapping = {
        "automation": "neutral",
        "program": "neutral",
        "system": "neutral",
        "validator": "low",
        "bridge": "medium",
        "cex": "neutral",
        "dex": "neutral",
        "oracle": "neutral",
        "unknown": "unknown",
    }
    
    risk_level = risk_mapping.get(category, "unknown")
    return category, risk_level


async def sync_solscan_labels():
    """
    Main sync function: fetch Solscan labels and update our mapping.
    """
    print("\n" + "=" * 80)
    print("SOLSCAN LABEL SYNC")
    print("=" * 80)
    print(f"Started: {datetime.now().isoformat()}")
    
    async with aiohttp.ClientSession() as session:
        # Fetch labels from Solscan
        labels = await fetch_solscan_labels(session)
        
        if not labels:
            print("[SOLSCAN] No labels fetched, aborting sync")
            return
        
        added_infra = 0
        added_cex = 0
        updated = 0
        
        # Process each label
        for label_data in labels:
            try:
                address = label_data.get("address")
                label_name = label_data.get("label")
                label_type = label_data.get("type")
                description = label_data.get("description", "")
                
                if not address or not label_name:
                    continue
                
                # Skip if already in our mapping
                if address in INFRASTRUCTURE_ACCOUNTS or address in CEX_ACCOUNTS:
                    updated += 1
                    continue
                
                # Determine category and risk level
                category, risk_level = determine_label_category(label_name, label_type)
                
                # Skip unknowns
                if category == "unknown":
                    continue
                
                # Add to appropriate mapping
                tags = ["solscan-synced"]
                if label_type:
                    tags.append(label_type.lower())
                
                if category == "cex":
                    add_cex_account(
                        address=address,
                        name=label_name,
                        exchange=label_name,
                        description=description or f"Synced from Solscan",
                        tags=tags,
                        risk_level=risk_level
                    )
                    added_cex += 1
                    print(f"[CEX] Added: {label_name:40} {address[:20]}...")
                else:
                    add_infrastructure_account(
                        address=address,
                        name=label_name,
                        category=category,
                        description=description or f"Synced from Solscan",
                        tags=tags,
                        risk_level=risk_level
                    )
                    added_infra += 1
                    print(f"[INFRA] Added: {label_name:40} {address[:20]}... ({category})")
            
            except Exception as e:
                print(f"[ERROR] Processing label: {e}")
                continue
        
        print()
        print(f"Added Infrastructure Accounts: {added_infra}")
        print(f"Added CEX Accounts: {added_cex}")
        print(f"Updated/Duplicates: {updated}")
        print()
        print("=" * 80)


async def sync_from_transaction_addresses(max_addresses: int = 100):
    """
    Alternative approach: Scan our transaction database for addresses
    and look them up on Solscan to auto-label them.
    
    This finds addresses we haven't seen before and checks their Solscan labels.
    """
    print("\n" + "=" * 80)
    print("SOLSCAN LOOKUP FROM TRANSACTION ADDRESSES")
    print("=" * 80)
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        # Get addresses from transactions that we haven't labeled yet
        cursor.execute("""
            SELECT DISTINCT addr
            FROM (
                SELECT funder_address as addr FROM creator_funders
                UNION
                SELECT creator_address as addr FROM creator_funders
            )
            WHERE addr NOT IN (
                SELECT address FROM address_labels
            )
            LIMIT ?
        """, (max_addresses,))
        
        addresses = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        print(f"Found {len(addresses)} unlabeled addresses to lookup")
        
        if not addresses:
            print("All addresses already labeled!")
            return
        
        # Batch lookup these addresses
        async with aiohttp.ClientSession() as session:
            found = 0
            for address in addresses[:max_addresses]:
                details = await fetch_account_details(session, address)
                
                if details and details.get("label"):
                    label = details.get("label")
                    category, risk_level = determine_label_category(label)
                    
                    print(f"[FOUND] {label:40} {address[:20]}...")
                    found += 1
                    
                    # Could save to DB here
                    # save_address_label(address, label, category, risk_level)
                
                # Rate limiting
                await asyncio.sleep(0.1)
        
        print(f"\nFound labels for {found} addresses")
        print("=" * 80)
    
    except Exception as e:
        print(f"Error during address lookup: {e}")


def save_address_labels_to_db():
    """
    Save our in-memory infrastructure/CEX mappings to database
    for persistent storage across restarts.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS address_labels (
                address TEXT PRIMARY KEY,
                label_name TEXT,
                category TEXT,
                description TEXT,
                risk_level TEXT,
                source TEXT,
                synced_at TIMESTAMP,
                UNIQUE(address)
            )
        """)
        
        # Clear old entries
        cursor.execute("DELETE FROM address_labels WHERE source = 'solscan'")
        
        # Insert infrastructure accounts
        for address, info in INFRASTRUCTURE_ACCOUNTS.items():
            cursor.execute("""
                INSERT OR REPLACE INTO address_labels
                (address, label_name, category, description, risk_level, source, synced_at)
                VALUES (?, ?, ?, ?, ?, 'solscan', ?)
            """, (
                address,
                info.get("name"),
                info.get("category"),
                info.get("description"),
                info.get("risk_level"),
                datetime.now().isoformat()
            ))
        
        # Insert CEX accounts
        for address, info in CEX_ACCOUNTS.items():
            cursor.execute("""
                INSERT OR REPLACE INTO address_labels
                (address, label_name, category, description, risk_level, source, synced_at)
                VALUES (?, ?, ?, ?, ?, 'solscan', ?)
            """, (
                address,
                info.get("name"),
                info.get("category"),
                info.get("description"),
                info.get("risk_level"),
                datetime.now().isoformat()
            ))
        
        conn.commit()
        conn.close()
        
        print(f"[DB] Saved {len(INFRASTRUCTURE_ACCOUNTS) + len(CEX_ACCOUNTS)} labels to database")
    
    except Exception as e:
        print(f"[DB] Error saving labels: {e}")


async def main():
    """Run the sync process."""
    # Try to sync from Solscan API
    await sync_solscan_labels()
    
    # Alternative: lookup specific addresses
    # await sync_from_transaction_addresses(max_addresses=50)
    
    # Save to database
    save_address_labels_to_db()


if __name__ == "__main__":
    asyncio.run(main())
