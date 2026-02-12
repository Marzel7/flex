#!/usr/bin/env python3
"""
Analyze Funder SOL Flow - Where do they send SOL?

For a given creator:
1. Get all funders
2. For each funder, fetch their recent transaction history via RPC
3. Show all SOL outflows (who they send to)
4. Identify receiving addresses and amounts
"""

import sqlite3
import asyncio
import aiohttp
from typing import Dict, List, Set
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

DB_PATH = "pumpswap_tokens.db"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator from database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT funder_address, amount_sol
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
        """,
            (creator_address,),
        )

        funders = [(row["funder_address"], row["amount_sol"]) for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error: {e}")
        return []


def get_funder_total_outflows(funder_address: str) -> Dict[str, float]:
    """Get where a funder sends SOL - from creator_receivers table if available"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if creator_receivers table exists and has data for this funder
        # This would contain where the funder sends SOL to
        cursor.execute(
            """
            SELECT receiver_address, SUM(amount_sol) as total_sol
            FROM creator_receivers
            WHERE creator_address = ? OR receiver_address = ?
            GROUP BY receiver_address
            ORDER BY total_sol DESC
        """,
            (funder_address, funder_address),
        )

        flows = {}
        for row in cursor.fetchall():
            flows[row["receiver_address"]] = row["total_sol"]

        conn.close()
        return flows
    except Exception as e:
        return {}


