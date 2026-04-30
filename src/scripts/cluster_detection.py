#!/usr/bin/env python3
"""
FLEX Phase 3.3 Cluster Detection — Daily Job

Runs daily at 3 AM UTC (after cleanup at 2 AM) to detect dev farms and
update developer reputation scores.

Exit codes:
- 0: Success or skipped (no errors)
- 1: Error occurred
"""

import sys
import logging
import logging.handlers
import os
from pathlib import Path

# Configure logging (try /var/log/flex first, fall back to local)
try:
    log_dir = Path('/var/log/flex')
    log_dir.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError):
    # Fall back to local logs directory if /var/log not accessible
    log_dir = Path('logs')
    log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            log_dir / 'clustering.log',
            maxBytes=20 * 1024 * 1024,
            backupCount=2,
            encoding='utf-8',
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.wallet_clustering import WalletClusteringEngine


def main():
    """Run cluster detection."""
    # Use Flask database path (database/flex_complete_database.db)
    db_path = 'database/flex_complete_database.db'

    # Fall back to root if database subdir doesn't exist
    if not Path(db_path).exists():
        db_path = 'flex_complete_database.db'

    # Verify database exists
    if not Path(db_path).exists():
        logger.error(f"Database not found at {db_path} or flex_complete_database.db")
        return 1

    try:
        logger.info("Starting wallet clustering job")

        engine = WalletClusteringEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Clustering completed: {result['message']}")
        logger.info(f"Clusters found: {result['clusters_found']}, Reputations updated: {result['reputations_updated']}, Duration: {result['duration_ms']:.0f}ms")

        return 0 if result['status'] == 'success' else 1

    except Exception as e:
        logger.error(f"Clustering job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
