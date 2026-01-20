#!/usr/bin/env python3
"""
Discover additional funder accounts by analyzing funder network connections.

Strategy:
1. Start with known funders
2. Query their transaction histories
3. Look for transfers TO other funder accounts (funder-to-funder transfers)
4. This reveals hub accounts and distribution networks
5. Add newly discovered funders to the pool
6. Repeat to find the complete funder network

This builds a transitive closure of all accounts that participate in the funding chain.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List, Set, Tuple
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class FunderNetworkDiscovery:
    """Discover complete funder networks through transitive analysis"""

    def __init__(self):
        self.creators_set = set()
        self.known_funders = set()
        self.funder_connections = {}  # {from_funder: {to_funder: amount}}
        self._load_creators()

    def _load_creators(self):
        """Load all creator addresses from database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL"
            )
            self.creators_set = set(row[0] for row in cursor.fetchall())
            conn.close()
            print(f"[INIT] Loaded {len(self.creators_set)} unique creators")
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

            signatures = [tx["signature"] for tx in sigs_data if "signature" in tx]
            return signatures

        except Exception as e:
            print(f"[ERROR] Getting transactions for {account[:8]}...: {e}")
            return []

    async def analyze_transaction_for_funders(
        self, account: str, signature: str, known_funders: Set[str]
    ) -> Dict[str, float]:
        """
        Analyze transaction to find:
        1. Transfers TO known funders (hub identification)
        2. Transfers TO potential new funders (funder-to-funder)

        Returns: {destination_account: amount_sol}
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

            # Get balance changes
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

            # Source balance change
            src_pre = pre_balances[src_idx]
            src_post = post_balances[src_idx]
            src_change = src_post - src_pre

            # If account sent SOL (balance decreased)
            if src_change < -100000:  # > 0.0001 SOL sent

                # Scan all recipient accounts
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
                            # Track all recipients
                            if acc_addr not in transfers:
                                transfers[acc_addr] = 0
                            transfers[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing {signature[:16]}...: {e}")

        return transfers

    async def process_funder_for_connections(
        self, funder: str, idx: int, total: int, known_funders: Set[str]
    ) -> Tuple[Set[str], Dict[str, float]]:
        """Process one funder to find connections to other funders"""
        funder_short = f"{funder[:8]}...{funder[-4:]}"
        print(f"[{idx}/{total}] {funder_short}", end=" ", flush=True)

        # Get all transactions
        signatures = await self.get_account_transactions(funder, limit=50)
        print(f"({len(signatures)} txs)", end=" ", flush=True)

        new_funders = set()
        funder_transfers = {}

        # Process each transaction
        for sig in signatures:
            transfers = await self.analyze_transaction_for_funders(
                funder, sig, known_funders
            )

            for recipient, amount in transfers.items():
                # Check if recipient is a known funder (hub identification)
                if recipient in known_funders:
                    if recipient not in funder_transfers:
                        funder_transfers[recipient] = 0
                    funder_transfers[recipient] += amount
                    continue

                # Potential new funder (transfer to non-creator, non-known-funder account)
                if recipient not in self.creators_set:
                    # This could be a hub or another funder
                    # Add to potential new funders if significant transfer
                    if amount > 0.01:  # > 0.01 SOL
                        new_funders.add(recipient)

        # Display results
        if funder_transfers:
            print(f"→ Sends to {len(funder_transfers)} hub(s)", end=" ")
        if new_funders:
            print(f"| Found {len(new_funders)} potential new funder(s)", flush=True)
        else:
            print(flush=True)

        return new_funders, funder_transfers

    async def discover_network(self, initial_funders: List[str], max_iterations: int = 3):
        """Discover complete funder network through iterative analysis"""
        try:
            known_funders = set(initial_funders)
            all_connections = {}

            for iteration in range(max_iterations):
                print(
                    f"\n[ITERATION {iteration + 1}] Analyzing {len(known_funders)} funders...\n"
                )

                new_funders_found = set()
                iteration_connections = {}

                # Process each known funder
                for idx, funder in enumerate(sorted(known_funders), 1):
                    if funder in all_connections:
                        continue  # Already processed

                    new_funders, transfers = await self.process_funder_for_connections(
                        funder, idx, len(known_funders), known_funders
                    )

                    if transfers:
                        iteration_connections[funder] = transfers

                    new_funders_found.update(new_funders)

                # Update collections
                all_connections.update(iteration_connections)
                old_size = len(known_funders)
                known_funders.update(new_funders_found)

                print(
                    f"\n[RESULT] Iteration {iteration + 1}: Found {len(new_funders_found)} new funder(s)"
                )

                # Stop if no new funders found
                if len(known_funders) == old_size:
                    print("[DONE] No new funders found. Network discovery complete.")
                    break

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Total funders discovered: {len(known_funders)}")
            print(f"  Funder-to-funder connections: {len(all_connections)}")

            if all_connections:
                print(f"\n[FUNDER NETWORK]:\n")
                for from_funder, transfers in sorted(
                    all_connections.items(), key=lambda x: sum(x[1].values()), reverse=True
                ):
                    from_short = f"{from_funder[:8]}...{from_funder[-4:]}"
                    total_transferred = sum(transfers.values())
                    print(f"  {from_short}: Sends {total_transferred:.6f} SOL to:")
                    for to_funder, amount in sorted(
                        transfers.items(), key=lambda x: x[1], reverse=True
                    ):
                        to_short = f"{to_funder[:8]}...{to_funder[-4:]}"
                        print(f"      → {to_short}: {amount:.6f} SOL")

            return known_funders

        except Exception as e:
            print(f"[ERROR] {e}")
            return known_funders

    async def run(self):
        """Main execution"""
        print(
            f"[START] Funder Network Discovery at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print("=" * 80)

        # Start with the known funder
        initial_funders = [
            "8hfTZP4hzPh2bBwMKounGnTzpiYMK7wiyEtrgqVKHhBM",  # Known funder of CQ3k9qYC...
        ]

        discovered_funders = await self.discover_network(initial_funders, max_iterations=3)

        print("\n" + "=" * 80)
        print(f"[FINAL] Discovered {len(discovered_funders)} funders total")
        print(f"[FINAL] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


async def main():
    discovery = FunderNetworkDiscovery()
    await discovery.run()


if __name__ == "__main__":
    asyncio.run(main())
