#!/usr/bin/env python3
"""
Real-time wallet clustering extractor.
Hooks into token migration events to identify creator-related wallet networks.

When a new token is detected as migrated:
  1. Get creator address from transaction
  2. Query creator's recent transaction history
  3. Identify wallets that interact with creator (SOL transfers, token trades)
  4. Calculate hop distance (0=root, 1=direct, 2=secondary)
  5. Assign confidence scores based on interaction patterns
  6. Save wallet cluster nodes to database
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List, Set, Tuple
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

# FluxRPC endpoint for wallet clustering (same as creator extraction)
FLUX_RPC_URL = os.getenv("FLUX_RPC_URL", "").strip()
if not FLUX_RPC_URL:
    FLUX_RPC_URL = "https://eu.fluxrpc.com?key=ca1a8797-c505-4c44-9918-5f832c89e91d"


# Privacy pool addresses - should NOT be included in CEX mapping
# These are mixer/privacy protocols where deposits/withdrawals break identity linkage
PRIVACY_POOL_SET: Set[str] = {
    "4AV2Qzp3N4c9RfzyEbNZs2wqWfW4EwKnnxFAZCndvfGh",  # Privacy Cash Pool
}

# Wallet addresses to exclude from BFS clustering expansion
# These are infrastructure accounts that don't represent actual wallets
NON_CLUSTER_SET: Set[str] = PRIVACY_POOL_SET


class RealtimeWalletClusteringExtractor:
    """Extract and build wallet clusters in real-time when new tokens launch"""

    def __init__(self):
        self.processed_creators: Set[str] = set()
        self.session = None

    async def init_session(self):
        """Initialize aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def _post_rpc(self, payload: dict, timeout: int = 15) -> Optional[dict]:
        """Post to FluxRPC endpoint"""
        try:
            async with self.session.post(
                FLUX_RPC_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Check for RPC error in response body
                    if "error" not in data:
                        return data
        except:
            pass
        return None

    async def get_transaction(self, signature: str) -> Optional[Dict]:
        """Get transaction details from FluxRPC"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        result = await self._post_rpc(payload, timeout=20)
        if result and "result" in result:
            return result.get("result")
        return None

    async def get_signatures_for_address(
        self, address: str, limit: int = 100, before: str = None
    ) -> List[Dict]:
        """Get recent signatures for an address from FluxRPC"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                address,
                {
                    "limit": limit,
                    **({"before": before} if before else {})
                }
            ]
        }
        result = await self._post_rpc(payload, timeout=15)
        if result and "result" in result:
            return result.get("result", [])
        return []

    def extract_wallet_interactions(self, tx: Dict, creator: str) -> List[Tuple[str, str, float]]:
        """
        Extract wallet interactions from a transaction.
        Returns list of (wallet_address, interaction_type, confidence) tuples.

        Interaction types: 'transfer', 'trade', 'consolidation'
        Confidence: 0-1 based on amount and pattern
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

            # Analyze all accounts that changed balance
            creator_balance_change = (
                post_balances[creator_idx] - pre_balances[creator_idx]
                if creator_idx < len(pre_balances) and creator_idx < len(post_balances)
                else 0
            )

            # Skip if no significant balance change for creator
            if abs(creator_balance_change) < 5000:  # < 0.000005 SOL
                return interactions

            # Look for counterparties
            for idx, acc in enumerate(accounts):
                if idx == creator_idx or idx >= len(pre_balances) or idx >= len(post_balances):
                    continue

                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)

                # Skip system/program accounts
                if acc_str in [
                    "11111111111111111111111111111111",  # System program
                    "TokenkegQfeZyiNwAJsyFbPVwwQQQg",     # Token program
                    "ATokenGPvbdGVqstVQmcLsNZAqeEbtQvN",  # ATA program
                ]:
                    continue

                # Skip privacy pools from cluster expansion (but log for behavioral analysis)
                if acc_str in PRIVACY_POOL_SET:
                    print(f"[CLUSTERING] 🔒 Privacy pool interaction detected: {acc_str[:16]}... (not expanding)", flush=True)
                    continue

                balance_change = post_balances[idx] - pre_balances[idx]

                # Check for SOL transfer relationship (opposite balance changes)
                if (
                    abs(creator_balance_change + balance_change) < 10000
                    and creator_balance_change != 0
                ):
                    amount_sol = abs(balance_change) / 1e9

                    # Classify interaction
                    interaction_type = "transfer"
                    confidence = 0.7  # Base confidence for SOL transfer

                    # Higher confidence for larger transfers (likely operational)
                    if amount_sol > 10:
                        confidence = 0.85
                    elif amount_sol > 50:
                        confidence = 0.95

                    # Consolidation pattern: creator receiving SOL back
                    if creator_balance_change > 0 and balance_change < 0:
                        interaction_type = "consolidation"
                        confidence = min(0.9, confidence + 0.1)

                    interactions.append((acc_str, interaction_type, confidence))
                    break  # Only one counterparty per transaction

        except Exception as e:
            pass

        return interactions

    def _save_cluster_node(
        self, root_creator: str, wallet: str, hop: int, confidence: float, tags: str = ""
    ):
        """Save wallet cluster node to database, checking for CEX wallets and privacy pools"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if wallet is a privacy pool (breaks identity linkage)
            if wallet in PRIVACY_POOL_SET:
                privacy_tag = "🔒 Privacy Pool"
                print(f"[CLUSTERING] 🔒 PRIVACY POOL INTERACTION: {wallet[:16]}... connected to {root_creator[:16]}...", flush=True)
                if tags:
                    tags = tags + " | " + privacy_tag
                else:
                    tags = privacy_tag

            # Check if wallet is a known CEX wallet
            cex_tag = None
            try:
                cursor.execute("""
                    SELECT exchange_name, wallet_type
                    FROM cex_wallets
                    WHERE cex_address = ? AND is_active = 1
                    LIMIT 1
                """, (wallet,))
                cex_row = cursor.fetchone()
                if cex_row:
                    exchange, wallet_type = cex_row
                    cex_tag = f"🏛️ {exchange} {wallet_type}"
                    print(f"[CLUSTERING] 🏛️ CEX WALLET IN NETWORK: {exchange} {wallet_type} connected to {root_creator[:16]}...", flush=True)
                    # Append CEX tag to existing tags
                    if tags:
                        tags = tags + " | " + cex_tag
                    else:
                        tags = cex_tag
            except:
                pass

            cursor.execute("""
                INSERT OR REPLACE INTO wallet_cluster_nodes
                (root_creator, wallet, hop, confidence, tags, first_seen_ts, last_seen_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                root_creator,
                wallet,
                hop,
                confidence,
                tags,
                int(datetime.utcnow().timestamp()),
                int(datetime.utcnow().timestamp())
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            pass

    async def build_cluster_for_creator(self, creator: str, limit_transactions: int = 50) -> Dict:
        """
        Build wallet cluster for a creator by analyzing recent transactions.

        Algorithm:
        1. Fetch recent signatures for creator
        2. Parse transactions to find wallet interactions
        3. Classify interactions by type and confidence
        4. Assign hop distances (0=creator, 1=direct, 2+=secondary)
        5. Save to database
        """
        if creator in self.processed_creators:
            return {"status": "already_processed"}

        self.processed_creators.add(creator)

        try:
            print(f"[CLUSTERING] 🔍 Building wallet cluster for {creator}", flush=True)

            # Hop 0: Creator is the root
            self._save_cluster_node(creator, creator, 0, 1.0, "root")
            hop0_count = 1
            hop1_wallets = set()
            hop1_confidence = {}
            hop1_tags = {}

            # Get creator's recent signatures
            signatures = await self.get_signatures_for_address(creator, limit=limit_transactions)
            print(f"[CLUSTERING]    Found {len(signatures)} recent signatures", flush=True)

            if not signatures:
                print(f"[CLUSTERING] ✓ No recent activity", flush=True)
                return {
                    "creator": creator,
                    "hop0": hop0_count,
                    "hop1": 0,
                    "status": "no_activity"
                }

            # Analyze transactions
            sigs_analyzed = 0

            for sig_info in signatures:
                sig = sig_info.get("signature")
                if not sig:
                    continue

                tx = await self.get_transaction(sig)
                if not tx:
                    continue

                sigs_analyzed += 1

                # Extract wallet interactions (hop-1)
                interactions = self.extract_wallet_interactions(tx, creator)
                for wallet, interaction_type, confidence in interactions:
                    hop1_wallets.add(wallet)

                    # Track confidence and tags
                    if wallet not in hop1_confidence:
                        hop1_confidence[wallet] = []
                    hop1_confidence[wallet].append(confidence)

                    if wallet not in hop1_tags:
                        hop1_tags[wallet] = []
                    hop1_tags[wallet].append(interaction_type)

                # Rate limiting
                if sigs_analyzed % 10 == 0:
                    await asyncio.sleep(0.05)

            # Save hop-1 wallets
            hop1_count = 0
            for wallet in hop1_wallets:
                # Average confidence across all transactions
                avg_confidence = sum(hop1_confidence.get(wallet, [0.7])) / max(1, len(hop1_confidence.get(wallet, [0.7])))

                # Most common tag
                tags_list = hop1_tags.get(wallet, ["transfer"])
                tag = max(set(tags_list), key=tags_list.count) if tags_list else "transfer"

                self._save_cluster_node(creator, wallet, 1, avg_confidence, tag)
                hop1_count += 1

            print(f"[CLUSTERING] ✅ Complete: {sigs_analyzed} txs analyzed, {hop1_count} hop-1 wallets", flush=True)

            return {
                "creator": creator,
                "transactions_analyzed": sigs_analyzed,
                "hop0": hop0_count,
                "hop1": hop1_count,
                "status": "success"
            }

        except Exception as e:
            print(f"[CLUSTERING] ⚠ Error: {e}", flush=True)
            return {"creator": creator, "error": str(e)}

    async def process_new_token(self, creator: str) -> Dict:
        """
        Process a newly detected token by building its wallet cluster.
        Call from main listener when migration is detected.
        """
        # Ensure session is initialized
        await self.init_session()

        # Build cluster in background (don't block main listener)
        try:
            result = await self.build_cluster_for_creator(creator)
            return result
        except Exception as e:
            print(f"[CLUSTERING] ⚠ Unexpected error: {e}", flush=True)
            return {"error": str(e)}


# Global instance
_extractor = None


async def get_clustering_extractor() -> RealtimeWalletClusteringExtractor:
    """Get or create global clustering extractor instance"""
    global _extractor
    if not _extractor:
        _extractor = RealtimeWalletClusteringExtractor()
        await _extractor.init_session()
    return _extractor


async def trigger_wallet_clustering(creator: str) -> Dict:
    """
    Public function to trigger wallet clustering when new token detected.

    Call from pumpfun_curve_listener.py after creator extraction:
        await trigger_wallet_clustering(creator_address)
    """
    extractor = await get_clustering_extractor()
    return await extractor.process_new_token(creator)


if __name__ == "__main__":
    # Test with a known creator
    async def test():
        extractor = RealtimeWalletClusteringExtractor()
        await extractor.init_session()

        # Example: Build cluster for a specific creator
        creator = "7Wge3B5EvNWZfvRgCqKwhPhqzRvEfg9TKHL1NXp1Ppse"  # Replace with real creator

        result = await extractor.build_cluster_for_creator(creator)
        print(f"\nResult: {result}")

        await extractor.close_session()

    asyncio.run(test())
