#!/usr/bin/env python3
"""
Extract and save funder outgoing transfers to database.

For each funder for a creator:
1. Get recent transaction signatures via Solana RPC
2. Parse SOL transfers (OUT flows)
3. Identify recipient addresses
4. Classify recipients (CEX, INFRA, unknown)
5. Save to funder_outgoing_transfers table
"""

import sqlite3
import asyncio
import aiohttp
from typing import Dict, List, Tuple
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
        print(f"[DB] Error getting funders: {e}")
        return []


def classify_recipient(recipient_address: str) -> Tuple[str, str]:
    """Classify a recipient address and return (classification, label)"""

    # Check CEX first
    cex_info = get_cex_info(recipient_address)
    if cex_info:
        return ("cex", cex_info.get('name', 'CEX'))

    # Check infrastructure
    infra_info = get_account_info(recipient_address)
    if infra_info:
        return ("infra", infra_info.get('name', 'Infrastructure'))

    # Check PumpFun creators
    pumpfun_info = get_pumpfun_creator_info(recipient_address)
    if pumpfun_info:
        return ("pumpfun", pumpfun_info.get('name', 'Creator'))

    # Check suspicious
    suspicious_info = get_suspicious_wallet_info(recipient_address)
    if suspicious_info:
        return ("suspicious", suspicious_info.get('name', 'Suspicious'))

    return ("unknown", "Unknown Wallet")


