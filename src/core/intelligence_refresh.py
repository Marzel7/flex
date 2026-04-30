"""
Intelligence Refresh — Level 1/2/3 watchlist system.

Level 1: Flag only, no RPC   → status='watchlist', rpc_allowed=0
Level 2: Queue candidate     → promoted via /api/intelligence-refresh/approve
Level 3: Approved scan       → worker picks up status='approved', rpc_allowed=1

The candidate *builder* is DB-only and runs inside the graph analyzer cycle.
The *worker* only touches rows that are explicitly approved and within budget.
RPC is NEVER triggered from UI routes.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "../../migration_settings.json")

def get_migration_setting(key: str, default=False) -> bool:
    try:
        with open(_SETTINGS_FILE) as f:
            return bool(json.load(f).get(key, default))
    except Exception:
        return default

logger = logging.getLogger(__name__)

# ── Daily hard caps ───────────────────────────────────────────────────────────
MAX_CREATOR_REFRESH_SCANS_PER_DAY = 5
MAX_FUNDER_SCANS_PER_DAY          = 25
MAX_RPC_CALLS_PER_DAY             = 1000
MAX_RPC_CALLS_PER_RUN             = 100

# Minimum hours between re-adding an ignored target
IGNORED_COOLDOWN_HOURS = 48

# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now() -> int:
    return int(time.time())


def _db(db_path: str, timeout: int = 30) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def apply_migration(db_path: str) -> None:
    migration = (
        Path(__file__).resolve().parent.parent.parent
        / "database" / "migrations" / "add_intelligence_refresh.sql"
    )
    conn = _db(db_path)
    for stmt in migration.read_text().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"[IRC] Migration warning: {e}")
    conn.commit()
    conn.close()


# ── Budget helpers ─────────────────────────────────────────────────────────────

def _get_budget_used(conn: sqlite3.Connection, key: str) -> int:
    today = _today_utc()
    row = conn.execute(
        "SELECT used FROM intelligence_refresh_rpc_budget WHERE budget_date=? AND budget_key=?",
        (today, key)
    ).fetchone()
    return row["used"] if row else 0


def _increment_budget(conn: sqlite3.Connection, key: str, amount: int = 1) -> None:
    today = _today_utc()
    conn.execute("""
        INSERT INTO intelligence_refresh_rpc_budget (budget_date, budget_key, used)
        VALUES (?, ?, ?)
        ON CONFLICT(budget_date, budget_key) DO UPDATE SET used = used + excluded.used
    """, (today, key, amount))


def get_budget_status(db_path: str) -> dict:
    conn = _db(db_path)
    try:
        creator_used = _get_budget_used(conn, "creator_scans")
        funder_used  = _get_budget_used(conn, "funder_scans")
        rpc_used     = _get_budget_used(conn, "rpc_calls")
        return {
            "creator_scans_today":    creator_used,
            "creator_scans_limit":    MAX_CREATOR_REFRESH_SCANS_PER_DAY,
            "funder_scans_today":     funder_used,
            "funder_scans_limit":     MAX_FUNDER_SCANS_PER_DAY,
            "rpc_calls_today":        rpc_used,
            "rpc_daily_budget":       MAX_RPC_CALLS_PER_DAY,
            "rpc_budget_remaining":   max(0, MAX_RPC_CALLS_PER_DAY - rpc_used),
            "budget_exhausted":       rpc_used >= MAX_RPC_CALLS_PER_DAY,
        }
    finally:
        conn.close()


# ── Candidate builder (DB-only, NO RPC) ──────────────────────────────────────

class IntelligenceRefreshCandidateBuilder:
    """
    Scans existing DB tables to find high-risk creators and priority funders.
    Upserts them into intelligence_refresh_candidates with status='watchlist'.
    Makes zero RPC calls.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self) -> dict:
        t0 = time.time()
        conn = _db(self.db_path)
        try:
            creators_added = self._build_creator_candidates(conn)
            auto_approved  = self._sweep_auto_approve_watchlist(conn)
            funders_added  = self._build_funder_candidates(conn)
            marked_scanned = self._mark_scanned(conn)
            conn.commit()
        finally:
            conn.close()

        duration = round(time.time() - t0, 2)
        logger.info(
            f"[IRC-Builder] Done — creators={creators_added} auto_approved={auto_approved} "
            f"funders={funders_added} marked_scanned={marked_scanned} duration={duration}s"
        )
        return {
            "status": "success",
            "creators_added": creators_added,
            "auto_approved": auto_approved,
            "funders_added": funders_added,
            "marked_scanned": marked_scanned,
            "duration_seconds": duration,
        }

    def _mark_scanned(self, conn: sqlite3.Connection) -> int:
        """Mark approved records as scanned once their scan work is complete."""
        # Funders: approved + exists in second_hop_lite_queue as done
        conn.execute("""
            UPDATE intelligence_refresh_candidates
            SET status = 'scanned', updated_at = strftime('%s','now')
            WHERE target_type = 'funder'
              AND status = 'approved'
              AND EXISTS (
                SELECT 1 FROM second_hop_lite_queue
                WHERE funder_address = target_address AND status = 'done'
              )
              AND NOT EXISTS (
                SELECT 1 FROM second_hop_lite_queue
                WHERE funder_address = target_address AND status IN ('pending','running')
              )
        """)
        funder_marked = conn.execute("SELECT changes()").fetchone()[0]

        # Creators: approved + no pending/running funders tagged approved_creator
        conn.execute("""
            UPDATE intelligence_refresh_candidates
            SET status = 'scanned', updated_at = strftime('%s','now')
            WHERE target_type = 'creator'
              AND status = 'approved'
              AND NOT EXISTS (
                SELECT 1 FROM second_hop_lite_queue slq
                JOIN creator_funders cf ON cf.funder_address = slq.funder_address
                WHERE cf.creator_address = target_address
                  AND slq.status IN ('pending','running')
                  AND slq.reason_codes LIKE '%approved_creator%'
              )
        """)
        creator_marked = conn.execute("SELECT changes()").fetchone()[0]

        return funder_marked + creator_marked

    def _upsert_candidate(
        self,
        conn: sqlite3.Connection,
        target_type: str,
        address: str,
        priority: int,
        reason_codes: list[str],
    ) -> bool:
        """
        Insert or update candidate. Never downgrades an approved/scanning row.
        Never re-adds ignored targets within cooldown window.
        Returns True if a row was written.
        """
        now = _now()
        cooldown_cutoff = now - (IGNORED_COOLDOWN_HOURS * 3600)

        existing = conn.execute(
            "SELECT status, updated_at FROM intelligence_refresh_candidates "
            "WHERE target_type=? AND target_address=?",
            (target_type, address)
        ).fetchone()

        if existing:
            if existing["status"] == "ignored" and existing["updated_at"] > cooldown_cutoff:
                return False  # respect cooldown
            if existing["status"] in ("approved", "scanning"):
                return False  # don't touch in-flight rows
            # Update priority + reason if watchlist/complete/failed
            conn.execute("""
                UPDATE intelligence_refresh_candidates
                SET priority=?, reason_codes=?, updated_at=?
                WHERE target_type=? AND target_address=?
            """, (priority, json.dumps(reason_codes), now, target_type, address))
        else:
            conn.execute("""
                INSERT INTO intelligence_refresh_candidates
                    (target_type, target_address, priority, reason_codes,
                     status, rpc_allowed, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'watchlist', 0, ?, ?)
            """, (target_type, address, priority, json.dumps(reason_codes), now, now))
        return True

    # ── Creator candidates ────────────────────────────────────────────────────

    def _build_creator_candidates(self, conn: sqlite3.Connection) -> int:
        """
        Identify high-risk creators from existing DB data.
        Criteria checked purely from local tables.
        """
        now = _now()
        stale_cutoff_7d  = now - (7  * 86400)
        stale_cutoff_30d = now - (30 * 86400)

        # Pull creator stats in one pass
        # single_creator_ratio pre-computed via CTE to avoid O(n²) correlated subquery
        rows = conn.execute("""
            WITH funder_creator_counts AS (
                SELECT funder_address, COUNT(DISTINCT creator_address) AS num_creators
                FROM creator_funders
                WHERE is_cex = 0
                GROUP BY funder_address
            ),
            outbound_signals AS (
                SELECT creator_address,
                    MAX(CASE WHEN relationship_type='return_to_funder'        THEN 1 ELSE 0 END) AS has_return_to_funder,
                    MAX(CASE WHEN relationship_type='shared_payout_wallet'    THEN 1 ELSE 0 END) AS has_shared_payout,
                    COUNT(DISTINCT CASE WHEN relationship_type='shared_payout_wallet' THEN recipient_address END) AS shared_wallet_count,
                    MAX(CASE WHEN relationship_type='creator_to_upstream_hub' THEN 1 ELSE 0 END) AS has_hub_link,
                    MAX(CASE WHEN relationship_type='large_outbound'          THEN 1 ELSE 0 END) AS has_large_outbound
                FROM creator_outbound_classifications
                GROUP BY creator_address
            )
            SELECT
                ta.earliest_tx_creator                          AS creator,
                COUNT(DISTINCT cf.funder_address)               AS funder_count,
                COUNT(DISTINCT ta.mint)                         AS token_count,
                SUM(CASE WHEN ta.migrated_at IS NOT NULL
                         THEN 1 ELSE 0 END)                     AS migrated_count,
                MAX(ta.analyzed_at)                             AS last_analyzed_at,
                csf.is_self_funding,
                csf.self_funding_percentage,
                (SELECT COUNT(*) FROM network_membership nm
                 WHERE nm.creator_address = ta.earliest_tx_creator) AS network_count,
                CAST(
                    SUM(CASE WHEN fcc.num_creators = 1 THEN 1 ELSE 0 END)
                    AS REAL
                ) / MAX(COUNT(DISTINCT cf.funder_address), 1)   AS single_creator_ratio,
                COALESCE(obs.has_return_to_funder, 0)           AS has_return_to_funder,
                COALESCE(obs.has_shared_payout, 0)              AS has_shared_payout,
                COALESCE(obs.shared_wallet_count, 0)            AS shared_wallet_count,
                COALESCE(obs.has_hub_link, 0)                   AS has_hub_link,
                COALESCE(obs.has_large_outbound, 0)             AS has_large_outbound
            FROM token_analysis ta
            LEFT JOIN creator_funders cf
                ON cf.creator_address = ta.earliest_tx_creator
                AND cf.is_cex = 0
                AND cf.funder_address NOT IN (SELECT funder_address FROM infra_funders_observed)
            LEFT JOIN funder_creator_counts fcc ON fcc.funder_address = cf.funder_address
            LEFT JOIN creator_self_funding csf ON csf.creator_address = ta.earliest_tx_creator
            LEFT JOIN outbound_signals obs ON obs.creator_address = ta.earliest_tx_creator
            WHERE ta.earliest_tx_creator IS NOT NULL
            GROUP BY ta.earliest_tx_creator
            HAVING funder_count >= 50 OR token_count >= 100 OR csf.is_self_funding = 1
               OR obs.has_return_to_funder = 1 OR obs.has_shared_payout = 1 OR obs.has_hub_link = 1
        """).fetchall()

        added = 0
        for row in rows:
            creator     = row["creator"]
            fc          = row["funder_count"] or 0
            tc          = row["token_count"] or 0
            mc          = row["migrated_count"] or 0
            self_fund   = bool(row["is_self_funding"])
            scr         = row["single_creator_ratio"] or 0.0
            no_network  = (row["network_count"] or 0) == 0
            last_scan   = row["last_analyzed_at"]

            # Compute age of last scan
            if last_scan:
                try:
                    ts = datetime.fromisoformat(str(last_scan)).timestamp()
                    age_days = (now - ts) / 86400
                except Exception:
                    age_days = 999
            else:
                age_days = 999

            priority, reasons = _score_creator(
                self_funding=self_fund,
                funder_count=fc,
                single_creator_ratio=scr,
                last_scan_age_days=age_days,
                migrated_count=mc,
                token_count=tc,
                no_network=no_network,
                return_to_funder=bool(row["has_return_to_funder"]),
                shared_payout=bool(row["has_shared_payout"]),
                shared_wallet_count=int(row["shared_wallet_count"] or 0),
                hub_link=bool(row["has_hub_link"]),
                large_outbound=bool(row["has_large_outbound"]),
            )

            if priority > 0 and self._upsert_candidate(conn, "creator", creator, priority, reasons):
                added += 1
                # Auto-approval: check settings and promote directly if criteria met
                self._maybe_auto_approve(conn, creator, priority, reasons, row)

        return added

    def _sweep_auto_approve_watchlist(self, conn: sqlite3.Connection) -> int:
        """
        Auto-approve all existing watchlist creators that meet enabled thresholds.
        Runs every analyzer cycle so toggling a setting takes effect at the next run
        without waiting for the creator to be re-upserted.
        """
        any_enabled = (
            get_migration_setting("auto_approve_high_priority", False)
            or get_migration_setting("auto_approve_network_member", False)
            or get_migration_setting("auto_approve_shared_funders", False)
        )
        if not any_enabled:
            return 0

        rows = conn.execute("""
            SELECT irc.target_address AS creator, irc.priority, irc.reason_codes,
                   COALESCE(nm.network_count, 0) AS network_count
            FROM intelligence_refresh_candidates irc
            LEFT JOIN (
                SELECT creator_address, COUNT(*) AS network_count
                FROM network_membership GROUP BY creator_address
            ) nm ON nm.creator_address = irc.target_address
            WHERE irc.target_type = 'creator'
              AND irc.status IN ('watchlist', 'failed')
        """).fetchall()

        approved = 0
        for row in rows:
            creator  = row["creator"]
            priority = row["priority"]
            try:
                reasons = json.loads(row["reason_codes"] or "[]")
            except Exception:
                reasons = []
            self._maybe_auto_approve(conn, creator, priority, reasons, row)
            # detect if it got promoted
            new_status = conn.execute(
                "SELECT status FROM intelligence_refresh_candidates WHERE target_type='creator' AND target_address=?",
                (creator,)
            ).fetchone()
            if new_status and new_status["status"] == "approved":
                approved += 1

        return approved

    def _maybe_auto_approve(self, conn: sqlite3.Connection, creator: str, priority: int, reasons: list, row) -> None:
        """Auto-approve creators that meet configured thresholds, then enqueue their funders."""
        existing = conn.execute(
            "SELECT status FROM intelligence_refresh_candidates WHERE target_type='creator' AND target_address=?",
            (creator,)
        ).fetchone()
        if not existing or existing["status"] not in ("watchlist", "failed"):
            return  # already approved/scanning/ignored — don't touch

        should_approve = False
        approval_reason = None

        if get_migration_setting("auto_approve_high_priority", False) and priority >= 80:
            should_approve = True
            approval_reason = "auto_approve_high_priority"

        if not should_approve and get_migration_setting("auto_approve_network_member", False):
            if (row["network_count"] or 0) > 0:
                should_approve = True
                approval_reason = "auto_approve_network_member"

        if not should_approve and get_migration_setting("auto_approve_shared_funders", False):
            # Check if any of this creator's funders also fund other creators
            shared = conn.execute("""
                SELECT COUNT(DISTINCT cf2.creator_address) as other_creators
                FROM creator_funders cf
                JOIN creator_funders cf2 ON cf2.funder_address = cf.funder_address
                    AND cf2.creator_address != cf.creator_address
                WHERE cf.creator_address = ? AND cf.is_cex = 0
            """, (creator,)).fetchone()
            if shared and (shared["other_creators"] or 0) >= 1:
                should_approve = True
                approval_reason = "auto_approve_shared_funders"

        if not should_approve:
            return

        now = _now()
        # Append auto-approval reason to existing reason_codes
        existing_reasons = conn.execute(
            "SELECT reason_codes FROM intelligence_refresh_candidates WHERE target_type='creator' AND target_address=?",
            (creator,)
        ).fetchone()
        try:
            current_reasons = json.loads(existing_reasons["reason_codes"] or "[]") if existing_reasons else []
        except Exception:
            current_reasons = []
        current_reasons.append(approval_reason)

        conn.execute("""
            UPDATE intelligence_refresh_candidates
            SET status='approved', rpc_allowed=1, updated_at=?, reason_codes=?
            WHERE target_type='creator' AND target_address=?
              AND status IN ('watchlist','failed')
        """, (now, json.dumps(current_reasons), creator))

        enqueue_result = enqueue_creator_funders_for_phase2_lite(conn, creator, force=False)

        # Also enqueue creator for outbound scan if not already done/pending
        try:
            from src.core.creator_outbound_worker import enqueue_creator_for_outbound_scan
            enqueue_creator_for_outbound_scan(conn, creator, priority=priority)
        except Exception:
            pass

        print(f"[AUTO_APPROVE] {creator[:8]}… reason={approval_reason} priority={priority} "
              f"funders_enqueued={enqueue_result.get('funders_enqueued', 0)}", flush=True)

    # ── Funder candidates ─────────────────────────────────────────────────────

    def _build_funder_candidates(self, conn: sqlite3.Connection) -> int:
        """
        Identify priority funders from existing DB data.
        """
        # Evict any CEX/infra candidates that slipped through previously
        conn.execute("""
            DELETE FROM intelligence_refresh_candidates
            WHERE target_type = 'funder'
              AND (
                target_address IN (SELECT funder_address FROM infra_funders_observed)
                OR target_address IN (SELECT funder_address FROM creator_funders WHERE is_cex = 1)
              )
        """)
        rows = conn.execute("""
            SELECT
                cf.funder_address,
                COUNT(DISTINCT cf.creator_address)      AS creators_funded,
                MAX(cf.is_cex)                          AS is_cex,
                MAX(ful.last_seen_network_count)        AS network_count,
                slq.status                              AS queue_status,
                slq.scanned_at                          AS last_scanned_at,
                (wc.funder_wallet IS NOT NULL)          AS in_wallet_cluster
            FROM creator_funders cf
            LEFT JOIN funder_upstream_links ful ON ful.funder_address = cf.funder_address
            LEFT JOIN second_hop_lite_queue slq ON slq.funder_address = cf.funder_address
            LEFT JOIN wallet_clusters wc ON wc.funder_wallet = cf.funder_address
            WHERE cf.is_cex = 0
              AND cf.funder_address NOT IN (SELECT funder_address FROM infra_funders_observed)
            GROUP BY cf.funder_address
            HAVING creators_funded >= 2
        """).fetchall()

        now = _now()
        added = 0
        for row in rows:
            funder          = row["funder_address"]
            creators_funded = row["creators_funded"] or 0
            in_wc           = bool(row["in_wallet_cluster"])
            last_scanned    = row["last_scanned_at"]

            cache_stale = True
            if last_scanned:
                cache_stale = (now - last_scanned) > (7 * 86400)

            priority, reasons = _score_funder(
                creators_funded=creators_funded,
                in_wallet_cluster=in_wc,
                cache_stale=cache_stale,
            )

            if priority > 0 and self._upsert_candidate(conn, "funder", funder, priority, reasons):
                added += 1

        return added


