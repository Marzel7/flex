#!/usr/bin/env python3
"""
Debug pool extraction failures by comparing working vs failing pools.

Dumps account structure, data layout, and decoder branch for side-by-side analysis.
"""

import asyncio
import aiohttp
import sys
import os
import base64
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv()

# Test pools
FAILING_POOL = "A1HFqQZF3t16RQ8ENV9NLkVXL6E5Fu31sWk5s33jH5wn"  # MOG pool - extraction fails
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAYDIUM_AMM = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"
ORCA_WHIRLPOOL = "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco"

# Colors
class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def print_section(title):
    print(f"\n{C.HEADER}{C.BOLD}{'='*80}{C.RESET}")
    print(f"{C.HEADER}{C.BOLD}{title}{C.RESET}")
    print(f"{C.HEADER}{C.BOLD}{'='*80}{C.RESET}\n")


def print_field(label, value, highlight=False):
    color = C.YELLOW if highlight else C.BLUE
    print(f"{color}{label}: {value}{C.RESET}")


async def fetch_account(pool_address: str, rpc_url: str) -> dict:
    """Fetch account info from RPC."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [pool_address, {"encoding": "base64"}]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            result = await resp.json()
            if "result" in result and result["result"]:
                return result["result"]["value"]
    return None


def determine_decoder_branch(owner: str) -> str:
    """Determine which decoder would be used."""
    if owner == RAYDIUM_AMM:
        return "RAYDIUM_AMM (Raydium AMM v4)"
    elif owner == "CPMMoo8L3F4rn9aUYn2QRiPK5VrKMjstm69edQaMQAC":
        return "RAYDIUM_CPMM (Raydium CPMM)"
    elif owner == ORCA_WHIRLPOOL:
        return "ORCA_WHIRLPOOL"
    elif owner == PUMPSWAP_PROGRAM:
        return "PUMPSWAP (uses Raydium AMM layout)"
    elif owner == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
        return "PUMPFUN_V1"
    else:
        return f"UNKNOWN ({owner[:20]}...)"


def extract_pubkeys_at_offset(data: bytes, offset: int) -> tuple:
    """Extract two 32-byte pubkeys at given offset."""
    try:
        if offset + 64 > len(data):
            return None, None
        pk1 = data[offset:offset+32]
        pk2 = data[offset+32:offset+64]
        return pk1, pk2
    except:
        return None, None


def hex_to_base58(data: bytes) -> str:
    """Convert 32-byte data to base58 address."""
    try:
        import base58
        return base58.b58encode(data).decode()
    except:
        return data.hex()[:40] + "..."


async def analyze_pool(pool_address: str, label: str, rpc_url: str):
    """Analyze a single pool."""
    print_section(f"Analyzing: {label}")
    print_field("Pool Address", pool_address)

    # Fetch account
    account = await fetch_account(pool_address, rpc_url)
    if not account:
        print(f"{C.RED}❌ Could not fetch account{C.RESET}")
        return None

    owner = account.get("owner")
    print_field("Owner", owner)

    # Decode data
    data_field = account.get("data", [])
    if isinstance(data_field, list) and len(data_field) > 0:
        data_b64 = data_field[0]
        data = base64.b64decode(data_b64)
    else:
        data = base64.b64decode(data_field) if isinstance(data_field, str) else b""

    data_len = len(data)
    print_field("Data Length", f"{data_len} bytes")

    # Determine decoder
    decoder = determine_decoder_branch(owner)
    print_field("Decoder Branch", decoder, highlight=True)

    # Show first 128 bytes in hex
    print(f"\n{C.BLUE}First 128 bytes (hex):{C.RESET}")
    hex_data = data[:128].hex()
    for i in range(0, len(hex_data), 64):
        offset_bytes = i // 2
        print(f"  +{offset_bytes:03d}: {hex_data[i:i+64]}")

    # Common offsets to check
    print(f"\n{C.BLUE}Vault extraction attempts at common offsets:{C.RESET}")

    offsets_to_check = [
        (72, "Raydium AMM v4 standard (base vault @ 72)"),
        (104, "Raydium AMM v4 standard (quote vault @ 104)"),
        (232, "PumpSwap/Raydium AMM layout (base vault @ 232)"),
        (264, "PumpSwap/Raydium AMM layout (quote vault @ 264)"),
    ]

    results = {}
    for offset, description in offsets_to_check:
        pk1, pk2 = extract_pubkeys_at_offset(data, offset)
        if pk1 and pk2:
            addr1 = hex_to_base58(pk1)
            addr2 = hex_to_base58(pk2)
            results[offset] = (addr1, addr2)
            print(f"  @{offset:3d}: {addr1} / {addr2}")
            print(f"          ({description})")
        else:
            print(f"  @{offset:3d}: ❌ Out of bounds or invalid")

    # Try to find vault pubkeys by pattern (32 bytes that look like base58 addresses)
    print(f"\n{C.BLUE}Scanning for potential vault addresses (32-byte aligned)::{C.RESET}")
    potential_vaults = []
    for offset in range(0, len(data) - 32, 32):
        pk = data[offset:offset+32]
        # Check if it might be a valid pubkey (simple heuristic: no obvious patterns)
        if pk != b'\x00' * 32 and pk.count(0) < 20:  # Not all zeros, not too many nulls
            try:
                addr = hex_to_base58(pk)
                if addr not in ["Invalid" * 5]:  # Avoid obviously invalid
                    potential_vaults.append((offset, addr))
            except:
                pass

    if potential_vaults:
        for offset, addr in potential_vaults[:5]:  # Show first 5
            print(f"  @{offset:3d}: {addr}")

    return {
        "address": pool_address,
        "label": label,
        "owner": owner,
        "decoder": decoder,
        "data_len": data_len,
        "data": data,
        "results": results,
    }


async def main():
    """Run analysis."""
    rpc_url = os.getenv("HELIUS_RPC_URL") or os.getenv("RPC_HTTP") or "https://api.mainnet-beta.solana.com"

    print(f"\n{C.BOLD}{C.HEADER}Pool Extraction Debug Analysis{C.RESET}")
    print(f"{C.YELLOW}RPC: {rpc_url[:50]}...{C.RESET}\n")

    # Analyze failing pool
    failing = await analyze_pool(FAILING_POOL, "FAILING POOL (MOG)", rpc_url)

    if not failing:
        print(f"{C.RED}❌ Could not analyze failing pool{C.RESET}")
        return

    # Find a working pool from database to compare
    print_section("Finding Working Pool for Comparison")

    import sqlite3
    db_path = os.getenv("DB_PATH", "database/flex_complete_database.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get a pool that was successfully registered and has different structure
    cursor.execute("""
        SELECT DISTINCT pool_address FROM token_pool_accounts
        WHERE pool_address IS NOT NULL
        AND vault_validation_status = 'validated'
        AND pool_address != ?
        LIMIT 5
    """, (FAILING_POOL,))

    working_pools = [row[0] for row in cursor.fetchall()]
    conn.close()

    if working_pools:
        print(f"Found {len(working_pools)} working pools in database")
        working_pool = working_pools[0]
        print(f"Analyzing first working pool: {working_pool[:20]}...")

        working = await analyze_pool(working_pool, "WORKING POOL", rpc_url)
    else:
        print(f"{C.YELLOW}⚠️  No other validated pools found in database{C.RESET}")
        print(f"Fetching a random Raydium pool as reference...")
        # Try a known Raydium pool
        working_pool = "58oQChx4yWmvKJS4aGZHaH5GcqXqToGihrepfutEnaHt"  # Example Raydium pool
        working = await analyze_pool(working_pool, "WORKING POOL (Raydium Reference)", rpc_url)

    # Comparison
    if working:
        print_section("Side-by-Side Comparison")

        print(f"{C.BOLD}FAILING POOL{C.RESET}")
        print(f"  Owner:        {failing['owner']}")
        print(f"  Decoder:      {failing['decoder']}")
        print(f"  Data Length:  {failing['data_len']} bytes")
        if failing['results']:
            for offset, (pk1, pk2) in failing['results'].items():
                print(f"  @Offset {offset}: {pk1} / {pk2}")

        print(f"\n{C.BOLD}WORKING POOL{C.RESET}")
        print(f"  Owner:        {working['owner']}")
        print(f"  Decoder:      {working['decoder']}")
        print(f"  Data Length:  {working['data_len']} bytes")
        if working['results']:
            for offset, (pk1, pk2) in working['results'].items():
                print(f"  @Offset {offset}: {pk1} / {pk2}")

        # Analysis
        print_section("Analysis")

        if failing['owner'] != working['owner']:
            print(f"{C.RED}⚠️  OWNER MISMATCH{C.RESET}")
            print(f"    Failing: {failing['owner']}")
            print(f"    Working: {working['owner']}")
            print(f"    → Decoder branches will be different!")

        if failing['data_len'] != working['data_len']:
            print(f"{C.YELLOW}⚠️  DATA LENGTH DIFFERENT{C.RESET}")
            print(f"    Failing: {failing['data_len']} bytes")
            print(f"    Working: {working['data_len']} bytes")
            print(f"    → Structure layout may be different")

        if failing['decoder'] == working['decoder']:
            print(f"{C.GREEN}✓ Same decoder branch{C.RESET}")
            print(f"  But extraction still fails → offsets may be wrong for this program")
        else:
            print(f"{C.RED}✗ Different decoder branches{C.RESET}")
            print(f"  → {failing['decoder']} vs {working['decoder']}")

        # Hypothesis
        print_section("Hypothesis")
        if failing['owner'] == PUMPSWAP_PROGRAM:
            print(f"{C.YELLOW}Failing pool is owned by PumpSwap program{C.RESET}")
            print(f"Data length: {failing['data_len']} bytes")
            print(f"\nPossible issues:")
            print(f"1. Offsets 232/264 may not be correct for this pool state structure")
            print(f"2. PumpSwap may use different account layout than Raydium AMM")
            print(f"3. Pool state may not be in the expected format")
            print(f"\nNext step: Manually decode the data at various offsets")
            print(f"and verify which addresses correspond to token vaults.")


if __name__ == "__main__":
    asyncio.run(main())
