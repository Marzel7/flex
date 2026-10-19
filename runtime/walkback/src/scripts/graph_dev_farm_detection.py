#!/usr/bin/env python3
"""
Graph-Based Dev Farm Detection — Daily Job

Runs at 4:30 AM UTC (after Phase 4 advanced farm intelligence at 4:00 AM).

Detects dev farm ecosystems using network graph clustering on transfer_index.
Identifies clusters with 2+ funders and 3+ creators as potential dev farms.

Exit codes:
- 0: Success or skipped (no errors)
- 1: Error occurred
"""

import sys
import logging
from pathlib import Path

# Configure logging (try /var/log/flex first, fall back to local)
try:
    log_dir = Path('/var/log/flex')
    log_dir.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError):
    log_dir = Path('logs')
    log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'graph_dev_farm_detection.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.graph_dev_farm_detection import GraphDevFarmDetectionEngine


def main():
    """Run graph-based dev farm detection."""
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
        logger.info("Starting graph-based dev farm detection")

        engine = GraphDevFarmDetectionEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Graph detection completed: {result['message']}")
        logger.info(
            f"Clusters: {result['clusters_detected']}, "
            f"Farms: {result['farms_identified']}, "
            f"Members: {result['farm_members_stored']}, "
            f"Edges: {result['farm_edges_stored']}, "
            f"Duration: {result['duration_ms']:.0f}ms"
        )

        return 0 if result['status'] == 'success' else 1

    except Exception as e:
        logger.error(f"Graph detection job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