# ── Scoring functions ─────────────────────────────────────────────────────────

def _score_creator(
    self_funding: bool,
    funder_count: int,
    single_creator_ratio: float,
    last_scan_age_days: float,
    migrated_count: int,
    token_count: int,
    no_network: bool,
    return_to_funder: bool = False,
    shared_payout: bool = False,
    shared_wallet_count: int = 0,
    hub_link: bool = False,
    large_outbound: bool = False,
) -> tuple[int, list[str]]:
    priority = 0
    reasons: list[str] = []

    if self_funding:
        priority += 60
        reasons.append("self_funding")

    if funder_count >= 500:
        priority += 50
        reasons.append("funder_count_500+")
    elif funder_count >= 100:
        priority += 35
        reasons.append("funder_count_100+")
    elif funder_count >= 50:
        priority += 20
        reasons.append("funder_count_50+")

    if single_creator_ratio >= 0.90:
        priority += 25
        reasons.append("dedicated_funders_90pct")

    if last_scan_age_days >= 30:
        priority += 30
        reasons.append("stale_30d")
    elif last_scan_age_days >= 7:
        priority += 15
        reasons.append("stale_7d")

    if migrated_count > 0:
        priority += 15
        reasons.append("has_migrated_tokens")

    if token_count >= 100:
        priority += 20
        reasons.append("token_count_100+")

    if no_network:
        priority += 10
        reasons.append("no_network_membership")

    # Outbound signals (from CreatorOutboundBuilder)
    if hub_link:
        priority += 50
        reasons.append("connected_to_upstream_hub")

    if return_to_funder:
        priority += 40
        reasons.append("self_funding_loop")

    if shared_payout and shared_wallet_count >= 2:
        priority += 30
        reasons.append("shared_operator_wallet")

    if large_outbound:
        priority += 10
        reasons.append("large_outbound")

    return priority, reasons


