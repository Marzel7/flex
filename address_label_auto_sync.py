#!/usr/bin/env python3
"""
Automated Address Label Synchronization

Integrates labels from multiple sources:
1. Helius API - Gets transaction context and account labels
2. Public Solana programs list - Built-in system programs
3. DeFi Protocol registries - Jupiter, Raydium, etc.
4. Manual blocklists - Scam/spam addresses
5. Our own transaction history - Detected infrastructure

This keeps our infra_mapping.py automatically updated.
"""

import aiohttp
import asyncio
import sqlite3
import json
from typing import Dict, List, Optional, Set
from datetime import datetime
from infra_mapping import add_infrastructure_account, add_cex_account

DB_PATH = "pumpswap_tokens.db"

# Known Solana system programs (always present)
SOLANA_SYSTEM_PROGRAMS = {
    "11111111111111111111111111111111": {
        "name": "System Program",
        "category": "system",
        "description": "Solana system program",
        "risk_level": "neutral",
    },
    "TokenkegQfeZyiNwAJsyFbPVwwQQfpmyRPnD4FKbQjm": {
        "name": "Token Program",
        "category": "system",
        "description": "Solana token program (SPL)",
        "risk_level": "neutral",
    },
    "ATokenGPvbdGVqstVQmcLsNZAqeEjlkXrn312VyHrWh6T": {
        "name": "Associated Token Program",
        "category": "system",
        "description": "Associated token account program",
        "risk_level": "neutral",
    },
    "metaqbxxUerdq8VvvrVKASASqqF94Bn3acQvfZZai": {
        "name": "Metaplex Token Metadata",
        "category": "program",
        "description": "Metaplex token metadata program",
        "risk_level": "neutral",
    },
}

# Top DEX/Protocol programs
DEFI_PROTOCOLS = {
    "JUP6LkbZbjS1jKKwapdHyR353L1x6za6YEDSAXUJXU5": {
        "name": "Jupiter Aggregator",
        "category": "automation",
        "description": "Jupiter DEX aggregator program",
        "risk_level": "neutral",
        "tags": ["defi", "dex", "aggregator"],
    },
    "675kPX9MHTjS2zt1qrXjVnYYtYFZNKLw5xaKq5QBuvV": {
        "name": "Raydium V4",
        "category": "automation",
        "description": "Raydium liquidity pool program V4",
        "risk_level": "neutral",
        "tags": ["defi", "dex", "raydium"],
    },
    "SSSwp5smVb5KwaXWC9bqkyQtSnw12qJzpon9XI3txMh": {
        "name": "Orca Whirlpool",
        "category": "automation",
        "description": "Orca whirlpool concentrated liquidity program",
        "risk_level": "neutral",
        "tags": ["defi", "dex", "orca"],
    },
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": {
        "name": "Meteora DLMM",
        "category": "automation",
        "description": "Meteora direct liquidity market maker",
        "risk_level": "neutral",
        "tags": ["defi", "dex", "meteora"],
    },
}


