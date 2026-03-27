#!/usr/bin/env python3
"""
Reprocess corrupted vault records.

Identifies records where pool_address = ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw
(the shared PDA that was mistakenly used as pool address) and re-extracts vaults using
the correct authority-based extraction logic.
"""

import asyncio
import sqlite3
import logging
import os
from src.core.pool_discovery import PoolDiscovery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The shared PDA that corrupted 25 records
CORRUPTED_POOL_MARKER = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"

async def main():
    db_path = "database/flex_complete_database.db"
    rpc_url = os.environ.get("HELIUS_RPC_URL") or os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Find corrupted records
    corrupted = conn.execute("""
        SELECT mint, base_account, quote_account, pool_address, discovery_method
        FROM token_pool_accounts
        WHERE pool_address = ? OR base_account = ?
        ORDER BY created_at DESC
    """, (CORRUPTED_POOL_MARKER, CORRUPTED_POOL_MARKER)).fetchall()

    logger.info(f"Found {len(corrupted)} potentially corrupted records")

    if not corrupted:
        logger.info("No corrupted records found. Exiting.")
        conn.close()
        return

    pool_discovery = PoolDiscovery(db_path, rpc_url)

    fixed = 0
    failed = 0

    for row in corrupted:
        mint = row["mint"]
        old_base = row["base_account"]
        old_quote = row["quote_account"]
        old_pool = row["pool_address"]

        logger.info(f"\nReprocessing {mint[:16]}...")
        logger.info(f"  Old pool: {old_pool[:16] if old_pool else 'NULL'}")
        logger.info(f"  Old base: {old_base[:16]}")
        logger.info(f"  Old quote: {old_quote[:16]}")

        # Try to discover the correct pool address from resolution telemetry
        telemetry = conn.execute("""
            SELECT pool_address FROM token_resolution_telemetry WHERE mint = ?
        """, (mint,)).fetchone()

        if not telemetry or not telemetry["pool_address"]:
            logger.warning(f"  ⚠️  No pool_address in telemetry, skipping")
            failed += 1
            continue

        correct_pool = telemetry["pool_address"]

        # Re-extract using correct pool address
        try:
            logger.info(f"  Extracting from correct pool: {correct_pool[:16]}...")
            new_reserves = await pool_discovery.extract_pool_reserves(correct_pool, mint)

            if not new_reserves:
                logger.warning(f"  ❌ Extraction failed")
                failed += 1
                continue

            new_base = new_reserves.get("base_account")
            new_quote = new_reserves.get("quote_account")
            new_authority = new_reserves.get("authority_account")
            new_program = new_reserves.get("pool_program")

            logger.info(f"  ✅ New base: {new_base[:16]}")
            logger.info(f"  ✅ New quote: {new_quote[:16]}")

            # Update database
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE token_pool_accounts
                SET base_account = ?, quote_account = ?, pool_address = ?,
                    authority_account = ?, pool_program = ?,
                    updated_at = ?
                WHERE mint = ?
            """, (
                new_base,
                new_quote,
                correct_pool,
                new_authority,
                new_program,
                int(__import__("time").time()),
                mint,
            ))
            conn.commit()

            fixed += 1
            logger.info(f"  ✅ Record updated")

        except Exception as e:
            logger.error(f"  ❌ Error: {e}")
            failed += 1

    conn.close()

    logger.info(f"\n{'='*60}")
    logger.info(f"Results:")
    logger.info(f"  Fixed: {fixed}")
    logger.info(f"  Failed: {failed}")
    logger.info(f"  Total: {fixed + failed}")
    logger.info(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
