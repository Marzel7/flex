#!/usr/bin/env python3
"""
Extract domain names from transaction data and metadata.

Two extraction methods:
1. Explicit domain mentions: .sol text found in transaction descriptions
2. Address domain resolution: Addresses mentioned in descriptions that own SNS domains

These extracted domains are saved as address tags for the creator.
"""

import re
import sqlite3
import asyncio
from typing import Set, Optional, Tuple, List
from src.utils.address_tags import add_tag
from src.utils.domain_mapping import register_domain, link_domain_to_address

# Try to import label resolver; skip if not available
try:
    from address_label_resolver import is_infrastructure
    HAS_LABEL_RESOLVER = True
except ImportError:
    HAS_LABEL_RESOLVER = False
    def is_infrastructure(addr: str) -> bool:
        return False

DB_PATH = "flex_complete_database.db"

# Regex pattern for Solana domain names (.sol TLD)
SOL_DOMAIN_PATTERN = re.compile(r'[\w\-]+\.sol\b', re.IGNORECASE)

# Common domain name formats we might see
DOMAIN_PATTERNS = [
    r'[\w\-]+\.sol\b',                    # name.sol
    r'https?://[\w\-]+\.sol\b',           # http://name.sol
    r'@[\w\-]+\.sol\b',                   # @name.sol (mention)
    r'\([\w\-]+\.sol\)',                  # (name.sol)
    r'domain[:\s]+[\w\-]+\.sol\b',        # domain: name.sol
]

# Solana address regex (base58 format: 32-44 characters)
# Base58 alphabet: 123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
# (excludes 0, O, I, l to avoid confusion)
SOLANA_ADDRESS_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')


def extract_domains_from_text(text: Optional[str]) -> Set[str]:
    """
    Extract all .sol domain names from a text string.
    Returns set of unique domains found.
    """
    if not text:
        return set()

    domains = set()

    # Try all patterns
    for pattern in DOMAIN_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # Clean up the match (remove protocol, mentions, etc.)
            domain = match.lower()
            domain = re.sub(r'^https?://', '', domain)
            domain = re.sub(r'^@', '', domain)
            domain = re.sub(r'[(\)\[\]]', '', domain)
            # Only remove "domain:" or "domain " prefix if explicitly separated by : or space
            domain = re.sub(r'^domain:\s*', '', domain)
            domain = re.sub(r'^domain\s+', '', domain)

            # Validate it's a proper domain
            if domain.endswith('.sol') and len(domain) > 4:
                domains.add(domain)

    return domains


def extract_addresses_from_text(text: Optional[str]) -> Set[str]:
    """
    Extract Solana addresses from a text string.
    Returns set of unique addresses found (excluding infrastructure accounts).
    """
    if not text:
        return set()

    addresses = set()
    matches = SOLANA_ADDRESS_PATTERN.findall(text)

    for match in matches:
        # Basic validation: Solana addresses are 32-44 base58 characters
        # Filter out common false positives (very short strings)
        if len(match) >= 32:
            # Skip infrastructure accounts (system programs, DEX routers, etc.)
            # These don't own SNS domains and are noise in domain extraction
            if not is_infrastructure(match):
                addresses.add(match)

    return addresses


