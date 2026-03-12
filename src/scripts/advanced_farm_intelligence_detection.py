#!/usr/bin/env python3
"""
FLEX Phase 4 Advanced Farm Intelligence Detection — Daily Job

Runs daily at 4:00 AM UTC (after Phase 3.3+ launch prediction at 3:30 AM) to detect
ecosystems (2+ funders sharing creators), launch waves (coordinated 1-hour bursts),
and pump.fun patterns.

Requires Phase 3.3+ tables to exist:
- creator_reuse (from Phase 3.3+)
- launch_watchlist (from Phase 3.3+)

Requires Phase 3.3 tables (if ecosystem linking):
- wallet_clusters (from Phase 3.3)

Creates/updates Phase 4 tables:
- dev_farm_ecosystems
- launch_waves
- ecosystem_member_tracking
- launch_wave_creators
- ecosystem_evolution_log

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
        logging.FileHandler(log_dir / 'advanced_farm_intelligence.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.advanced_farm_intelligence_engine import AdvancedFarmIntelligenceEngine


def main():
    """Run advanced farm intelligence detection."""
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
        logger.info("Starting Phase 4 advanced farm intelligence detection")

        engine = AdvancedFarmIntelligenceEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Advanced farm intelligence completed: {result['message']}")
        logger.info(
            f"Ecosystems: {result.get('ecosystems_found', 0)}, "
            f"Launch waves: {result.get('launch_waves_found', 0)}, "
            f"Members tracked: {result.get('ecosystem_members_tracked', 0)}, "
            f"Wave creators: {result.get('wave_creators_linked', 0)}, "
            f"Duration: {result.get('duration_ms', 0):.0f}ms"
        )

        return 0 if result['status'] == 'success' else 1

    except Exception as e:
        logger.error(f"Advanced farm intelligence job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
