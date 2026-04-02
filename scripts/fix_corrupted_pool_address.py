#!/usr/bin/env python3
"""
Fix corrupted pool_address records.

These 29 records have valid base_account and quote_account (validated vaults)
but have an incorrect pool_address (shared PDA marker). Since we don't have
the original pool addresses in migration transaction data, we clear pool_address
to NULL to allow fresh discovery on next pool query.

The vault accounts are correct and validated, so we preserve them.
"""

import sqlite3
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The shared PDA that corrupted these records
CORRUPTED_POOL_MARKER = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"

def main():
    db_path = "database/flex_complete_database.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find corrupted records
    cursor.execute("""
        SELECT mint, base_account, quote_account, pool_address, vault_validation_status
        FROM token_pool_accounts
        WHERE pool_address = ?
        ORDER BY created_at DESC
    """, (CORRUPTED_POOL_MARKER,))

    corrupted = cursor.fetchall()
    logger.info(f"Found {len(corrupted)} corrupted records with pool_address = {CORRUPTED_POOL_MARKER[:16]}...")

    if not corrupted:
        logger.info("No corrupted records found. Exiting.")
        conn.close()
        return

    # Sample display
    logger.info("\nSample records to fix:")
    for i, (mint, base, quote, pool, status) in enumerate(corrupted[:3]):
        logger.info(f"  {i+1}. {mint[:16]}... → base={base[:16]} quote={quote[:16]} status={status}")

    if len(corrupted) > 3:
        logger.info(f"  ... and {len(corrupted) - 3} more")

    # Fix: Clear the bad pool_address but keep the validated vault accounts
    logger.info(f"\nClearing corrupted pool_address for all {len(corrupted)} records...")

    cursor.execute("""
        UPDATE token_pool_accounts
        SET pool_address = NULL, updated_at = ?
        WHERE pool_address = ?
    """, (int(time.time()), CORRUPTED_POOL_MARKER))

    conn.commit()

    # Verify
    cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE pool_address = ?
    """, (CORRUPTED_POOL_MARKER,))

    remaining = cursor.fetchone()[0]

    if remaining == 0:
        logger.info(f"✅ Fixed: {len(corrupted)} records updated")
        logger.info(f"   - pool_address cleared to NULL")
        logger.info(f"   - vault accounts preserved (validated)")
        logger.info(f"   - authority_account will be populated on next fresh discovery")
    else:
        logger.error(f"❌ Failed: {remaining} records still have corrupted pool_address")

    conn.close()

if __name__ == "__main__":
    main()
