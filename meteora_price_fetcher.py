#!/usr/bin/env python3
"""
Comprehensive Meteora Token Price Fetcher
Supports fetching prices from both DAMM V2 and DLMM pools using on-chain data.

Features:
- Fetch prices directly from on-chain vault balances (DAMM V2)
- Support for DLMM pools with bin-based pricing formula
- Batch processing of multiple pools
- DexScreener API comparison
- Detailed output with vault information
"""

import requests
import base64
import struct
import base58
import sys
import json
from typing import Optional, Dict, List, Tuple

RPC_URL = "https://mainnet.helius-rpc.com/?api-key=0ae07551-32df-4d9d-af2a-1925fb7f561f"

# Solana program IDs
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
METEORA_DAMM_V2 = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"
METEORA_DLMM = "Lbry5nCI5mNyvrYBxCJryAu2hVggA74g2MPhtVomjcc"


def rpc_call(method: str, params: list) -> Optional[Dict]:
    """Make an RPC call to Solana"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    try:
        response = requests.post(RPC_URL, json=payload, timeout=15)
        return response.json()
    except Exception as e:
        print(f"RPC Error: {e}", file=sys.stderr)
        return None


def get_pool_creation_tx(pool_address: str) -> str:
    """Fetch the creation transaction signature for a pool"""
    result = rpc_call("getSignaturesForAddress", [pool_address, {"limit": 10}])

    if not result or not result.get("result"):
        raise Exception("No transactions found for pool")

    sigs = result["result"]
    if sigs:
        return sigs[-1]["signature"]

    raise Exception("No signatures found")


def is_token_account(account_addr: str) -> bool:
    """Check if an account is an SPL token account"""
    result = rpc_call("getAccountInfo", [account_addr, {"encoding": "base64"}])

    if not result or not result.get("result"):
        return False

    acc_info = result["result"]["value"]
    if acc_info is None:
        return False

    owner = acc_info.get("owner", "")
    return owner == SPL_TOKEN_PROGRAM


def get_vaults_from_tx(tx_sig: str, pool_address: str) -> List[str]:
    """Extract vault token accounts from pool creation transaction"""
    result = rpc_call("getTransaction", [tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])

    if not result or not result.get("result"):
        raise Exception("Transaction not found or parse failed")

    tx_data = result["result"]
    accounts = tx_data["transaction"]["message"]["accountKeys"]
    account_keys = [acc["pubkey"] for acc in accounts]

    # Filter to find vault candidates (only SPL token accounts)
    vaults = []
    for acc in account_keys:
        if acc != pool_address and len(acc) == 44:
            if is_token_account(acc):
                vaults.append(acc)

    # Also check inner instructions
    meta = tx_data.get("meta", {})
    inner_instructions = meta.get("innerInstructions", [])

    for inner in inner_instructions:
        for instr in inner["instructions"]:
            for idx in instr.get("accounts", []):
                if isinstance(idx, int) and idx < len(account_keys):
                    acc = account_keys[idx]
                    if acc != pool_address and acc not in vaults and len(acc) == 44:
                        if is_token_account(acc):
                            vaults.append(acc)

    # Remove duplicates while preserving order
    vaults = list(dict.fromkeys(vaults))

    return vaults


def get_mint_decimals(mint_address: str) -> int:
    """Fetch decimals from mint account"""
    result = rpc_call("getAccountInfo", [mint_address, {"encoding": "base64"}])

    if not result or not result.get("result") or result["result"]["value"] is None:
        return 0

    acc_info = result["result"]["value"]
    data = acc_info['data'][0]
    account_data = base64.b64decode(data)

    # Mint account structure - decimals at offset 44
    if len(account_data) < 45:
        return 0

    decimals = struct.unpack("<B", account_data[44:45])[0]
    return decimals


def get_token_info(token_account: str) -> Optional[Dict]:
    """Get token account info including mint and balance"""
    result = rpc_call("getAccountInfo", [token_account, {"encoding": "base64"}])

    if not result or not result.get("result") or result["result"]["value"] is None:
        return None

    acc_info = result["result"]["value"]
    data = acc_info['data'][0]
    account_data = base64.b64decode(data)

    if len(account_data) < 80:
        return None

    # Token account structure
    mint = account_data[0:32]
    amount = struct.unpack("<Q", account_data[64:72])[0]
    token_account_decimals = struct.unpack("<B", account_data[72:73])[0]

    mint_addr = base58.b58encode(mint).decode('ascii')

    # Get actual decimals from mint if token account has 0
    if token_account_decimals == 0:
        decimals = get_mint_decimals(mint_addr)
    else:
        decimals = token_account_decimals

    return {
        "mint": mint_addr,
        "amount": amount,
        "decimals": decimals,
        "human": amount / (10 ** decimals) if decimals > 0 else amount
    }


def get_damm_v2_price(pool_address: str, verbose: bool = False) -> Optional[float]:
    """Get DAMM V2 pool price from vault balances"""
    try:
        # Get pool account info
        result = rpc_call("getAccountInfo", [pool_address, {"encoding": "base64"}])
        if not result or not result.get("result") or result["result"]["value"] is None:
            return None

        # Get creation transaction
        tx_sig = get_pool_creation_tx(pool_address)

        # Extract vaults
        vaults = get_vaults_from_tx(tx_sig, pool_address)

        if verbose:
            print(f"  Found {len(vaults)} vaults")

        # Get token accounts
        token_vaults = []
        for vault in vaults:
            info = get_token_info(vault)
            if info:
                token_vaults.append((vault, info))
                if verbose:
                    print(f"    Vault: {vault[:8]}... ({info['mint'][:8]}...)")
                    print(f"      Balance: {info['human']:.8f} (decimals: {info['decimals']})")

        if len(token_vaults) < 2:
            return None

        # Strategy: If we have more than 2 vaults, find the best pair
        # Prefer prices in a reasonable range (0.00001 to 100000)
        best_price = None
        best_i = -1
        best_j = -1

        for i in range(len(token_vaults)):
            for j in range(i+1, len(token_vaults)):
                vault_i, info_i = token_vaults[i]
                vault_j, info_j = token_vaults[j]

                if info_i['human'] > 0 and info_j['human'] > 0:
                    # Try both directions
                    price1 = info_j['human'] / info_i['human']
                    price2 = info_i['human'] / info_j['human']

                    # Prefer prices between 0.00001 and 100000 as reasonable range
                    if 0.00001 < price1 < 100000:
                        if best_price is None or (price1 > 0.0001 and price1 < best_price):
                            best_price = price1
                            best_i = i
                            best_j = j
                    if 0.00001 < price2 < 100000:
                        if best_price is None or (price2 > 0.0001 and price2 < best_price):
                            best_price = price2
                            best_i = i
                            best_j = j

        if best_price is not None:
            if verbose and len(token_vaults) > 2:
                print(f"  Selected vault pair [{best_i}] and [{best_j}]")
            return best_price

        return None

    except Exception as e:
        if verbose:
            print(f"  Error fetching DAMM V2 price: {e}", file=sys.stderr)
        return None


def get_dlmm_price(pool_address: str, verbose: bool = False) -> Optional[float]:
    """Get DLMM pool price using bin formula"""
    try:
        # Get pool account info
        result = rpc_call("getAccountInfo", [pool_address, {"encoding": "base64"}])
        if not result or not result.get("result") or result["result"]["value"] is None:
            return None

        acc_info = result["result"]["value"]
        account_data = base64.b64decode(acc_info['data'][0])

        if len(account_data) < 78:
            return None

        # Read DLMM formula parameters
        base_decimals = struct.unpack_from("<B", account_data, 44)[0]
        quote_decimals = struct.unpack_from("<B", account_data, 45)[0]
        active_id = struct.unpack_from("<i", account_data, 72)[0]
        bin_step = struct.unpack_from("<H", account_data, 76)[0]

        if bin_step == 0 or bin_step > 10000:
            return None

        # Formula: (1 + bin_step/10_000)^active_id * 10^(base_decimals - quote_decimals)
        base = 1.0 + (bin_step / 10_000.0)
        raw_price = base ** active_id
        decimal_adjustment = 10 ** (base_decimals - quote_decimals)
        price = raw_price * decimal_adjustment

        if verbose:
            print(f"  Active ID: {active_id}")
            print(f"  Bin Step: {bin_step}")
            print(f"  Base Decimals: {base_decimals}, Quote Decimals: {quote_decimals}")

        if 1e-20 < price < 1e20:
            return price
        return None

    except Exception as e:
        if verbose:
            print(f"  Error fetching DLMM price: {e}", file=sys.stderr)
        return None


def get_dexscreener_price(pool_address: str) -> Optional[float]:
    """Get price from DexScreener API"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/solana/{pool_address}"
        response = requests.get(url, timeout=10)
        data = response.json()

        if "pair" in data and data["pair"]:
            price = float(data["pair"]["priceUsd"])
            return price
        return None
    except Exception:
        return None