async def resolve_domains_for_addresses_async(addresses: Set[str], creator_address: str, domain_resolver=None) -> Tuple[int, Set[str]]:
    """
    Resolve SNS domains for addresses found in transaction descriptions.

    Args:
        addresses: Set of Solana addresses to resolve
        creator_address: Creator address to tag with discovered domains
        domain_resolver: Optional DomainResolver instance. If None, will try to import from extractor

    Returns (count_of_domains_found, set_of_domains).
    """
    if not addresses:
        return 0, set()

    if not domain_resolver:
        try:
            # Try to import from the global extractor if available
            from src.extractors.realtime_creator_funding_extractor import _extractor
            if _extractor and _extractor.domain_resolver:
                domain_resolver = _extractor.domain_resolver
            else:
                return 0, set()
        except Exception:
            return 0, set()

    if not domain_resolver:
        return 0, set()

    try:
        # Resolve domains for all addresses
        results = await domain_resolver.resolve_primary_domains(list(addresses))

        domains_found = set()

        # Tag creator with each domain found and register in mapping
        for address, domain in results.items():
            if domain:
                domains_found.add(domain)
                try:
                    # Tag the creator with this domain reference
                    add_tag(creator_address, 'domain_referenced', domain, source='address_resolution_in_tx')

                    # Register domain in persistent mapping
                    register_domain(domain, domain_type='mentioned',
                                  metadata={'source': 'address_in_transaction', 'owner': address},
                                  source='address_resolution')

                    # Link the owner address to the domain
                    link_domain_to_address(domain, address)

                except Exception as e:
                    print(f"[DOMAIN_EXTRACTION] ⚠ Error tagging domain {domain}: {e}", flush=True)

        return len(domains_found), domains_found

    except Exception as e:
        print(f"[DOMAIN_EXTRACTION] ⚠ Error resolving domains: {e}", flush=True)
        return 0, set()


def extract_from_transaction_description(description: str, address: str) -> int:
    """
    Extract domains from a transaction description and tag the address.
    Two methods:
    1. Explicit domain mentions (.sol text in description)
    2. Address domain resolution (addresses in description that own SNS domains)
    Returns count of domains extracted.
    """
    if not description:
        return 0

    total_domains = 0

    # METHOD 1: Extract explicit domain mentions
    domains = extract_domains_from_text(description)

    if domains:
        # Tag the address with each extracted domain
        for domain in domains:
            try:
                # 1. Tag the SOURCE address (creator/funder) with the domain reference
                add_tag(address, 'domain_referenced', domain, source='tx_extraction')

                # 2. Register domain in persistent mapping
                register_domain(domain, domain_type='mentioned',
                              metadata={'source': 'transaction', 'first_address': address},
                              source='tx_extraction')

                # 3. Link this address to the domain in the mapping
                link_domain_to_address(domain, address)

            except Exception as e:
                pass  # Non-critical

        total_domains += len(domains)

    # METHOD 2: Extract addresses and resolve their domains
    addresses_in_tx = extract_addresses_from_text(description)

    if addresses_in_tx:
        # Need to run async domain resolution
        # This is handled by the caller via extract_from_helius_transaction_with_address_resolution
        pass

    return total_domains


def extract_from_helius_transaction(tx_data: dict, address: str) -> int:
    """
    Extract domains from a Helius-formatted transaction.
    Looks in: description, instructions, related programs, etc.
    Returns count of domains extracted.
    """
    if not isinstance(tx_data, dict):
        return 0

    total_extracted = 0
    description = tx_data.get('description', '')

    if description:
        total_extracted += extract_from_transaction_description(description, address)

    # Also check in instruction descriptions if available
    if 'instructions' in tx_data:
        for instruction in tx_data.get('instructions', []):
            if isinstance(instruction, dict):
                instr_desc = instruction.get('description', '')
                if instr_desc:
                    total_extracted += extract_from_transaction_description(instr_desc, address)

    return total_extracted


