#!/usr/bin/env python3
"""
Solana domain name mapping registry.

Similar to INFRASTRUCTURE_ACCOUNTS and CEX_ACCOUNTS, this maintains
a persistent mapping of discovered domain names to their metadata.

Domains are discovered through:
1. SNS resolution (domains that addresses own)
2. Transaction extraction (domains mentioned in tx data)
"""

import sqlite3
import json
from typing import Dict, Optional, Set

DB_PATH = "flex_complete_database.db"

# In-memory cache of domains
DOMAIN_REGISTRY: Dict[str, Dict] = {}


def init_domain_registry():
    """Load domain registry from database into memory"""
    global DOMAIN_REGISTRY

    # X78.0 -- found live during the X78.0 soak: called on EVERY
    # extract_funding_for_new_token call (via init_session(), unconditionally,
    # before DomainResolver's own construction even finishes) -- the
    # earliest possible write in the whole extraction pipeline. conn.close()
    # was only reached on success; any exception (including from the CREATE
    # TABLE, which was NOT guarded by a sqlite_master check first) left the
    # connection, and its write lease, open for the rest of that thread's
    # life.
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        if not cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_registry'"
        ).fetchone():
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS domain_registry (
                    domain_name TEXT PRIMARY KEY,
                    domain_type TEXT,
                    first_seen_at INTEGER,
                    metadata TEXT,
                    sources TEXT,
                    confidence_score REAL
                )
            """)
            conn.commit()

        # Load all domains into memory
        cursor.execute("SELECT domain_name, domain_type, metadata FROM domain_registry")
        for domain_name, domain_type, metadata_json in cursor.fetchall():
            try:
                metadata = json.loads(metadata_json) if metadata_json else {}
            except:
                metadata = {}

            DOMAIN_REGISTRY[domain_name] = {
                'name': domain_name,
                'type': domain_type,
                'metadata': metadata
            }
    except Exception as e:
        print(f"[DOMAIN_MAPPING] Warning: Could not init registry: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def register_domain(domain_name: str, domain_type: str = 'mentioned',
                   metadata: Optional[Dict] = None, source: str = 'auto') -> bool:
    """
    Register a domain in the persistent mapping.

    domain_type can be:
    - 'owned': Address owns this domain (SNS resolution)
    - 'mentioned': Domain mentioned in transactions
    - 'handler': Domain handler/marketplace
    - 'verified': Verified/trusted domain

    Returns True if registered successfully.
    """
    if not domain_name or not domain_name.endswith('.sol'):
        return False

    try:
        # Update in-memory registry
        if domain_name not in DOMAIN_REGISTRY:
            DOMAIN_REGISTRY[domain_name] = {
                'name': domain_name,
                'type': domain_type,
                'metadata': metadata or {}
            }
        else:
            # Update type if more specific
            if domain_type == 'owned' and DOMAIN_REGISTRY[domain_name]['type'] != 'owned':
                DOMAIN_REGISTRY[domain_name]['type'] = domain_type
            if metadata:
                DOMAIN_REGISTRY[domain_name]['metadata'].update(metadata)

        # Save to database
        # X78.0 -- conn.close() was only reached on success; called from
        # DomainResolver._save_address_tag, itself called from inside
        # creator_funding_worker's extraction hot path -- an exception here
        # left the connection, and its write lease, open for the rest of
        # that thread's life.
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()

            metadata_json = json.dumps(metadata or {})
            cursor.execute("""
                INSERT OR REPLACE INTO domain_registry
                (domain_name, domain_type, first_seen_at, metadata, sources, confidence_score)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (domain_name, domain_type, int(__import__('time').time()),
                  metadata_json, source, 1.0))

            conn.commit()
            return True
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    except Exception as e:
        return False


def get_domain_info(domain_name: str) -> Optional[Dict]:
    """Get information about a registered domain"""
    return DOMAIN_REGISTRY.get(domain_name)


def is_registered_domain(domain_name: str) -> bool:
    """Check if a domain is registered in the mapping"""
    return domain_name in DOMAIN_REGISTRY


def get_all_domains() -> Dict[str, Dict]:
    """Get all registered domains"""
    return DOMAIN_REGISTRY.copy()


def get_domains_by_type(domain_type: str) -> Dict[str, Dict]:
    """Get all domains of a specific type"""
    return {
        name: info for name, info in DOMAIN_REGISTRY.items()
        if info.get('type') == domain_type
    }


def link_domain_to_address(domain_name: str, address: str) -> bool:
    """
    Link a domain to an address (owner or user).
    Stores in the domain's metadata.
    """
    if domain_name not in DOMAIN_REGISTRY:
        return False

    try:
        if 'associated_addresses' not in DOMAIN_REGISTRY[domain_name]['metadata']:
            DOMAIN_REGISTRY[domain_name]['metadata']['associated_addresses'] = []

        if address not in DOMAIN_REGISTRY[domain_name]['metadata']['associated_addresses']:
            DOMAIN_REGISTRY[domain_name]['metadata']['associated_addresses'].append(address)

        # Save back to database
        # X78.0 -- same fix as register_domain above: conn.close() was only
        # reached on success.
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()
            metadata_json = json.dumps(DOMAIN_REGISTRY[domain_name]['metadata'])

            cursor.execute("""
                UPDATE domain_registry
                SET metadata = ?
                WHERE domain_name = ?
            """, (metadata_json, domain_name))

            conn.commit()
            return True
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception as e:
        return False


def get_domain_owners(domain_name: str) -> Set[str]:
    """Get all addresses associated with a domain"""
    if domain_name not in DOMAIN_REGISTRY:
        return set()

    addresses = DOMAIN_REGISTRY[domain_name]['metadata'].get('associated_addresses', [])
    return set(addresses)


if __name__ == "__main__":
    print("Domain Registry")
    print("=" * 80)

    # Initialize registry
    init_domain_registry()

    # Show stats
    print(f"\nTotal registered domains: {len(DOMAIN_REGISTRY)}\n")

    # Show by type
    for domain_type in ['owned', 'mentioned', 'verified', 'handler']:
        domains = get_domains_by_type(domain_type)
        if domains:
            print(f"{domain_type.upper()} domains ({len(domains)}):")
            for domain_name, info in list(domains.items())[:5]:
                owners = get_domain_owners(domain_name)
                owner_str = f" → {len(owners)} owner(s)" if owners else ""
                print(f"  • {domain_name}{owner_str}")
            if len(domains) > 5:
                print(f"  ... and {len(domains) - 5} more")
            print()

    print("=" * 80)