def save_funder_transfer(funder_address: str, recipient_address: str, amount_sol: float,
                        tx_signature: str, block_time: int):
    """Save a funder transfer to database (store CEX/INFRA but mark as terminal)"""
    try:
        # Classify recipient
        recipient_type, recipient_label = classify_recipient(recipient_address)

        # Check if recipient is CEX or INFRA (mark for display but don't trace through)
        is_cex = 1 if recipient_type == "cex" else 0
        cex_exchange = None
        cex_type = None

        if is_cex:
            cex_info = get_cex_info(recipient_address)
            if cex_info:
                cex_exchange = cex_info.get('exchange', cex_info.get('name'))
                cex_type = cex_info.get('cex_type', cex_info.get('category'))

        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Insert or update
        cursor.execute(
            """
            INSERT OR REPLACE INTO funder_outgoing_transfers
            (funder_address, recipient_address, amount_sol, transaction_signature,
             block_time, recipient_type, is_cex, cex_exchange, cex_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (funder_address, recipient_address, amount_sol, tx_signature,
             block_time, recipient_type, is_cex, cex_exchange, cex_type),
        )

        conn.commit()
        conn.close()

        # Show marker for terminal accounts (don't trace further)
        marker = "🚫" if recipient_type in ("cex", "infra") else "✅"
        print(f"[DB] {marker} Saved transfer: {funder_address[:16]}... → {recipient_address[:16]}... | {amount_sol:.4f} SOL ({recipient_type})")
        return True
    except Exception as e:
        print(f"[DB] Error saving transfer: {e}")
        return False


class FunderOutgoingExtractor:
    """Extract funder outgoing transfers via RPC"""

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

    async def get_signatures_for_address(self, address: str, limit: int = 1000) -> List[Dict]:
        """Get ALL signatures for an address using Solana RPC (paginated)"""
        all_signatures = []
        before = None

        try:
            while len(all_signatures) < limit:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [
                        address,
                        {"limit": 100, "before": before},
                    ],
                }

                async with self.session.post(self.solana_rpc, json=payload, timeout=15) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if "result" in result:
                            sigs = result["result"]
                            if not sigs:
                                break  # No more signatures
                            all_signatures.extend(sigs)
                            before = sigs[-1].get("signature")  # Paginate
                        else:
                            break
                    else:
                        break
        except Exception as e:
            print(f"[RPC] Error getting signatures for {address[:8]}...: {e}")

        return all_signatures[:limit]

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

    async def extract_funder_outflows(self, funder_address: str, limit: int = 100) -> Dict[str, float]:
        """Extract SOL transfers FROM this funder to other addresses"""
        transfers = {}

        try:
            signatures = await self.get_signatures_for_address(funder_address, limit)

            for sig_info in signatures:
                signature = sig_info.get("signature")
                block_time = sig_info.get("blockTime")
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

                # Find funder's index
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
                    funder_pre = pre_balances[funder_index]
                    funder_post = post_balances[funder_index]

                    # Track ANY SOL movement involving this funder in ANY direction
                    for idx, key in enumerate(account_keys):
                        if idx == funder_index:
                            continue

                        if isinstance(key, dict):
                            other_addr = key.get("pubkey", "")
                        else:
                            other_addr = str(key)

                        if idx < len(pre_balances) and idx < len(post_balances):
                            other_pre = pre_balances[idx]
                            other_post = post_balances[idx]

                            # Case 1: Funder SENT SOL (funder balance decreased, other increased)
                            if funder_pre > funder_post and other_post > other_pre:
                                amount = (other_post - other_pre) / 1e9
                                if amount > 0.001:  # Ignore dust
                                    save_funder_transfer(funder_address, other_addr, amount,
                                                       signature, block_time)
                                    if other_addr not in transfers:
                                        transfers[other_addr] = 0
                                    transfers[other_addr] += amount

                            # Case 2: Funder RECEIVED SOL (funder balance increased, other decreased)
                            elif other_pre > other_post and funder_post > funder_pre:
                                amount = (funder_post - funder_pre) / 1e9
                                if amount > 0.001:  # Ignore dust
                                    save_funder_transfer(funder_address, other_addr, amount,
                                                       signature, block_time)
                                    if other_addr not in transfers:
                                        transfers[other_addr] = 0
                                    transfers[other_addr] += amount

        except Exception as e:
            pass

        return transfers


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Extract and save funder outgoing transfers")
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

    print(f"[EXTRACTION] Funder Outgoing Transfer Extraction")
    print(f"[EXTRACTION] Creator: {creator}\n")

    # Get all funders for this creator
    funders = get_creator_funders(creator)
    if not funders:
        print(f"[DB] ❌ No funders found for this creator")
        return

    print(f"[DB] ✅ Found {len(funders)} total funders\n")

    # Determine how many funders to analyze
    analyze_count = len(funders) if args.all else min(args.limit, len(funders))
    if args.all:
        print(f"[EXTRACTION] Extracting outflows for ALL {len(funders)} funders:\n")
    else:
        print(f"[EXTRACTION] Extracting outflows for top {analyze_count} funders:\n")

    extractor = FunderOutgoingExtractor(solana_rpc=SOLANA_RPC)
    await extractor.init_session()

    total_transfers_saved = 0
    funder_stats = {}

    try:
        for i, (funder_address, amount_to_creator) in enumerate(funders[:analyze_count], 1):
            # Classify funder
            funder_type = ""
            cex_info = get_cex_info(funder_address)
            if cex_info:
                funder_type = f"✅ CEX: {cex_info.get('name', 'CEX')}"
            else:
                infra_info = get_account_info(funder_address)
                if infra_info:
                    funder_type = f"✅ INFRA: {infra_info.get('name', 'Infrastructure')}"
                else:
                    pumpfun_info = get_pumpfun_creator_info(funder_address)
                    if pumpfun_info:
                        funder_type = f"🎯 PUMPFUN: {pumpfun_info.get('name', 'Creator')}"

            print(f"[{i}/{analyze_count}] Funder: {funder_address[:12]}... ({amount_to_creator:.2f} SOL to creator)")
            if funder_type:
                print(f"      Type: {funder_type}")

            # Extract outflows for this funder (request more signatures for better coverage)
            transfers = await extractor.extract_funder_outflows(funder_address, limit=500)

            if transfers:
                print(f"      📤 Extracted {len(transfers)} recipient addresses")
                total_transfers_saved += len(transfers)

                # Show top 5 recipients
                sorted_transfers = sorted(transfers.items(), key=lambda x: x[1], reverse=True)
                for j, (dest_address, sol_amount) in enumerate(sorted_transfers[:5], 1):
                    # Classify recipient
                    recipient_type = ""
                    dest_cex = get_cex_info(dest_address)
                    if dest_cex:
                        recipient_type = f"[✅ CEX: {dest_cex.get('name')}]"
                    else:
                        dest_infra = get_account_info(dest_address)
                        if dest_infra:
                            recipient_type = f"[✅ INFRA: {dest_infra.get('name')}]"

                    print(f"         {j}. {dest_address[:12]}... → {sol_amount:.3f} SOL {recipient_type}")

                if len(sorted_transfers) > 5:
                    remaining_sol = sum(amt for _, amt in sorted_transfers[5:])
                    print(f"         ... and {len(sorted_transfers) - 5} more addresses ({remaining_sol:.2f} SOL total)")

                funder_stats[funder_address] = len(transfers)
            else:
                print(f"      ℹ️  No recent outflows detected")

    finally:
        await extractor.close_session()

    # Summary
    print(f"\n{'='*100}")
    print(f"EXTRACTION SUMMARY - FUNDER OUTFLOWS")
    print(f"{'='*100}")
    print(f"Funders analyzed: {analyze_count}")
    print(f"Total recipient addresses saved: {total_transfers_saved}")

    if funder_stats:
        avg_recipients = total_transfers_saved / len(funder_stats)
        print(f"Average recipients per funder: {avg_recipients:.1f}")

    print(f"\n✅ All transfers saved to funder_outgoing_transfers table")
    print(f"   Use fast DB queries instead of RPC next time!")


if __name__ == "__main__":
    asyncio.run(main())