async def extract_from_helius_transaction_async(tx_data: dict, address: str, domain_resolver=None) -> Tuple[int, Set[str]]:
    """
    Async version: Extract domains from a Helius transaction, including address resolution.
    Handles both explicit domain mentions and resolving SNS domains for addresses in descriptions.

    Args:
        tx_data: Helius transaction data
        address: Creator address to tag with discovered domains
        domain_resolver: Optional DomainResolver instance for SNS lookups

    Returns (total_count, set_of_domains_found).
    """
    if not isinstance(tx_data, dict):
        return 0, set()

    total_extracted = 0
    all_domains = set()
    description = tx_data.get('description', '')

    if description:
        # Extract explicit domains
        explicit_domains = extract_domains_from_text(description)
        if explicit_domains:
            all_domains.update(explicit_domains)
            for domain in explicit_domains:
                try:
                    add_tag(address, 'domain_referenced', domain, source='tx_extraction')
                    register_domain(domain, domain_type='mentioned',
                                  metadata={'source': 'transaction', 'first_address': address},
                                  source='tx_extraction')
                    link_domain_to_address(domain, address)
                except Exception as e:
                    pass
            total_extracted += len(explicit_domains)

        # Extract and resolve addresses mentioned in description
        addresses_in_tx = extract_addresses_from_text(description)
        if addresses_in_tx:
            count, domains = await resolve_domains_for_addresses_async(addresses_in_tx, address, domain_resolver)
            total_extracted += count
            all_domains.update(domains)

    # Also check in instruction descriptions
    if 'instructions' in tx_data:
        for instruction in tx_data.get('instructions', []):
            if isinstance(instruction, dict):
                instr_desc = instruction.get('description', '')
                if instr_desc:
                    # Extract explicit domains from instruction
                    explicit_domains = extract_domains_from_text(instr_desc)
                    if explicit_domains:
                        all_domains.update(explicit_domains)
                        for domain in explicit_domains:
                            try:
                                add_tag(address, 'domain_referenced', domain, source='tx_extraction')
                                register_domain(domain, domain_type='mentioned',
                                              metadata={'source': 'instruction', 'first_address': address},
                                              source='tx_extraction')
                                link_domain_to_address(domain, address)
                            except Exception as e:
                                pass
                        total_extracted += len(explicit_domains)

                    # Extract and resolve addresses in instruction description
                    addresses_in_instr = extract_addresses_from_text(instr_desc)
                    if addresses_in_instr:
                        count, domains = await resolve_domains_for_addresses_async(addresses_in_instr, address, domain_resolver)
                        total_extracted += count
                        all_domains.update(domains)

    return total_extracted, all_domains


def extract_from_creator_transactions(creator_address: str, transactions: list) -> Tuple[int, Set[str]]:
    """
    Extract domains from all transactions for a creator.
    Returns (total_count, all_domains_found).
    """
    total_extracted = 0
    all_domains = set()

    for tx in transactions:
        if isinstance(tx, dict):
            count = extract_from_helius_transaction(tx, creator_address)
            total_extracted += count

            # Also extract domain names (not just tag them)
            description = tx.get('description', '')
            domains = extract_domains_from_text(description)
            all_domains.update(domains)

    return total_extracted, all_domains


def get_domains_for_address(address: str) -> Set[str]:
    """Get all domains referenced in transactions for an address."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT tag_value
            FROM address_tags
            WHERE address = ? AND tag_type = 'domain_referenced'
        """, (address,))

        domains = {row[0] for row in cursor.fetchall()}
        conn.close()
        return domains
    except Exception as e:
        return set()


def get_all_referenced_domains() -> dict:
    """Get all domains referenced in any transaction, grouped by address."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT address, GROUP_CONCAT(DISTINCT tag_value, ', ')
            FROM address_tags
            WHERE tag_type = 'domain_referenced'
            GROUP BY address
        """)

        result = {row[0]: row[1].split(', ') for row in cursor.fetchall()}
        conn.close()
        return result
    except Exception as e:
        return {}


if __name__ == "__main__":
    print("Domain Extraction Module")
    print("=" * 80)

    # Test domain extraction
    test_texts = [
        "Sent 5 SOL to vitalik.sol",
        "Transaction with memo: @alice.sol -> @bob.sol",
        "Payment to (dex.sol) for swap",
        "Domain: trading.sol mentioned in metadata",
        "Visit https://exchange.sol for more info",
        "No domains here!",
    ]

    print("\nTesting domain extraction:")
    for text in test_texts:
        domains = extract_domains_from_text(text)
        status = f"✓ Found: {', '.join(domains)}" if domains else "✗ None"
        print(f"  '{text[:40]}...' → {status}")

    print("\n" + "=" * 80)
    print("Domain extraction ready for transaction processing!")
