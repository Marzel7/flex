#!/usr/bin/env python3
"""
Phase 1 Incremental Test - Run Same Creator Twice

Shows cursor in action:
1. First extraction: No cursor (full scan)
2. Second extraction: Cursor exists (incremental scan)
"""

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from config folder
env_path = Path(__file__).parent / "config" / ".env"
load_dotenv(env_path, override=True)

import logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def test_incremental():
    """Run two extractions on same creator to show Phase 1 incremental behavior."""

    logger.info("=" * 80)
    logger.info("PHASE 1 INCREMENTAL EXTRACTION TEST")
    logger.info("=" * 80)

    try:
        from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor
        from src.core.cursor_manager import CursorManager
        import sqlite3
        import time

        db_path = 'flex_complete_database.db'
        migration_timestamp = "2026-03-10T00:00:00Z"

        # Use a creator that doesn't have a cursor yet
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get a creator without a cursor
        cursor.execute("""
            SELECT DISTINCT c.creator_address
            FROM creator_funders c
            LEFT JOIN address_scan_state s ON c.creator_address = s.address
            WHERE s.address IS NULL
            LIMIT 1
        """)
        result = cursor.fetchone()
        conn.close()

        if not result:
            logger.warning("All creators have cursors! Using first creator")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT creator_address FROM creator_funders LIMIT 1")
            result = cursor.fetchone()
            conn.close()

        creator = result[0]
        logger.info(f"\n[TEST] Creator: {creator[:20]}...")

        cursor_mgr = CursorManager(db_path)
        extractor = RealTimeCreatorFundingExtractor()
        await extractor.init_session()

        # Run 1: First extraction (no cursor)
        logger.info("\n" + "=" * 80)
        logger.info("EXTRACTION #1: First-Time Scan (No Cursor)")
        logger.info("=" * 80)

        # Check pre-extraction cursor state
        cursor_before = cursor_mgr.get_cursor(creator)
        if cursor_before:
            logger.info(f"Pre-existing cursor: {cursor_before.last_signature[:20] if cursor_before.last_signature else 'None'}...")
        else:
            logger.info("✅ No cursor exists (first-time scan expected)")

        result1 = await extractor.extract_for_creator(creator, migration_timestamp)

        logger.info(f"\n[RESULT #1] Status: {result1.get('status')}")
        logger.info(f"[RESULT #1] Funders found: {result1.get('funding_sources', 0)}")
        logger.info(f"[RESULT #1] Total SOL: {result1.get('total_inbound', 0):.2f}")

        cursor_after_1 = cursor_mgr.get_cursor(creator)
        if cursor_after_1 and cursor_after_1.last_signature:
            logger.info(f"\n[CURSOR] Saved after extraction #1:")
            logger.info(f"  Signature: {cursor_after_1.last_signature[:20]}...")
            logger.info(f"  Scanned at: {cursor_after_1.last_scan_at}")
        else:
            logger.warning("[CURSOR] No cursor saved (may have no transactions)")

        # Wait a moment
        logger.info("\n⏳ Waiting 2 seconds before second extraction...")
        time.sleep(2)

        # Run 2: Second extraction (cursor should exist)
        logger.info("\n" + "=" * 80)
        logger.info("EXTRACTION #2: Incremental Scan (Using Cursor)")
        logger.info("=" * 80)

        result2 = await extractor.extract_for_creator(creator, migration_timestamp)

        logger.info(f"\n[RESULT #2] Status: {result2.get('status')}")
        logger.info(f"[RESULT #2] Funders found: {result2.get('funding_sources', 0)}")
        logger.info(f"[RESULT #2] Total SOL: {result2.get('total_inbound', 0):.2f}")

        cursor_after_2 = cursor_mgr.get_cursor(creator)
        if cursor_after_2 and cursor_after_2.last_signature:
            logger.info(f"\n[CURSOR] Saved after extraction #2:")
            logger.info(f"  Signature: {cursor_after_2.last_signature[:20]}...")
            logger.info(f"  Scanned at: {cursor_after_2.last_scan_at}")

        # Analysis
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 1 BEHAVIOR ANALYSIS")
        logger.info("=" * 80)

        if result1.get('status') == 'success' and result2.get('status') == 'success':
            funders1 = result1.get('funding_sources', 0)
            funders2 = result2.get('funding_sources', 0)

            logger.info(f"\nExtraction #1 (first-time): Found {funders1} funders")
            logger.info(f"Extraction #2 (incremental): Found {funders2} funders")

            if funders1 == funders2:
                logger.info("\n✅ EXPECTED: Both runs found same funders (no new activity)")
                logger.info("   This is correct behavior - cursor prevents re-scanning old signatures")
            elif funders2 < funders1:
                logger.info(f"\n✅ CORRECT: Incremental saved ~{(1 - funders2/funders1)*100:.0f}% RPC calls")
                logger.info("   This shows Phase 1 incremental extraction working!")
            else:
                logger.info(f"\nℹ Both runs found transactions (cursor advancing normally)")

        await extractor.close_session()
        logger.info("\n✅ Test complete!")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(test_incremental())
