#!/usr/bin/env python3
"""
Batch Wallet Clustering Script
Runs clustering on recent tokens to identify wallet networks and repeat funders.

Usage:
    python3 batch_wallet_clustering.py --limit 5  # Test on 5 most recent tokens
    python3 batch_wallet_clustering.py --limit 0  # Run on all tokens without clustering

Features:
- Fetches creator transaction history using Solana RPC
- Identifies direct wallet interactions (hop 1)
- Identifies repeat funders (any address funding multiple creators)
- Logs funder patterns for network analysis
"""

import sqlite3
import asyncio
import aiohttp
import sys
from typing import Optional, Dict, List, Set, Tuple
from datetime import datetime
import json

import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "pumpswap_tokens.db"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Use Helius if available (much better transaction history and parsing)
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_RPC = f"https://mainnet.helius-rpc.com?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else None

# Track repeat funders across the entire run
REPEAT_FUNDERS: Dict[str, List[str]] = {}  # {funder_address: [creator1, creator2, ...]}


class BatchWalletClusteringAnalyzer:
    """Analyze wallet interactions for multiple creators"""

    def __init__(self, solana_rpc: str = SOLANA_RPC, helius_rpc: str = None):
        self.solana_rpc = solana_rpc
        self.helius_rpc = helius_rpc or HELIUS_RPC
        self.session = None

    async def init_session(self):
        """Initialize aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()

        print(f"[RPC] Using Solana RPC: {self.solana_rpc}")

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def get_signatures_for_address(
        self, address: str, limit: int = 1000
    ) -> List[Dict]:
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
            print(f"[RPC] Error getting signatures for {address[:8]}...: {e}")

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
            print(f"[RPC] Error getting transaction {sig[:8]}...: {e}")

        return None

    def extract_wallet_interactions(self, tx: Dict, creator: str) -> List[Tuple[str, str, float]]:
        """
        Extract wallet interactions from a transaction.
        Returns list of (wallet_address, interaction_type, confidence) tuples.
        """
        interactions = []

        try:
            if not tx or "meta" not in tx:
                return interactions

            meta = tx.get("meta", {})
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts:
                return interactions

            # Find creator account index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)
                if acc_str == creator:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return interactions

            # Analyze balance changes
            creator_balance_change = (
                post_balances[creator_idx] - pre_balances[creator_idx]
                if creator_idx < len(pre_balances) and creator_idx < len(post_balances)
                else 0
            )

            # Skip if no significant balance change
            if abs(creator_balance_change) < 5000:
                return interactions

            # Look for counterparties
            for idx, acc in enumerate(accounts):
                if idx == creator_idx or idx >= len(pre_balances) or idx >= len(post_balances):
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

                # Check for SOL transfer relationship
                if (
                    abs(creator_balance_change + balance_change) < 10000
                    and creator_balance_change != 0
                ):
                    amount_sol = abs(balance_change) / 1e9

                    interaction_type = "transfer"
                    confidence = 0.7

                    if amount_sol > 10:
                        confidence = 0.85
                    elif amount_sol > 50:
                        confidence = 0.95

                    if creator_balance_change > 0 and balance_change < 0:
                        interaction_type = "consolidation"
                        confidence = min(0.9, confidence + 0.1)

                    interactions.append((acc_str, interaction_type, confidence))
                    break

        except Exception as e:
            pass

        return interactions

    async def analyze_creator(self, creator: str, limit_transactions: int = 500) -> Dict:
        """
        Analyze a single creator's wallet interactions.
        Returns dict with hop counts and wallet info.
        """
        print(f"\n[CLUSTERING] 🔍 Analyzing creator {creator[:12]}...")

        hop1_wallets = {}  # {wallet: (interaction_type, confidence)}

        # Get creator's recent signatures
        signatures = await self.get_signatures_for_address(creator, limit=limit_transactions)
        print(f"[CLUSTERING]    Found {len(signatures)} recent signatures")

        if not signatures:
            print(f"[CLUSTERING] ✓ No recent activity")
            return {
                "creator": creator,
                "hop0": 1,
                "hop1": 0,
                "hop1_wallets": {},
                "status": "no_activity",
            }

        sigs_analyzed = 0
        for sig_info in signatures:
            sig = sig_info.get("signature")
            if not sig:
                continue

            tx = await self.get_transaction(sig)
            if not tx:
                continue

            sigs_analyzed += 1

            # Extract wallet interactions
            interactions = self.extract_wallet_interactions(tx, creator)
            for wallet, interaction_type, confidence in interactions:
                if wallet not in hop1_wallets:
                    hop1_wallets[wallet] = (interaction_type, confidence)
                else:
                    # Take highest confidence
                    prev_type, prev_conf = hop1_wallets[wallet]
                    if confidence > prev_conf:
                        hop1_wallets[wallet] = (interaction_type, confidence)

            # Rate limiting
            if sigs_analyzed % 10 == 0:
                await asyncio.sleep(0.05)

        print(
            f"[CLUSTERING] ✅ Complete: {sigs_analyzed} txs analyzed, {len(hop1_wallets)} hop-1 wallets"
        )

        return {
            "creator": creator,
            "transactions_analyzed": sigs_analyzed,
            "hop0": 1,
            "hop1": len(hop1_wallets),
            "hop1_wallets": hop1_wallets,
            "status": "success",
        }

    def save_clustering_results(self, result: Dict):
        """Save clustering results to database"""
        if result["status"] != "success":
            return

        creator = result["creator"]
        hop1_wallets = result["hop1_wallets"]

        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()

            # Save hop 0 (creator)
            cursor.execute(
                """
                INSERT OR REPLACE INTO wallet_cluster_nodes
                (root_creator, wallet, hop, confidence, tags, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (creator, creator, 0, 1.0, "root"),
            )

            # Save hop 1 wallets
            for wallet, (interaction_type, confidence) in hop1_wallets.items():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO wallet_cluster_nodes
                    (root_creator, wallet, hop, confidence, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (creator, wallet, 1, confidence, interaction_type),
                )

            conn.commit()
            conn.close()
            print(f"[DB] Saved {len(hop1_wallets)} hop-1 wallets for {creator[:12]}...")
        except Exception as e:
            print(f"[DB] Error saving clustering results: {e}")


