"""
Token Lifecycle Schema Migrations - V2

Adds early signal fields and improved monitoring state tracking.
Run this once to initialize new columns.
"""

import sqlite3
import logging

logger = logging.getLogger(__name__)


def migrate_to_schema_v2(db_path: str) -> bool:
    """
    Add new columns for early signal engine and improved monitoring.
    Safe to run multiple times (uses IF NOT EXISTS / IF NOT EXISTS patterns).
    """

    try:
        conn = sqlite3.connect(db_path, timeout=15)
        cursor = conn.cursor()

        logger.info("[SCHEMA_V2] Starting migration to schema V2...")

        # ===== MIGRATION 1: Add early signal fields to token_monitoring_state =====

        migration_1_fields = [
            ("early_score", "REAL DEFAULT 0"),
            ("early_label", "TEXT"),
            ("early_prediction_computed_at", "INTEGER"),
            ("early_rug_score", "REAL DEFAULT 0"),
            ("early_success_score", "REAL DEFAULT 0"),
            ("early_warning_flags", "TEXT DEFAULT ''"),
        ]

        for field_name, field_type in migration_1_fields:
            try:
                cursor.execute(f"ALTER TABLE token_monitoring_state ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to token_monitoring_state")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 2: Add velocity and momentum tracking =====

        migration_2_fields = [
            ("velocity_current_pct_per_min", "REAL DEFAULT 0"),
            ("velocity_peak_pct_per_min", "REAL DEFAULT 0"),
            ("velocity_decay_rate", "REAL DEFAULT 0"),
        ]

        for field_name, field_type in migration_2_fields:
            try:
                cursor.execute(f"ALTER TABLE token_monitoring_state ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to token_monitoring_state")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 3: Add timeline milestone tracking =====

        migration_3_fields = [
            ("time_to_10k_mc_seconds", "INTEGER"),
            ("time_to_50k_mc_seconds", "INTEGER"),
            ("time_to_100k_mc_seconds", "INTEGER"),
        ]

        for field_name, field_type in migration_3_fields:
            try:
                cursor.execute(f"ALTER TABLE token_monitoring_state ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to token_monitoring_state")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 4: Add drawdown and volatility tracking =====

        migration_4_fields = [
            ("early_drawdown_pct", "REAL DEFAULT 0"),
            ("has_recovered_from_early_dip", "INTEGER DEFAULT 0"),
            ("liquidity_to_mc_ratio", "REAL DEFAULT 0"),
            ("liquidity_trend", "TEXT DEFAULT 'flat'"),
        ]

        for field_name, field_type in migration_4_fields:
            try:
                cursor.execute(f"ALTER TABLE token_monitoring_state ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to token_monitoring_state")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 5: Add data quality fields to token_lifecycle_snapshots =====

        migration_5_fields = [
            ("liquidity_confidence", "REAL DEFAULT 0.5"),
            ("data_quality_flags", "TEXT DEFAULT ''"),
        ]

        for field_name, field_type in migration_5_fields:
            try:
                cursor.execute(f"ALTER TABLE token_lifecycle_snapshots ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to token_lifecycle_snapshots")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 6: Add peak classification metadata to token_outcomes =====

        migration_6_fields = [
            ("peak_type", "TEXT"),
            ("peak_hold_time_minutes", "INTEGER"),
            ("peak_confidence", "REAL"),
        ]

        for field_name, field_type in migration_6_fields:
            try:
                cursor.execute(f"ALTER TABLE token_outcomes ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to token_outcomes")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 7: Enhance cluster_outcome_stats =====

        migration_7_fields = [
            ("consistency_score", "REAL DEFAULT 0"),
            ("success_momentum", "REAL DEFAULT 0"),
            ("early_signal_accuracy", "REAL DEFAULT 0"),
            ("avg_time_to_peak_minutes", "INTEGER DEFAULT 0"),
            ("volatility_index", "REAL DEFAULT 0"),
            ("recommended_action", "TEXT DEFAULT 'watch'"),
        ]

        for field_name, field_type in migration_7_fields:
            try:
                cursor.execute(f"ALTER TABLE cluster_outcome_stats ADD COLUMN {field_name} {field_type}")
                logger.info(f"  ✓ Added {field_name} to cluster_outcome_stats")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ {field_name} already exists (skipping)")
                else:
                    raise

        # ===== MIGRATION 8: Create token_early_signals table =====

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_early_signals (
                mint TEXT PRIMARY KEY,
                minutes_alive INT,
                rug_probability REAL,
                success_probability REAL,
                momentum_score REAL,
                liquidity_trend TEXT,
                volume_trend TEXT,
                price_stability REAL,
                predicted_outcome TEXT,
                prediction_confidence REAL,
                computed_at INTEGER,
                FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_early_signals_rug_prob
            ON token_early_signals(rug_probability DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_early_signals_success_prob
            ON token_early_signals(success_probability DESC)
        """)

        logger.info("  ✓ Created token_early_signals table")

        # ===== MIGRATION 9: Create indexes for new early signal fields =====

        indexes = [
            ("idx_early_label", "token_monitoring_state", "early_label"),
            ("idx_early_score", "token_monitoring_state", "early_score DESC"),
            ("idx_early_prediction_time", "token_monitoring_state", "early_prediction_computed_at DESC"),
        ]

        for index_name, table_name, columns in indexes:
            try:
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {table_name}({columns})
                """)
                logger.info(f"  ✓ Created index {index_name}")
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.info(f"  ℹ Index {index_name} already exists (skipping)")
                else:
                    raise

        conn.commit()
        conn.close()

        logger.info("[SCHEMA_V2] ✅ Migration complete!")
        return True

    except Exception as e:
        logger.error(f"[SCHEMA_V2] ❌ Migration failed: {e}")
        return False


# =========================================================================
# EXAMPLE USAGE
# =========================================================================

if __name__ == "__main__":
    import os

    db_path = os.environ.get('DB_PATH', 'database/flex_complete_database.db')

    print(f"[SCHEMA_V2] Migrating database: {db_path}")
    success = migrate_to_schema_v2(db_path)

    if success:
        print("[SCHEMA_V2] ✅ Database schema updated successfully!")
    else:
        print("[SCHEMA_V2] ❌ Migration failed")