class FunderSOLFlowAnalyzer:
    """Analyze SOL outflows for funders via RPC"""

    def __init__(self, solana_rpc: str = SOLANA_RPC):
        self.solana_rpc = solana_rpc
        self.session = None

    async def init_session(self):
        """Initialize aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def get_signatures_for_address(self, address: str, limit: int = 100) -> List[Dict]:
        """Get recent signatures for an address using Solana RPC"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    address,
                    {"limit": min(limit, 100), "before": None},
                ],
            }

            async with self.session.post(self.solana_rpc, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "result" in result:
                        return result["result"]
        except Exception as e:
            print(f"[RPC] Error getting signatures for {address[:8]}...: {e}")

        return []

    async def get_transaction(self, signature: str) -> Dict:
        """Get transaction details from RPC"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            }

            async with self.session.post(self.solana_rpc, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return result["result"]
        except Exception as e:
            pass

        return {}

    async def extract_sol_transfers(self, funder_address: str, limit: int = 50) -> Dict[str, float]:
        """Extract SOL transfers FROM this funder to other addresses"""
        transfers = {}

        try:
            signatures = await self.get_signatures_for_address(funder_address, limit)

            for sig_info in signatures:
                signature = sig_info.get("signature")
                if not signature:
                    continue

                # Skip if transaction failed
                if sig_info.get("err"):
                    continue

                tx = await self.get_transaction(signature)
                if not tx or not isinstance(tx, dict):
                    continue

                transaction = tx.get("transaction")
                meta = tx.get("meta")

                if not transaction or not meta:
                    continue

                message = transaction.get("message", {})
                account_keys = message.get("accountKeys", [])

                # Check pre and post balances to find SOL transfers
                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])

                # Find our funder's index
                funder_index = None
                for idx, key in enumerate(account_keys):
                    if isinstance(key, dict):
                        key_str = key.get("pubkey", "")
                    else:
                        key_str = str(key)

                    if key_str == funder_address:
                        funder_index = idx
                        break

                if funder_index is None:
                    continue

                # Check for balance changes
                if funder_index < len(pre_balances) and funder_index < len(post_balances):
                    pre_bal = pre_balances[funder_index]
                    post_bal = post_balances[funder_index]

                    if pre_bal > post_bal:
                        # Funder spent SOL
                        spent_lamports = pre_bal - post_bal
                        spent_sol = spent_lamports / 1e9

                        # Look for receiving addresses (accounts whose balance increased)
                        for idx, key in enumerate(account_keys):
                            if idx == funder_index:
                                continue

                            if isinstance(key, dict):
                                dest_addr = key.get("pubkey", "")
                            else:
                                dest_addr = str(key)

                            if idx < len(pre_balances) and idx < len(post_balances):
                                dest_pre = pre_balances[idx]
                                dest_post = post_balances[idx]

                                if dest_post > dest_pre:
                                    received = (dest_post - dest_pre) / 1e9
                                    if dest_addr not in transfers:
                                        transfers[dest_addr] = 0
                                    transfers[dest_addr] += received

        except Exception as e:
            pass

        return transfers


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze where funders send SOL")
    parser.add_argument("creator", type=str, help="Creator address to analyze")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit funders to analyze (default 20)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze ALL funders (overrides --limit)",
    )
    args = parser.parse_args()

    creator = args.creator

    print(f"[ANALYSIS] Analyzing funder SOL flows for creator:")
    print(f"[ANALYSIS] {creator}\n")

    # Get all funders for this creator
    funders = get_creator_funders(creator)
    if not funders:
        print(f"[DB] ❌ No funders found for this creator")
        return

    print(f"[DB] ✅ Found {len(funders)} total funders\n")

    # Determine how many funders to analyze
    analyze_count = len(funders) if args.all else min(args.limit, len(funders))
    if args.all:
        print(f"[ANALYSIS] Analyzing SOL flows for ALL {len(funders)} funders:\n")
    else:
        print(f"[ANALYSIS] Analyzing top {analyze_count} funders by amount:\n")

    analyzer = FunderSOLFlowAnalyzer(solana_rpc=SOLANA_RPC)
    await analyzer.init_session()

    all_outflow_addresses = {}
    funder_flows = {}

    try:
        for i, (funder_address, amount_to_creator) in enumerate(funders[:analyze_count], 1):
            # Get account type
            account_type = ""
            cex_info = get_cex_info(funder_address)
            if cex_info:
                account_type = f"✅ CEX: {cex_info.get('name', 'CEX')}"
            else:
                infra_info = get_account_info(funder_address)
                if infra_info:
                    account_type = f"✅ INFRA: {infra_info.get('name', 'Infrastructure')}"
                else:
                    pumpfun_info = get_pumpfun_creator_info(funder_address)
                    if pumpfun_info:
                        account_type = f"🎯 PUMPFUN: {pumpfun_info.get('name', 'Creator')}"
                    else:
                        suspicious_info = get_suspicious_wallet_info(funder_address)
                        if suspicious_info:
                            account_type = f"⚠️ SUSPICIOUS: {suspicious_info.get('name', 'Suspicious')}"

            print(f"\n[{i}/{analyze_count}] Funder: {funder_address[:12]}... ({amount_to_creator:.2f} SOL to creator)")
            if account_type:
                print(f"     Type: {account_type}")

            # Analyze SOL flows for this funder
            transfers = await analyzer.extract_sol_transfers(funder_address, limit=100)

            if transfers:
                print(f"     📤 Outflows to {len(transfers)} addresses:")

                # Sort by amount descending
                sorted_transfers = sorted(transfers.items(), key=lambda x: x[1], reverse=True)

                for j, (dest_address, sol_amount) in enumerate(sorted_transfers[:10], 1):
                    # Check if destination is known account
                    dest_type = ""
                    dest_cex = get_cex_info(dest_address)
                    if dest_cex:
                        dest_type = f"[✅ CEX: {dest_cex.get('name')}]"
                    else:
                        dest_infra = get_account_info(dest_address)
                        if dest_infra:
                            dest_type = f"[✅ INFRA: {dest_infra.get('name')}]"

                    print(f"        {j:2}. {dest_address[:12]}... ← {sol_amount:.3f} SOL {dest_type}")

                if len(sorted_transfers) > 10:
                    remaining_sol = sum(amt for _, amt in sorted_transfers[10:])
                    print(f"        ... and {len(sorted_transfers) - 10} more addresses ({remaining_sol:.2f} SOL total)")

                funder_flows[funder_address] = transfers
                for addr, amt in transfers.items():
                    if addr not in all_outflow_addresses:
                        all_outflow_addresses[addr] = 0
                    all_outflow_addresses[addr] += amt
            else:
                print(f"     ℹ️  No recent SOL outflows detected (or data unavailable)")

    finally:
        await analyzer.close_session()

    # Summary
    print(f"\n\n{'='*80}")
    print(f"SUMMARY - FUNDER SOL FLOWS")
    print(f"{'='*80}")
    print(f"Funders analyzed: {len(funder_flows)}")
    print(f"Total unique outflow addresses: {len(all_outflow_addresses)}")
    print(f"Total SOL outflows tracked: {sum(all_outflow_addresses.values()):.2f} SOL")

    if all_outflow_addresses:
        print(f"\n🔥 Most common outflow addresses:")
        sorted_addresses = sorted(all_outflow_addresses.items(), key=lambda x: x[1], reverse=True)
        for i, (addr, total_sol) in enumerate(sorted_addresses[:10], 1):
            # Check address type
            addr_type = ""
            addr_cex = get_cex_info(addr)
            if addr_cex:
                addr_type = f"[✅ CEX: {addr_cex.get('name')}]"
            else:
                addr_infra = get_account_info(addr)
                if addr_infra:
                    addr_type = f"[✅ INFRA: {addr_infra.get('name')}]"

            print(f"  {i:2}. {addr[:12]}... ← {total_sol:8.2f} SOL {addr_type}")


if __name__ == "__main__":
    asyncio.run(main())