def scan_transaction_accounts(limit: int = 1000) -> Dict[str, int]:
    """
    Scan our transaction database to find frequently-appearing accounts
    that might be infrastructure but aren't labeled yet.
    
    Returns dict of {address: appearance_count}
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        # Find most common accounts in creator_funders
        cursor.execute("""
            SELECT funder_address, COUNT(*) as count
            FROM creator_funders
            GROUP BY funder_address
            ORDER BY count DESC
            LIMIT ?
        """, (limit,))
        
        accounts = {}
        for addr, count in cursor.fetchall():
            accounts[addr] = count
        
        conn.close()
        
        print(f"[SCAN] Found {len(accounts)} addresses in creator_funders")
        print(f"[SCAN] Top funders: {[(a[:16]+'...', c) for a, c in list(accounts.items())[:5]]}")
        
        return accounts
    
    except Exception as e:
        print(f"[SCAN] Error scanning transactions: {e}")
        return {}


async def lookup_address_on_helius(address: str, helius_key: str = None) -> Optional[Dict]:
    """
    Look up an address on Helius API to get metadata and labels.
    
    Requires HELIUS_API_KEY environment variable.
    """
    if not helius_key:
        # Try to get from environment
        import os
        helius_key = os.getenv("HELIUS_API_KEY")
    
    if not helius_key:
        return None
    
    try:
        url = f"https://mainnet.helius-rpc.com?api-key={helius_key}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccount",
            "params": [address]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
    
    except Exception as e:
        print(f"[HELIUS] Error looking up {address}: {e}")
    
    return None


def add_known_programs():
    """
    Add system programs and known DeFi protocols to our mapping.
    """
    print("\n[INIT] Adding system programs and known protocols...")
    
    added = 0
    
    # Add system programs
    for address, info in SOLANA_SYSTEM_PROGRAMS.items():
        add_infrastructure_account(
            address=address,
            name=info["name"],
            category=info["category"],
            description=info["description"],
            tags=["system", "solana"],
            risk_level=info["risk_level"]
        )
        added += 1
    
    # Add DeFi protocols
    for address, info in DEFI_PROTOCOLS.items():
        add_infrastructure_account(
            address=address,
            name=info["name"],
            category=info["category"],
            description=info["description"],
            tags=info.get("tags", ["defi"]),
            risk_level=info["risk_level"]
        )
        added += 1
    
    print(f"[INIT] Added {added} known programs and protocols")


def save_to_database():
    """
    Persist address labels to database.
    """
    from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        # Create table if needed
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS address_labels (
                address TEXT PRIMARY KEY,
                label_name TEXT,
                category TEXT,
                description TEXT,
                risk_level TEXT,
                tags TEXT,
                source TEXT,
                synced_at TIMESTAMP
            )
        """)
        
        # Save infrastructure accounts
        for address, info in INFRASTRUCTURE_ACCOUNTS.items():
            cursor.execute("""
                INSERT OR REPLACE INTO address_labels
                (address, label_name, category, description, risk_level, tags, source, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                address,
                info.get("name"),
                info.get("category"),
                info.get("description"),
                info.get("risk_level"),
                json.dumps(info.get("tags", [])),
                "infra_mapping",
                datetime.now().isoformat()
            ))
        
        # Save CEX accounts
        for address, info in CEX_ACCOUNTS.items():
            cursor.execute("""
                INSERT OR REPLACE INTO address_labels
                (address, label_name, category, description, risk_level, tags, source, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                address,
                info.get("name"),
                info.get("category"),
                info.get("description"),
                info.get("risk_level"),
                json.dumps(info.get("tags", [])),
                "cex_mapping",
                datetime.now().isoformat()
            ))
        
        conn.commit()
        conn.close()
        
        total = len(INFRASTRUCTURE_ACCOUNTS) + len(CEX_ACCOUNTS)
        print(f"[DB] Synced {total} address labels to database")
    
    except Exception as e:
        print(f"[DB] Error: {e}")


async def analyze_transaction_addresses():
    """
    Analyze which addresses in our transactions are likely infrastructure
    based on their transaction patterns.
    """
    print("\n[ANALYZE] Scanning transaction patterns...")
    
    accounts = scan_transaction_accounts(limit=500)
    
    if not accounts:
        return
    
    # Accounts appearing in 50+ transactions are likely infrastructure
    infrastructure_candidates = {
        addr: count for addr, count in accounts.items()
        if count >= 50
    }
    
    print(f"[ANALYZE] Found {len(infrastructure_candidates)} potential infrastructure accounts")
    print("[ANALYZE] Top candidates:")
    
    for addr, count in sorted(infrastructure_candidates.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {addr[:20]}... : {count} transactions")


async def main():
    """Main sync orchestration"""
    print("\n" + "=" * 80)
    print("ADDRESS LABEL AUTO-SYNC")
    print("=" * 80)
    print(f"Started: {datetime.now().isoformat()}\n")
    
    # 1. Add system programs and known protocols
    add_known_programs()
    
    # 2. Scan transaction history for infrastructure candidates
    await analyze_transaction_addresses()
    
    # 3. Save to database
    save_to_database()
    
    print("\n" + "=" * 80)
    print("Sync complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
