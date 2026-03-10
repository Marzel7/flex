#!/usr/bin/env python3
"""
FLEX Phase 3.3+ Launch Prediction Detection — Daily Job

Runs daily at 3:30 AM UTC (after Phase 3.3 detection at 3 AM) to detect
pump.fun farms, creator reuse patterns, and update launch watchlist.

Requires Phase 3.3 tables to exist:
- wallet_clusters (from Phase 3.3)
- dev_reputation (from Phase 3.3)

Creates/updates Phase 3.3+ tables:
- creator_reuse
- launch_watchlist
- launch_detection_history

Exit codes:
- 0: Success or skipped (no errors)
- 1: Error occurred
"""

import sys
import logging
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
        logging.FileHandler(log_dir / 'launch_prediction.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.launch_prediction_engine import LaunchPredictionEngine


def main():
    """Run launch prediction detection."""
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
        logger.info("Starting Phase 3.3+ launch prediction detection")

        engine = LaunchPredictionEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Launch prediction completed: {result['message']}")
        logger.info(
            f"Pump.fun farms: {result['pumpfun_farms']}, "
            f"Creator reuses: {result['creator_reuses']}, "
            f"Watchlist: {result['launch_watchlist']}, "
            f"Duration: {result['duration_ms']:.0f}ms"
        )

        return 0 if result['status'] == 'success' else 1

    except Exception as e:
        logger.error(f"Launch prediction job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
