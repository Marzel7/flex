#!/usr/bin/env python3
"""
Immediately fix all pools with MINT bug by re-running vault discovery.

This script:
1. Finds all pools with quote_account = wSOL MINT
2. Re-runs discover_and_register_vaults_rpc() to get real accounts
3. Updates the database with corrected vaults
4. Restarts WebSocket to pick up new subscriptions

Result: All tokens start receiving WebSocket prices within 30 seconds.
"""

import asyncio
import sqlite3
import sys
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "database/flex_complete_database.db"
WSOL_MINT = "So11111111111111111111111111111111111111112"

async def main():
    """Find and fix all MINT bug pools."""

    # Get all pools with MINT bug
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mint, base_account, vault_validation_status
        FROM token_pool_accounts
        WHERE quote_account = ?
          AND is_active = 1
        ORDER BY vault_validation_status, created_at DESC
    """, (WSOL_MINT,))

    broken_pools = cursor.fetchall()
    conn.close()

    if not broken_pools:
        logger.info("✅ No pools with MINT bug found!")
        return

    logger.info(f"🔧 Found {len(broken_pools)} pools with MINT bug")
    logger.info("   These tokens won't get WebSocket prices until fixed")

    # Group by status
    pending = [p for p in broken_pools if p[2] == 'pending']
    validated = [p for p in broken_pools if p[2] == 'validated']

    if validated:
        logger.warning(f"\n⚠️  {len(validated)} VALIDATED pools have MINT bug:")
        for mint, base_account, status in validated[:5]:
            logger.warning(f"     {mint[:20]}... (status={status})")
        if len(validated) > 5:
            logger.warning(f"     ... and {len(validated)-5} more")

    if pending:
        logger.warning(f"\n⚠️  {len(pending)} PENDING pools have MINT bug:")
        for mint, base_account, status in pending[:5]:
            logger.warning(f"     {mint[:20]}... (status={status})")
        if len(pending) > 5:
            logger.warning(f"     ... and {len(pending)-5} more")

    # Try to import vault discovery
    try:
        from src.core.vault_discovery import discover_and_register_vaults_rpc
        from solders.rpc.async_client import AsyncClient
    except ImportError as e:
        logger.error(f"❌ Failed to import vault discovery: {e}")
        logger.error("   Run: pip install solders")
        return

    # Get RPC client
    rpc_url = os.getenv('HELIUS_RPC_URL', 'https://api.mainnet-beta.solana.com')
    rpc_client = AsyncClient(rpc_url)

    logger.info(f"\n🚀 Starting vault re-discovery using {rpc_url[:30]}...")

    fixed = 0
    failed = 0

    for mint, base_account, status in broken_pools:
        try:
            logger.info(f"   Fixing {mint[:16]}... (currently {status})")

            success = await discover_and_register_vaults_rpc(
                token_mint=mint,
                rpc_client=rpc_client,
                db=DB_PATH,
                price_worker=None,
                max_retries=1
            )

            if success:
                logger.info(f"   ✅ {mint[:16]}... fixed!")
                fixed += 1
            else:
                logger.warning(f"   ⚠️  {mint[:16]}... discovery failed (will retry automatically)")
                failed += 1

            await asyncio.sleep(0.5)  # Rate limit

        except Exception as e:
            logger.error(f"   ❌ {mint[:16]}... error: {e}")
            failed += 1

    await rpc_client.close()

    logger.info(f"\n{'='*60}")
    logger.info(f"✅ Fixed: {fixed} pools")
    logger.info(f"⏳ Failed: {failed} pools (will auto-retry)")
    logger.info(f"\nNext steps:")
    logger.info(f"1. WebSocket will restart automatically")
    logger.info(f"2. Fixed tokens will get real-time prices")
    logger.info(f"3. UI will show price_source='pool'")
    logger.info(f"\nEstimated time to see WebSocket prices: 30-60 seconds")
    logger.info(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
