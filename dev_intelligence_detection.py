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
from src.core.creator_seed_metrics import CreatorSeedMetricsAnalyzer
from src.core.funder_overlap_analysis import FunderOverlapAnalyzer
from src.core.launch_wave_detection import LaunchWaveDetectionEngine
from src.core.master_launch_score import MasterLaunchScoreEngine


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

        # Phase 4: Creator Seed Metrics — Coordinated funding analysis
        logger.info("Starting creator seed metrics analysis (seed concentration)")
        seed_analyzer = CreatorSeedMetricsAnalyzer(db_path)
        result_seeds = seed_analyzer.compute_and_store()

        logger.info(f"Creator seed metrics completed: {result_seeds['message']}")
        logger.info(
            f"Metrics computed: {result_seeds.get('metrics_computed', 0)}, "
            f"High concentration: {result_seeds.get('high_concentration_count', 0)}, "
            f"Duration: {result_seeds.get('duration_ms', 0):.0f}ms"
        )

        # Phase 4.5: Funder Overlap Analysis — Wallet coordination detection
        logger.info("Starting funder overlap analysis (wallet coordination)")
        overlap_analyzer = FunderOverlapAnalyzer(db_path)
        result_overlap = overlap_analyzer.analyze_and_store()

        logger.info(f"Funder overlap analysis completed: {result_overlap['message']}")
        logger.info(
            f"Overlaps found: {result_overlap.get('overlaps_found', 0)}, "
            f"High coordination: {result_overlap.get('high_coordination_count', 0)}, "
            f"Very strong: {result_overlap.get('very_strong_count', 0)}, "
            f"Duration: {result_overlap.get('duration_ms', 0):.0f}ms"
        )

        # Phase 5: Launch Wave Detection — Multi-token launch pattern recognition
        logger.info("Starting launch wave detection (multi-launch patterns)")
        wave_engine = LaunchWaveDetectionEngine(db_path)
        result_waves = wave_engine.detect_and_store()

        logger.info(f"Launch wave detection completed: {result_waves['message']}")
        logger.info(
            f"Orgs processed: {result_waves.get('orgs_processed', 0)}, "
            f"Waves detected: {result_waves.get('waves_detected', 0)}, "
            f"Duration: {result_waves.get('duration_ms', 0):.0f}ms"
        )

        # Phase 6: Master Launch Score — Unified alert scoring
        logger.info("Starting master launch score computation (unified alerting)")
        mls_engine = MasterLaunchScoreEngine(db_path)
        result_mls = mls_engine.detect_and_store()

        logger.info(f"Master launch score computation completed: {result_mls['message']}")
        logger.info(
            f"Orgs processed: {result_mls.get('orgs_processed', 0)}, "
            f"CRITICAL: {result_mls.get('critical_count', 0)}, "
            f"HIGH: {result_mls.get('high_count', 0)}, "
            f"WATCH: {result_mls.get('watch_count', 0)}, "
            f"Duration: {result_mls.get('duration_ms', 0):.0f}ms"
        )

        # Return success only if all phases succeeded
        if (result['status'] == 'success' and result_v2['status'] == 'success' and
            result_v3['status'] == 'success' and result_seeds['status'] == 'success' and
            result_overlap['status'] == 'success' and result_waves['status'] == 'success' and
            result_mls['status'] == 'success'):
            return 0
        else:
            logger.warning(f"One or more phases failed: v1={result['status']}, v2={result_v2['status']}, v3={result_v3['status']}, seeds={result_seeds['status']}, overlap={result_overlap['status']}, waves={result_waves['status']}, mls={result_mls['status']}")
            return 1

    except Exception as e:
        logger.error(f"Dev intelligence job failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
