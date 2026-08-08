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
import os
import time

from src.core.network_display_names import NetworkDisplayNameBuilder
from src.utils.infra_mapping import ensure_infra_wallets_table


@contextmanager
def db_transaction(db_path):
    """Context manager for safe database transactions."""
    db = sqlite3.connect(db_path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


class PhaseProfiler:
    """Optional performance profiler for build phases (BUILD_PROFILE=1)."""

    def __init__(self):
        self.enabled = os.environ.get('BUILD_PROFILE', '0') == '1'
        self.phases = {}
        self.start_time = time.time() if self.enabled else None

    def mark(self, phase_name):
        """Mark the start of a phase."""
        if not self.enabled:
            return
        self.phases[phase_name] = {'start': time.time(), 'end': None}

    def unmark(self, phase_name):
        """Mark the end of a phase."""
        if not self.enabled or phase_name not in self.phases:
            return
        self.phases[phase_name]['end'] = time.time()

    def report(self):
        """Print profiling report."""
        if not self.enabled or not self.phases:
            return

        print("\n" + "="*60)
        print("⏱️  Build Profile Report (BUILD_PROFILE=1)")
        print("="*60)

        total_time = time.time() - self.start_time

        for phase_name in sorted(self.phases.keys()):
            phase = self.phases[phase_name]
            if phase['end']:
                elapsed = (phase['end'] - phase['start']) * 1000  # ms
                pct = (elapsed / total_time * 100) if total_time > 0 else 0
                print(f"  {phase_name:20s} {elapsed:8.2f}ms  ({pct:5.1f}%)")

        print(f"  {'TOTAL':20s} {total_time*1000:8.2f}ms")
        print("="*60 + "\n")


def _ensure_column(db, table: str, col: str, col_type: str):
    """Idempotently add a column to a table if it doesn't exist."""
    cur = db.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}  # r[1] = column name
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


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


def phase_h1_snapshot_score_history(db):
    """
    Phase H.1 — Snapshot score history for ALL networks each run using build_tick.

    Replaces the old H.1 logic that used nr.build_version as the history key.
    That approach resulted in sparse history (only changed networks recorded).

    New approach:
    - build_tick is the global per-run time axis (increments every successful build)
    - Snapshot ALL networks to network_score_history(network_name, build_tick)
    - Preserves network_version = networks_release.build_version (Phase C structural)
    - Enables stability/trend calculations for all networks

    Returns:
        dict with build_tick and history_rows_written
    """

    # 1) Ensure schema (idempotent)
    _ensure_column(db, "network_score_history", "build_tick", "INTEGER")
    _ensure_column(db, "network_score_history", "network_version", "INTEGER")

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_score_history_network_tick
        ON network_score_history(network_name, build_tick)
    """)

    # Optional perf indexes
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_score_history_tick
        ON network_score_history(build_tick)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_score_history_network_tick_desc
        ON network_score_history(network_name, build_tick DESC)
    """)

    # 2) Compute global build_tick (time axis)
    row = db.execute(
        "SELECT COALESCE(MAX(build_tick), 0) + 1 FROM network_score_history"
    ).fetchone()
    build_tick = int(row[0])

    # 3) Snapshot ALL networks for this build_tick
    #
    # Challenge: PRIMARY KEY is (network_name, build_version), but we want to snapshot
    # all networks per build_tick (time axis). Multiple build_ticks can have the same
    # network_name but would have conflicting PRIMARY KEYs if we reuse build_version.
    #
    # Solution: Update build_version to build_tick when inserting, so the PRIMARY KEY
    # becomes unique. This allows us to keep all historical snapshots.
    # We use INSERT OR REPLACE with updated build_version to make each tick unique.
    #
    # Note: This means build_version in the history will match build_tick, NOT the
    # structural build_version from Phase C. Both are stored:
    #   - build_version = build_tick (used as PK)
    #   - network_version = nr.build_version (structural, from Phase C)

    # First, check if this tick already exists
    existing = db.execute(
        "SELECT COUNT(*) as cnt FROM network_score_history WHERE build_tick = ?",
        (build_tick,)
    ).fetchone()['cnt']

    if existing > 0:
        # Already snapshotted this tick, skip
        return {
            "build_tick": build_tick,
            "history_rows_written": 0,
        }

    # Insert all networks with this build_tick
    inserted = db.execute("""
        INSERT OR REPLACE INTO network_score_history (
            network_name,
            build_version,
            build_tick,
            network_version,
            score,
            score_version,
            components_json,
            computed_at
        )
        SELECT
            nr.network_name,
            ? AS build_version,
            ? AS build_tick,
            nr.build_version AS network_version,
            ns.score,
            ns.score_version,
            ns.score_components_json,
            COALESCE(ns.computed_at, CURRENT_TIMESTAMP)
        FROM networks_release nr
        LEFT JOIN network_scores ns
          ON ns.network_name = nr.network_name
    """, (build_tick, build_tick)).rowcount

    return {
        "build_tick": build_tick,
        "history_rows_written": inserted,
    }


