#!/usr/bin/env python3
"""
FLEX Dev Intelligence Graph Detection — Daily Job

Runs daily at 5:00 AM UTC (after graph detection at 4:30 AM) to detect
developer organizations spanning wallet → creator → token relationships.

Phase 1 (v1): Detects multi-layer developer organizations and computes organization scores.
Phase 2 (v2): Computes launch probability predictions and organization reputation.

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
from src.core.dev_intelligence_v2 import DevIntelligenceV2Engine
from src.core.dev_intelligence_v3 import DevIntelligenceV3Engine


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
        # Phase 1: V1 — Organization detection and scoring
        logger.info("Starting dev intelligence graph detection (v1)")
        engine = DevIntelligenceEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Dev intelligence v1 detection completed: {result['message']}")
        logger.info(
            f"Organizations: {result.get('orgs_detected', 0)}, "
            f"Members: {result.get('members_stored', 0)}, "
            f"Duration: {result.get('duration_ms', 0):.0f}ms"
        )

        # Phase 2: V2 — Launch probability predictions and reputation tracking
        logger.info("Starting dev intelligence v2 (launch predictions + reputation)")
        engine_v2 = DevIntelligenceV2Engine(db_path)
        result_v2 = engine_v2.detect_and_store()

        logger.info(f"Dev intelligence v2 completed: {result_v2['message']}")
        logger.info(
            f"Orgs processed: {result_v2.get('orgs_processed', 0)}, "
            f"Duration: {result_v2.get('duration_ms', 0):.0f}ms"
        )

        # Phase 3: V3 — Predictive analytics (multi-window, snapshots, risk, alerts)
        logger.info("Starting dev intelligence v3 (predictive analytics)")
        engine_v3 = DevIntelligenceV3Engine(db_path)
        result_v3 = engine_v3.detect_and_store()

        logger.info(f"Dev intelligence v3 completed: {result_v3['message']}")
        logger.info(
            f"Orgs processed: {result_v3.get('orgs_processed', 0)}, "
            f"Tokens predicted: {result_v3.get('tokens_predicted', 0)}, "
            f"Alerts fired: {result_v3.get('alerts_fired', 0)}, "
            f"Duration: {result_v3.get('duration_ms', 0):.0f}ms"
        )

        # Return success only if all three phases succeeded
        if result['status'] == 'success' and result_v2['status'] == 'success' and result_v3['status'] == 'success':
            return 0
        else:
            logger.warning(f"One or more phases failed: v1={result['status']}, v2={result_v2['status']}, v3={result_v3['status']}")
            return 1

    except Exception as e:
        logger.error(f"Dev intelligence job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
