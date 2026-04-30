"""
SecondHopExpansionBuilder — Phase 1 (SQL-only, no RPC).

Reads transfer_index to find upstream funders of known first-hop funders,
builds confidence-scored network bridges, and populates creator_second_hop.

Runs as step 8 in run_graph_analyzers.py, after NetworkMembershipBuilder
and before NetworksReleaseBuilder.

Feature flag: SECOND_HOP_SQL_ENABLED=true (default false)

Schema note: upstream_network_bridge.time_span_seconds and
funders_bridged_count are added by add_second_hop_span.sql migration.
The builder applies them at runtime; missing columns are tolerated.
"""

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Set

from src.utils.infra_mapping import build_excluded_set
from src.core.second_hop_scorer import score_upstream_bridge

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────────────────
SECOND_HOP_SQL_ENABLED = os.getenv("SECOND_HOP_SQL_ENABLED", "false").lower() == "true"

# ── Tunables (override via env) ───────────────────────────────────────────────
LOOKBACK_DAYS        = int(os.getenv("SECOND_HOP_LOOKBACK_DAYS", "30"))
MIN_UPSTREAM_SOL     = 0.01
MAX_UPSTREAM_SOL     = 1000.0
MIN_UPSTREAM_FUNDERS = 2    # upstream must reach ≥2 known funders
MAX_UPSTREAM_FUNDERS = 50   # upstream reaching >50 known funders → infra, excluded
BURST_WINDOW_HOURS   = 4    # funders activated within this window → burst
CONFIDENCE_STORE_MIN = 15   # don't write bridges below this score
CONFIDENCE_UI_FLOOR  = 35   # documented; enforced in API layer not here


class SecondHopExpansionBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    # ── Public entry point ────────────────────────────────────────────────────

    def _apply_span_migration(self, conn) -> None:
        """Add time_span_seconds / funders_bridged_count if not yet present."""
        existing = {row[1] for row in conn.execute(
            "PRAGMA table_info(upstream_network_bridge)"
        ).fetchall()}
        if "time_span_seconds" not in existing:
            conn.execute(
                "ALTER TABLE upstream_network_bridge ADD COLUMN time_span_seconds INTEGER DEFAULT NULL"
            )
        if "funders_bridged_count" not in existing:
            conn.execute(
                "ALTER TABLE upstream_network_bridge ADD COLUMN funders_bridged_count INTEGER DEFAULT NULL"
            )

    def _is_enabled(self) -> bool:
        """DB setting takes priority over env var, same pattern as SecondHopLiteWorker."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            row = conn.execute(
                "SELECT setting_value FROM listener_settings WHERE setting_key='second_hop_sql_enabled'"
            ).fetchone()
            conn.close()
            if row is not None:
                return row[0] == 'true'
        except Exception:
            pass
        return os.getenv("SECOND_HOP_SQL_ENABLED", "false").lower() == "true"

    def build(self) -> dict:
        if not self._is_enabled():
            logger.info("[SecondHop] SECOND_HOP_SQL_ENABLED=false — skipping")
            return {
                "status": "skipped",
                "reason": "SECOND_HOP_SQL_ENABLED=false",
                "links_written": 0,
                "bridges_written": 0,
                "creator_second_hops": 0,
                "duration_seconds": 0,
            }

        t0 = time.time()
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row

            self._apply_span_migration(conn)
            self._apply_hub_migration(conn)
            excluded = build_excluded_set(conn)
            logger.info(f"[SecondHop] Exclusion set: {len(excluded)} addresses")

            links   = self._build_upstream_links(conn, excluded)
            bridges = self._build_network_bridges(conn, excluded)
            hops    = self._build_creator_second_hop(conn)

            conn.commit()
            conn.close()

            duration = round(time.time() - t0, 2)
            logger.info(
                f"[SecondHop] Done — links={links} bridges={bridges} "
                f"creator_hops={hops} duration={duration}s"
            )
            return {
                "status": "success",
                "links_written": links,
                "bridges_written": bridges,
                "creator_second_hops": hops,
                "duration_seconds": duration,
            }

        except Exception as exc:
            logger.error(f"[SecondHop] FAILED: {exc}", exc_info=True)
            return {
                "status": "failed",
                "error": str(exc),
                "links_written": 0,
                "bridges_written": 0,
                "creator_second_hops": 0,
                "duration_seconds": round(time.time() - t0, 2),
            }

    # ── Step 1: build funder_upstream_links ───────────────────────────────────

    def _build_upstream_links(self, conn, excluded: frozenset) -> int:
        """
        Find wallets that sent SOL to known non-CEX funders within the lookback
        window. Apply volume gate (2–50 downstream funders) inline via HAVING.
        Mark static exclusion addresses is_excluded=1.

        Clears only transfer_index-sourced rows before rebuilding so that
        rpc_backfill rows (Phase 2) are preserved.
        """
        cutoff = int(time.time()) - (LOOKBACK_DAYS * 86400)

        conn.execute(
            "DELETE FROM funder_upstream_links WHERE source = 'transfer_index'"
        )

        excluded_list = list(excluded)
        if excluded_list:
            ph = ",".join("?" * len(excluded_list))
            excl_case = f"CASE WHEN ti.source IN ({ph}) THEN 1 ELSE 0 END"
        else:
            ph = None
            excl_case = "0"

        # Two-step approach:
        # 1. Find upstream addresses that reach ≥MIN and ≤MAX qualifying funders.
        #    "Qualifying funder" = in creator_funders with is_cex=0.
        #    This is the volume gate — applied on the SET of known funders reached,
        #    not total transfer count.
        # 2. Insert one row per (funder_address, upstream_address) pair.
        sql = f"""
            INSERT OR REPLACE INTO funder_upstream_links (
                funder_address, upstream_address,
                transfer_count, total_sol, avg_transfer_sol,
                first_transfer_ts, last_transfer_ts,
                source, is_excluded, funders_touched, built_at
            )
            SELECT
                ti.destination                              AS funder_address,
                ti.source                                   AS upstream_address,
                COUNT(*)                                    AS transfer_count,
                SUM(ti.amount_sol)                          AS total_sol,
                SUM(ti.amount_sol) / COUNT(*)               AS avg_transfer_sol,
                MIN(ti.block_time)                          AS first_transfer_ts,
                MAX(ti.block_time)                          AS last_transfer_ts,
                'transfer_index'                            AS source,
                {excl_case}                                 AS is_excluded,
                qual.funder_count                           AS funders_touched,
                strftime('%s','now')                        AS built_at
            FROM transfer_index ti
            JOIN (
                SELECT DISTINCT funder_address
                FROM creator_funders
                WHERE is_cex = 0
            ) cf ON cf.funder_address = ti.destination
            -- Volume gate: join pre-computed per-upstream qualifying funder counts
            JOIN (
                SELECT ti2.source AS upstream_address,
                       COUNT(DISTINCT ti2.destination) AS funder_count
                FROM transfer_index ti2
                JOIN (
                    SELECT DISTINCT funder_address
                    FROM creator_funders WHERE is_cex = 0
                ) cf2 ON cf2.funder_address = ti2.destination
                WHERE ti2.block_time > {cutoff}
                  AND ti2.amount_sol BETWEEN {MIN_UPSTREAM_SOL} AND {MAX_UPSTREAM_SOL}
                  AND ti2.is_valid = 1
                GROUP BY ti2.source
                HAVING COUNT(DISTINCT ti2.destination)
                       BETWEEN {MIN_UPSTREAM_FUNDERS} AND {MAX_UPSTREAM_FUNDERS}
            ) qual ON qual.upstream_address = ti.source
            WHERE ti.block_time > {cutoff}
              AND ti.amount_sol BETWEEN {MIN_UPSTREAM_SOL} AND {MAX_UPSTREAM_SOL}
              AND ti.is_valid = 1
            GROUP BY ti.source, ti.destination
        """
        params = excluded_list if excluded_list else []
        conn.execute(sql, params)

        # Update last_seen_network_count for non-excluded upstreams
        conn.execute("""
            UPDATE funder_upstream_links
            SET last_seen_network_count = (
                SELECT COUNT(DISTINCT fnm.network_name)
                FROM funder_network_map fnm
                WHERE fnm.funder_address = funder_upstream_links.funder_address
            )
            WHERE is_excluded = 0
        """)

        count = conn.execute(
            "SELECT COUNT(*) FROM funder_upstream_links WHERE source='transfer_index'"
        ).fetchone()[0]
        logger.info(f"[SecondHop] Upstream links built: {count}")
        return count

    # ── Step 2: build upstream_network_bridge ─────────────────────────────────

    def _build_network_bridges(self, conn, excluded: frozenset) -> int:
        """
        For each non-excluded upstream wallet, find all named networks it can
        reach (via its downstream funders). Score every pair of networks it
        bridges. Write bridges with confidence >= CONFIDENCE_STORE_MIN.
        """
        conn.execute("DELETE FROM upstream_network_bridge")

        # Pull upstream → (network, per-funder rows) in one query
        rows = conn.execute("""
            SELECT
                ful.upstream_address,
                fnm.network_name,
                COUNT(DISTINCT ful.funder_address)  AS funders_bridged,
                SUM(ful.avg_transfer_sol)            AS sum_avg_sol,
                COUNT(DISTINCT ful.funder_address)   AS funder_count,
                MIN(ful.first_transfer_ts)           AS first_ts,
                MAX(ful.last_transfer_ts)            AS last_ts
            FROM funder_upstream_links ful
            JOIN funder_network_map fnm ON fnm.funder_address = ful.funder_address
            WHERE ful.is_excluded = 0
              AND ful.upstream_address NOT IN (
                  SELECT bonding_curve_pda FROM token_analysis WHERE bonding_curve_pda IS NOT NULL
                  UNION
                  SELECT pool_address FROM token_analysis WHERE pool_address IS NOT NULL
                  UNION
                  SELECT pumpswap_pool_address FROM token_analysis WHERE pumpswap_pool_address IS NOT NULL
                  UNION
                  SELECT address FROM shl_excluded_upstreams
              )
            GROUP BY ful.upstream_address, fnm.network_name
        """).fetchall()

        # Also pull individual avg_transfer_sol per (upstream, funder) for stddev
        funder_amounts: Dict[str, List[float]] = defaultdict(list)
        for r in conn.execute("""
            SELECT ful.upstream_address, ful.avg_transfer_sol
            FROM funder_upstream_links ful
            WHERE ful.is_excluded = 0
              AND ful.upstream_address NOT IN (
                  SELECT bonding_curve_pda FROM token_analysis WHERE bonding_curve_pda IS NOT NULL
                  UNION
                  SELECT pool_address FROM token_analysis WHERE pool_address IS NOT NULL
                  UNION
                  SELECT pumpswap_pool_address FROM token_analysis WHERE pumpswap_pool_address IS NOT NULL
                  UNION
                  SELECT address FROM shl_excluded_upstreams
              )
        """).fetchall():
            funder_amounts[r["upstream_address"]].append(r["avg_transfer_sol"])

        # Cluster lookup caches (avoid per-row DB hits)
        wc_upstreams: Set[str] = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT funder_wallet FROM wallet_clusters"
            ).fetchall()
        }
        fc_upstreams: Set[str] = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT wallet_address FROM farm_cluster_members"
            ).fetchall()
        }

        # Group rows by upstream
        upstream_map: Dict[str, List[dict]] = defaultdict(list)
        for r in rows:
            upstream_map[r["upstream_address"]].append(dict(r))

        written = 0
        for upstream, nets in upstream_map.items():
            if len(nets) < 2:
                continue  # must bridge ≥2 networks

            # Amount stddev across individual funder avg_transfer_sol values
            amounts = funder_amounts.get(upstream, [])
            if len(amounts) >= 2:
                mean = sum(amounts) / len(amounts)
                variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
                stddev = variance ** 0.5
            else:
                stddev = 0.0

            # Burst: did downstream funders' first transfers cluster in time?
            timestamps = [n["first_ts"] for n in nets if n["first_ts"]]
            burst = (
                len(timestamps) >= 2
                and (max(timestamps) - min(timestamps)) <= (BURST_WINDOW_HOURS * 3600)
            )

            in_wc = upstream in wc_upstreams
            in_fc = upstream in fc_upstreams
            networks_bridged = len(nets)

            # Score every pair
            for i, na in enumerate(nets):
                for nb in nets[i + 1:]:
                    score = score_upstream_bridge(
                        upstream_address=upstream,
                        funders_bridged=na["funders_bridged"] + nb["funders_bridged"],
                        networks_bridged=networks_bridged,
                        amount_stddev=stddev,
                        burst_detected=burst,
                        in_wallet_cluster=in_wc,
                        in_farm_cluster=in_fc,
                        is_excluded=upstream in excluded,
                    )

                    if score.confidence_score < CONFIDENCE_STORE_MIN:
                        continue

                    net_a = min(na["network_name"], nb["network_name"])
                    net_b = max(na["network_name"], nb["network_name"])

                    # Time span across all funder first-transfer timestamps for this upstream
                    span_seconds = None
                    if timestamps and len(timestamps) >= 2:
                        span_seconds = max(timestamps) - min(timestamps)

                    conn.execute("""
                        INSERT OR REPLACE INTO upstream_network_bridge (
                            upstream_address, network_a, network_b,
                            shared_funders, confidence_score, risk_level,
                            reason_codes, is_excluded, built_at,
                            time_span_seconds, funders_bridged_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, strftime('%s','now'), ?, ?)
                    """, (
                        upstream, net_a, net_b,
                        na["funders_bridged"] + nb["funders_bridged"],
                        score.confidence_score,
                        score.risk_level,
                        json.dumps(score.reason_codes),
                        span_seconds,
                        na["funders_bridged"] + nb["funders_bridged"],
                    ))
                    written += 1

        logger.info(f"[SecondHop] Network bridges written: {written}")
        self._upsert_significant_hubs(conn)
        return written

    # ── Hub detection ─────────────────────────────────────────────────────────

    def _apply_hub_migration(self, conn) -> None:
        from pathlib import Path
        migration = (
            Path(__file__).resolve().parent.parent.parent
            / "database" / "migrations" / "add_monitored_upstream_hubs.sql"
        )
        for stmt in migration.read_text().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e).lower():
                        logger.warning(f"[SecondHop] Hub migration: {e}")

    def _upsert_significant_hubs(self, conn) -> int:
        """
        After bridges are built, identify upstream wallets that are
        significant hubs and upsert them into monitored_upstream_hubs.
        Significance: confidence_score>=55 OR networks_bridged>=3 OR funders_bridged_count>=5
        """
        try:
            self._apply_hub_migration(conn)
        except Exception as e:
            logger.warning(f"[SecondHop] Hub migration failed: {e}")
            return 0

        # Aggregate per upstream: max confidence, distinct networks, total funders bridged
        hubs = conn.execute("""
            SELECT
                upstream_address,
                MAX(confidence_score)               AS max_confidence,
                COUNT(DISTINCT network_a || network_b) AS networks_bridged,
                MAX(COALESCE(funders_bridged_count, shared_funders, 0)) AS max_funders,
                MAX(risk_level)                     AS risk_level,
                GROUP_CONCAT(DISTINCT reason_codes) AS reason_blob
            FROM upstream_network_bridge
            WHERE is_excluded = 0
            GROUP BY upstream_address
            HAVING max_confidence >= 55
                OR networks_bridged >= 3
                OR max_funders >= 5
        """).fetchall()

        now = int(time.time())
        written = 0
        for h in hubs:
            reasons = []
            if h["max_confidence"] >= 55:
                reasons.append("high_confidence")
            if h["networks_bridged"] >= 3:
                reasons.append("multi_network_bridge")
            if h["max_funders"] >= 5:
                reasons.append("high_funders_bridged")

            conn.execute("""
                INSERT INTO monitored_upstream_hubs
                    (upstream_address, confidence_score, networks_bridged,
                     funders_bridged, risk_level, reason_codes, status, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(upstream_address) DO UPDATE SET
                    confidence_score = MAX(confidence_score, excluded.confidence_score),
                    networks_bridged = excluded.networks_bridged,
                    funders_bridged  = excluded.funders_bridged,
                    risk_level       = excluded.risk_level,
                    reason_codes     = excluded.reason_codes
            """, (
                h["upstream_address"],
                h["max_confidence"],
                h["networks_bridged"],
                h["max_funders"],
                h["risk_level"],
                json.dumps(reasons),
                now,
            ))
            written += 1

        logger.info(f"[SecondHop] Significant hubs upserted: {written}")
        return written

    # ── Step 3: build creator_second_hop (atomic swap) ────────────────────────

    def _build_creator_second_hop(self, conn) -> int:
        """
        Per-creator second-hop enrichment.

        Uses a TEMP table (session-scoped, avoids view validation issues on DROP)
        then merges into the permanent creator_second_hop table via DELETE + INSERT.
        The permanent table is never empty during the swap — old rows are removed
        only after new rows are ready.
        """
        # Build into a true TEMP table (not persisted, no view validation)
        conn.execute("DROP TABLE IF EXISTS temp.csh_build")
        conn.execute(f"""
            CREATE TEMP TABLE csh_build AS
            SELECT
                cf.creator_address,
                ful.upstream_address,
                cf.funder_address        AS via_funder,
                unb.confidence_score,
                unb.risk_level,
                unb.reason_codes,
                ful.source,
                strftime('%s','now')     AS built_at
            FROM creator_funders cf
            JOIN funder_upstream_links ful
                ON ful.funder_address = cf.funder_address
               AND ful.is_excluded = 0
            JOIN funder_network_map fnm
                ON fnm.funder_address = cf.funder_address
            JOIN upstream_network_bridge unb
                ON  unb.upstream_address = ful.upstream_address
                AND (unb.network_a = fnm.network_name
                     OR unb.network_b = fnm.network_name)
                AND unb.confidence_score >= {CONFIDENCE_STORE_MIN}
            GROUP BY cf.creator_address, ful.upstream_address, cf.funder_address
        """)

        count = conn.execute("SELECT COUNT(*) FROM temp.csh_build").fetchone()[0]

        # Atomic swap: clear permanent table, insert from temp
        conn.execute("DELETE FROM creator_second_hop")
        conn.execute("""
            INSERT INTO creator_second_hop
            (creator_address, upstream_address, via_funder,
             confidence_score, risk_level, reason_codes, source, built_at)
            SELECT creator_address, upstream_address, via_funder,
                   confidence_score, risk_level, reason_codes, source, built_at
            FROM temp.csh_build
        """)
        conn.execute("DROP TABLE IF EXISTS temp.csh_build")

        logger.info(f"[SecondHop] creator_second_hop rows: {count}")
        return count