def _score_funder(
    creators_funded: int,
    in_wallet_cluster: bool,
    cache_stale: bool,
) -> tuple[int, list[str]]:
    priority = 0
    reasons: list[str] = []

    if creators_funded >= 10:
        priority += 40
        reasons.append("multi_creator_10+")
    elif creators_funded >= 5:
        priority += 30
        reasons.append("multi_creator_5+")
    elif creators_funded >= 2:
        priority += 15
        reasons.append("multi_creator_2+")

    if in_wallet_cluster:
        priority += 20
        reasons.append("wallet_cluster")

    if cache_stale:
        priority += 10
        reasons.append("cache_stale")

    return priority, reasons


# ── Phase 2 Lite funder enqueue ───────────────────────────────────────────────

MAX_FUNDER_ENQUEUE_PER_CREATOR  = 20
MAX_FUNDER_ENQUEUE_APPROVE_TOP  = 50

# Cache TTL: funders scanned within this window are considered fresh
_CACHE_FRESH_SECONDS = 7 * 86400


def enqueue_creator_funders_for_phase2_lite(
    conn: sqlite3.Connection,
    creator_address: str,
    limit: int = MAX_FUNDER_ENQUEUE_PER_CREATOR,
    force: bool = False,
) -> dict:
    """
    Select top-priority funders of creator_address and insert them into
    second_hop_lite_queue for Phase 2 Lite scanning.
    Makes zero RPC calls. Returns count summary.
    """
    now = _now()
    cache_cutoff = now - _CACHE_FRESH_SECONDS

    # Pull creator-level signals we'll use for reason_codes
    creator_row = conn.execute("""
        SELECT csf.is_self_funding, irc.priority
        FROM intelligence_refresh_candidates irc
        LEFT JOIN creator_self_funding csf ON csf.creator_address = irc.target_address
        WHERE irc.target_type='creator' AND irc.target_address=?
    """, (creator_address,)).fetchone()

    creator_self_funding = bool(creator_row["is_self_funding"]) if creator_row else False
    creator_priority     = (creator_row["priority"] or 0) if creator_row else 0
    creator_high_risk    = creator_priority >= 80

    # Collect all eligible funders with scoring signals in one query
    rows = conn.execute("""
        SELECT
            cf.funder_address,
            cf.amount_sol,
            COUNT(DISTINCT cf2.creator_address)     AS creators_funded,
            (wc.funder_wallet IS NOT NULL)           AS in_wallet_cluster,
            (fcm.wallet_address IS NOT NULL)         AS in_farm_cluster,
            (slq.funder_address IS NOT NULL
             AND slq.status IN ('pending','running')) AS already_queued,
            (
                cache.funder_address IS NOT NULL
                AND cache.scanned_at > ?
                AND cache.status = 'ok'
            )                                        AS cache_fresh,
            (inf.funder_address IS NOT NULL)         AS is_infra
        FROM creator_funders cf
        LEFT JOIN creator_funders cf2
            ON cf2.funder_address = cf.funder_address AND cf2.is_cex = 0
        LEFT JOIN wallet_clusters wc
            ON wc.funder_wallet = cf.funder_address
        LEFT JOIN farm_cluster_members fcm
            ON fcm.wallet_address = cf.funder_address
        LEFT JOIN second_hop_lite_queue slq
            ON slq.funder_address = cf.funder_address
        LEFT JOIN funder_rpc_scan_cache cache
            ON cache.funder_address = cf.funder_address
        LEFT JOIN infra_funders_observed inf
            ON inf.funder_address = cf.funder_address
        WHERE cf.creator_address = ?
          AND cf.is_cex = 0
        GROUP BY cf.funder_address
    """, (cache_cutoff, creator_address)).fetchall()

    skipped_excluded      = 0
    skipped_cached        = 0
    skipped_existing_queue = 0
    candidates            = []

    for row in rows:
        if row["is_infra"]:
            skipped_excluded += 1
            continue
        if row["cache_fresh"]:
            skipped_cached += 1
            continue
        if row["already_queued"]:
            skipped_existing_queue += 1
            continue

        creators_funded = row["creators_funded"] or 0
        in_wc           = bool(row["in_wallet_cluster"])
        in_fc           = bool(row["in_farm_cluster"])
        amount_sol      = row["amount_sol"] or 0.0

        # Scoring
        priority = 0
        reasons: list[str] = ["approved_creator"]

        if creators_funded >= 5:
            priority += 60
            reasons.append("multi_creator_funder")
        elif creators_funded >= 2:
            priority += 40
            reasons.append("multi_creator_funder")

        if in_wc:
            priority += 30
            reasons.append("wallet_cluster_funder")
        if in_fc:
            priority += 30
            reasons.append("farm_cluster_funder")
        if amount_sol > 0:
            priority += 15
            reasons.append("top_creator_funder")
        if creator_high_risk:
            priority += 10
            reasons.append("creator_high_risk")
        if creator_self_funding:
            priority += 10
            reasons.append("creator_self_funding")
        if row["cache_fresh"] is not None and not row["cache_fresh"]:
            priority += 10  # never scanned before

        # For single-creator funders, only include if creator is high-risk,
        # this funder is a significant contributor, or manually forced (user approved)
        if creators_funded < 2 and not force and not (creator_high_risk and amount_sol > 0):
            continue

        candidates.append((priority, row["funder_address"], reasons))

    funders_found = len(candidates)

    # Sort by descending priority, take top N
    candidates.sort(key=lambda x: -x[0])
    selected = candidates[:limit]

    enqueued = 0
    for priority, funder_address, reasons in selected:
        try:
            conn.execute("""
                INSERT INTO second_hop_lite_queue
                    (funder_address, priority, reason_codes, status, next_attempt_at)
                VALUES (?, ?, ?, 'pending', 0)
                ON CONFLICT(funder_address) DO UPDATE SET
                    priority    = MAX(priority, excluded.priority),
                    reason_codes = excluded.reason_codes,
                    status      = CASE WHEN status IN ('done','failed') THEN 'pending' ELSE status END,
                    next_attempt_at = 0
            """, (funder_address, priority, json.dumps(reasons)))
            enqueued += 1
        except Exception as e:
            logger.debug(f"[IRC] enqueue skip {funder_address}: {e}")

    logger.info(
        f"[IRC] enqueue_creator_funders creator={creator_address} "
        f"found={funders_found} enqueued={enqueued} "
        f"skip_cached={skipped_cached} skip_queue={skipped_existing_queue} "
        f"skip_excl={skipped_excluded}"
    )
    return {
        "funders_found":          funders_found,
        "funders_enqueued":       enqueued,
        "skipped_cached":         skipped_cached,
        "skipped_existing_queue": skipped_existing_queue,
        "skipped_excluded":       skipped_excluded,
    }


