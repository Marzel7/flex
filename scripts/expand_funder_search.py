#!/usr/bin/env python3
"""
Expand funder search by analyzing all transactions from known funders
to find additional creators they might have funded.

Start with: 8hfTZP4hzPh2bBwMKounGnTzpiYMK7wiyEtrgqVKHhBM
Look for: ALL SOL transfers TO any account, then check if that account
is a known creator or potential creator
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List, Tuple
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class FunderExpansionSearch:
    """Expand funder search from known sources"""

    def __init__(self):
        self.known_creators = set()
        self._load_all_creators()

    def _load_all_creators(self):
        """Load all known creator addresses (from all fields)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all unique creator addresses from all fields
            cursor.execute("SELECT DISTINCT creator_address FROM token_analysis WHERE creator_address IS NOT NULL AND creator_address != ''")
            self.known_creators.update(row[0] for row in cursor.fetchall())

            cursor.execute("SELECT DISTINCT token_creator FROM token_analysis WHERE token_creator IS NOT NULL AND token_creator != ''")
            self.known_creators.update(row[0] for row in cursor.fetchall())

            cursor.execute("SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL")
            self.known_creators.update(row[0] for row in cursor.fetchall())

            conn.close()
            print(f"[INIT] Loaded {len(self.known_creators)} unique creator addresses\n")
        except Exception as e:
            print(f"[ERROR] Loading creators: {e}")

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC with failover"""
        try:
            async with aiohttp.ClientSession() as session:
                for rpc_url in RPC_URLS:
                    try:
                        async with session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout),
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if "result" in data and data["result"] is not None:
                                    return data
                    except Exception:
                        continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}")
            return None

    async def get_account_transactions(self, account: str, limit: int = 100) -> List[str]:
        """Get all transactions for an account"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [account, {"limit": limit}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            sigs_data = result.get("result")
            if not sigs_data:
                return []

            return [tx["signature"] for tx in sigs_data if "signature" in tx]

        except Exception as e:
            print(f"[ERROR] Getting transactions for {account[:8]}...: {e}")
            return []

    async def find_all_sol_transfers(self, account: str, signature: str) -> Dict[str, float]:
        """
        Find ALL SOL transfers FROM this account in this transaction.
        Returns: {destination_address: amount_sol}
        """
        transfers = {}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return transfers

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return transfers

            meta = tx.get("meta")
            if not meta:
                return transfers

            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return transfers

            # Find source account's index
            src_idx = None
            for idx, acc in enumerate(accounts):
                acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                if acc_addr == account:
                    src_idx = idx
                    break

            if src_idx is None:
                return transfers

            if src_idx >= len(pre_balances) or src_idx >= len(post_balances):
                return transfers

            src_pre = pre_balances[src_idx]
            src_post = post_balances[src_idx]
            src_change = src_post - src_pre

            # If account sent SOL
            if src_change < -100000:  # > 0.0001 SOL sent
                for idx, acc in enumerate(accounts):
                    if idx >= len(pre_balances) or idx >= len(post_balances):
                        continue

                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == account:
                        continue

                    pre_bal = pre_balances[idx]
                    post_bal = post_balances[idx]
                    acc_change = post_bal - pre_bal

                    # Account received SOL
                    if acc_change > 100000:  # > 0.0001 SOL received
                        amount_sol = acc_change / 1e9
                        if amount_sol > 0:
                            if acc_addr not in transfers:
                                transfers[acc_addr] = 0
                            transfers[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing {signature[:16]}...: {e}")

        return transfers

    async def analyze_funder(self, funder: str, idx: int, total: int) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Analyze one funder's transactions"""
        funder_short = f"{funder[:8]}...{funder[-4:]}"
        print(f"[{idx}/{total}] {funder_short}", end=" ", flush=True)

        signatures = await self.get_account_transactions(funder, limit=100)
        print(f"({len(signatures)} txs)", end=" ", flush=True)

        creator_transfers = {}
        other_transfers = {}

        for sig in signatures:
            transfers = await self.find_all_sol_transfers(funder, sig)
            for dest, amount in transfers.items():
                if dest in self.known_creators:
                    if dest not in creator_transfers:
                        creator_transfers[dest] = 0
                    creator_transfers[dest] += amount
                else:
                    if dest not in other_transfers:
                        other_transfers[dest] = 0
                    other_transfers[dest] += amount

        print(f"→ TO {len(creator_transfers)} creator(s), {len(other_transfers)} other account(s)", flush=True)

        if creator_transfers:
            for creator, amount in sorted(creator_transfers.items(), key=lambda x: x[1], reverse=True):
                creator_short = f"{creator[:8]}...{creator[-4:]}"
                print(f"    → CREATOR {creator_short}: {amount:.6f} SOL", flush=True)

        if other_transfers:
            for dest, amount in sorted(other_transfers.items(), key=lambda x: x[1], reverse=True)[:3]:
                dest_short = f"{dest[:8]}...{dest[-4:]}"
                print(f"    → OTHER {dest_short}: {amount:.6f} SOL", flush=True)

        return creator_transfers, other_transfers

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Funder Expansion Search at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            # Start with known funder
            known_funders = ["8hfTZP4hzPh2bBwMKounGnTzpiYMK7wiyEtrgqVKHhBM"]

            all_creator_transfers = {}
            all_other_transfers = {}

            for idx, funder in enumerate(known_funders, 1):
                creator_xfers, other_xfers = await self.analyze_funder(
                    funder, idx, len(known_funders)
                )
                all_creator_transfers.update(creator_xfers)
                all_other_transfers.update(other_xfers)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Known funders analyzed: {len(known_funders)}")
            print(f"  Transfers to known creators: {len(all_creator_transfers)}")
            print(f"  Transfers to other accounts: {len(all_other_transfers)}")

            total_creator_sol = sum(all_creator_transfers.values())
            total_other_sol = sum(all_other_transfers.values())
            print(f"  Total SOL to creators: {total_creator_sol:.6f}")
            print(f"  Total SOL to others: {total_other_sol:.6f}")

            if all_creator_transfers:
                print(f"\n[TO CREATORS]:")
                for creator, amount in sorted(all_creator_transfers.items(), key=lambda x: x[1], reverse=True):
                    creator_short = f"{creator[:8]}...{creator[-4:]}"
                    print(f"  {creator_short}: {amount:.6f} SOL")

            if all_other_transfers:
                print(f"\n[TO OTHER ACCOUNTS] (potential secondary funders or hubs):")
                for dest, amount in sorted(all_other_transfers.items(), key=lambda x: x[1], reverse=True)[:10]:
                    dest_short = f"{dest[:8]}...{dest[-4:]}"
                    print(f"  {dest_short}: {amount:.6f} SOL")

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    print(f"[START] Funder Expansion Search")
    print("=" * 80)

    analyzer = FunderExpansionSearch()
    await analyzer.run()

    print("\n" + "=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