def detect_pool_type(pool_address: str) -> str:
    """Detect whether a pool is DAMM V2 or DLMM"""
    try:
        # Try DAMM V2 first by attempting vault extraction
        try:
            tx_sig = get_pool_creation_tx(pool_address)
            vaults = get_vaults_from_tx(tx_sig, pool_address)
            if len(vaults) >= 2:
                return "DAMM_V2"
        except:
            pass

        # Fall back to size-based detection
        result = rpc_call("getAccountInfo", [pool_address, {"encoding": "base64"}])
        if not result or not result.get("result"):
            return "UNKNOWN"

        account_info = result["result"]["value"]
        if account_info:
            data_size = len(account_info['data'][0]) if account_info['data'] else 0

            # DLMM pools are typically very large (3000+ bytes) due to bin data
            if data_size > 2000:
                return "DLMM"
            else:
                # Assume DAMM V2 for smaller pools
                return "DAMM_V2"

        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def fetch_price(pool_address: str, verbose: bool = False) -> Dict:
    """Fetch price from a Meteora pool with auto-detection"""
    result = {
        "pool": pool_address,
        "type": "UNKNOWN",
        "on_chain_price": None,
        "dex_screener_price": None,
        "difference_pct": None,
        "error": None
    }

    try:
        # Detect pool type
        pool_type = detect_pool_type(pool_address)
        result["type"] = pool_type

        if verbose:
            print(f"  Pool Type: {pool_type}")

        # Fetch on-chain price
        if pool_type == "DAMM_V2":
            result["on_chain_price"] = get_damm_v2_price(pool_address, verbose=verbose)
        elif pool_type == "DLMM":
            result["on_chain_price"] = get_dlmm_price(pool_address, verbose=verbose)

        # Fetch DexScreener price
        result["dex_screener_price"] = get_dexscreener_price(pool_address)

        # Calculate difference
        if result["on_chain_price"] is not None and result["dex_screener_price"] is not None:
            diff = abs(result["on_chain_price"] - result["dex_screener_price"]) / result["dex_screener_price"] * 100
            result["difference_pct"] = diff

    except Exception as e:
        result["error"] = str(e)

    return result


