#!/usr/bin/env python3
"""
Extract domain names from transaction data and metadata.

Unlike SNS domain resolution (which checks if an address OWNS a domain),
this module extracts domains that are MENTIONED in transactions:
- Transaction descriptions/memos
- Instruction data
- Account metadata
- Program-specific domain references

These extracted domains are saved as address tags for the accounts involved.
"""

import re
import sqlite3
from typing import Set, Optional, Tuple
from address_tags import add_tag
from domain_mapping import register_domain, link_domain_to_address

DB_PATH = "pumpswap_tokens.db"

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
            domain = re.sub(r'^domain[:\s]*', '', domain)

            # Validate it's a proper domain
            if domain.endswith('.sol') and len(domain) > 4:
                domains.add(domain)

    return domains


def extract_from_transaction_description(description: str, address: str) -> int:
    """
    Extract domains from a transaction description and tag the address.
    Also creates tags for the domain names themselves for future reference.
    Returns count of domains extracted.
    """
    if not description:
        return 0

    domains = extract_domains_from_text(description)

    if not domains:
        return 0

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

    return len(domains)


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
