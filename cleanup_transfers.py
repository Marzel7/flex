#!/usr/bin/env python3
"""
Cron job for daily transfer index cleanup.

This is a standalone script meant to be executed by cron to clean up old
transfers from the transfer_index table, keeping hot storage bounded to
the retention window (default 90 days).

Schedule in crontab with:
    0 2 * * * /usr/bin/python3 /path/to/cleanup_transfers.py

This runs daily at 2 AM UTC, when traffic is minimal.
"""

import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/flex/cleanup.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Import the cleanup class
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
from src.core.storage_cleanup import TransferIndexCleanup


def main():
    """Run cleanup job."""
    db_path = '/Users/kevinkeaveney/Dev/claude/flex/flex_complete_database.db'

    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    logger.info("Starting transfer index cleanup job")

    cleanup = TransferIndexCleanup(db_path, retention_days=90)
    result = cleanup.cleanup_old_transfers()

    if result['status'] == 'success':
        logger.info(f"✓ Cleanup successful: {result['message']}")
        logger.info(f"  Duration: {result['duration_ms']:.0f}ms")
        logger.info(f"  DB size: {result['db_size_before']/(1024**3):.1f}GB → {result['db_size_after']/(1024**3):.1f}GB")
        sys.exit(0)
    elif result['status'] == 'skipped':
        logger.warning(f"⊘ Cleanup skipped: {result['message']}")
        sys.exit(0)
    elif result['status'] == 'dry_run':
        logger.info(f"📋 Dry run: {result['message']}")
        sys.exit(0)
    else:
        logger.error(f"✗ Cleanup failed: {result['message']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
