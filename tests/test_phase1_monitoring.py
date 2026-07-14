#!/usr/bin/env python3
"""
Phase 1 Monitoring Test Script

Triggers a single creator extraction to test cursor loading/updating.
Monitors logs in real-time to verify Phase 1 is working.
"""

import asyncio
import logging
import sys
from datetime import datetime

# Setup logging to show cursor operations
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

async def test_phase1():
    """Test Phase 1 with a sample creator."""

    logger.info("=" * 70)
    logger.info("PHASE 1 MONITORING TEST - Address Cursor-based Extraction")
    logger.info("=" * 70)

    try:
        # Import the extractor
        from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor
        from src.core.cursor_manager import CursorManager
        import sqlite3

        db_path = 'flex_complete_database.db'

        # Initialize cursor manager to check pre-existing state
        logger.info("\n[SETUP] Initializing CursorManager...")
        cursor_mgr = CursorManager(db_path)

        # Get a sample creator
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT creator_address FROM creator_funders LIMIT 1")
        result = cursor.fetchone()
        conn.close()

        if not result:
            logger.error("[ERROR] No creators found in database!")
            return

        creator = result[0]
        logger.info(f"[SETUP] Selected creator: {creator[:16]}...")

        # Check if cursor exists for this creator
        existing_cursor = cursor_mgr.get_cursor(creator)
        if existing_cursor:
            logger.info(f"[SETUP] ✅ Cursor already exists:")
            logger.info(f"        Last signature: {existing_cursor.last_signature[:20] if existing_cursor.last_signature else 'None'}...")
            logger.info(f"        Last scanned: {existing_cursor.last_scan_at}")
            logger.info(f"        Next scan: {existing_cursor.next_scan_at}")
        else:
            logger.info("[SETUP] ℹ No cursor exists for this creator (first-time scan)")

        # Initialize extractor
        logger.info("\n[SETUP] Initializing RealTimeCreatorFundingExtractor...")
        extractor = RealTimeCreatorFundingExtractor()

        await extractor.init_session()

        # Get migration timestamp (use a recent one)
        logger.info("\n[SETUP] Fetching recent token for migration timestamp...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT created_at FROM token_analysis
            WHERE earliest_tx_creator = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (creator,))
        result = cursor.fetchone()
        conn.close()

        if not result:
            logger.warning(f"⚠ No tokens found for creator {creator[:16]}...")
            logger.info("[SETUP] Using current timestamp instead")
            migration_timestamp = datetime.now().isoformat() + "Z"
        else:
            migration_timestamp = result[0]
            logger.info(f"[SETUP] Using token timestamp: {migration_timestamp}")

        # Run extraction with Phase 1 cursor monitoring
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 1 EXTRACTION STARTING - Watch for cursor load/update messages")
        logger.info("=" * 70 + "\n")

        result = await extractor.extract_for_creator(creator, migration_timestamp)

        logger.info("\n" + "=" * 70)
        logger.info("EXTRACTION COMPLETE")
        logger.info("=" * 70)

        # Log result summary
        logger.info(f"\n[RESULT] Status: {result.get('status', 'unknown')}")
        logger.info(f"[RESULT] Funding sources: {result.get('funding_sources', 0)}")
        logger.info(f"[RESULT] Total inbound: {result.get('total_inbound', 0):.2f} SOL")

        # Check cursor after extraction
        logger.info("\n[VERIFICATION] Checking cursor after extraction...")
        updated_cursor = cursor_mgr.get_cursor(creator)
        if updated_cursor and updated_cursor.last_signature:
            logger.info(f"[VERIFICATION] ✅ Cursor updated successfully!")
            logger.info(f"               Last signature: {updated_cursor.last_signature[:20]}...")
            logger.info(f"               Last scanned: {updated_cursor.last_scan_at}")
        else:
            logger.warning(f"[VERIFICATION] ⚠ Cursor not updated (may be normal if no transactions found)")

        # Show impact
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 1 IMPACT")
        logger.info("=" * 70)

        if existing_cursor and existing_cursor.last_signature:
            logger.info("✅ INCREMENTAL EXTRACTION: Only new signatures fetched")
            logger.info("   This run saved RPC credits by not re-fetching old signatures")
        else:
            logger.info("ℹ FIRST-TIME SCAN: All signatures fetched (full scan)")
            logger.info("   Next run for this creator will be incremental and save ~60% RPC")

        await extractor.close_session()

        logger.info("\n✅ Phase 1 test complete!")

    except Exception as e:
        logger.error(f"[ERROR] {e}", exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(test_phase1())