# ── Approval / ignore API helpers ─────────────────────────────────────────────

def approve_candidate(
    db_path: str,
    target_type: str,
    target_address: str,
    ttl_hours: int = 24,
) -> dict:
    conn = _db(db_path)
    try:
        now = _now()
        cur = conn.execute("""
            UPDATE intelligence_refresh_candidates
            SET status='approved', rpc_allowed=1,
                next_eligible_scan_at=?, updated_at=?
            WHERE target_type=? AND target_address=?
              AND status NOT IN ('scanning')
        """, (now, now, target_type, target_address))
        if cur.rowcount == 0:
            conn.close()
            return {"ok": False, "error": "not found or already scanning"}

        enqueue_result = {}
        if target_type == "creator":
            # force=True: manual approval always enqueues all non-CEX funders
            # regardless of creator risk level or funder creator-count
            enqueue_result = enqueue_creator_funders_for_phase2_lite(conn, target_address, force=True)
        elif target_type == "funder":
            # Skip infra/CEX funders
            is_infra = conn.execute(
                "SELECT 1 FROM infra_funders_observed WHERE funder_address=?", (target_address,)
            ).fetchone()
            is_cex = conn.execute(
                "SELECT MAX(is_cex) FROM creator_funders WHERE funder_address=?", (target_address,)
            ).fetchone()
            if is_infra or (is_cex and is_cex[0]):
                enqueue_result = {"funders_enqueued": 0, "skipped_infra_cex": 1}
            else:
                irc_row = conn.execute(
                    "SELECT priority FROM intelligence_refresh_candidates WHERE target_address=? AND target_type='funder'",
                    (target_address,)
                ).fetchone()
                priority = irc_row["priority"] if irc_row else 50
                conn.execute("""
                    INSERT INTO second_hop_lite_queue (funder_address, priority, reason_codes, status, next_attempt_at)
                    VALUES (?, ?, '["approved_funder"]', 'pending', 0)
                    ON CONFLICT(funder_address) DO UPDATE SET
                        status = CASE WHEN status IN ('done','failed') THEN 'pending' ELSE status END,
                        priority = MAX(priority, excluded.priority),
                        next_attempt_at = 0
                """, (target_address, priority))
                enqueue_result = {"funders_enqueued": 1}

        conn.commit()
        return {"ok": True, "next_eligible_scan_at": now, "phase2_lite_enqueue": enqueue_result}
    finally:
        conn.close()


