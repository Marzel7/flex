"""
SecondHopLiteQueueBuilder — Phase 2 Lite.

Scores non-CEX funders by coordination signal strength and enqueues them
for RPC-based upstream discovery.  Writes to second_hop_lite_queue.

Priority is additive (no cap by design — high-signal funders can score >100):
  +40  funder appears in wallet_clusters (as funder_wallet)
  +40  funder appears in farm_cluster_members (wallet_role='funder')
  +20  funds >= 5 creators (from creator_funders where is_cex=0)
  +10  funds >= 3 creators
  +20  funder's network has risk HIGH or CRITICAL
  +15  funder linked to migrated token with market_cap_current >= 50000
  +15  funder already in upstream_network_bridge (as via shared-funder path)

Rules:
  - Exclude CEX funders (is_cex=1)
  - Exclude addresses in build_excluded_set()
  - INSERT OR IGNORE — never overwrite existing queue entries
  - Only insert funders where creator_count >= 2 OR they have cluster/farm signal
"""

import json
import logging
import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Set

from src.utils.infra_mapping import build_excluded_set

logger = logging.getLogger(__name__)


class SecondHopLiteQueueBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def build(self, conn: sqlite3.Connection | None = None) -> dict:
        """
        Populate second_hop_lite_queue with priority-scored funders.

        Args:
            conn: Optional existing connection.  If None, opens its own.

        Returns:
            dict with enqueued, skipped_excluded, skipped_cex, duration_seconds.
        """
        t0 = time.time()
        own_conn = conn is None
        if own_conn:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")

        try:
            conn.row_factory = sqlite3.Row
            result = self._build(conn)
            if own_conn:
                conn.commit()
            return result
        finally:
            if own_conn:
                conn.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build(self, conn: sqlite3.Connection) -> dict:
        t0 = time.time()

        # ── Exclusion set ─────────────────────────────────────────────────────
        excluded: frozenset = build_excluded_set(conn)
        logger.info(f"[SHL-Queue] Exclusion set: {len(excluded)}")

        # ── Candidate funders: non-CEX with creator_count ─────────────────────
        funder_rows = conn.execute("""
            SELECT funder_address, COUNT(DISTINCT creator_address) AS creator_count
            FROM creator_funders
            WHERE is_cex = 0
            GROUP BY funder_address
        """).fetchall()

        # ── Signal sets ───────────────────────────────────────────────────────
        wc_funders: Set[str] = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT funder_wallet FROM wallet_clusters"
            ).fetchall()
        }

        fc_funders: Set[str] = set()
        try:
            fc_funders = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT wallet_address FROM farm_cluster_members WHERE wallet_role='funder'"
                ).fetchall()
            }
        except Exception:
            pass

        # Funders whose network has HIGH or CRITICAL risk
        high_risk_funders: Set[str] = set()
        try:
            rows = conn.execute("""
                SELECT DISTINCT fnm.funder_address
                FROM funder_network_map fnm
                JOIN networks_release nr ON nr.network_name = fnm.network_name
                WHERE nr.network_risk_level IN ('HIGH', 'CRITICAL')
            """).fetchall()
            high_risk_funders = {r[0] for r in rows}
        except Exception:
            pass

        # Funders linked to migrated token with market_cap_current >= 50000
        high_mc_funders: Set[str] = set()
        try:
            rows = conn.execute("""
                SELECT DISTINCT cf.funder_address
                FROM creator_funders cf
                JOIN token_analysis ta ON ta.earliest_tx_creator = cf.creator_address
                WHERE ta.lifecycle_stage = 'migrated'
                  AND ta.market_cap_current >= 50000
                  AND cf.is_cex = 0
            """).fetchall()
            high_mc_funders = {r[0] for r in rows}
        except Exception:
            pass

        # Funders already present in upstream_network_bridge (as an upstream address
        # that bridges networks funded by this funder)
        bridge_funders: Set[str] = set()
        try:
            rows = conn.execute("""
                SELECT DISTINCT ful.funder_address
                FROM funder_upstream_links ful
                JOIN upstream_network_bridge unb
                    ON unb.upstream_address = ful.upstream_address
                WHERE ful.is_excluded = 0
            """).fetchall()
            bridge_funders = {r[0] for r in rows}
        except Exception:
            pass

        # ── Score and filter ──────────────────────────────────────────────────
        skipped_excluded = 0
        skipped_cex = 0  # already filtered in SQL — kept for symmetry
        enqueued = 0
        to_insert = []

        for row in funder_rows:
            funder = row["funder_address"]
            creator_count = row["creator_count"]

            if funder in excluded:
                skipped_excluded += 1
                continue

            in_wc = funder in wc_funders
            in_fc = funder in fc_funders

            # Require creator_count >= 2 UNLESS the funder has a cluster/farm signal
            if creator_count < 2 and not in_wc and not in_fc:
                continue

            reasons: List[str] = []
            priority = 0

            if in_wc:
                priority += 40
                reasons.append("wallet_cluster")

            if in_fc:
                priority += 40
                reasons.append("farm_cluster")

            if creator_count >= 5:
                priority += 20
                reasons.append("funds_5plus_creators")
            elif creator_count >= 3:
                priority += 10
                reasons.append("funds_3plus_creators")

            # Core priority set bonus — ensures these are scanned before single-creator funders
            if creator_count >= 2:
                priority += 30
                reasons.append("priority_funder")

            if funder in high_risk_funders:
                priority += 20
                reasons.append("high_risk_network")

            if funder in high_mc_funders:
                priority += 15
                reasons.append("linked_high_mc_token")

            if funder in bridge_funders:
                priority += 15
                reasons.append("existing_bridge_path")

            to_insert.append((
                funder,
                priority,
                json.dumps(reasons),
                "pending",
                0,          # attempts
                None,       # last_error
                0,          # rpc_calls_used
                int(time.time()),   # created_at
                None,               # scanned_at
                int(time.time()),   # next_attempt_at
            ))

        # ── Bulk insert (INSERT OR IGNORE — never overwrite existing rows) ────
        if to_insert:
            conn.executemany("""
                INSERT OR IGNORE INTO second_hop_lite_queue (
                    funder_address, priority, reason_codes,
                    status, attempts, last_error, rpc_calls_used,
                    created_at, scanned_at, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, to_insert)
            enqueued = len(to_insert)

        duration = round(time.time() - t0, 2)
        logger.info(
            f"[SHL-Queue] Done — candidates={len(funder_rows)} "
            f"enqueued={enqueued} excluded={skipped_excluded} "
            f"duration={duration}s"
        )
        return {
            "status": "success",
            "enqueued": enqueued,
            "skipped_excluded": skipped_excluded,
            "skipped_cex": skipped_cex,
            "duration_seconds": duration,
        }
