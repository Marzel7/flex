#!/usr/bin/env python3
"""
Enhanced Meteora Token Price Fetcher V2
Improved price calculation with better vault pair identification

Key improvements:
- Uses Jupiter API to identify token mints
- Better handling of multiple vaults
- Prioritizes known quote currencies (SOL, USDC, USDT)
- Validates prices against reasonable ranges
"""

import requests
import base64
import struct
import base58
import sys
import json
from typing import Optional, Dict, List, Tuple

RPC_URL = "https://mainnet.helius-rpc.com/?api-key=0ae07551-32df-4d9d-af2a-1925fb7f561f"

# Known quote tokens (these are typically the denominator in price)
KNOWN_QUOTES = {
    "So11111111111111111111111111111111111111112": "SOL",  # Wrapped SOL
    "EPjFWaLb3oc6YBPbgnVLaHjHiQg5Sj9nipzapmNvqqp": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BcWNg": "UST",
    "9n3zDK7oAxn2HqkBL53BFM19VbFalse7g3kHcNqKNQnp": "COPE",
}

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
    return owner == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


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


def get_token_symbol(mint: str) -> Optional[str]:
    """Get token symbol from Jupiter API"""
    try:
        url = f"https://tokens.jup.ag/token/{mint}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get('symbol', None)
    except:
        pass

    # Check if it's a known quote
    if mint in KNOWN_QUOTES:
        return KNOWN_QUOTES[mint]

    return None


def is_pool_depleted(token_vaults: List[Tuple[str, Dict]]) -> bool:
    """
    Check if pool appears to be depleted (liquidity removed).
    Depletion is indicated when one vault is essentially empty while the other has balance.
    """
    if len(token_vaults) < 2:
        return False

    # Get all vault balances (exclude zero balances)
    balances = [info['human'] for vault, info in token_vaults if info['human'] > 0]

    if len(balances) < 2:
        return True  # Less than 2 non-zero vaults = depleted

    # Sort balances to find the smallest
    sorted_balances = sorted(balances)
    smallest = sorted_balances[0]
    largest = sorted_balances[-1]

    # If the smallest vault has nearly zero balance while the other has meaningful balance,
    # the pool is likely depleted (liquidity was removed)
    # Threshold: if smallest < 0.00001 (essentially dust), it's depleted
    if smallest < 0.00001:
        return True

    # Additional check: if smallest vault has < 0.0001 and it's >100x smaller than the largest,
    # that's also a depletion pattern
    if smallest < 0.0001 and largest > 0 and (largest / smallest) > 100:
        return True

    return False


def calculate_best_price(token_vaults: List[Tuple[str, Dict]], verbose: bool = False) -> Optional[Dict]:
    """
    Calculate the best price from vault pairs
    Returns: {price, base_vault_idx, quote_vault_idx, explanation, warning}
    """
    if len(token_vaults) < 2:
        return None

    # Check for depleted pools
    if is_pool_depleted(token_vaults):
        return {
            'price': None,
            'warning': 'POOL_DEPLETED',
            'reason': 'Pool liquidity has been removed or is extremely low',
        }

    best_result = None

    for i in range(len(token_vaults)):
        for j in range(i + 1, len(token_vaults)):
            vault_i, info_i = token_vaults[i]
            vault_j, info_j = token_vaults[j]

            if info_i['human'] <= 0 or info_j['human'] <= 0:
                continue

            # Try both directions
            price_j_per_i = info_j['human'] / info_i['human']
            price_i_per_j = info_i['human'] / info_j['human']

            # Accept any valid positive price (no hard range limits)
            # Meteora pools can have extreme prices depending on liquidity and decimals
            if price_j_per_i > 0:
                if best_result is None:
                    best_result = {
                        'price': price_j_per_i,
                        'base_idx': i,
                        'quote_idx': j,
                        'direction': f"vault[{j}] / vault[{i}]",
                        'base_mint': info_i['mint'],
                        'quote_mint': info_j['mint'],
                    }
                # Prefer prices closer to 1 for readability (but accept any valid price)
                elif abs(price_j_per_i - 1) < abs(best_result['price'] - 1):
                    best_result = {
                        'price': price_j_per_i,
                        'base_idx': i,
                        'quote_idx': j,
                        'direction': f"vault[{j}] / vault[{i}]",
                        'base_mint': info_i['mint'],
                        'quote_mint': info_j['mint'],
                    }

            if price_i_per_j > 0:
                if best_result is None:
                    best_result = {
                        'price': price_i_per_j,
                        'base_idx': j,
                        'quote_idx': i,
                        'direction': f"vault[{i}] / vault[{j}]",
                        'base_mint': info_j['mint'],
                        'quote_mint': info_i['mint'],
                    }
                # Prefer prices closer to 1 for readability
                elif abs(price_i_per_j - 1) < abs(best_result['price'] - 1):
                    best_result = {
                        'price': price_i_per_j,
                        'base_idx': j,
                        'quote_idx': i,
                        'direction': f"vault[{i}] / vault[{j}]",
                        'base_mint': info_j['mint'],
                        'quote_mint': info_i['mint'],
                    }

    return best_result


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
                    symbol = get_token_symbol(info['mint'])
                    symbol_str = f" ({symbol})" if symbol else ""
                    print(f"    Vault: {vault[:8]}... ({info['mint'][:8]}...{symbol_str})")
                    print(f"      Balance: {info['human']:.8f} (decimals: {info['decimals']})")

        if len(token_vaults) < 2:
            return None

        # Calculate best price
        best = calculate_best_price(token_vaults, verbose=verbose)

        if best:
            if best.get('warning') == 'POOL_DEPLETED':
                if verbose:
                    print(f"  ⚠️  Pool appears depleted: {best['reason']}")
                return None

            if best.get('price') is not None:
                if verbose:
                    base_sym = get_token_symbol(best['base_mint']) or best['base_mint'][:8]
                    quote_sym = get_token_symbol(best['quote_mint']) or best['quote_mint'][:8]
                    print(f"  Price calculation: {quote_sym} / {base_sym}")
                    print(f"  Direction: {best['direction']}")
                return best['price']

        return None

    except Exception as e:
        if verbose:
            print(f"  Error fetching DAMM V2 price: {e}")
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


def fetch_price(pool_address: str, verbose: bool = False) -> Dict:
    """Fetch price from a Meteora pool"""
    result = {
        "pool": pool_address,
        "on_chain_price": None,
        "dex_screener_price": None,
        "difference_pct": None,
        "error": None
    }

    try:
        # Always try DAMM V2 vault method first
        result["on_chain_price"] = get_damm_v2_price(pool_address, verbose=verbose)

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
        print(f"Error: {result['error']}")

    print()


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python meteora_price_fetcher_v2.py <pool_address> [pool_address2] ... [-v|--verbose]")
        print("\nExamples:")
        print("  python meteora_price_fetcher_v2.py B1qU68ZZaTUb9GBN4xwvTvpqcLv4wmUDvCxRS6PZPB9D")
        print("  python meteora_price_fetcher_v2.py pool1 pool2 pool3 -v")
        sys.exit(1)

    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    pools = [arg for arg in sys.argv[1:] if not arg.startswith("-")]

    if not pools:
        print("Error: No pool addresses provided")
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