def approve_top(
    db_path: str,
    target_type: str,
    limit: int = 5,
    min_priority: int = 0,
) -> dict:
    conn = _db(db_path)
    try:
        now = _now()
        rows = conn.execute("""
            SELECT target_address FROM intelligence_refresh_candidates
            WHERE target_type=? AND status='watchlist'
              AND priority >= ?
            ORDER BY priority DESC
            LIMIT ?
        """, (target_type, min_priority, limit)).fetchall()

        approved       = []
        enqueue_totals = {
            "funders_found": 0, "funders_enqueued": 0,
            "skipped_cached": 0, "skipped_existing_queue": 0, "skipped_excluded": 0,
        }
        budget_remaining = MAX_FUNDER_ENQUEUE_APPROVE_TOP

        for row in rows:
            conn.execute("""
                UPDATE intelligence_refresh_candidates
                SET status='approved', rpc_allowed=1,
                    next_eligible_scan_at=?, updated_at=?
                WHERE target_type=? AND target_address=?
            """, (now, now, target_type, row["target_address"]))
            approved.append(row["target_address"])

            if target_type == "creator" and budget_remaining > 0:
                per_creator_limit = min(MAX_FUNDER_ENQUEUE_PER_CREATOR, budget_remaining)
                eq = enqueue_creator_funders_for_phase2_lite(
                    conn, row["target_address"], limit=per_creator_limit
                )
                budget_remaining -= eq.get("funders_enqueued", 0)
                for k in enqueue_totals:
                    enqueue_totals[k] += eq.get(k, 0)
            elif target_type == "funder":
                irc_row = conn.execute(
                    "SELECT priority FROM intelligence_refresh_candidates WHERE target_address=? AND target_type='funder'",
                    (row["target_address"],)
                ).fetchone()
                priority = irc_row["priority"] if irc_row else 50
                conn.execute("""
                    INSERT INTO second_hop_lite_queue (funder_address, priority, reason_codes, status, next_attempt_at)
                    VALUES (?, ?, '["approved_funder"]', 'pending', 0)
                    ON CONFLICT(funder_address) DO UPDATE SET
                        status = CASE WHEN status IN ('done','failed') THEN 'pending' ELSE status END,
                        priority = MAX(priority, excluded.priority),
                        next_attempt_at = 0
                """, (row["target_address"], priority))
                enqueue_totals["funders_enqueued"] += 1

        conn.commit()
        result = {"ok": True, "approved": approved, "count": len(approved)}
        if target_type == "creator":
            result["phase2_lite_enqueue"] = enqueue_totals
        elif target_type == "funder":
            result["phase2_lite_enqueue"] = enqueue_totals
        return result
    finally:
        conn.close()


