#!/usr/bin/env python3
"""
Analyze Funder Networks - Deep Dive into SOL Transfer Patterns

For a given creator:
1. Get all funders (from creator_funders table)
2. For each funder, fetch their SOL transfer history via RPC
3. Check if those SOL transfers show up funding OTHER creators
4. Identify coordinated funding networks

This reveals which addresses are actively moving SOL to fund creators.
"""

import sqlite3
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple
import json
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

DB_PATH = "pumpswap_tokens.db"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"


class FunderNetworkAnalyzer:
    """Analyze SOL transfer patterns for funders"""

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

    async def get_signatures_for_address(self, address: str, limit: int = 500) -> List[Dict]:
        """Get recent signatures for an address using Solana RPC"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    address,
                    {"limit": min(limit, 1000), "before": None},
                ],
            }

            async with self.session.post(self.solana_rpc, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "result" in result:
                        return result["result"]
        except Exception as e:
            print(f"[RPC] Error getting signatures: {e}")

        return []

    async def get_transaction(self, sig: str) -> Optional[Dict]:
        """Get transaction details using Solana RPC"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            }

            async with self.session.post(self.solana_rpc, json=payload, timeout=30) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "result" in result:
                        return result["result"]
        except Exception as e:
            pass

        return None

    def extract_sol_transfers(
        self, tx: Dict, address: str
    ) -> List[Tuple[str, float, str]]:
        """
        Extract SOL transfers from a transaction.
        Returns list of (counterparty_address, amount_sol, direction) tuples.
        direction = "in" (received) or "out" (sent)
        """
        transfers = []

        try:
            if not tx or "meta" not in tx:
                return transfers

            meta = tx.get("meta", {})
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts:
                return transfers

            # Find address account index
            addr_idx = None
            for idx, acc in enumerate(accounts):
                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)
                if acc_str == address:
                    addr_idx = idx
                    break

            if addr_idx is None:
                return transfers

            # Get balance change for this address
            addr_balance_change = (
                post_balances[addr_idx] - pre_balances[addr_idx]
                if addr_idx < len(pre_balances) and addr_idx < len(post_balances)
                else 0
            )

            # Skip if no significant balance change
            if abs(addr_balance_change) < 5000:  # < 0.000005 SOL
                return transfers

            # Look for counterparties
            for idx, acc in enumerate(accounts):
                if idx == addr_idx or idx >= len(pre_balances) or idx >= len(post_balances):
                    continue

                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)

                # Skip system/program accounts
                if acc_str in [
                    "11111111111111111111111111111111",
                    "TokenkegQfeZyiNwAJsyFbPVwwQQQg",
                    "ATokenGPvbdGVqstVQmcLsNZAqeEbtQvN",
                ]:
                    continue

                balance_change = post_balances[idx] - pre_balances[idx]

                # Check for SOL transfer relationship (opposite balance changes)
                if abs(addr_balance_change + balance_change) < 10000 and addr_balance_change != 0:
                    amount_sol = abs(balance_change) / 1e9
                    direction = "in" if addr_balance_change > 0 else "out"

                    transfers.append((acc_str, amount_sol, direction))
                    break  # Only one counterparty per transaction

        except Exception as e:
            pass

        return transfers

    async def analyze_funder(
        self, funder_address: str, limit_transactions: int = 500
    ) -> Dict:
        """
        Analyze a single funder's SOL transfer history.
        Returns dict with all transfers and counterparties.
        """
        print(f"\n[FUNDER] 🔍 Analyzing {funder_address[:12]}...")

        transfers = {}  # {counterparty: [(amount, direction), ...]}

        # Get funder's recent signatures
        signatures = await self.get_signatures_for_address(funder_address, limit=limit_transactions)
        print(f"[FUNDER]    Found {len(signatures)} recent signatures")

        if not signatures:
            print(f"[FUNDER] ✓ No recent activity")
            return {
                "funder": funder_address,
                "transactions_analyzed": 0,
                "counterparties": {},
                "status": "no_activity",
            }

        txs_analyzed = 0
        for sig_info in signatures:
            sig = sig_info.get("signature")
            if not sig:
                continue

            tx = await self.get_transaction(sig)
            if not tx:
                continue

            txs_analyzed += 1

            # Extract SOL transfers
            sol_transfers = self.extract_sol_transfers(tx, funder_address)
            for counterparty, amount, direction in sol_transfers:
                if counterparty not in transfers:
                    transfers[counterparty] = []
                transfers[counterparty].append((amount, direction))

            # Rate limiting
            if txs_analyzed % 10 == 0:
                await asyncio.sleep(0.05)

        print(
            f"[FUNDER] ✅ Complete: {txs_analyzed} txs analyzed, {len(transfers)} unique counterparties"
        )

        return {
            "funder": funder_address,
            "transactions_analyzed": txs_analyzed,
            "counterparties": transfers,
            "status": "success",
        }


