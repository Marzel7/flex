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
        db.execute('''
            CREATE TEMP TABLE stability_deltas AS
            SELECT
              nr.network_name,
              nr.network_size,
              old.network_size as old_size,
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
        db.execute('''
            CREATE TEMP TABLE version_updates AS
            SELECT
              nr.network_name,
              CASE
                WHEN old.network_name IS NULL THEN 1
                WHEN nr.network_size != old.network_size THEN old.build_version + 1
                WHEN nr.network_type != old.network_type THEN old.build_version + 1
                ELSE old.build_version
              END as new_version
            FROM networks_release nr
            LEFT JOIN networks_release_prev old ON nr.network_name = old.network_name;
        ''')

        # Update from temp table
        db.execute('''
            UPDATE networks_release
            SET build_version = (
              SELECT new_version FROM version_updates
              WHERE version_updates.network_name = networks_release.network_name
            )
            WHERE network_name IN (SELECT network_name FROM version_updates);
        ''')

        # Check version changes
        version_check = db.execute('''
            SELECT nr.network_name, nr.build_version, old.build_version as old_version
            FROM networks_release nr
            LEFT JOIN networks_release_prev old ON nr.network_name = old.network_name
            WHERE (old.build_version IS NULL OR nr.build_version != old.build_version)
            ORDER BY nr.build_version DESC
            LIMIT 20
        ''').fetchall()

        print(f"   ✅ Version updates: {len(version_check)} networks changed")
        if version_check:
            print(f"      Top changes (new/incremented networks):")
            for row in version_check[:5]:
                old_v = row['old_version'] or 'NEW'
                new_v = row['build_version']
                print(f"      - {row['network_name']}: v{old_v} → v{new_v}")
            stats['versions_incremented'] = len(version_check)

        # Phase E: Finalize (update timestamp)
        db.execute('UPDATE networks_release SET last_built_at = CURRENT_TIMESTAMP')

        # Cleanup temp table
        db.execute('DROP TABLE IF EXISTS networks_release_prev')

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
   Versions incremented: {stats['versions_incremented']}
   New networks:         {stats['new_networks']}

✅ Build successful!
""")
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Build failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