def ignore_candidate(
    db_path: str,
    target_type: str,
    target_address: str,
) -> dict:
    conn = _db(db_path)
    try:
        now = _now()
        cur = conn.execute("""
            UPDATE intelligence_refresh_candidates
            SET status='ignored', rpc_allowed=0, updated_at=?
            WHERE target_type=? AND target_address=?
        """, (now, target_type, target_address))
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "not found"}
        return {"ok": True}
    finally:
        conn.close()


# ── Status / watchlist query ──────────────────────────────────────────────────

def get_refresh_status(db_path: str, limit: int = 50) -> dict:
    conn = _db(db_path)
    try:
        now = _now()

        counts = {}
        for row in conn.execute("""
            SELECT target_type, status, COUNT(*) as cnt
            FROM intelligence_refresh_candidates
            WHERE status NOT IN ('ignored','scanned')
            GROUP BY target_type, status
        """).fetchall():
            counts.setdefault(row["target_type"], {})[row["status"]] = row["cnt"]

        # Last builder run from analyzer_runs
        last_run = conn.execute("""
            SELECT MAX(started_at) as ts FROM analyzer_runs
            WHERE analyzer_name='IntelligenceRefreshCandidateBuilder'
        """).fetchone()
        last_local_refresh = last_run["ts"] if last_run and last_run["ts"] else None

        # Recent candidates (last 24h)
        recent_cutoff = now - 86400
        recent = conn.execute("""
            SELECT target_type, target_address, priority, reason_codes, created_at
            FROM intelligence_refresh_candidates
            WHERE created_at >= ?
            ORDER BY priority DESC LIMIT 10
        """, (recent_cutoff,)).fetchall()

        _creator_select = f"""
            SELECT
                irc.target_address, irc.priority, irc.reason_codes,
                irc.status, irc.last_rpc_scan_at, irc.updated_at,
                (SELECT COUNT(DISTINCT funder_address) FROM creator_funders
                 WHERE creator_address = irc.target_address) AS funder_count,
                (SELECT COUNT(*) FROM token_analysis
                 WHERE earliest_tx_creator = irc.target_address
                   AND migrated_at IS NOT NULL) AS migrated_count,
                (SELECT COUNT(*) FROM token_analysis
                 WHERE earliest_tx_creator = irc.target_address) AS token_count,
                (SELECT self_funding_percentage FROM creator_self_funding
                 WHERE creator_address = irc.target_address) AS self_funding_percentage,
                (SELECT COUNT(*) FROM second_hop_lite_queue slq
                 JOIN creator_funders cf ON cf.funder_address = slq.funder_address
                 WHERE cf.creator_address = irc.target_address
                   AND slq.status IN ('pending','running')
                   AND slq.reason_codes LIKE '%approved_creator%') AS pending_funders_in_queue,
                (SELECT GROUP_CONCAT(DISTINCT relationship_type)
                 FROM creator_outbound_classifications
                 WHERE creator_address = irc.target_address) AS outbound_types,
                (SELECT status FROM creator_outbound_queue
                 WHERE creator_address = irc.target_address) AS outbound_queue_status
            FROM intelligence_refresh_candidates irc
            WHERE irc.target_type='creator' AND irc.status NOT IN ('ignored','scanned')
        """

        # Signal creators: priority > 15 OR has any non-baseline reason code
        creators = conn.execute(f"""
            SELECT * FROM ({_creator_select}) sub
            WHERE NOT (sub.status = 'approved' AND sub.pending_funders_in_queue = 0)
              AND (sub.priority > 15
                   OR (sub.reason_codes NOT LIKE '%baseline_watchlist%'))
            ORDER BY sub.priority DESC
            LIMIT {limit}
        """).fetchall()

        # Baseline creators: priority=15, only baseline reason codes, watchlist only
        baseline_creators = conn.execute(f"""
            SELECT * FROM ({_creator_select}) sub
            WHERE sub.status = 'watchlist'
              AND sub.priority <= 15
              AND sub.reason_codes LIKE '%baseline_watchlist%'
              AND sub.reason_codes NOT LIKE '%self_fund%'
              AND sub.reason_codes NOT LIKE '%coordinated%'
            ORDER BY sub.updated_at DESC
            LIMIT {limit}
        """).fetchall()

        # Priority funders (approved+scan-complete excluded)
        funders = conn.execute(f"""
            SELECT * FROM (
            SELECT
                irc.target_address, irc.priority, irc.reason_codes,
                irc.status, irc.last_rpc_scan_at, irc.updated_at,
                COUNT(DISTINCT cf.creator_address) AS creators_funded,
                CASE
                    WHEN EXISTS (SELECT 1 FROM second_hop_lite_queue slq
                                 WHERE slq.funder_address = irc.target_address
                                   AND slq.status IN ('pending','running')) THEN 'pending'
                    WHEN EXISTS (SELECT 1 FROM funder_rpc_scan_cache src
                                 WHERE src.funder_address = irc.target_address
                                   AND src.expires_at > strftime('%s','now')) THEN 'done'
                    ELSE 'none'
                END AS scan_state,
                CASE
                    WHEN EXISTS (SELECT 1 FROM infra_funders_observed WHERE funder_address=irc.target_address) THEN 'INFRA'
                    WHEN MAX(cf.is_cex) = 1 THEN 'CEX'
                    ELSE NULL
                END AS cex_infra_label
            FROM intelligence_refresh_candidates irc
            LEFT JOIN creator_funders cf ON cf.funder_address = irc.target_address
            WHERE irc.target_type='funder' AND irc.status NOT IN ('ignored','scanned')
            GROUP BY irc.target_address
        ) sub
        WHERE NOT (sub.status = 'approved' AND sub.scan_state = 'done')
          AND sub.cex_infra_label IS NULL
        ORDER BY sub.priority DESC
        LIMIT {limit}
        """).fetchall()

        budget = get_budget_status(db_path)

        return {
            "watchlist_count":     sum(v.get("watchlist", 0) for v in counts.values()),
            "approved_count":      sum(v.get("approved", 0) for v in counts.values()),
            "scanned_today":       budget["creator_scans_today"] + budget["funder_scans_today"],
            "rpc_calls_today":     budget["rpc_calls_today"],
            "rpc_daily_budget":    budget["rpc_daily_budget"],
            "rpc_budget_remaining":budget["rpc_budget_remaining"],
            "budget_exhausted":    budget["budget_exhausted"],
            "last_local_refresh":  last_local_refresh,
            "high_risk_creators":  [dict(r) for r in creators],
            "baseline_creators":   [dict(r) for r in baseline_creators],
            "priority_funders":    [dict(r) for r in funders],
            "recent_candidates":   [dict(r) for r in recent],
            "counts_by_type":      counts,
        }
    finally:
        conn.close()
