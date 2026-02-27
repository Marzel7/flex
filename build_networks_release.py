#!/usr/bin/env python3
"""
Build networks_release table with version tracking and stability state.

This script implements Phase 1 of the optimization work:
- Issue #3: Build version incrementing (delta-based)
- Issue #4: Stability state enforcement (±10% threshold)

Follows transaction-safe snapshot-and-compare pattern:
1. Snapshot previous state to TEMP table
2. Compute new state (existing Phases 1-4)
3. Compare deltas (Phase C)
4. Set stability states (Phase D)
5. Set build versions (Phase C)
6. Atomic commit
"""

import sqlite3
from datetime import datetime
import json
from contextlib import contextmanager


@contextmanager
def db_transaction(db_path):
    """Context manager for safe database transactions."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def ensure_network_evidence_table(db):
    """Ensure network_evidence table exists with proper schema."""
    db.execute('''
        CREATE TABLE IF NOT EXISTS network_evidence (
          network_name           TEXT PRIMARY KEY,

          -- Evidence counts
          total_edges            INTEGER DEFAULT 0,
          total_evidence_txs     INTEGER DEFAULT 0,
          average_confidence     REAL DEFAULT 0.0,

          -- Evidence confidence buckets
          high_confidence_edges  INTEGER DEFAULT 0,
          medium_confidence_edges INTEGER DEFAULT 0,
          low_confidence_edges   INTEGER DEFAULT 0,

          -- Time-based evidence
          earliest_evidence_time INTEGER,
          latest_evidence_time   INTEGER,
          evidence_span_days     INTEGER,

          -- Bridge funders
          unique_bridge_funders  INTEGER DEFAULT 0,
          bridge_funder_list     TEXT,

          -- Risk scoring
          evidence_risk_score    REAL DEFAULT 0.0,

          -- Metadata
          evidence_version       INTEGER DEFAULT 1,
          last_updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          last_changed_at        TIMESTAMP,

          FOREIGN KEY(network_name) REFERENCES networks_release(network_name)
            ON DELETE CASCADE
        );
    ''')

    # Create indexes
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_evidence_risk
          ON network_evidence(evidence_risk_score DESC);
    ''')

    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_network_evidence_updated
          ON network_evidence(last_updated_at DESC);
    ''')


def build_networks_release(db_path: str) -> dict:
    """
    Build networks_release with version tracking and stability states.

    Returns:
        dict with build statistics and status
    """

    stats = {
        'networks_processed': 0,
        'versions_incremented': 0,
        'new_networks': 0,
        'stability_states': {
            'new': 0,
            'stable': 0,
            'growing': 0,
            'shrinking': 0,
        },
        'changed_networks': 0,
        'growth_spikes': [],  # Track networks with >25% growth
        'errors': [],
    }

    with db_transaction(db_path) as db:
        print("🔄 Phase A: Snapshot previous state...")

        # Phase A: Snapshot old state before building new
        db.execute('''
            DROP TABLE IF EXISTS networks_release_prev;
        ''')
        db.execute('''
            CREATE TABLE networks_release_prev AS
            SELECT network_name, network_size, network_type, build_version
            FROM networks_release;
        ''')

        prev_count = db.execute('SELECT COUNT(*) as cnt FROM networks_release_prev').fetchone()['cnt']
        print(f"   ✅ Snapshot: {prev_count} previous networks saved")

        # Phase B: Compute new state (Phases 1-4 from Step 3)
        print("🔄 Phase B: Compute new network state...")

        # Phase B.1: Network sizes from network_membership
        db.execute('''
            WITH network_data AS (
              SELECT
                nm.network_name,
                COUNT(DISTINCT nm.creator_address) as network_size,
                cn.network_risk_level
              FROM network_membership nm
              LEFT JOIN creator_networks cn ON nm.network_name = cn.network_name
              GROUP BY nm.network_name
            )
            INSERT OR REPLACE INTO networks_release
            (network_name, network_size, network_risk_level, last_built_at, build_version)
            SELECT
              nd.network_name,
              nd.network_size,
              COALESCE(nd.network_risk_level, 'MEDIUM'),
              CURRENT_TIMESTAMP,
              1
            FROM network_data nd;
        ''')

        # Phase B.2: CEX tagging
        db.execute('''
            WITH network_funders AS (
              SELECT DISTINCT
                nm.network_name,
                cf.funder_address,
                CASE
                  WHEN cw.cex_address IS NOT NULL THEN 'cex'
                  ELSE 'other'
                END as funder_type
              FROM network_membership nm
              JOIN creator_funders cf ON nm.creator_address = cf.creator_address
              LEFT JOIN cex_wallets cw ON cf.funder_address = cw.cex_address
            ),
            cex_counts AS (
              SELECT
                network_name,
                COUNT(DISTINCT CASE WHEN funder_type = 'cex' THEN funder_address END) as cex_count,
                GROUP_CONCAT(DISTINCT CASE WHEN funder_type = 'cex' THEN '\"' || funder_address || '\"' END) as cex_addresses
              FROM network_funders
              GROUP BY network_name
            )
            UPDATE networks_release
            SET
              has_cex_funder = CASE WHEN cc.cex_count > 0 THEN 1 ELSE 0 END,
              cex_funder_count = COALESCE(cc.cex_count, 0),
              cex_funder_addresses = CASE
                WHEN cc.cex_addresses IS NOT NULL
                THEN '[' || cc.cex_addresses || ']'
                ELSE '[]'
              END
            FROM cex_counts cc
            WHERE networks_release.network_name = cc.network_name;
        ''')

        # Phase B.3: Infrastructure tagging
        db.execute('''
            WITH network_infra_funders AS (
              SELECT DISTINCT
                nm.network_name,
                cf.funder_address,
                CASE
                  WHEN ifo.funder_address IS NOT NULL THEN 'infra'
                  ELSE 'other'
                END as funder_type
              FROM network_membership nm
              JOIN creator_funders cf ON nm.creator_address = cf.creator_address
              LEFT JOIN infra_funders_observed ifo ON cf.funder_address = ifo.funder_address
            ),
            infra_counts AS (
              SELECT
                network_name,
                COUNT(DISTINCT CASE WHEN funder_type = 'infra' THEN funder_address END) as infra_count,
                GROUP_CONCAT(DISTINCT CASE WHEN funder_type = 'infra' THEN '\"' || funder_address || '\"' END) as infra_addresses
              FROM network_infra_funders
              GROUP BY network_name
            )
            UPDATE networks_release
            SET
              has_infra_funder = CASE WHEN ic.infra_count > 0 THEN 1 ELSE 0 END,
              infra_funder_count = COALESCE(ic.infra_count, 0),
              infra_funder_addresses = CASE
                WHEN ic.infra_addresses IS NOT NULL
                THEN '[' || ic.infra_addresses || ']'
                ELSE '[]'
              END
            FROM infra_counts ic
            WHERE networks_release.network_name = ic.network_name;
        ''')

        # Phase B.4: Network type classification
        db.execute('''
            UPDATE networks_release
            SET network_type = CASE
              WHEN has_cex_funder = 1 AND has_infra_funder = 1 THEN 'cex_and_infra_connected'
              WHEN has_cex_funder = 1 THEN 'cex_connected'
              WHEN has_infra_funder = 1 THEN 'infra_connected'
              ELSE 'organic'
            END;
        ''')

        new_count = db.execute('SELECT COUNT(*) as cnt FROM networks_release').fetchone()['cnt']
        print(f"   ✅ New state computed: {new_count} networks")

        # Phase D: Compute stability state based on deltas
        # BEFORE incrementing versions (critical ordering)
        print("🔄 Phase D: Compute stability states...")

        # Compute deltas in a temp table first (SQLite doesn't support UPDATE...FROM with complex joins)
        # Track delta_pct for future risk scoring and spike detection
        db.execute('''
            CREATE TEMP TABLE stability_deltas AS
            SELECT
              nr.network_name,
              nr.network_size,
              old.network_size as old_size,
              CASE
                WHEN old.network_size IS NULL THEN NULL
                WHEN old.network_size = 0 THEN NULL
                ELSE ROUND((nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) * 100, 2)
              END as delta_pct,
              CASE
                WHEN old.network_size IS NULL THEN 'new'
                WHEN old.network_size = 0 THEN 'new'
                WHEN (nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) > 0.1 THEN 'growing'
                WHEN (nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) < -0.1 THEN 'shrinking'
                ELSE 'stable'
              END as computed_state
            FROM networks_release nr
            LEFT JOIN networks_release_prev old ON nr.network_name = old.network_name;
        ''')

        # Update from temp table
        db.execute('''
            UPDATE networks_release
            SET stability_state = (
              SELECT computed_state FROM stability_deltas
              WHERE stability_deltas.network_name = networks_release.network_name
            )
            WHERE network_name IN (SELECT network_name FROM stability_deltas);
        ''')

        # Verify stability states were set
        stability_check = db.execute('''
            SELECT stability_state, COUNT(*) as count FROM networks_release
            GROUP BY stability_state
        ''').fetchall()

        print(f"   ✅ Stability states computed:")
        for row in stability_check:
            state = row['stability_state']
            count = row['count']
            stats['stability_states'][state] = count
            print(f"      - {state}: {count}")

        # Phase C: Update build versions (AFTER stability, as you noted)
        print("🔄 Phase C: Update build versions...")

        # Compute version changes in temp table
        # Also track which networks changed (for last_changed_at)
        db.execute('''
            CREATE TEMP TABLE version_updates AS
            SELECT
              nr.network_name,
              CASE
                WHEN old.network_name IS NULL THEN 1
                WHEN nr.network_size != old.network_size THEN old.build_version + 1
                WHEN nr.network_type != old.network_type THEN old.build_version + 1
                ELSE old.build_version
              END as new_version,
              CASE
                WHEN old.network_name IS NULL THEN 1
                WHEN nr.network_size != old.network_size THEN 1
                WHEN nr.network_type != old.network_type THEN 1
                ELSE 0
              END as changed_flag
            FROM networks_release nr
            LEFT JOIN networks_release_prev old ON nr.network_name = old.network_name;
        ''')

        # Update from temp table
        db.execute('''
            UPDATE networks_release
            SET
              build_version = (
                SELECT new_version FROM version_updates
                WHERE version_updates.network_name = networks_release.network_name
              ),
              last_changed_at = CASE
                WHEN (SELECT changed_flag FROM version_updates WHERE version_updates.network_name = networks_release.network_name) = 1
                THEN CURRENT_TIMESTAMP
                ELSE last_changed_at
              END
            WHERE network_name IN (SELECT network_name FROM version_updates);
        ''')

        # Check version changes and growth spikes
        version_check = db.execute('''
            SELECT
              nr.network_name,
              nr.build_version,
              old.build_version as old_version,
              sd.delta_pct
            FROM networks_release nr
            LEFT JOIN networks_release_prev old ON nr.network_name = old.network_name
            LEFT JOIN stability_deltas sd ON nr.network_name = sd.network_name
            WHERE (old.build_version IS NULL OR nr.build_version != old.build_version)
            ORDER BY sd.delta_pct DESC NULLS LAST
            LIMIT 20
        ''').fetchall()

        print(f"   ✅ Version updates: {len(version_check)} networks changed")
        if version_check:
            print(f"      Top changes (new/incremented networks):")
            for row in version_check[:5]:
                old_v = row['old_version'] or 'NEW'
                new_v = row['build_version']
                delta_str = f" ({row['delta_pct']:+.1f}%)" if row['delta_pct'] is not None else ""
                print(f"      - {row['network_name']}: v{old_v} → v{new_v}{delta_str}")

                # Track growth spikes (>25% growth)
                if row['delta_pct'] is not None and row['delta_pct'] > 25:
                    stats['growth_spikes'].append({
                        'network': row['network_name'],
                        'delta_pct': row['delta_pct']
                    })

            stats['versions_incremented'] = len(version_check)
            stats['changed_networks'] = len(version_check)

        # Phase E: Finalize (update timestamp)
        db.execute('UPDATE networks_release SET last_built_at = CURRENT_TIMESTAMP')

        # Phase F: Aggregate network evidence (rollup table)
        print("🔄 Phase F: Aggregate network evidence...")

        # Ensure table exists
        ensure_network_evidence_table(db)

        # Snapshot previous evidence state
        db.execute('DROP TABLE IF EXISTS network_evidence_prev')
        db.execute('''
            CREATE TABLE network_evidence_prev AS
            SELECT
              network_name, total_edges, average_confidence, evidence_version
            FROM network_evidence;
        ''')

        prev_evidence_count = db.execute(
            'SELECT COUNT(*) as cnt FROM network_evidence_prev'
        ).fetchone()['cnt']

        # Compute evidence aggregation (only if coordinated_creator_edges exists)
        try:
            # Phase F.1: Aggregate edges by network
            db.execute('''
                WITH network_edges AS (
                  SELECT
                    nm.network_name,
                    COUNT(*) as total_edges,
                    COUNT(DISTINCT cce.evidence_tx) as total_evidence_txs,
                    ROUND(AVG(cce.confidence), 2) as avg_confidence,
                    COUNT(CASE WHEN cce.confidence >= 75 THEN 1 END) as high_conf,
                    COUNT(CASE WHEN cce.confidence >= 50 AND cce.confidence < 75 THEN 1 END) as med_conf,
                    COUNT(CASE WHEN cce.confidence < 50 THEN 1 END) as low_conf,
                    MIN(cce.first_seen_block_time) as earliest_time,
                    MAX(cce.first_seen_block_time) as latest_time,
                    COUNT(DISTINCT cce.bridge_funder) as unique_funders,
                    GROUP_CONCAT(DISTINCT cce.bridge_funder) as funder_list
                  FROM network_membership nm
                  LEFT JOIN coordinated_creator_edges cce
                    ON (nm.creator_address = cce.creator_a OR nm.creator_address = cce.creator_b)
                  GROUP BY nm.network_name
                ),
                evidence_with_risk AS (
                  SELECT
                    ne.network_name,
                    COALESCE(ne.total_edges, 0) as total_edges,
                    COALESCE(ne.total_evidence_txs, 0) as total_evidence_txs,
                    COALESCE(ne.avg_confidence, 0.0) as avg_confidence,
                    COALESCE(ne.high_conf, 0) as high_confidence_edges,
                    COALESCE(ne.med_conf, 0) as medium_confidence_edges,
                    COALESCE(ne.low_conf, 0) as low_confidence_edges,
                    ne.earliest_time,
                    ne.latest_time,
                    COALESCE(
                      CAST((ne.latest_time - ne.earliest_time) / 86400.0 AS INTEGER),
                      0
                    ) as evidence_span_days,
                    COALESCE(ne.unique_funders, 0) as unique_bridge_funders,
                    COALESCE('[' || ne.funder_list || ']', '[]') as bridge_funder_list,
                    -- Risk score: frequency (40%) + confidence (40%) + concentration (20%)
                    ROUND(
                      MIN(100,
                        (COALESCE(ne.total_edges, 0) / CAST(
                          CASE WHEN COUNT(DISTINCT nm.creator_address) * 10 < 50 THEN 50 ELSE COUNT(DISTINCT nm.creator_address) * 10 END AS FLOAT
                        )) * 40 +
                        (COALESCE(ne.avg_confidence, 0) / 100.0) * 40 +
                        CASE
                          WHEN COALESCE(ne.evidence_span_days, 0) <= 1 THEN 20
                          WHEN COALESCE(ne.evidence_span_days, 0) <= 7 THEN 15
                          WHEN COALESCE(ne.evidence_span_days, 0) <= 30 THEN 10
                          ELSE 5
                        END
                      ),
                      2
                    ) as risk_score
                  FROM network_edges ne
                  CROSS JOIN (SELECT COUNT(DISTINCT creator_address) FROM network_membership) nm
                )
                INSERT OR REPLACE INTO network_evidence
                (
                  network_name, total_edges, total_evidence_txs, average_confidence,
                  high_confidence_edges, medium_confidence_edges, low_confidence_edges,
                  earliest_evidence_time, latest_evidence_time, evidence_span_days,
                  unique_bridge_funders, bridge_funder_list, evidence_risk_score,
                  last_updated_at, evidence_version
                )
                SELECT
                  network_name, total_edges, total_evidence_txs, avg_confidence,
                  high_confidence_edges, medium_confidence_edges, low_confidence_edges,
                  earliest_time, latest_time, evidence_span_days,
                  unique_bridge_funders, bridge_funder_list, risk_score,
                  CURRENT_TIMESTAMP, 1
                FROM evidence_with_risk;
            ''')

            # Phase F.2: Update evidence versions (idempotent)
            db.execute('''
                CREATE TEMP TABLE evidence_deltas AS
                SELECT
                  ne.network_name,
                  ne.total_edges,
                  COALESCE(old.total_edges, 0) as old_total_edges,
                  ne.average_confidence,
                  COALESCE(old.average_confidence, 0.0) as old_avg_confidence,
                  CASE
                    WHEN old.network_name IS NULL THEN 1
                    WHEN ne.total_edges != old.total_edges THEN 1
                    WHEN ABS(ne.average_confidence - old.average_confidence) > 0.01 THEN 1
                    ELSE 0
                  END as changed_flag
                FROM network_evidence ne
                LEFT JOIN network_evidence_prev old ON ne.network_name = old.network_name;
            ''')

            db.execute('''
                UPDATE network_evidence
                SET
                  evidence_version = CASE
                    WHEN (SELECT changed_flag FROM evidence_deltas
                          WHERE evidence_deltas.network_name = network_evidence.network_name) = 1
                    THEN evidence_version + 1
                    ELSE evidence_version
                  END,
                  last_changed_at = CASE
                    WHEN (SELECT changed_flag FROM evidence_deltas
                          WHERE evidence_deltas.network_name = network_evidence.network_name) = 1
                    THEN CURRENT_TIMESTAMP
                    ELSE last_changed_at
                  END
                WHERE network_name IN (SELECT network_name FROM evidence_deltas);
            ''')

            # Check evidence aggregation results
            evidence_stats = db.execute('''
                SELECT
                  COUNT(*) as total_networks,
                  COUNT(CASE WHEN total_edges > 0 THEN 1 END) as networks_with_evidence,
                  ROUND(AVG(evidence_risk_score), 2) as avg_risk_score,
                  MAX(evidence_risk_score) as max_risk_score
                FROM network_evidence
            ''').fetchone()

            print(f"   ✅ Evidence aggregated: {evidence_stats['networks_with_evidence']} networks with coordinated edges")
            print(f"      Average risk score: {evidence_stats['avg_risk_score']}")
            print(f"      Maximum risk score: {evidence_stats['max_risk_score']}")

        except Exception as e:
            print(f"   ⚠️  Evidence aggregation skipped: {e}")
            stats['errors'].append(f"Evidence aggregation: {str(e)}")

        # Phase G: Compute network scores (deterministic, precomputed)
        print("🔄 Phase G: Compute network scores...")

        # Ensure network_scores table exists
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_scores (
                network_name TEXT PRIMARY KEY,
                score INTEGER NOT NULL DEFAULT 0,  -- 0-100 scale
                score_version INTEGER NOT NULL DEFAULT 1,  -- Track scoring rule updates
                score_components_json TEXT,  -- JSON with {connectivity, lifecycle, evidence} breakdown
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
            );
        ''')

        # Create indexes for common queries
        db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_score ON network_scores(score DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_computed_at ON network_scores(computed_at DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_name ON network_scores(network_name);')

        # Snapshot previous scores for version tracking
        db.execute('DROP TABLE IF EXISTS network_scores_prev')
        db.execute('''
            CREATE TABLE network_scores_prev AS
            SELECT network_name, score, score_version
            FROM network_scores;
        ''')

        # Compute scores with three components (v2 model):
        # A) Connectivity Risk (0-40): Based on network_type and CEX/infra connections
        # B) Lifecycle Risk (0-25): Based on stability_state and growth patterns
        # C) Evidence Risk v2 (0-35): Weighted confidence mix (H*1.0 + M*0.6 + L*0.2) / total
        db.execute('''
            WITH score_components AS (
              SELECT
                nr.network_name,
                -- Component A: Connectivity Risk (0-40)
                -- organic: 0, cex: 10, infra: 15, cex_and_infra: 25
                CASE
                  WHEN nr.network_type = 'organic' THEN 0
                  WHEN nr.network_type = 'cex_connected' THEN 10
                  WHEN nr.network_type = 'infra_connected' THEN 15
                  WHEN nr.network_type = 'cex_and_infra_connected' THEN 25
                  ELSE 0
                END as connectivity_risk,
                -- Component B: Lifecycle Risk (0-25)
                -- stable: 0, new: 10, growing: 20, shrinking: 5
                CASE
                  WHEN nr.stability_state = 'stable' THEN 0
                  WHEN nr.stability_state = 'new' THEN 10
                  WHEN nr.stability_state = 'growing' THEN 20
                  WHEN nr.stability_state = 'shrinking' THEN 5
                  ELSE 0
                END as lifecycle_risk,
                -- Component C: Evidence Risk v2 (0-35)
                -- Phase 5A: Weighted confidence mix instead of simple high-confidence ratio
                -- conf = (H*1.0 + M*0.6 + L*0.2) / max(total_edges, 1)
                -- evidence_points = round(conf * 35)
                CASE
                  WHEN ne.total_edges IS NULL THEN 0
                  WHEN ne.total_edges = 0 THEN 0
                  ELSE CAST(ROUND(
                    (
                      CAST(ne.high_confidence_edges AS FLOAT) * 1.0 +
                      CAST(ne.medium_confidence_edges AS FLOAT) * 0.6 +
                      CAST(COALESCE(ne.low_confidence_edges, 0) AS FLOAT) * 0.2
                    ) / CAST(CASE WHEN ne.total_edges IS NULL OR ne.total_edges = 0 THEN 1 ELSE ne.total_edges END AS FLOAT) * 35.0
                  ) AS INTEGER)
                END as evidence_risk,
                -- Store confidence components for transparency
                CAST(
                  (
                    CAST(ne.high_confidence_edges AS FLOAT) * 1.0 +
                    CAST(ne.medium_confidence_edges AS FLOAT) * 0.6 +
                    CAST(COALESCE(ne.low_confidence_edges, 0) AS FLOAT) * 0.2
                  ) / CAST(CASE WHEN ne.total_edges IS NULL OR ne.total_edges = 0 THEN 1 ELSE ne.total_edges END AS FLOAT)
                AS REAL) as confidence_score,
                ne.high_confidence_edges,
                ne.medium_confidence_edges,
                ne.total_edges
              FROM networks_release nr
              LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
            ),
            final_scores AS (
              SELECT
                network_name,
                connectivity_risk,
                lifecycle_risk,
                evidence_risk,
                MIN(100, connectivity_risk + lifecycle_risk + evidence_risk) as final_score,
                -- JSON component breakdown for explainability (Phase 5A: includes evidence_conf and model)
                json_object(
                  'connectivity', connectivity_risk,
                  'lifecycle', lifecycle_risk,
                  'evidence', evidence_risk,
                  'evidence_conf', COALESCE(ROUND(confidence_score, 4), 0.0),
                  'model', 'v2',
                  'high_confidence_edges', COALESCE(high_confidence_edges, 0),
                  'medium_confidence_edges', COALESCE(medium_confidence_edges, 0),
                  'total_edges', COALESCE(total_edges, 0)
                ) as components_json
              FROM score_components
            )
            INSERT OR REPLACE INTO network_scores
            (network_name, score, score_version, score_components_json, computed_at)
            SELECT
              network_name,
              final_score,
              2,
              components_json,
              CURRENT_TIMESTAMP
            FROM final_scores;
        ''')

        # Update score versions idempotently (only if score changed, not for new networks)
        db.execute('''
            CREATE TEMP TABLE score_deltas AS
            SELECT
              ns.network_name,
              ns.score,
              COALESCE(old.score, -1) as old_score,
              CASE
                WHEN old.network_name IS NULL THEN 0
                WHEN ns.score != old.score THEN 1
                ELSE 0
              END as changed_flag
            FROM network_scores ns
            LEFT JOIN network_scores_prev old ON ns.network_name = old.network_name;
        ''')

        db.execute('''
            UPDATE network_scores
            SET score_version = CASE
              WHEN (SELECT changed_flag FROM score_deltas
                    WHERE score_deltas.network_name = network_scores.network_name) = 1
              THEN score_version + 1
              ELSE score_version
            END
            WHERE network_name IN (SELECT network_name FROM score_deltas);
        ''')

        # Verify scoring
        score_stats = db.execute('''
            SELECT
              COUNT(*) as total_networks,
              ROUND(AVG(score), 2) as avg_score,
              MAX(score) as max_score,
              MIN(score) as min_score,
              COUNT(CASE WHEN score >= 70 THEN 1 END) as high_risk,
              COUNT(CASE WHEN score >= 30 AND score < 70 THEN 1 END) as medium_risk,
              COUNT(CASE WHEN score < 30 THEN 1 END) as low_risk
            FROM network_scores
        ''').fetchone()

        print(f"   ✅ Scores computed: {score_stats['total_networks']} networks")
        print(f"      Average score: {score_stats['avg_score']}")
        print(f"      Risk distribution: High({score_stats['high_risk']}) | Med({score_stats['medium_risk']}) | Low({score_stats['low_risk']})")

        # Phase H: Generate monitoring history and alerts
        print("🔄 Phase H: Generate monitoring history and alerts...")

        # Ensure tables exist
        db.execute('''
            CREATE TABLE IF NOT EXISTS network_score_history (
                network_name TEXT NOT NULL,
                build_version INTEGER NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                score_version INTEGER NOT NULL DEFAULT 1,
                components_json TEXT,
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (network_name, build_version),
                FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
            );
        ''')

        db.execute('''
            CREATE TABLE IF NOT EXISTS network_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                network_name TEXT NOT NULL,
                build_version INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (network_name) REFERENCES networks_release(network_name),
                UNIQUE (network_name, build_version, alert_type)
            );
        ''')

        # Create indexes
        db.execute('CREATE INDEX IF NOT EXISTS idx_nsh_computed_at ON network_score_history(computed_at DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_nsh_score ON network_score_history(score DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_nsh_build_version ON network_score_history(build_version DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON network_alerts(created_at DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_type ON network_alerts(alert_type);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_severity ON network_alerts(severity);')

        # Phase H.1: Insert current scores into history (idempotent via INSERT OR IGNORE)
        db.execute('''
            INSERT OR IGNORE INTO network_score_history
            (network_name, build_version, score, score_version, components_json, computed_at)
            SELECT
              nr.network_name,
              nr.build_version,
              ns.score,
              ns.score_version,
              ns.score_components_json,
              ns.computed_at
            FROM networks_release nr
            LEFT JOIN network_scores ns ON nr.network_name = ns.network_name;
        ''')

        history_count = db.execute('SELECT COUNT(*) as cnt FROM network_score_history').fetchone()['cnt']
        print(f"   ✅ Score history: {history_count} entries")

        # Phase H.2: Generate alerts based on score changes
        # Get current build version for reference
        current_build = db.execute('SELECT MAX(build_version) as max_build FROM networks_release').fetchone()['max_build']

        # Detect alerts
        alerts_created = 0

        # A) SCORE_SPIKE: delta >= +20
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH score_deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score as curr_score,
                (SELECT score FROM network_score_history p
                 WHERE p.network_name = h.network_name
                 AND p.build_version = h.build_version - 1) as prev_score
              FROM network_score_history h
              WHERE h.build_version = ?
            )
            SELECT
              sd.network_name,
              sd.build_version,
              'SCORE_SPIKE',
              CASE
                WHEN (sd.curr_score - COALESCE(sd.prev_score, 0)) >= 35 THEN 'high'
                ELSE 'medium'
              END,
              'Score increased by ' || (sd.curr_score - COALESCE(sd.prev_score, 0)) ||
                ' points (from ' || COALESCE(sd.prev_score, 'N/A') || ' to ' || sd.curr_score || ')',
              json_object(
                'prev_score', sd.prev_score,
                'curr_score', sd.curr_score,
                'delta', sd.curr_score - COALESCE(sd.prev_score, 0)
              )
            FROM score_deltas sd
            WHERE COALESCE(sd.prev_score, 0) IS NOT NULL
              AND (sd.curr_score - COALESCE(sd.prev_score, 0)) >= 20;
        ''', (current_build,))

        # B) NEW_HIGH_RISK: prev_score is NULL and curr_score >= 70
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH new_high_risk AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score as curr_score
              FROM network_score_history h
              WHERE h.build_version = ?
                AND h.score >= 70
                AND NOT EXISTS (
                  SELECT 1 FROM network_score_history p
                  WHERE p.network_name = h.network_name
                  AND p.build_version = h.build_version - 1
                )
            )
            SELECT
              nhr.network_name,
              nhr.build_version,
              'NEW_HIGH_RISK',
              'high',
              'New network with high risk score: ' || nhr.curr_score || ' / 100',
              json_object('score', nhr.curr_score)
            FROM new_high_risk nhr;
        ''', (current_build,))

        # C) TYPE_FLIP: network_type changed
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH type_changes AS (
              SELECT
                nr.network_name,
                nr.build_version,
                nr.network_type as new_type,
                (SELECT network_type FROM networks_release p
                 WHERE p.network_name = nr.network_name
                 AND p.build_version = nr.build_version - 1) as old_type
              FROM networks_release nr
              WHERE nr.build_version = ?
            )
            SELECT
              tc.network_name,
              tc.build_version,
              'TYPE_FLIP',
              CASE
                WHEN tc.new_type = 'cex_and_infra_connected' THEN 'high'
                WHEN tc.new_type IN ('infra_connected', 'cex_connected') THEN 'medium'
                ELSE 'low'
              END,
              'Network type changed from ' || COALESCE(tc.old_type, 'unknown') || ' to ' || tc.new_type,
              json_object(
                'old_type', tc.old_type,
                'new_type', tc.new_type
              )
            FROM type_changes tc
            WHERE tc.old_type IS NOT NULL
              AND tc.old_type != tc.new_type;
        ''', (current_build,))

        # D) LIFECYCLE_FLIP: stability_state changed AND curr_score >= 50
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH state_changes AS (
              SELECT
                nr.network_name,
                nr.build_version,
                nr.stability_state as new_state,
                ns.score,
                (SELECT stability_state FROM networks_release p
                 WHERE p.network_name = nr.network_name
                 AND p.build_version = nr.build_version - 1) as old_state
              FROM networks_release nr
              LEFT JOIN network_scores ns ON nr.network_name = ns.network_name
              WHERE nr.build_version = ?
            )
            SELECT
              sc.network_name,
              sc.build_version,
              'LIFECYCLE_FLIP',
              CASE
                WHEN sc.new_state = 'growing' THEN 'medium'
                ELSE 'low'
              END,
              'Network lifecycle changed from ' || COALESCE(sc.old_state, 'unknown') || ' to ' || sc.new_state,
              json_object(
                'old_state', sc.old_state,
                'new_state', sc.new_state,
                'score', sc.score
              )
            FROM state_changes sc
            WHERE sc.old_state IS NOT NULL
              AND sc.old_state != sc.new_state
              AND COALESCE(sc.score, 0) >= 50;
        ''', (current_build,))

        # E) SCORE_DROP (Phase 5A): delta <= -20 (downward move)
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH score_deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score as curr_score,
                (SELECT score FROM network_score_history p
                 WHERE p.network_name = h.network_name
                 AND p.build_version = h.build_version - 1) as prev_score
              FROM network_score_history h
              WHERE h.build_version = ?
            )
            SELECT
              sd.network_name,
              sd.build_version,
              'SCORE_DROP',
              CASE
                WHEN (sd.curr_score - COALESCE(sd.prev_score, 0)) <= -35 THEN 'high'
                ELSE 'medium'
              END,
              'Score decreased by ' || ABS(sd.curr_score - COALESCE(sd.prev_score, 0)) ||
                ' points (from ' || COALESCE(sd.prev_score, 'N/A') || ' to ' || sd.curr_score || ')',
              json_object(
                'prev_score', sd.prev_score,
                'curr_score', sd.curr_score,
                'delta', sd.curr_score - COALESCE(sd.prev_score, 0)
              )
            FROM score_deltas sd
            WHERE COALESCE(sd.prev_score, 0) IS NOT NULL
              AND (sd.curr_score - COALESCE(sd.prev_score, 0)) <= -20;
        ''', (current_build,))

        # F) VOLATILITY_SPIKE (Phase 5A): avg(|score_i - score_{i-1}|) over last 5 builds >= 15
        # Safe: only generates if network has >= 2 history points (ensures prev_score exists)
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH volatility_data AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                (SELECT score FROM network_score_history p
                 WHERE p.network_name = h.network_name
                 AND p.build_version = h.build_version - 1) as prev_score,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) as window_size
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            score_diffs AS (
              SELECT
                network_name,
                build_version,
                window_size,
                AVG(ABS(score - COALESCE(prev_score, score))) as volatility
              FROM volatility_data
              WHERE window_size >= 2
              GROUP BY network_name, build_version, window_size
            )
            SELECT
              sd.network_name,
              sd.build_version,
              'VOLATILITY_SPIKE',
              CASE
                WHEN sd.volatility >= 25 THEN 'high'
                ELSE 'medium'
              END,
              'High volatility detected: avg score change ' || CAST(ROUND(sd.volatility, 1) AS TEXT) || ' over last ' || sd.window_size || ' builds',
              json_object(
                'volatility', ROUND(sd.volatility, 2),
                'window_size', sd.window_size
              )
            FROM score_diffs sd
            WHERE sd.build_version = ?
              AND sd.volatility >= 15;
        ''', (current_build, current_build))

        # G) RISK_MOMENTUM_UP (Phase 5B): momentum >= 10 (sustained upward risk movement)
        # Safe: only generates if network has >= 3 history points (window_size >= 3)
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                h.score - LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as delta,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as window_size
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            momentum_calc AS (
              SELECT
                network_name,
                build_version,
                window_size,
                AVG(delta) OVER (PARTITION BY network_name ORDER BY build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as momentum
              FROM deltas
              WHERE window_size >= 3
            )
            SELECT
              mc.network_name,
              mc.build_version,
              'RISK_MOMENTUM_UP',
              CASE
                WHEN mc.momentum >= 20 THEN 'high'
                ELSE 'medium'
              END,
              'Risk momentum increasing: avg +' || CAST(ROUND(mc.momentum, 1) AS TEXT) || ' over last 3 builds',
              json_object(
                'momentum', ROUND(mc.momentum, 2),
                'window', 3
              )
            FROM momentum_calc mc
            WHERE mc.build_version = ?
              AND mc.momentum >= 10;
        ''', (current_build, current_build))

        # H) RISK_ACCELERATION_SPIKE (Phase 5B): |acceleration| >= 15 (rapid change in delta)
        # Acceleration = Δ_t - Δ_{t-1}
        # Safe: only generates if network has >= 3 history points (ensures both Δ_t and Δ_{t-1} exist)
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                h.score - LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as delta,
                LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as prev_score,
                LAG(h.score, 2) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as prev_prev_score,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as window_size
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            acceleration_calc AS (
              SELECT
                network_name,
                build_version,
                window_size,
                delta,
                CASE WHEN prev_score IS NOT NULL AND prev_prev_score IS NOT NULL
                     THEN prev_score - prev_prev_score
                     ELSE NULL
                END as prev_delta,
                delta - CASE WHEN prev_score IS NOT NULL AND prev_prev_score IS NOT NULL
                             THEN prev_score - prev_prev_score
                             ELSE NULL
                        END as acceleration
              FROM deltas
              WHERE window_size >= 3
            )
            SELECT
              ac.network_name,
              ac.build_version,
              'RISK_ACCELERATION_SPIKE',
              CASE
                WHEN ABS(ac.acceleration) >= 25 THEN 'high'
                ELSE 'medium'
              END,
              'Risk acceleration spike: Δ increased by ' || CAST(ROUND(ac.acceleration, 1) AS TEXT),
              json_object(
                'delta_current', ROUND(ac.delta, 2),
                'delta_previous', ROUND(ac.prev_delta, 2),
                'acceleration', ROUND(ac.acceleration, 2)
              )
            FROM acceleration_calc ac
            WHERE ac.build_version = ?
              AND ac.prev_delta IS NOT NULL
              AND ac.acceleration >= 15;
        ''', (current_build, current_build))

        # Get alert counts
        alert_counts = db.execute('''
            SELECT alert_type, COUNT(*) as count
            FROM network_alerts
            WHERE build_version = ?
            GROUP BY alert_type
        ''', (current_build,)).fetchall()

        print(f"   ✅ Alerts generated:")
        for row in alert_counts:
            print(f"      - {row['alert_type']}: {row['count']}")

        # Phase I: Apply exponential smoothing and compute stability coefficient
        print("🔄 Phase I: Apply score smoothing & stability coefficient...")

        # Ensure smoothing columns exist (safe for older SQLite)
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN smoothed_score INTEGER;')
        except Exception:
            pass  # Column already exists

        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN stability_coeff REAL;')
        except Exception:
            pass

        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN smoothing_alpha REAL DEFAULT 0.3;')
        except Exception:
            pass

        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN smoothing_version INTEGER DEFAULT 1;')
        except Exception:
            pass

        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN smoothed_updated_at TIMESTAMP;')
        except Exception:
            pass

        # Phase I.1: Compute exponential smoothing
        # Formula: smooth_t = alpha * raw_t + (1 - alpha) * smooth_{t-1}
        smoothing_alpha = 0.3  # Default smoothing factor

        db.execute('''
            CREATE TEMP TABLE smoothing_data AS
            SELECT
              ns.network_name,
              ns.score as raw_score,
              COALESCE(old.smoothed_score, ns.score) as prev_smoothed,
              ? as alpha
            FROM network_scores ns
            LEFT JOIN (
              SELECT network_name, smoothed_score
              FROM network_scores
              WHERE smoothed_score IS NOT NULL
            ) old ON ns.network_name = old.network_name
              AND old.smoothed_score IS NOT NULL;
        ''', (smoothing_alpha,))

        # Apply smoothing formula
        db.execute('''
            UPDATE network_scores
            SET
              smoothed_score = CAST(ROUND(
                (SELECT alpha FROM smoothing_data WHERE network_name = network_scores.network_name) *
                (SELECT raw_score FROM smoothing_data WHERE network_name = network_scores.network_name) +
                (1 - (SELECT alpha FROM smoothing_data WHERE network_name = network_scores.network_name)) *
                (SELECT prev_smoothed FROM smoothing_data WHERE network_name = network_scores.network_name)
              ) AS INTEGER),
              smoothing_alpha = ?,
              smoothing_version = CASE
                WHEN smoothed_score IS NULL THEN 1
                ELSE smoothing_version
              END,
              smoothed_updated_at = CURRENT_TIMESTAMP
            WHERE network_name IN (SELECT network_name FROM smoothing_data);
        ''', (smoothing_alpha,))

        # Phase I.2: Compute stability coefficient from vol5 (volatility over last 5 transitions)
        # Formula: stability = 1 / (1 + (vol5 / 10)), clamped to [0.1, 1.0]

        db.execute('''
            CREATE TEMP TABLE volatility_data AS
            WITH score_deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as prev_score,
                ABS(h.score - LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version)) as abs_delta,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as delta_count
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            vol5_calc AS (
              SELECT
                network_name,
                CASE
                  WHEN COUNT(*) < 2 THEN 0.0
                  ELSE AVG(abs_delta)
                END as vol5
              FROM score_deltas
              WHERE delta_count >= 2
                AND prev_score IS NOT NULL
              GROUP BY network_name
            )
            SELECT
              network_name,
              vol5,
              MAX(0.1, MIN(1.0, 1.0 / (1.0 + (vol5 / 10.0)))) as stability_coeff
            FROM vol5_calc;
        ''', (current_build,))

        # Update stability coefficient
        db.execute('''
            UPDATE network_scores
            SET
              stability_coeff = (
                SELECT COALESCE(stability_coeff, 1.0) FROM volatility_data
                WHERE volatility_data.network_name = network_scores.network_name
              ),
              smoothed_updated_at = CURRENT_TIMESTAMP
            WHERE network_name IN (SELECT network_name FROM volatility_data);
        ''')

        # For networks without history, set stability to 1.0 (fully stable)
        db.execute('''
            UPDATE network_scores
            SET stability_coeff = 1.0
            WHERE stability_coeff IS NULL;
        ''')

        # Verify smoothing and stability
        smoothing_stats = db.execute('''
            SELECT
              COUNT(*) as total_networks,
              COUNT(CASE WHEN smoothed_score IS NOT NULL THEN 1 END) as smoothed_count,
              COUNT(CASE WHEN stability_coeff IS NOT NULL THEN 1 END) as stability_count,
              ROUND(AVG(stability_coeff), 3) as avg_stability,
              ROUND(MIN(stability_coeff), 3) as min_stability,
              ROUND(MAX(stability_coeff), 3) as max_stability
            FROM network_scores
        ''').fetchone()

        print(f"   ✅ Smoothing applied: {smoothing_stats['smoothed_count']} networks")
        print(f"      Stability coefficient: avg={smoothing_stats['avg_stability']} (min={smoothing_stats['min_stability']}, max={smoothing_stats['max_stability']})")

        # Cleanup temp tables
        db.execute('DROP TABLE IF EXISTS networks_release_prev')
        db.execute('DROP TABLE IF EXISTS network_evidence_prev')
        db.execute('DROP TABLE IF EXISTS network_scores_prev')

        # Get final stats
        final_count = db.execute('SELECT COUNT(*) as cnt FROM networks_release').fetchone()['cnt']
        stats['networks_processed'] = final_count
        stats['new_networks'] = len([r for r in version_check if r['old_version'] is None])

        print("\n✅ Build complete!")
        return stats


def verify_build(db_path: str) -> None:
    """Verify build results."""

    with db_transaction(db_path) as db:
        print("\n🔍 Verification Report")
        print("=" * 60)

        # Network type distribution
        print("\n📊 Network Types:")
        types = db.execute('''
            SELECT network_type, COUNT(*) as count FROM networks_release
            GROUP BY network_type
            ORDER BY count DESC
        ''').fetchall()

        for row in types:
            print(f"   {row['network_type']:.<30} {row['count']:>4} networks")

        # Stability distribution
        print("\n🔄 Stability States:")
        stability = db.execute('''
            SELECT stability_state, COUNT(*) as count FROM networks_release
            GROUP BY stability_state
            ORDER BY count DESC
        ''').fetchall()

        for row in stability:
            print(f"   {row['stability_state']:.<30} {row['count']:>4} networks")

        # Version distribution
        print("\n📈 Build Versions:")
        versions = db.execute('''
            SELECT build_version, COUNT(*) as count FROM networks_release
            GROUP BY build_version
            ORDER BY build_version DESC
        ''').fetchall()

        for row in versions:
            print(f"   version {row['build_version']:>2}:        {row['count']:>4} networks")

        # Growing networks (high interest)
        print("\n🚀 Growing Networks (sample):")
        growing = db.execute('''
            SELECT network_name, network_size, build_version, cex_funder_count, infra_funder_count
            FROM networks_release
            WHERE stability_state = 'growing'
            ORDER BY network_size DESC
            LIMIT 5
        ''').fetchall()

        if growing:
            for row in growing:
                print(f"   {row['network_name']:.<25} {row['network_size']:>4} creators | "
                      f"v{row['build_version']} | CEX:{row['cex_funder_count']} Infra:{row['infra_funder_count']}")
        else:
            print("   (no growing networks in this build)")

        # Sample of new networks
        print("\n🆕 New Networks (sample):")
        new = db.execute('''
            SELECT network_name, network_size, network_type
            FROM networks_release
            WHERE stability_state = 'new'
            LIMIT 5
        ''').fetchall()

        if new:
            for row in new:
                print(f"   {row['network_name']:.<25} {row['network_size']:>4} creators | "
                      f"{row['network_type']}")
        else:
            print("   (no new networks since last build)")

        # Recently changed networks (useful for monitoring/alerting)
        print("\n⏱️  Recently Changed Networks (last 24 hours):")
        recent = db.execute('''
            SELECT
              network_name,
              network_size,
              build_version,
              stability_state,
              last_changed_at
            FROM networks_release
            WHERE last_changed_at IS NOT NULL
            AND last_changed_at > datetime('now', '-1 day')
            ORDER BY last_changed_at DESC
            LIMIT 5
        ''').fetchall()

        if recent:
            for row in recent:
                changed = row['last_changed_at'].split('T')[0] if row['last_changed_at'] else 'unknown'
                print(f"   {row['network_name']:.<25} v{row['build_version']} | {row['stability_state']:<10} | {changed}")
        else:
            print("   (no networks changed in last 24 hours)")

        # Evidence verification (new in Phase F)
        print("\n🔍 Network Evidence Summary:")
        try:
            evidence_summary = db.execute('''
                SELECT
                  COUNT(*) as total_networks,
                  COUNT(CASE WHEN total_edges > 0 THEN 1 END) as networks_with_evidence,
                  ROUND(AVG(evidence_risk_score), 2) as avg_risk_score,
                  COUNT(CASE WHEN evidence_risk_score >= 75 THEN 1 END) as high_risk_networks,
                  COUNT(CASE WHEN evidence_risk_score >= 50 AND evidence_risk_score < 75 THEN 1 END) as medium_risk_networks
                FROM network_evidence
            ''').fetchone()

            print(f"   Total networks: {evidence_summary['total_networks']}")
            print(f"   Networks with evidence: {evidence_summary['networks_with_evidence']}")
            print(f"   Average risk score: {evidence_summary['avg_risk_score']}")
            print(f"   High-risk networks (≥75): {evidence_summary['high_risk_networks']}")
            print(f"   Medium-risk networks (50-74): {evidence_summary['medium_risk_networks']}")

            # Sample high-risk networks
            high_risk = db.execute('''
                SELECT network_name, total_edges, evidence_risk_score
                FROM network_evidence
                WHERE evidence_risk_score >= 75
                ORDER BY evidence_risk_score DESC
                LIMIT 3
            ''').fetchall()

            if high_risk:
                print("\n   ⚠️  High-Risk Networks (sample):")
                for row in high_risk:
                    print(f"      {row['network_name']:.<25} Risk: {row['evidence_risk_score']:.1f} | Edges: {row['total_edges']}")
        except Exception as e:
            print(f"   (evidence data not available: {e})")

        print("\n" + "=" * 60)


if __name__ == '__main__':
    import sys

    db_path = 'pumpswap_tokens.db'

    print("""
╔════════════════════════════════════════════════════════════════╗
║     Build networks_release with Versioning & Stability         ║
║              Phase 1 Implementation (Issues #3-4)              ║
╚════════════════════════════════════════════════════════════════╝
""")

    try:
        stats = build_networks_release(db_path)
        verify_build(db_path)

        print(f"""
📊 Build Statistics:
   Networks processed:   {stats['networks_processed']}
   Networks changed:     {stats['changed_networks']}
   Versions incremented: {stats['versions_incremented']}
   New networks:         {stats['new_networks']}
   Growth spikes (>25%): {len(stats['growth_spikes'])}
""")

        if stats['growth_spikes']:
            print("\n⚠️  Growth Spikes Detected:")
            for spike in sorted(stats['growth_spikes'], key=lambda x: x['delta_pct'], reverse=True):
                print(f"   🚀 {spike['network']}: +{spike['delta_pct']:.1f}% growth")

        print("\n✅ Build successful!")
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Build failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
