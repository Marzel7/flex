"""
UpstreamExpansionBuilder — enqueues Phase 2 Lite scan work driven by
significant upstream hubs detected by SecondHopExpansionBuilder.

For each active hub in monitored_upstream_hubs, finds its downstream funders
that haven't been scanned recently and enqueues them into second_hop_lite_queue.

Makes zero RPC calls. All work is DB-only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from src.utils.infra_mapping import sync_infra_wallets

logger = logging.getLogger(__name__)

# ── Caps ──────────────────────────────────────────────────────────────────────
MAX_FUNDER_ENQUEUE_PER_UPSTREAM = 20
MAX_UPSTREAMS_PER_RUN           = 5
HUB_EXPANSION_COOLDOWN_SECONDS  = 3600        # 1 hour between expansions per hub
CACHE_FRESH_SECONDS             = 7 * 86400   # skip funders scanned within 7 days


class UpstreamExpansionBuilder:

    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self) -> dict:
        t0 = time.time()
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        sync_infra_wallets(conn)

        try:
            result = self._run(conn)
        except Exception as exc:
            logger.error(f"[UEB] Failed: {exc}", exc_info=True)
            result = {"status": "failed", "error": str(exc),
                      "hubs_processed": 0, "funders_enqueued": 0}
        finally:
            conn.close()

        result["duration_seconds"] = round(time.time() - t0, 2)
        logger.info(f"[UEB] Done — {result}")
        return result

    def _run(self, conn: sqlite3.Connection) -> dict:
        now = int(time.time())
        cooldown_cutoff = now - HUB_EXPANSION_COOLDOWN_SECONDS
        cache_cutoff    = now - CACHE_FRESH_SECONDS

        # Pick active hubs not recently expanded, ordered by confidence desc
        hubs = conn.execute("""
            SELECT upstream_address, confidence_score, networks_bridged,
                   funders_bridged, risk_level, reason_codes, last_expanded_at
            FROM monitored_upstream_hubs
            WHERE status = 'active'
              AND upstream_address NOT IN (SELECT address FROM infra_wallets)
              AND (last_expanded_at IS NULL OR last_expanded_at < ?)
            ORDER BY confidence_score DESC
            LIMIT ?
        """, (cooldown_cutoff, MAX_UPSTREAMS_PER_RUN)).fetchall()

        hubs_processed = 0
        total_enqueued = 0
        events         = []

        for hub in hubs:
            upstream    = hub["upstream_address"]
            hub_reasons = json.loads(hub["reason_codes"] or "[]")

            # Find downstream funders of this upstream
            funders = conn.execute("""
                SELECT
                    ful.funder_address,
                    ful.avg_transfer_sol,
                    COUNT(DISTINCT cf.creator_address)          AS creators_funded,
                    (wc.funder_wallet IS NOT NULL)               AS in_wallet_cluster,
                    (fcm.wallet_address IS NOT NULL)             AS in_farm_cluster,
                    (slq.funder_address IS NOT NULL
                     AND slq.status IN ('pending','running'))    AS already_queued,
                    (
                        cache.funder_address IS NOT NULL
                        AND cache.scanned_at > ?
                        AND cache.status = 'ok'
                    )                                            AS cache_fresh,
                    (inf.funder_address IS NOT NULL
                     OR cf_cex.is_cex = 1
                     OR iw.address IS NOT NULL)                 AS is_excluded
                FROM funder_upstream_links ful
                LEFT JOIN creator_funders cf
                    ON cf.funder_address = ful.funder_address AND cf.is_cex = 0
                LEFT JOIN creator_funders cf_cex
                    ON cf_cex.funder_address = ful.funder_address AND cf_cex.is_cex = 1
                LEFT JOIN wallet_clusters wc
                    ON wc.funder_wallet = ful.funder_address
                LEFT JOIN farm_cluster_members fcm
                    ON fcm.wallet_address = ful.funder_address
                LEFT JOIN second_hop_lite_queue slq
                    ON slq.funder_address = ful.funder_address
                LEFT JOIN funder_rpc_scan_cache cache
                    ON cache.funder_address = ful.funder_address
                LEFT JOIN infra_funders_observed inf
                    ON inf.funder_address = ful.funder_address
                LEFT JOIN infra_wallets iw
                    ON iw.address = ful.funder_address
                WHERE ful.upstream_address = ?
                  AND COALESCE(ful.is_excluded, 0) = 0
                  AND ful.funder_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY ful.funder_address
            """, (cache_cutoff, upstream)).fetchall()

            candidates = []
            for f in funders:
                if f["is_excluded"] or f["cache_fresh"] or f["already_queued"]:
                    continue

                creators_funded = f["creators_funded"] or 0
                in_wc           = bool(f["in_wallet_cluster"])
                in_fc           = bool(f["in_farm_cluster"])
                amount_sol      = f["avg_transfer_sol"] or 0.0

                priority = 0
                reasons  = ["upstream_expansion", "significant_upstream"] + hub_reasons

                if creators_funded >= 5:
                    priority += 50
                    reasons.append("multi_creator_funder")
                elif creators_funded >= 2:
                    priority += 35
                    reasons.append("multi_creator_funder")

                if in_wc:
                    priority += 30
                    reasons.append("wallet_cluster_funder")
                if in_fc:
                    priority += 30
                    reasons.append("farm_cluster_funder")
                if amount_sol > 0:
                    priority += 15
                if hub["confidence_score"] >= 55:
                    priority += 20
                    if "high_confidence_upstream" not in reasons:
                        reasons.append("high_confidence_upstream")
                if hub["networks_bridged"] >= 3:
                    priority += 10
                    if "multi_network_bridge" not in reasons:
                        reasons.append("multi_network_bridge")

                candidates.append((priority, f["funder_address"], reasons))

            candidates.sort(key=lambda x: -x[0])
            selected = candidates[:MAX_FUNDER_ENQUEUE_PER_UPSTREAM]

            enqueued = 0
            for priority, funder_address, reasons in selected:
                try:
                    conn.execute("""
                        INSERT INTO second_hop_lite_queue
                            (funder_address, priority, reason_codes, status, next_attempt_at)
                        VALUES (?, ?, ?, 'pending', 0)
                        ON CONFLICT(funder_address) DO UPDATE SET
                            priority     = MAX(priority, excluded.priority),
                            reason_codes = excluded.reason_codes,
                            status       = CASE WHEN status IN ('done','failed')
                                           THEN 'pending' ELSE status END,
                            next_attempt_at = 0
                    """, (funder_address, priority, json.dumps(reasons)))
                    enqueued += 1
                except Exception as e:
                    logger.debug(f"[UEB] enqueue skip {funder_address}: {e}")

            # Update last_expanded_at
            conn.execute("""
                UPDATE monitored_upstream_hubs
                SET last_expanded_at = ?
                WHERE upstream_address = ?
            """, (now, upstream))

            logger.info(
                f"[UEB] Hub {upstream[:8]}… — "
                f"candidates={len(candidates)} enqueued={enqueued}"
            )

            hubs_processed += 1
            total_enqueued += enqueued

            # Log relationship events (best-effort)
            if enqueued > 0:
                events.append((upstream, enqueued))

            # Commit per-hub so each write lock window is small under contention
            conn.commit()

        # Emit relationship events
        if events:
            self._log_events(conn, events)

        return {
            "status":          "success",
            "hubs_processed":  hubs_processed,
            "funders_enqueued": total_enqueued,
        }

    def _log_events(self, conn: sqlite3.Connection, events: list) -> None:
        try:
            conn.execute("PRAGMA table_info(intelligence_relationship_events)").fetchone()
            for upstream, count in events:
                conn.execute("""
                    INSERT OR IGNORE INTO intelligence_relationship_events
                        (event_type, source_type, source_address,
                         target_type, target_address, relationship_type,
                         scan_source)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    "upstream_expansion_enqueued",
                    "upstream", upstream,
                    "queue", f"{count}_funders",
                    "upstream_expansion_enqueued",
                    "upstream_expansion",
                ))
        except Exception as e:
            logger.debug(f"[UEB] event log skip: {e}")
