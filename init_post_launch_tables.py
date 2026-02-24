#!/usr/bin/env python3
"""
Initialize database tables for post-launch automation

Creates tables for:
- funder_distribution_metrics: Metrics for top funding senders
- coordinated_funders: Detected coordinated funding patterns
- unified_creator_clusters: Unified view of creator clusters
"""

import sqlite3

DB_PATH = "pumpswap_tokens.db"


def init_post_launch_tables():
    """Create necessary tables for post-launch automation"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Table 1: Funder Distribution Metrics (for Top Funding Distribution Senders)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_distribution_metrics (
                funder_address TEXT PRIMARY KEY,
                creators_funded INTEGER,
                tokens_created INTEGER,
                total_sol_distributed REAL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Created funder_distribution_metrics table")

        # Table 2: Coordinated Funders
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coordinated_funders (
                funder_address TEXT PRIMARY KEY,
                creator_count INTEGER,
                creator_addresses TEXT,  -- JSON array of creator addresses
                risk_level TEXT,  -- LOW, MEDIUM, HIGH
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Created coordinated_funders table")

        # Table 3: Unified Creator Clusters
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unified_creator_clusters (
                cluster_id INTEGER PRIMARY KEY,
                member_addresses TEXT,  -- JSON array of all addresses in cluster
                cluster_size INTEGER,
                risk_level TEXT,  -- LOW, MEDIUM, HIGH
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Created unified_creator_clusters table")

        # Add indexes for performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_funder_dist_creators
            ON funder_distribution_metrics(creators_funded DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_coordinated_funders_risk
            ON coordinated_funders(risk_level)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_clusters_risk
            ON unified_creator_clusters(risk_level)
        """)

        conn.commit()
        conn.close()

        print("\n✅ All post-launch automation tables initialized successfully")

    except Exception as e:
        print(f"❌ Error initializing tables: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    init_post_launch_tables()