def build_networks_release(db_path: str) -> dict:
    """
    Build networks_release with version tracking and stability states.

    Returns:
        dict with build statistics and status

    Performance Profiling:
        Optional timing via BUILD_PROFILE=1 environment variable
        Prints per-phase execution time and total build time
    """

    profiler = PhaseProfiler()

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
        'build_tick': None,  # Global per-run time axis for score history
        'history_rows_written': 0,  # Rows inserted into network_score_history
        'errors': [],
        'infra_sync_status': None,  # X78.14 -- health signal, see below
    }

    # X78.14 -- root cause of a creator_funding_worker stall (X78.13):
    # sync_infra_wallets() previously ran here, inside this function's own
    # open write transaction. Its read phase alone is a documented ~2min,
    # three-full-SELECT-DISTINCT scan of token_analysis (1.6M+ rows) --
    # sync_infra_wallets()'s own docstring already warns callers not to run
    # it on a connection that's holding a write lease, but this call site
    # did exactly that, invoked (via creator_funding_worker's post-extraction
    # enrichment) from an untimed asyncio.to_thread with nothing to bound it.
    #
    # build_networks_release only ever READS infra_wallets (NOT IN
    # subqueries below) -- it never required the sync to have just run.
    # X78.8's caller census already established every sync_infra_wallets()
    # consumer, including this one, tolerates "last successful state": the
    # underlying source columns are append-only, so bounded staleness only
    # means a brand-new infra wallet is briefly treated as non-infra until
    # the next refresh -- a self-correcting lag, not a correctness break.
    # Refresh ownership belongs entirely to the standalone, independently
    # supervised src.core.infra_sync_scheduler process (same pattern
    # risk_scoring_builder.score_creator_now already adopted in X78.8).
    # This function now only ensures the table exists (so the NOT IN
    # queries below never fail on a freshly-created database) and exposes
    # the scheduler's own staleness signal in stats -- it performs no scan
    # and holds no write lease across a scan, ever.
    with db_transaction(db_path) as db:
        ensure_infra_wallets_table(db)
        try:
            from src.core.infra_sync_scheduler import get_status as _infra_sync_status
            stats['infra_sync_status'] = _infra_sync_status()
        except Exception as e:
            stats['infra_sync_status'] = {"status": "error", "error": str(e)}
        profiler.mark('Phase A: Snapshot')
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
        profiler.unmark('Phase A: Snapshot')

        # Phase A.1: Ensure system_metadata table exists (Phase 10)
        try:
            db.execute('''
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        except Exception as e:
            print(f"   ⚠️  system_metadata table creation skipped: {e}")

        # Phase B: Compute new state (Phases 1-4 from Step 3)
        profiler.mark('Phase B: Compute state')
        print("🔄 Phase B: Compute new network state...")

        # Phase B.0: Prune stale networks — remove any networks_release row that has
        # no current rows in network_membership (orphans from legacy manual seeds).
        pruned = db.execute('''
            DELETE FROM networks_release
            WHERE network_name NOT IN (
                SELECT DISTINCT network_name FROM network_membership
            )
        ''').rowcount
        if pruned:
            print(f"   Pruned {pruned} orphaned networks from networks_release")

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
        profiler.unmark('Phase B: Compute state')

        # Phase D: Compute stability state based on deltas
        # BEFORE incrementing versions (critical ordering)
        profiler.mark('Phase D: Stability')
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
        profiler.unmark('Phase D: Stability')
        profiler.mark('Phase C: Versions')
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
        profiler.unmark('Phase C: Versions')
        db.execute('UPDATE networks_release SET last_built_at = CURRENT_TIMESTAMP')

        # Phase F: Aggregate network evidence (rollup table)
        profiler.mark('Phase F: Evidence')
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
                    0 as total_evidence_txs,
                    ROUND(AVG(cce.confidence), 2) as avg_confidence,
                    COUNT(CASE WHEN cce.confidence >= 75 THEN 1 END) as high_conf,
                    COUNT(CASE WHEN cce.confidence >= 50 AND cce.confidence < 75 THEN 1 END) as med_conf,
                    COUNT(CASE WHEN cce.confidence < 50 THEN 1 END) as low_conf,
                    NULL as earliest_time,
                    NULL as latest_time,
                    COUNT(DISTINCT cce.bridge_funder) as unique_funders,
                    GROUP_CONCAT(DISTINCT cce.bridge_funder) as funder_list
                  FROM network_membership nm
                  LEFT JOIN coordinated_creator_edges cce
                    ON (nm.creator_address = cce.creator_a OR nm.creator_address = cce.creator_b)
                   AND cce.bridge_funder NOT IN (SELECT address FROM infra_wallets)
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
                          CASE WHEN nm_count.creator_count * 10 < 50 THEN 50 ELSE nm_count.creator_count * 10 END AS FLOAT
                        )) * 40 +
                        (COALESCE(ne.avg_confidence, 0) / 100.0) * 40 +
                        CASE
                          WHEN COALESCE(
                            CAST((ne.latest_time - ne.earliest_time) / 86400.0 AS INTEGER),
                            0
                          ) <= 1 THEN 20
                          WHEN COALESCE(
                            CAST((ne.latest_time - ne.earliest_time) / 86400.0 AS INTEGER),
                            0
                          ) <= 7 THEN 15
                          WHEN COALESCE(
                            CAST((ne.latest_time - ne.earliest_time) / 86400.0 AS INTEGER),
                            0
                          ) <= 30 THEN 10
                          ELSE 5
                        END
                      ),
                      2
                    ) as risk_score
                  FROM network_edges ne
                  CROSS JOIN (SELECT COUNT(DISTINCT creator_address) as creator_count FROM network_membership) nm_count
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
            import traceback
            print(f"   ⚠️  Evidence aggregation skipped: {e}")
            print(f"\n   Full traceback:")
            print(traceback.format_exc())
            stats['errors'].append(f"Evidence aggregation: {str(e)}")

        profiler.unmark('Phase F: Evidence')

        # Phase G: Compute network scores (deterministic, precomputed)
        profiler.mark('Phase G: Scores')
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

        # Define severity modulation function (Phase 7C)
        # Returns adjusted severity based on stability_coeff
        def apply_severity_modulation(base_severity, stability_coeff):
            """
            Apply Phase 7C severity modulation based on stability_coeff.

            Rules:
            - If stability_coeff < 0.3: bump severity up one level
            - If stability_coeff > 0.8: bump severity down one level
            - Otherwise: keep base severity

            Severity levels: low < medium < high
            """
            if stability_coeff is None:
                return base_severity

            severity_map = {'low': 0, 'medium': 1, 'high': 2}
            reverse_map = {0: 'low', 1: 'medium', 2: 'high'}

            base_level = severity_map.get(base_severity, 1)

            if stability_coeff < 0.3:
                # Unstable: bump up (low→medium, medium→high, high→high)
                final_level = min(base_level + 1, 2)
            elif stability_coeff > 0.8:
                # Very stable: bump down (high→medium, medium→low, low→low)
                final_level = max(base_level - 1, 0)
            else:
                # Moderate stability: no change
                final_level = base_level

            return reverse_map.get(final_level, base_severity)

        # Phase H: Generate monitoring history and alerts
        profiler.unmark('Phase G: Scores')
        profiler.mark('Phase H: Alerts')
        print("🔄 Phase H: Generate monitoring history and alerts...")

        # Ensure Phase 7A columns exist (needed for stability-aware alerting in Phase 7C)
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN stability_coeff REAL;')
        except Exception:
            pass  # Column already exists
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN smoothed_score INTEGER;')
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

        # I) STABILITY_DROP (Phase 7C): stability_coeff decreased by >= 0.25
        # Requires prev_stability to exist and curr smoothed_score >= 50
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH stability_history AS (
              SELECT
                ns.network_name,
                ns.stability_coeff as curr_stability,
                (SELECT stability_coeff FROM network_scores p
                 WHERE p.network_name = ns.network_name
                 AND p.network_name IN (
                   SELECT DISTINCT network_name FROM network_score_history
                   WHERE build_version = (? - 1)
                 )) as prev_stability,
                ns.smoothed_score,
                COALESCE(ns.smoothed_score, ns.score) as display_smoothed
              FROM network_scores ns
              WHERE EXISTS (
                SELECT 1 FROM network_score_history
                WHERE network_name = ns.network_name
                AND build_version = ?
              )
            ),
            stability_drops AS (
              SELECT
                sh.network_name,
                sh.curr_stability,
                sh.prev_stability,
                sh.prev_stability - sh.curr_stability as stability_delta,
                sh.display_smoothed
              FROM stability_history sh
              WHERE sh.prev_stability IS NOT NULL
                AND (sh.prev_stability - sh.curr_stability) >= 0.25
                AND sh.display_smoothed >= 50
            )
            SELECT
              sd.network_name,
              ?,
              'STABILITY_DROP',
              CASE
                WHEN sd.stability_delta >= 0.40 THEN 'high'
                ELSE 'medium'
              END,
              'Stability dropped: ' || PRINTF('%.2f', sd.prev_stability) || ' → ' || PRINTF('%.2f', sd.curr_stability) || ' (Δ -' || PRINTF('%.2f', sd.stability_delta) || ')',
              json_object(
                'prev_stability', ROUND(sd.prev_stability, 3),
                'curr_stability', ROUND(sd.curr_stability, 3),
                'drop', ROUND(sd.stability_delta, 3),
                'curr_smoothed', sd.display_smoothed
              )
            FROM stability_drops sd;
        ''', (current_build, current_build - 1, current_build))

        # J) STABILITY_TREND_DOWN (Phase 7D): Stability decreasing strictly over 3 builds
        # Requires S_t < S_t-1 < S_t-2 (strictly decreasing) with total drop >= 0.20
        # Requires non-null stability for all 3 builds and current smoothed_score >= 50
        #
        # ARCHITECTURAL NOTE (Data Source):
        # This query joins networks_release (current build) with network_scores (current state).
        # LAG window functions retrieve previous build stability values from network_scores snapshots.
        # This works for sequential builds because network_scores holds the latest state after each Phase I.
        #
        # If future features require backfill, parallel builds, or retroactive corrections,
        # consider migrating to query network_score_history (which stores historical values).
        # See PHASE7D_ARCHITECTURAL_NOTES.md for upgrade path and alternatives.
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH stability_trends AS (
              SELECT
                ns.network_name,
                ns.stability_coeff as s_t,
                LAG(ns.stability_coeff, 1) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version) as s_t_minus_1,
                LAG(ns.stability_coeff, 2) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version) as s_t_minus_2,
                COALESCE(ns.smoothed_score, ns.score) as display_smoothed,
                nr.build_version,
                COUNT(*) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as history_count
              FROM networks_release nr
              LEFT JOIN network_scores ns ON nr.network_name = ns.network_name
              WHERE nr.build_version <= ?
            ),
            trend_down_candidates AS (
              SELECT
                st.network_name,
                st.s_t,
                st.s_t_minus_1,
                st.s_t_minus_2,
                st.s_t_minus_2 - st.s_t as total_drop,
                st.display_smoothed,
                st.build_version
              FROM stability_trends st
              WHERE st.history_count >= 3
                AND st.s_t IS NOT NULL
                AND st.s_t_minus_1 IS NOT NULL
                AND st.s_t_minus_2 IS NOT NULL
                AND st.s_t < st.s_t_minus_1
                AND st.s_t_minus_1 < st.s_t_minus_2
                AND (st.s_t_minus_2 - st.s_t) >= 0.20
                AND st.display_smoothed >= 50
            )
            SELECT
              tdc.network_name,
              ?,
              'STABILITY_TREND_DOWN',
              CASE
                WHEN tdc.total_drop >= 0.35 THEN 'high'
                ELSE 'medium'
              END,
              'Stability trending down: ' || PRINTF('%.2f', tdc.s_t_minus_2) || ' → ' || PRINTF('%.2f', tdc.s_t) || ' over 3 builds',
              json_object(
                'stability_t_minus_2', ROUND(tdc.s_t_minus_2, 3),
                'stability_t_minus_1', ROUND(tdc.s_t_minus_1, 3),
                'stability_t', ROUND(tdc.s_t, 3),
                'total_drop', ROUND(tdc.total_drop, 3)
              )
            FROM trend_down_candidates tdc;
        ''', (current_build, current_build))

        # K) STABILITY_TREND_UP (Phase 7D): Stability increasing strictly over 3 builds
        # Requires S_t > S_t-1 > S_t-2 (strictly increasing) with total increase >= 0.20
        # Requires non-null stability for all 3 builds (no smoothed_score threshold)
        #
        # ARCHITECTURAL NOTE (Data Source):
        # Same as STABILITY_TREND_DOWN - uses network_scores with LAG window functions.
        # See PHASE7D_ARCHITECTURAL_NOTES.md for upgrade path if backfill or parallel processing needed.
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH stability_trends AS (
              SELECT
                ns.network_name,
                ns.stability_coeff as s_t,
                LAG(ns.stability_coeff, 1) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version) as s_t_minus_1,
                LAG(ns.stability_coeff, 2) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version) as s_t_minus_2,
                nr.build_version,
                COUNT(*) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as history_count
              FROM networks_release nr
              LEFT JOIN network_scores ns ON nr.network_name = ns.network_name
              WHERE nr.build_version <= ?
            ),
            trend_up_candidates AS (
              SELECT
                st.network_name,
                st.s_t,
                st.s_t_minus_1,
                st.s_t_minus_2,
                st.s_t - st.s_t_minus_2 as total_increase,
                st.build_version
              FROM stability_trends st
              WHERE st.history_count >= 3
                AND st.s_t IS NOT NULL
                AND st.s_t_minus_1 IS NOT NULL
                AND st.s_t_minus_2 IS NOT NULL
                AND st.s_t > st.s_t_minus_1
                AND st.s_t_minus_1 > st.s_t_minus_2
                AND (st.s_t - st.s_t_minus_2) >= 0.20
            )
            SELECT
              tuc.network_name,
              ?,
              'STABILITY_TREND_UP',
              CASE
                WHEN tuc.total_increase >= 0.35 THEN 'low'
                ELSE 'medium'
              END,
              'Stability improving: ' || PRINTF('%.2f', tuc.s_t_minus_2) || ' → ' || PRINTF('%.2f', tuc.s_t) || ' over 3 builds',
              json_object(
                'stability_t_minus_2', ROUND(tuc.s_t_minus_2, 3),
                'stability_t_minus_1', ROUND(tuc.s_t_minus_1, 3),
                'stability_t', ROUND(tuc.s_t, 3),
                'total_increase', ROUND(tuc.total_increase, 3)
              )
            FROM trend_up_candidates tuc;
        ''', (current_build, current_build))

        # Phase 7C: Apply severity modulation based on stability_coeff
        # Modulation rules:
        # - stability_coeff < 0.3: bump severity up one level
        # - stability_coeff > 0.8: bump severity down one level
        db.execute('''
            UPDATE network_alerts
            SET severity = CASE
              WHEN (
                SELECT stability_coeff FROM network_scores
                WHERE network_name = network_alerts.network_name
              ) < 0.3 THEN
                CASE severity
                  WHEN 'low' THEN 'medium'
                  WHEN 'medium' THEN 'high'
                  ELSE 'high'
                END
              WHEN (
                SELECT stability_coeff FROM network_scores
                WHERE network_name = network_alerts.network_name
              ) > 0.8 THEN
                CASE severity
                  WHEN 'high' THEN 'medium'
                  WHEN 'medium' THEN 'low'
                  ELSE 'low'
                END
              ELSE severity
            END
            WHERE build_version = ?
              AND (
                SELECT stability_coeff FROM network_scores
                WHERE network_name = network_alerts.network_name
              ) IS NOT NULL;
        ''', (current_build,))

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

        # Phase 8B: Escalation Rules (Deterministic, Build-Time)
        print("🔄 Phase 8B: Applying escalation rules...")

        try:
            # Design note on suppression behavior:
            # Suppression is durable (not time-based expiry). If an operator suppresses an alert,
            # it stays suppressed until explicitly unsuppressed. Escalation respects this decision.
            # This prevents escalation from reactivating suppressed alerts after expiry.
            # If time-based auto-unsuppress is needed in future, change to:
            #   AND (suppressed_until IS NULL OR suppressed_until <= CURRENT_TIMESTAMP)

            # R1_CRITICAL_HIGH: risk_band = CRITICAL AND severity = high
            db.execute('''
                UPDATE network_alerts
                SET is_escalated = 1,
                    escalated_at = COALESCE(escalated_at, CURRENT_TIMESTAMP),
                    escalation_rule = 'R1_CRITICAL_HIGH',
                    escalation_reason = 'Risk band CRITICAL with high severity'
                WHERE build_version = ?
                  AND is_escalated = 0
                  AND acknowledged = 0
                  AND suppressed_until IS NULL
                  AND severity = 'high'
                  AND (SELECT COALESCE(risk_band, 'LOW') FROM network_scores WHERE network_name = network_alerts.network_name) = 'CRITICAL';
            ''', (current_build,))

            # R2_STABILITY_REPEATED: >= 2 STABILITY_DROP alerts in last 3 builds
            db.execute('''
                UPDATE network_alerts
                SET is_escalated = 1,
                    escalated_at = COALESCE(escalated_at, CURRENT_TIMESTAMP),
                    escalation_rule = 'R2_STABILITY_REPEATED',
                    escalation_reason = 'Repeated stability drops detected'
                WHERE build_version = ?
                  AND is_escalated = 0
                  AND acknowledged = 0
                  AND suppressed_until IS NULL
                  AND alert_type = 'STABILITY_DROP'
                  AND network_name IN (
                    SELECT network_name FROM network_alerts
                    WHERE alert_type = 'STABILITY_DROP'
                      AND build_version >= (? - 2)
                      AND build_version <= ?
                    GROUP BY network_name
                    HAVING COUNT(*) >= 2
                  );
            ''', (current_build, current_build, current_build))

            # R3_ACCEL_UNSTABLE: (SCORE_SPIKE or RISK_ACCELERATION_SPIKE) AND stability < 0.30 AND smoothed_score >= 60
            db.execute('''
                UPDATE network_alerts
                SET is_escalated = 1,
                    escalated_at = COALESCE(escalated_at, CURRENT_TIMESTAMP),
                    escalation_rule = 'R3_ACCEL_UNSTABLE',
                    escalation_reason = 'Score acceleration with high instability'
                WHERE build_version = ?
                  AND is_escalated = 0
                  AND acknowledged = 0
                  AND suppressed_until IS NULL
                  AND alert_type IN ('SCORE_SPIKE', 'RISK_ACCELERATION_SPIKE')
                  AND (SELECT stability_coeff FROM network_scores WHERE network_name = network_alerts.network_name) < 0.30
                  AND (SELECT COALESCE(smoothed_score, score) FROM network_scores WHERE network_name = network_alerts.network_name) >= 60;
            ''', (current_build,))

            escalated_count = db.execute('''
                SELECT COUNT(*) as count FROM network_alerts
                WHERE build_version = ? AND is_escalated = 1
            ''', (current_build,)).fetchone()['count']

            print(f"   ✅ Escalation rules applied: {escalated_count} alerts escalated")

        except Exception as e:
            # Phase 8B columns may not exist in older test databases
            # This is safe because escalation is optional and build-time only
            print(f"   ⚠️  Escalation rules skipped: {e}")

        # Phase I: Apply exponential smoothing and compute stability coefficient
        profiler.unmark('Phase H: Alerts')
        profiler.mark('Phase I: Smoothing')
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

        # Phase 7E: Ensure trend and risk band columns exist (added after Phase I computation)
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN stability_trend REAL;')
        except Exception:
            pass  # Column already exists
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN trend_direction TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN risk_band TEXT;')
        except Exception:
            pass

        # Phase 8B: Ensure alert lifecycle & escalation columns exist (idempotent)
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged_by TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_until TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppression_reason TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_by TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN is_escalated INTEGER NOT NULL DEFAULT 0;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalated_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalation_reason TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalation_rule TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN operator_note TEXT;')
        except Exception:
            pass

        # Add indexes for alert lifecycle operations (Phase 8B)
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_unacked_created ON network_alerts(acknowledged, created_at DESC);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_suppressed_until ON network_alerts(suppressed_until);')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_escalated_created ON network_alerts(is_escalated, created_at DESC);')

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
        profiler.unmark('Phase I: Smoothing')

        # Phase J: Compute trend metrics and risk band classification
        profiler.mark('Phase J: Trends')
        print("🔄 Phase J: Computing trend metrics and risk band classification...")

        # Phase J.1: Compute stability_trend = S_t - S_t-2 using window functions
        # Requires at least 3 builds; if insufficient history, stability_trend is NULL
        db.execute('''
            CREATE TEMP TABLE stability_trends AS
            SELECT
              ns.network_name,
              ns.stability_coeff as s_t,
              LAG(ns.stability_coeff, 2) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version) as s_t_minus_2,
              ns.stability_coeff - LAG(ns.stability_coeff, 2) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version) as stability_trend,
              COUNT(*) OVER (PARTITION BY ns.network_name ORDER BY nr.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as history_count,
              ns.smoothed_score,
              COALESCE(ns.smoothed_score, ns.score) as display_smoothed
            FROM networks_release nr
            LEFT JOIN network_scores ns ON nr.network_name = ns.network_name
            WHERE nr.build_version <= ?
        ''', (current_build,))

        # Phase J.2: Update stability_trend (NULL if < 3 builds)
        db.execute('''
            UPDATE network_scores
            SET stability_trend = (
              SELECT CASE
                WHEN st.history_count >= 3 THEN st.stability_trend
                ELSE NULL
              END
              FROM stability_trends st
              WHERE st.network_name = network_scores.network_name
            )
            WHERE network_name IN (SELECT DISTINCT network_name FROM stability_trends);
        ''')

        # Phase J.3: Compute trend_direction based on stability_trend
        # DOWN: stability_trend <= -0.20
        # UP: stability_trend >= +0.20
        # FLAT: otherwise (including NULL)
        db.execute('''
            UPDATE network_scores
            SET trend_direction = CASE
              WHEN stability_trend <= -0.20 THEN 'DOWN'
              WHEN stability_trend >= 0.20 THEN 'UP'
              ELSE 'FLAT'
            END
            WHERE network_name IN (SELECT network_name FROM stability_trends);
        ''')

        # Phase J.4: Compute risk_band using deterministic CASE logic
        # Priority order: CRITICAL > ELEVATED > MODERATE > LOW
        #
        # CRITICAL:
        #   smoothed_score >= 75
        #   OR (smoothed_score >= 60 AND stability_coeff < 0.30)
        #
        # ELEVATED:
        #   smoothed_score BETWEEN 50 AND 74
        #   OR (smoothed_score >= 40 AND stability_coeff < 0.50)
        #
        # MODERATE:
        #   smoothed_score BETWEEN 25 AND 49
        #
        # LOW:
        #   smoothed_score < 25
        db.execute('''
            UPDATE network_scores
            SET risk_band = CASE
              -- CRITICAL: high score OR (moderate-high score with instability)
              WHEN COALESCE(smoothed_score, score) >= 75 THEN 'CRITICAL'
              WHEN (COALESCE(smoothed_score, score) >= 60 AND stability_coeff < 0.30) THEN 'CRITICAL'
              -- ELEVATED: medium-high score OR (moderate score with instability)
              WHEN COALESCE(smoothed_score, score) BETWEEN 50 AND 74 THEN 'ELEVATED'
              WHEN (COALESCE(smoothed_score, score) >= 40 AND stability_coeff < 0.50) THEN 'ELEVATED'
              -- MODERATE: medium score range
              WHEN COALESCE(smoothed_score, score) BETWEEN 25 AND 49 THEN 'MODERATE'
              -- LOW: low score
              WHEN COALESCE(smoothed_score, score) < 25 THEN 'LOW'
              -- Fallback (shouldn't happen)
              ELSE 'LOW'
            END
            WHERE network_name IN (SELECT network_name FROM stability_trends);
        ''')

        # Verify trend and risk band computation
        trend_stats = db.execute('''
            SELECT
              COUNT(*) as total_networks,
              COUNT(CASE WHEN stability_trend IS NOT NULL THEN 1 END) as trend_computed,
              COUNT(CASE WHEN trend_direction IS NOT NULL THEN 1 END) as direction_computed,
              COUNT(CASE WHEN risk_band IS NOT NULL THEN 1 END) as risk_band_computed,
              COUNT(CASE WHEN trend_direction = 'UP' THEN 1 END) as trend_up_count,
              COUNT(CASE WHEN trend_direction = 'DOWN' THEN 1 END) as trend_down_count,
              COUNT(CASE WHEN trend_direction = 'FLAT' THEN 1 END) as trend_flat_count,
              COUNT(CASE WHEN risk_band = 'CRITICAL' THEN 1 END) as critical_count,
              COUNT(CASE WHEN risk_band = 'ELEVATED' THEN 1 END) as elevated_count,
              COUNT(CASE WHEN risk_band = 'MODERATE' THEN 1 END) as moderate_count,
              COUNT(CASE WHEN risk_band = 'LOW' THEN 1 END) as low_count
            FROM network_scores
        ''').fetchone()

        print(f"   ✅ Trend metrics: {trend_stats['trend_computed']} networks computed")
        print(f"      Direction: UP={trend_stats['trend_up_count']}, DOWN={trend_stats['trend_down_count']}, FLAT={trend_stats['trend_flat_count']}")
        print(f"   ✅ Risk bands assigned:")
        print(f"      CRITICAL={trend_stats['critical_count']}, ELEVATED={trend_stats['elevated_count']}, MODERATE={trend_stats['moderate_count']}, LOW={trend_stats['low_count']}")

        # Phase K: Create performance indexes (Phase 9A optimization)
        # All indexes use IF NOT EXISTS for idempotency
        try:
            # Alert indexes (high priority)
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_alerts_created_at_desc ON network_alerts(created_at DESC);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_alerts_acknowledged_created ON network_alerts(acknowledged, created_at DESC);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_alerts_escalated_created ON network_alerts(is_escalated, created_at DESC);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_alerts_severity_type_created ON network_alerts(severity, alert_type, created_at DESC);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_alerts_suppressed_until ON network_alerts(suppressed_until);')

            # Network score indexes (medium priority)
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_risk_band ON network_scores(risk_band, smoothed_score DESC);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_trend ON network_scores(stability_trend DESC);')

            # Score history indexes (medium priority)
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_score_history_build_version ON network_score_history(build_version DESC);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_network_score_history_network_build ON network_score_history(network_name, build_version DESC);')

        except Exception as e:
            print(f"   ⚠️  Index creation skipped: {e}")

        # Cleanup temp tables
        db.execute('DROP TABLE IF EXISTS networks_release_prev')
        db.execute('DROP TABLE IF EXISTS network_evidence_prev')
        db.execute('DROP TABLE IF EXISTS network_scores_prev')

        # Get final stats
        final_count = db.execute('SELECT COUNT(*) as cnt FROM networks_release').fetchone()['cnt']
        stats['networks_processed'] = final_count
        stats['new_networks'] = len([r for r in version_check if r['old_version'] is None])

        profiler.unmark('Phase J: Trends')

        # Phase H.1: Snapshot score history for ALL networks (build_tick time axis)
        profiler.mark('Phase H.1: History Snapshot')
        print("🔄 Phase H.1: Snapshot score history for all networks...")

        h1_result = phase_h1_snapshot_score_history(db)
        stats['build_tick'] = h1_result['build_tick']
        stats['history_rows_written'] = h1_result['history_rows_written']

        print(f"   ✅ Score history snapshot: {h1_result['history_rows_written']} rows written")
        print(f"      build_tick: {h1_result['build_tick']}")

        profiler.unmark('Phase H.1: History Snapshot')
        profiler.report()

        # Phase L: Record build metadata (Phase 10 - Version 1.0 Governance)
        # Record system versioning, build timing, and counts for operational tracking
        # All updates idempotent (INSERT OR REPLACE pattern)
        try:
            build_duration_ms = int((time.time() - profiler.start_time) * 1000) if profiler.enabled else None

            # Get current build version for metadata
            current_build = db.execute('SELECT MAX(build_version) FROM network_score_history').fetchone()[0] or 0

            # Count stats for this build
            alerts_count = db.execute('''
                SELECT COUNT(*) as cnt FROM network_alerts
                WHERE build_version = ?
            ''', (current_build,)).fetchone()['cnt']

            escalations_count = db.execute('''
                SELECT COUNT(*) as cnt FROM network_alerts
                WHERE build_version = ? AND is_escalated = 1
            ''', (current_build,)).fetchone()['cnt']

            networks_count = db.execute('SELECT COUNT(*) as cnt FROM network_scores').fetchone()['cnt']

            now = datetime.utcnow().isoformat()

            # Update system metadata (all idempotent)
            metadata_updates = [
                ('SYSTEM_VERSION', '1.0.0'),
                ('SCHEMA_VERSION', '10'),
                ('BUILD_PIPELINE_VERSION', '10'),
                ('LAST_BUILD_VERSION', str(current_build)),
                ('LAST_BUILD_AT', now),
                ('LAST_BUILD_NETWORKS_PROCESSED', str(networks_count)),
                ('LAST_BUILD_ALERTS_INSERTED', str(alerts_count)),
                ('LAST_BUILD_ESCALATIONS_SET', str(escalations_count)),
            ]

            # Only add duration if profiler was enabled (accurate measurement)
            if build_duration_ms is not None:
                metadata_updates.append(('LAST_BUILD_DURATION_MS', str(build_duration_ms)))

            for key, value in metadata_updates:
                db.execute('''
                    INSERT OR REPLACE INTO system_metadata (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', (key, value))

            print(f"\n📊 Build Metadata Recorded:")
            print(f"   Version: 1.0.0 (Phase 10)")
            print(f"   Build: version {current_build}")
            print(f"   Networks: {networks_count}")
            print(f"   Alerts: {alerts_count}")
            print(f"   Escalations: {escalations_count}")
            if build_duration_ms:
                print(f"   Duration: {build_duration_ms}ms")

        except Exception as e:
            print(f"   ⚠️  Metadata recording failed: {e}")
            stats['errors'].append(f"Metadata recording: {str(e)}")

        # Stamp second-hop bridge counts onto networks_release (Phase 1 integration).
        # upstream_network_bridge may not exist yet (pre-Phase-1 deploys) — safe to skip.
        try:
            db.execute("""
                UPDATE networks_release
                SET
                    second_hop_bridge_count = (
                        SELECT COUNT(*)
                        FROM upstream_network_bridge unb
                        WHERE (unb.network_a = networks_release.network_name
                           OR unb.network_b = networks_release.network_name)
                          AND unb.upstream_address NOT IN (SELECT address FROM infra_wallets)
                    ),
                    max_second_hop_confidence = (
                        SELECT COALESCE(MAX(unb.confidence_score), 0)
                        FROM upstream_network_bridge unb
                        WHERE (unb.network_a = networks_release.network_name
                           OR unb.network_b = networks_release.network_name)
                          AND unb.upstream_address NOT IN (SELECT address FROM infra_wallets)
                    )
            """)
        except Exception as _e:
            # upstream_network_bridge not yet created — ignore
            pass

        # Phase M: Human-readable display names for UI labels.
        # network_name remains the canonical stable ID for joins and URLs.
        try:
            display_names = NetworkDisplayNameBuilder().build(db)
            stats['display_names_updated'] = len(display_names)
            print(f"   ✅ Display names updated: {len(display_names)} networks")
        except Exception as e:
            print(f"   ⚠️  Display name build failed: {e}")
            stats['errors'].append(f"Display names: {str(e)}")

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