def print_price_result(result: Dict, verbose: bool = False):
    """Pretty print price result"""
    print(f"\nPool: {result['pool']}")
    print(f"Type: {result['type']}")

    if result["on_chain_price"] is not None:
        print(f"On-chain price:    {result['on_chain_price']:.18f}")
    else:
        print("On-chain price:    Failed to fetch")

    if result["dex_screener_price"] is not None:
        print(f"DexScreener price: {result['dex_screener_price']:.18f}")

        if result["difference_pct"] is not None:
            print(f"Difference:        {result['difference_pct']:.2f}%")
    else:
        print("DexScreener price: Not indexed (pool may be too new)")

    if result["error"]:
        print(f"Error: {result['error']}", file=sys.stderr)

    print()


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python meteora_price_fetcher.py <pool_address> [pool_address2] ... [-v|--verbose]")
        print("\nExamples:")
        print("  # Fetch single pool price")
        print("  python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi")
        print("\n  # Fetch multiple pools")
        print("  python meteora_price_fetcher.py pool1_addr pool2_addr pool3_addr")
        print("\n  # Verbose output with details")
        print("  python meteora_price_fetcher.py pool_addr -v")
        sys.exit(1)

    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    pools = [arg for arg in sys.argv[1:] if not arg.startswith("-")]

    if not pools:
        print("Error: No pool addresses provided", file=sys.stderr)
        sys.exit(1)

    results = []
    for pool in pools:
        if verbose:
            print(f"Fetching price for {pool}...")

        result = fetch_price(pool, verbose=verbose)
        results.append(result)
        print_price_result(result, verbose=verbose)

    # If multiple pools, print summary
    if len(results) > 1:
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)

        successful = [r for r in results if r["on_chain_price"] is not None]
        print(f"Successfully fetched: {len(successful)}/{len(results)} pools")

        for result in successful:
            print(f"  {result['pool'][:16]}... : ${result['on_chain_price']:.10f}")


if __name__ == "__main__":
    main()
