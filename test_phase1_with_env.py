#!/usr/bin/env python3
"""
Phase 1 Test with Environment Variables Loaded

Loads .env from config/ folder and runs a sample extraction.
"""

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from config folder
env_path = Path(__file__).parent / "config" / ".env"
print(f"Loading environment from: {env_path}")
load_dotenv(env_path, override=True)

print(f"✅ HELIUS_API_KEY set: {'HELIUS_API_KEY' in os.environ}")
print(f"✅ HELIUS_MONITORING_API_KEY set: {'HELIUS_MONITORING_API_KEY' in os.environ}")

import logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def test_phase1_with_cursor():
    """Test Phase 1 extraction with cursor monitoring."""

    logger.info("=" * 80)
    logger.info("PHASE 1 EXTRACTION TEST - With Cursor Monitoring")
    logger.info("=" * 80)

    try:
        from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor
        from src.core.cursor_manager import CursorManager
        import sqlite3

        db_path = 'flex_complete_database.db'

        # Get sample creator
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT creator_address FROM creator_funders LIMIT 1")
        result = cursor.fetchone()
        conn.close()

        if not result:
            logger.error("No creators found")
            return

        creator = result[0]
        logger.info(f"\n[SETUP] Creator: {creator[:20]}...")

        # Initialize components
        cursor_mgr = CursorManager(db_path)
        extractor = RealTimeCreatorFundingExtractor()
        await extractor.init_session()

        # Get migration timestamp
        migration_timestamp = "2026-03-10T00:00:00Z"

        logger.info("\n[EXTRACTION] Starting Phase 1 test...")
        logger.info("Watch for: ✅ Loaded cursor / ℹ No cursor found / ✅ Updated cursor")

        result = await extractor.extract_for_creator(creator, migration_timestamp)

        logger.info(f"\n[RESULT] Status: {result.get('status')}")
        logger.info(f"[RESULT] Funders found: {result.get('funding_sources', 0)}")

        # Check cursor state
        cursor_state = cursor_mgr.get_cursor(creator)
        if cursor_state:
            logger.info(f"\n[CURSOR] State after extraction:")
            logger.info(f"  Last signature: {cursor_state.last_signature[:30] if cursor_state.last_signature else 'None'}...")
            logger.info(f"  Last scanned: {cursor_state.last_scan_at}")
        else:
            logger.info(f"\n[CURSOR] No cursor found (may not have transactions)")

        await extractor.close_session()
        logger.info("\n✅ Test complete!")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(test_phase1_with_cursor())
