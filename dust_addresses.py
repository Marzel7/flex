#!/usr/bin/env python3
"""
Centralized dust and plumbing address registry.

These addresses are involved in account creation, token wrapping, and protocol operations
that don't represent actual funding. They should be filtered out from funding analysis.
"""

# Unified dust addresses used across all funding extraction modules
DUST_ADDRESSES = {
    "3XxhMgcsvzCcDi6UKvWoSqUxt8JuGN5CR73tRkkDNDs5",  # Known spam dust account
    "3jYf1yHVQEkHNvacdz4wFRXcvFirF6nFjwLq9m8ML1ME",  # WSOL token account (wrap/unwrap plumbing)
    "GeuiPGMCpwDFQBCUqZ7h6NGyT6cpR5fULz9mnXeN3yRJ",  # Creator-specific WSOL ATA (zero balance change)
    "HT629WJGphX8XEbpcD62SMcbNSzEHDEkCVD5tzjwkYbb",  # Dust/plumbing account
}


def is_dust_address(address: str) -> bool:
    """Check if an address is a known dust/plumbing account"""
    return address in DUST_ADDRESSES


def add_dust_address(address: str):
    """Add a new dust address to the registry"""
    DUST_ADDRESSES.add(address)


if __name__ == "__main__":
    print("Dust Addresses Registry")
    print("=" * 80)
    print(f"Total addresses: {len(DUST_ADDRESSES)}")
    print("\nAddresses:")
    for addr in sorted(DUST_ADDRESSES):
        print(f"  {addr}")
