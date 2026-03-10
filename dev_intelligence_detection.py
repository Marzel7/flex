#!/usr/bin/env python3
"""
FLEX Dev Intelligence Graph Detection — Daily Job

Runs daily at 5:00 AM UTC (after graph detection at 4:30 AM) to detect
developer organizations spanning wallet → creator → token relationships.

Detects multi-layer developer organizations and computes organization scores.

Exit codes:
- 0: Success
- 1: Error
"""

import sys
import logging
from pathlib import Path

# Configure logging (try /var/log/flex first, fall back to local logs)
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
        logging.FileHandler(log_dir / 'dev_intelligence.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

from src.core.dev_intelligence_graph import DevIntelligenceEngine


def main():
    """Main entry point for daily detection job."""
    # Resolve database path (primary: database/flex_complete_database.db, fallback: flex_complete_database.db)
    db_path = 'database/flex_complete_database.db'
    if not Path(db_path).exists():
        db_path = 'flex_complete_database.db'
    if not Path(db_path).exists():
        logger.error(f"Database not found at database/flex_complete_database.db or flex_complete_database.db")
        return 1

    try:
        logger.info("Starting dev intelligence graph detection")
        engine = DevIntelligenceEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Dev intelligence detection completed: {result['message']}")
        logger.info(
            f"Organizations: {result.get('orgs_detected', 0)}, "
            f"Members: {result.get('members_stored', 0)}, "
            f"Duration: {result.get('duration_ms', 0):.0f}ms"
        )

        return 0 if result['status'] == 'success' else 1

    except Exception as e:
        logger.error(f"Dev intelligence job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
