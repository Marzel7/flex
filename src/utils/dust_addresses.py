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
    "HLSHeeM2Q141C4PEYMeeKtWeP4uVQeYsk4fmVCMxhi2F",  # Dust/plumbing account
    "3hFaTuKqykxPJUAek94xb5Bq2f9Sa6CMFMyGYJ4pXd1u",  # Dust/plumbing account
    "DBHJYkbC2tJ1uwdKBW1UzW6zfLXk7baWa21bt4hbkcny",  # Dust/plumbing account
    "8FhkMDysBTAQ6cY9nsD8anyiD9s84ortwYAcEpyE9635",  # Dust/plumbing account
    "CVrarjyezhe1748dG43ao8M93RET49UbpEhX3Vqbp9AT",  # Dust/plumbing account
    "4jwhAfFif9APxoNmEvQucX8xrrwrsBCGcvUYNTM2yPec",  # Dust/plumbing account
}


# WSOL wrapping accounts that act as intermediaries (should be filtered)
# These are often created per-transaction for SOL-to-WSOL conversions
# The actual funder is typically one step before these accounts
WSOL_INTERMEDIARY_PATTERNS = {
    # Common WSOL mint and wrapper accounts
    "So11111111111111111111111111111111111111112",  # Wrapped SOL token mint itself
}


def is_wsol_intermediary(address: str) -> bool:
    """
    Check if an address is a WSOL intermediary account that should be filtered.
    These are temporary accounts used for SOL wrapping/unwrapping.
    Returns True if this is likely a WSOL wrapper account.
    """
    # Check explicit list
    if address in WSOL_INTERMEDIARY_PATTERNS:
        return True

    # Note: In practice, most WSOL ATAs are unique per user, so we'd need to:
    # 1. Check if the address is a token account (has specific structure)
    # 2. Verify its mint is the WSOL token mint
    # This requires token account introspection which is outside this module's scope.
    # For now, we rely on the explicit DUST_ADDRESSES set and transaction-level filtering.

    return False


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
