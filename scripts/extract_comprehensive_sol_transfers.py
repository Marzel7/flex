#!/usr/bin/env python3
"""
Extract COMPREHENSIVE SOL transfers for all token creators.

For EVERY creator, logs:
1. ALL accounts that SENT SOL to the creator (inbound funding)
2. ALL accounts that RECEIVED SOL from the creator (outbound/rugs/distributions)
3. Transfer amounts and transaction signatures
4. Identifies funding sources AND extraction destinations

This reveals:
- Who funded each creator
- Where creators send funds (treasury addresses)
- Centralization points (shared destinations)
- Network relationships
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"

# RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class ComprehensiveSOLExtractor:
    """Extract ALL SOL transfers (in/out) for token creators"""

    def __init__(self):
        self.rpc_index = 0
        self.inbound_transfers = defaultdict(lambda: defaultdict(float))  # creator -> {sender: amount}
        self.outbound_transfers = defaultdict(lambda: defaultdict(float))  # creator -> {receiver: amount}
        self.transfer_records = []  # All transfer records with signatures

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC with failover"""
        try:
            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        async with session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout)
                        ) as resp:
                            if resp.status == 200:
                                return await resp.json()
                    except Exception:
                        continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}")
            return None

    async def get_all_creator_transactions(self, creator: str, max_txs: int = 100) -> List[str]:
        """Get all transaction signatures for a creator"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": max_txs}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            signatures = [tx["signature"] for tx in result["result"]]
            return signatures

        except Exception as e:
            print(f"[ERROR] Failed to get signatures for {creator}: {e}")
            return []

    async def extract_sol_transfers_from_tx(self, creator: str, signature: str) -> Tuple[Dict, Dict]:
        """
        Extract SOL transfers from a single transaction.

        Returns:
        - inbound: {sender_address: amount_sol}
        - outbound: {receiver_address: amount_sol}
        """
        inbound = {}
        outbound = {}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return inbound, outbound

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return inbound, outbound

            meta = tx.get("meta", {})
            if not meta:
                return inbound, outbound

            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return inbound, outbound

            # Find creator's account index
            creator_index = None
            for i, account in enumerate(accounts):
                account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                if account_addr == creator:
                    creator_index = i
                    break

            if creator_index is None or creator_index >= len(pre_balances) or creator_index >= len(post_balances):
                return inbound, outbound

            creator_pre = pre_balances[creator_index]
            creator_post = post_balances[creator_index]
            creator_change = creator_post - creator_pre

            # Scan all accounts in this transaction
            for i, account in enumerate(accounts):
                if i >= len(pre_balances) or i >= len(post_balances):
                    continue

                account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                if account_addr == creator:
                    continue  # Skip creator themselves

                pre_bal = pre_balances[i]
                post_bal = post_balances[i]
                balance_change = post_bal - pre_bal

                # Account sent SOL (negative balance change)
                if balance_change < -100000:  # > 0.0001 SOL sent
                    # If creator's balance increased, this account funded creator
                    if creator_change > 100000:  # Creator received SOL
                        amount_sol = abs(balance_change) / 1e9
                        inbound[account_addr] = inbound.get(account_addr, 0) + amount_sol

                # Account received SOL (positive balance change)
                if balance_change > 100000:  # > 0.0001 SOL received
                    # If creator's balance decreased, creator sent to this account
                    if creator_change < -100000:  # Creator sent SOL
                        amount_sol = balance_change / 1e9
                        outbound[account_addr] = outbound.get(account_addr, 0) + amount_sol

        except Exception as e:
            print(f"[ERROR] Failed to extract transfers from {signature}: {e}")

        return inbound, outbound

    async def process_creator(self, creator: str, creator_short: str) -> Dict:
        """Process a single creator's SOL transfers"""
        print(f"[EXTRACT] Processing {creator_short}...", flush=True)

        # Get all transactions for this creator
        signatures = await self.get_all_creator_transactions(creator)
        if not signatures:
            print(f"[EXTRACT]   No transactions found", flush=True)
            return {"creator": creator, "inbound": {}, "outbound": {}, "tx_count": 0}

        all_inbound = {}
        all_outbound = {}
        transfer_count = 0

        # Process each transaction
        for sig in signatures:
            inbound, outbound = await self.extract_sol_transfers_from_tx(creator, sig)

            # Aggregate
            for addr, amount in inbound.items():
                all_inbound[addr] = all_inbound.get(addr, 0) + amount
                transfer_count += 1

            for addr, amount in outbound.items():
                all_outbound[addr] = all_outbound.get(addr, 0) + amount
                transfer_count += 1

        # Log summary
        if all_inbound or all_outbound:
            print(f"[EXTRACT]   Transfers found: {transfer_count}", flush=True)

            if all_inbound:
                print(f"[EXTRACT]   INBOUND ({len(all_inbound)} sources):", flush=True)
                for addr, amount in sorted(all_inbound.items(), key=lambda x: x[1], reverse=True)[:3]:
                    addr_short = f"{addr[:8]}...{addr[-4:]}"
                    print(f"[EXTRACT]     ← {addr_short}: {amount:.4f} SOL", flush=True)

            if all_outbound:
                print(f"[EXTRACT]   OUTBOUND ({len(all_outbound)} destinations):", flush=True)
                for addr, amount in sorted(all_outbound.items(), key=lambda x: x[1], reverse=True)[:3]:
                    addr_short = f"{addr[:8]}...{addr[-4:]}"
                    print(f"[EXTRACT]     → {addr_short}: {amount:.4f} SOL", flush=True)

        return {
            "creator": creator,
            "inbound": all_inbound,
            "outbound": all_outbound,
            "tx_count": len(signatures)
        }

    async def process_all_creators(self):
        """Process all token creators"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all unique creators (first account to buy = token creator)
            cursor.execute("SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL")
            all_creators = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"\n[EXTRACT] Processing {len(all_creators)} unique token creators...\n")

            # Process each creator
            for idx, creator in enumerate(all_creators, 1):
                creator_short = f"{creator[:8]}...{creator[-4:]}"
                print(f"[{idx}/{len(all_creators)}]", end=" ")
                result = await self.process_creator(creator, creator_short)

                # Store in database
                await self._store_transfers(creator, result["inbound"], result["outbound"])

                # Track for analysis
                self.inbound_transfers[creator] = result["inbound"]
                self.outbound_transfers[creator] = result["outbound"]

            print(f"\n[EXTRACT] ✅ Extraction complete!")

            # Analyze patterns
            await self._analyze_transfer_patterns()

        except Exception as e:
            print(f"[ERROR] Failed to process creators: {e}")

    async def _store_transfers(self, creator: str, inbound: Dict[str, float], outbound: Dict[str, float]):
        """Store SOL transfers in database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Store inbound transfers (who funded the creator)
            for funder, amount in inbound.items():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO sol_transfers
                    (creator_address, transfer_type, counterparty_address, amount_sol, first_detected_at)
                    VALUES (?, 'INBOUND', ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (creator, funder, amount)
                )

            # Store outbound transfers (where creator sent SOL)
            for recipient, amount in outbound.items():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO sol_transfers
                    (creator_address, transfer_type, counterparty_address, amount_sol, first_detected_at)
                    VALUES (?, 'OUTBOUND', ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (creator, recipient, amount)
                )

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[DB_ERROR] Failed to store transfers: {e}")

    async def _analyze_transfer_patterns(self):
        """Analyze SOL transfer patterns"""
        print(f"\n[ANALYSIS] Analyzing transfer patterns...\n")

        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Find top funding sources (accounts that funded multiple creators)
            print("[ANALYSIS] 💰 TOP FUNDING SOURCES:\n")
            cursor.execute("""
                SELECT counterparty_address, COUNT(DISTINCT creator_address) as creators_funded, SUM(amount_sol) as total_sol
                FROM sol_transfers
                WHERE transfer_type = 'INBOUND'
                GROUP BY counterparty_address
                ORDER BY creators_funded DESC
                LIMIT 10
            """)

            for funder, creator_count, total_sol in cursor.fetchall():
                print(f"  {funder[:40]}...")
                print(f"    Funded: {creator_count} creator(s), Total: {total_sol:.4f} SOL\n")

            # Find top extraction destinations (where creators send SOL - likely treasuries)
            print("[ANALYSIS] 🎯 TOP EXTRACTION DESTINATIONS (likely treasury addresses):\n")
            cursor.execute("""
                SELECT counterparty_address, COUNT(DISTINCT creator_address) as creators_using, SUM(amount_sol) as total_sol
                FROM sol_transfers
                WHERE transfer_type = 'OUTBOUND'
                GROUP BY counterparty_address
                ORDER BY creators_using DESC
                LIMIT 10
            """)

            for recipient, creator_count, total_sol in cursor.fetchall():
                print(f"  {recipient[:40]}...")
                print(f"    Used by: {creator_count} creator(s), Total: {total_sol:.4f} SOL\n")

            conn.close()

        except Exception as e:
            print(f"[ANALYSIS_ERROR] {e}")


async def main():
    print(f"[EXTRACT] Starting comprehensive SOL extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100 + "\n")

    extractor = ComprehensiveSOLExtractor()
    await extractor.process_all_creators()

    print("\n" + "="*100)
    print(f"[EXTRACT] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