async def get_creator_funders(creator_address: str) -> List[Tuple[str, float]]:
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
        print(f"[DB] Error getting creator funders: {e}")
        return []


def check_funder_in_other_creators(funder_address: str, exclude_creator: str) -> Dict:
    """Check if this funder funds other creators in the system"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT creator_address, amount_sol, COUNT(*) as count
            FROM creator_funders
            WHERE funder_address = ? AND creator_address != ?
            GROUP BY creator_address
            ORDER BY amount_sol DESC
        """,
            (funder_address, exclude_creator),
        )

        other_creators = {}
        for row in cursor.fetchall():
            other_creators[row["creator_address"]] = row["amount_sol"]

        conn.close()
        return other_creators
    except Exception as e:
        print(f"[DB] Error checking other creators: {e}")
        return {}


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Analyze funder networks")
    parser.add_argument(
        "creator",
        type=str,
        help="Creator address to analyze",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Limit signatures per funder (default 500)",
    )
    args = parser.parse_args()

    creator = args.creator

    print(f"[ANALYSIS] Starting funder network analysis for creator")
    print(f"[ANALYSIS] Creator: {creator}")

    # Get all funders for this creator
    funders = await get_creator_funders(creator)
    if not funders:
        print(f"[DB] No funders found for this creator")
        return

    print(f"\n[DB] Found {len(funders)} funders:")
    for funder, amount in funders[:10]:
        print(f"     • {funder[:12]}... ({amount:.2f} SOL)")
    if len(funders) > 10:
        print(f"     ... and {len(funders) - 10} more")

    # Analyze each funder
    analyzer = FunderNetworkAnalyzer()
    await analyzer.init_session()

    print(f"\n{'='*80}")
    print(f"ANALYZING FUNDER SOL TRANSFER HISTORY")
    print(f"{'='*80}")

    funder_results = {}
    try:
        for i, (funder_address, db_amount) in enumerate(funders[:20], 1):  # Limit to first 20
            # Check for known accounts
            account_type = ""
            cex_info = get_cex_info(funder_address)
            if cex_info:
                account_type = f"✅ CEX: {cex_info.get('name', 'CEX')}"
            else:
                infra_info = get_account_info(funder_address)
                if infra_info:
                    account_type = f"✅ INFRA: {infra_info.get('name', 'Infra')}"
                else:
                    pumpfun_info = get_pumpfun_creator_info(funder_address)
                    if pumpfun_info:
                        account_type = f"🎯 PUMPFUN: {pumpfun_info.get('name', 'Creator')}"
                    else:
                        suspicious_info = get_suspicious_wallet_info(funder_address)
                        if suspicious_info:
                            account_type = f"⚠️  SUSPICIOUS: {suspicious_info.get('name', 'Suspicious')}"

            type_str = f" [{account_type}]" if account_type else ""
            print(f"\n[{i}/{min(20, len(funders))}] Funder: {funder_address[:12]}... ({db_amount:.2f} SOL to this creator){type_str}")

            # Analyze funder's SOL transfers
            result = await analyzer.analyze_funder(funder_address, limit_transactions=args.limit)
            funder_results[funder_address] = result

            # Check if this funder funds other creators
            other_creators = check_funder_in_other_creators(funder_address, creator)
            if other_creators:
                print(
                    f"[NETWORK] 🔗 This funder ALSO funds {len(other_creators)} other creators!"
                )
                for other_creator, amount in list(other_creators.items())[:5]:
                    print(f"           • {other_creator[:12]}... ({amount:.2f} SOL)")
                if len(other_creators) > 5:
                    print(f"           ... and {len(other_creators) - 5} more")

            await asyncio.sleep(0.5)

    finally:
        await analyzer.close_session()

    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")

    repeat_funders = 0
    total_counterparties = 0
    for funder, result in funder_results.items():
        if result["status"] == "success":
            total_counterparties += len(result["counterparties"])
            other = check_funder_in_other_creators(funder, creator)
            if other:
                repeat_funders += 1

    print(f"Funders analyzed: {len(funder_results)}")
    print(f"Funders funding multiple creators: {repeat_funders}")
    print(f"Total unique counterparties: {total_counterparties}")

    print(f"\n[ANALYSIS] ✅ Complete")


if __name__ == "__main__":
    asyncio.run(main())
