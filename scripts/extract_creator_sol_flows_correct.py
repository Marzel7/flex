#!/usr/bin/env python3
"""
Extract complete SOL in/out history for ALL token creators using correct creator_address.

For each creator (wallet that signed CREATE instruction):
1. Get their transaction history via getSignaturesForAddress
2. Analyze all transactions for SOL transfers
3. Identify inbound sources (funders)
4. Identify outbound destinations (extraction/treasury)
5. Store relationships

This uses the CORRECT creator_address (CREATE instruction signers), not earliest_tx_creator.
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


class CreatorSOLFlowAnalyzer:
    """Analyze complete SOL flows for all creators"""

    def __init__(self):
        self.creators = {}  # {creator_address: {mints: [list]}}
        self.load_creators()

    def load_creators(self):
        """Load all unique creator addresses (correct creator_address field)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT DISTINCT creator_address, GROUP_CONCAT(mint, ',')
                FROM token_analysis
                WHERE creator_address IS NOT NULL AND creator_address != ''
                GROUP BY creator_address
                ORDER BY creator_address
            """
            )
            for creator, mints_str in cursor.fetchall():
                self.creators[creator] = {
                    "mints": mints_str.split(","),
                    "token_count": len(mints_str.split(",")),
                }

            conn.close()
            print(
                f"[INIT] Loaded {len(self.creators)} unique creators\n"
            )
        except Exception as e:
            print(f"[ERROR] Loading creators: {e}")

    async def _post_rpc(
        self, payload: dict, timeout: int = 10
    ) -> Optional[dict]:
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

    async def get_creator_transactions(
        self, creator: str, limit: int = 100
    ) -> List[str]:
        """Get all transactions for a creator"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": limit}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            sigs_data = result.get("result")
            if not sigs_data:
                return []

            return [tx["signature"] for tx in sigs_data if "signature" in tx]
        except Exception as e:
            print(f"[ERROR] Getting transactions for {creator[:8]}...: {e}")
            return []

    async def analyze_creator_sol_flow(
        self, creator: str, signatures: List[str]
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        Analyze all transactions for a creator to find:
        - inbound: who sent SOL TO creator
        - outbound: who creator sent SOL TO
        """
        inbound = {}
        outbound = {}

        for sig in signatures:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed"}],
                }

                result = await self._post_rpc(payload)
                if not result or "result" not in result:
                    continue

                tx = result["result"]
                if not tx or not tx.get("transaction"):
                    continue

                meta = tx.get("meta")
                if not meta:
                    continue

                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

                if not accounts or len(accounts) == 0:
                    continue

                # Find creator's index
                creator_idx = None
                for idx, acc in enumerate(accounts):
                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == creator:
                        creator_idx = idx
                        break

                if creator_idx is None:
                    continue

                if creator_idx >= len(pre_balances) or creator_idx >= len(post_balances):
                    continue

                creator_pre = pre_balances[creator_idx]
                creator_post = post_balances[creator_idx]
                creator_change = creator_post - creator_pre

                # Analyze all account changes
                for idx, acc in enumerate(accounts):
                    if idx >= len(pre_balances) or idx >= len(post_balances):
                        continue

                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == creator:
                        continue

                    pre_bal = pre_balances[idx]
                    post_bal = post_balances[idx]
                    acc_change = post_bal - pre_bal

                    # INBOUND: Creator received SOL
                    if creator_change > 100000 and acc_change < -100000:
                        abs_change = abs(acc_change)
                        if abs_change >= creator_change - 10000:
                            amount_sol = abs_change / 1e9
                            if amount_sol > 0:
                                if acc_addr not in inbound:
                                    inbound[acc_addr] = 0
                                inbound[acc_addr] += amount_sol

                    # OUTBOUND: Creator sent SOL
                    if creator_change < -100000 and acc_change > 100000:
                        amount_sol = acc_change / 1e9
                        if amount_sol > 0:
                            if acc_addr not in outbound:
                                outbound[acc_addr] = 0
                            outbound[acc_addr] += amount_sol

            except Exception as e:
                continue

        return inbound, outbound

    async def process_creator(
        self, creator: str, idx: int, total: int, token_info: dict
    ) -> Dict:
        """Process one creator"""
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        token_count = token_info["token_count"]
        print(
            f"[{idx}/{total}] {creator_short} ({token_count} tokens)",
            end=" ",
            flush=True,
        )

        signatures = await self.get_creator_transactions(creator, limit=100)
        print(f"({len(signatures)} sigs)", end=" ", flush=True)

        inbound, outbound = await self.analyze_creator_sol_flow(creator, signatures)

        inbound_sol = sum(inbound.values())
        outbound_sol = sum(outbound.values())
        net_sol = inbound_sol - outbound_sol

        status = ""
        if inbound_sol > 0 and outbound_sol > 0:
            status = f"→ IN:{inbound_sol:.3f} OUT:{outbound_sol:.3f} NET:{net_sol:+.3f}"
        elif outbound_sol > 0:
            status = f"→ OUT:{outbound_sol:.3f} (EXTRACTION)"
        elif inbound_sol > 0:
            status = f"→ IN:{inbound_sol:.3f} (FUNDED)"
        else:
            status = "→ No transfers"

        print(status, flush=True)

        return {
            "creator": creator,
            "tokens_created": token_count,
            "signatures": len(signatures),
            "inbound": inbound,
            "outbound": outbound,
            "inbound_total": inbound_sol,
            "outbound_total": outbound_sol,
        }

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Complete Creator SOL Flow Analysis at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            creators_with_inbound = 0
            creators_with_outbound = 0
            total_inbound = 0
            total_outbound = 0

            for idx, (creator, info) in enumerate(sorted(self.creators.items()), 1):
                result = await self.process_creator(
                    creator, idx, len(self.creators), info
                )
                results.append(result)

                if result["inbound_total"] > 0:
                    creators_with_inbound += 1
                    total_inbound += result["inbound_total"]

                if result["outbound_total"] > 0:
                    creators_with_outbound += 1
                    total_outbound += result["outbound_total"]

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Total creators analyzed: {len(self.creators)}")
            print(f"  Creators with inbound SOL: {creators_with_inbound}")
            print(f"  Creators with outbound SOL: {creators_with_outbound}")
            print(f"  Total inbound SOL: {total_inbound:.6f}")
            print(f"  Total outbound SOL: {total_outbound:.6f}")

            # Top inbound funders
            if creators_with_inbound > 0:
                print(f"\n[TOP CREATORS BY INBOUND SOL]")
                top_inbound = sorted(
                    [r for r in results if r["inbound_total"] > 0],
                    key=lambda x: x["inbound_total"],
                    reverse=True,
                )[:10]
                for r in top_inbound:
                    creator_short = f"{r['creator'][:8]}...{r['creator'][-4:]}"
                    print(
                        f"  {creator_short} ({r['tokens_created']} tokens): {r['inbound_total']:.6f} SOL from {len(r['inbound'])} source(s)"
                    )

            # Top outbound extractors
            if creators_with_outbound > 0:
                print(f"\n[TOP CREATORS BY OUTBOUND SOL (EXTRACTORS)]")
                top_outbound = sorted(
                    [r for r in results if r["outbound_total"] > 0],
                    key=lambda x: x["outbound_total"],
                    reverse=True,
                )[:10]
                for r in top_outbound:
                    creator_short = f"{r['creator'][:8]}...{r['creator'][-4:]}"
                    print(
                        f"  {creator_short} ({r['tokens_created']} tokens): {r['outbound_total']:.6f} SOL to {len(r['outbound'])} destination(s)"
                    )

            # Store results
            print(f"\n[STORE] Saving detailed results...")
            await self._store_results(results)

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results: List[Dict]):
        """Store analysis results to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create table for summary
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_sol_flow_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT UNIQUE NOT NULL,
                    tokens_created INTEGER,
                    transaction_count INTEGER,
                    inbound_sol REAL,
                    inbound_sources INTEGER,
                    outbound_sol REAL,
                    outbound_destinations INTEGER,
                    net_sol REAL,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            # Store individual sources/destinations
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_inbound_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    source_address TEXT NOT NULL,
                    total_amount_sol REAL,
                    UNIQUE(creator_address, source_address)
                )
            """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_outbound_destinations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    destination_address TEXT NOT NULL,
                    total_amount_sol REAL,
                    UNIQUE(creator_address, destination_address)
                )
            """
            )

            count = 0
            for r in results:
                net_sol = r["inbound_total"] - r["outbound_total"]

                cursor.execute(
                    """INSERT OR REPLACE INTO creator_sol_flow_summary
                       (creator_address, tokens_created, transaction_count, inbound_sol, inbound_sources,
                        outbound_sol, outbound_destinations, net_sol)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r["creator"],
                        r["tokens_created"],
                        r["signatures"],
                        r["inbound_total"],
                        len(r["inbound"]),
                        r["outbound_total"],
                        len(r["outbound"]),
                        net_sol,
                    ),
                )
                count += 1

                # Store inbound sources
                for source, amount in r["inbound"].items():
                    cursor.execute(
                        """INSERT OR REPLACE INTO creator_inbound_sources
                           (creator_address, source_address, total_amount_sol)
                           VALUES (?, ?, ?)""",
                        (r["creator"], source, amount),
                    )

                # Store outbound destinations
                for dest, amount in r["outbound"].items():
                    cursor.execute(
                        """INSERT OR REPLACE INTO creator_outbound_destinations
                           (creator_address, destination_address, total_amount_sol)
                           VALUES (?, ?, ?)""",
                        (r["creator"], dest, amount),
                    )

            conn.commit()
            conn.close()

            print(f"✅ Stored analysis for {count} creators\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    analyzer = CreatorSOLFlowAnalyzer()
    await analyzer.run()

    print("=" * 80)
    print(
        f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