async def get_recent_tokens(limit: int = 0) -> List[Tuple[str, str]]:
    """
    Get recent tokens from database.
    Returns list of (mint, creator) tuples ordered by creation date (most recent first).
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if limit > 0:
            cursor.execute(
                """
                SELECT mint, earliest_tx_creator
                FROM token_analysis
                WHERE earliest_tx_creator IS NOT NULL
                ORDER BY created_at DESC
                LIMIT ?
            """,
                (limit,),
            )
        else:
            cursor.execute(
                """
                SELECT mint, earliest_tx_creator
                FROM token_analysis
                WHERE earliest_tx_creator IS NOT NULL
                ORDER BY created_at DESC
            """
            )

        tokens = [(row["mint"], row["earliest_tx_creator"]) for row in cursor.fetchall()]
        conn.close()
        return tokens
    except Exception as e:
        print(f"[DB] Error getting recent tokens: {e}")
        return []


def log_repeat_funders():
    """Log all repeat funders found during analysis"""
    print(f"\n{'='*80}")
    print(f"REPEAT FUNDERS SUMMARY")
    print(f"{'='*80}")

    repeat_count = len(REPEAT_FUNDERS)
    if repeat_count == 0:
        print("No repeat funders found in this analysis.")
        return

    # Sort by number of creators funded (descending)
    sorted_funders = sorted(REPEAT_FUNDERS.items(), key=lambda x: len(x[1]), reverse=True)

    print(f"Found {repeat_count} addresses funding multiple creators:\n")

    for funder, creators in sorted_funders:
        print(f"📊 {funder}")
        print(f"   Funds {len(creators)} creators:")
        for creator in creators[:5]:  # Show first 5
            print(f"     • {creator[:12]}...")
        if len(creators) > 5:
            print(f"     ... and {len(creators) - 5} more")
        print()


def find_repeat_funders_in_database() -> Dict[str, List[str]]:
    """
    Find addresses that fund multiple creators (from creator_funders table).
    Returns dict of {funder_address: [creator1, creator2, ...]}
    """
    repeat_funders = {}

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find addresses that fund multiple creators
        cursor.execute(
            """
            SELECT funder_address, creator_address
            FROM creator_funders
            GROUP BY funder_address, creator_address
            ORDER BY funder_address
        """
        )

        rows = cursor.fetchall()

        # Build repeat funder map
        funder_creators = {}
        for row in rows:
            funder = row["funder_address"]
            creator = row["creator_address"]
            if funder not in funder_creators:
                funder_creators[funder] = []
            funder_creators[funder].append(creator)

        # Filter to only repeat funders (funding > 1 creator)
        for funder, creators in funder_creators.items():
            if len(creators) > 1:
                repeat_funders[funder] = creators

        conn.close()
    except Exception as e:
        print(f"[DB] Error finding repeat funders: {e}")

    return repeat_funders


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Batch wallet clustering analysis")
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of recent tokens to analyze (0 = all)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save results to database",
    )
    parser.add_argument(
        "--find-repeat-funders",
        action="store_true",
        help="Only find repeat funders from database (no RPC analysis)",
    )
    args = parser.parse_args()

    print(f"[BATCH] Starting wallet clustering analysis...")
    print(f"[BATCH] Limit: {'All tokens' if args.limit == 0 else f'{args.limit} tokens'}")
    print(f"[BATCH] Save to DB: {args.save}")

    # Option 1: Just find repeat funders from database
    if args.find_repeat_funders:
        print(f"\n[BATCH] Searching database for repeat funders...")
        repeat_funders = find_repeat_funders_in_database()

        print(f"\n{'='*80}")
        print(f"REPEAT FUNDERS FROM DATABASE")
        print(f"{'='*80}")
        print(f"Found {len(repeat_funders)} addresses funding multiple creators:\n")

        sorted_funders = sorted(repeat_funders.items(), key=lambda x: len(x[1]), reverse=True)
        for funder, creators in sorted_funders:
            print(f"📊 {funder}")
            print(f"   Funds {len(creators)} creators:")
            for creator in creators[:10]:
                print(f"     • {creator}")
            if len(creators) > 10:
                print(f"     ... and {len(creators) - 10} more")
            print()
        return

    # Option 2: Run RPC clustering analysis
    # Get recent tokens
    tokens = await get_recent_tokens(limit=args.limit)
    print(f"[BATCH] Found {len(tokens)} tokens to analyze\n")

    if not tokens:
        print("[BATCH] No tokens found!")
        return

    analyzer = BatchWalletClusteringAnalyzer()
    await analyzer.init_session()

    try:
        for mint, creator in tokens:
            if not creator:
                print(f"[BATCH] ⏭️  Skipping {mint[:12]}... (no creator)")
                continue

            result = await analyzer.analyze_creator(creator, limit_transactions=500)

            # Track repeat funders
            for wallet in result["hop1_wallets"].keys():
                if wallet not in REPEAT_FUNDERS:
                    REPEAT_FUNDERS[wallet] = []
                REPEAT_FUNDERS[wallet].append(creator)

            # Save if requested
            if args.save:
                analyzer.save_clustering_results(result)

            # Add spacing between tokens
            await asyncio.sleep(0.5)

    finally:
        await analyzer.close_session()

    # Log repeat funders
    log_repeat_funders()

    # Print summary
    print(f"\n{'='*80}")
    print(f"ANALYSIS COMPLETE")
    print(f"{'='*80}")
    print(f"Tokens analyzed: {len(tokens)}")
    print(f"Repeat funders found: {len(REPEAT_FUNDERS)}")
    print(f"\nRun with --save flag to store results in database")


if __name__ == "__main__":
    asyncio.run(main())
