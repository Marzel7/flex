"""
DB-only creator and network risk scoring.

Coordination answers who is linked, outcomes answer what they produce, G history
captures repeat behaviour, and explicit liquidity removal captures intent.  Raw
evidence is never deleted; infra wallets are ignored only in interpretation.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from src.utils.infra_mapping import ensure_infra_wallets_table, sync_infra_wallets

logger = logging.getLogger(__name__)


def _token_class_from_peak(peak_mc: float | int | None) -> str | None:
    try:
        value = float(peak_mc or 0)
    except Exception:
        return None
    if value <= 0:
        return None
    if value >= 5_000_000:
        return "G1"
    if value >= 2_000_000:
        return "G2"
    if value >= 500_000:
        return "G3"
    if value >= 300_000:
        return "G4"
    if value >= 150_000:
        return "G5"
    if value >= 75_000:
        return "G6"
    return "G7"


def _parse_ts(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else 0
    raw = str(value).strip()
    if not raw:
        return 0
    try:
        return int(float(raw))
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


@dataclass
class ScoreBreakdown:
    operator_score: float = 0
    outcome_score: float = 0
    g_score: float = 0
    liquidation_score: float = 0
    reason_codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    explanation: dict[str, Any] = field(default_factory=dict)

    def add(self, dimension: str, points: float, code: str, reason: str) -> None:
        setattr(self, dimension, getattr(self, dimension) + points)
        self.reason_codes.append(code)
        self.reasons.append(reason)


# X78.7 -- sync_infra_wallets() reads INFRASTRUCTURE_ACCOUNTS/CEX_ACCOUNTS
# (static, in-process) plus infra_funders_observed/cex_wallets (small) and
# three full SELECT DISTINCT scans of token_analysis (~1.4-1.6M rows each,
# measured ~48s combined against production -- see
# docs/audits/x78_6_risk_scoring_runtime_reentrancy.md). Its output
# (infra_wallets) only ever grows monotonically -- new bonding
# curves/pools/CEX wallets are ADDED as tokens launch, never removed --
# so re-running it on every single score_creator_now() call (called once
# per completed extraction job, i.e. potentially every few seconds under
# load) re-scans the same ~1.6M rows repeatedly for a result that changes
# at most a few times per hour. This mirrors the exact debounce pattern
# already used for _post_extraction_intelligence_refresh in
# creator_funding_worker.py (INTEL_REFRESH_DEBOUNCE_SEC) -- module-level
# (not instance-level) because RiskScoringBuilder is constructed fresh
# per call. Bounded staleness of up to this window on infra
# classification is an accepted tradeoff already established by that
# precedent; RUN() (full-batch scoring) is unaffected -- it does not use
# this debounce, since a full batch run legitimately needs a fresh sync.
SYNC_INFRA_WALLETS_DEBOUNCE_SEC = int(os.environ.get("RSB_SYNC_INFRA_WALLETS_DEBOUNCE_SEC", "300"))
_sync_infra_wallets_last_run = 0.0


class RiskScoringBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self) -> dict[str, Any]:
        started = time.time()
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            self.apply_migration(conn)
            sync_infra_wallets(conn)

            creators = self._load_creator_universe(conn)
            context = self._build_context(conn, creators)
            creator_scores = []
            for creator in creators:
                creator_scores.append(self._score_creator_fast(creator, context))

            self._write_creator_scores(conn, creator_scores)
            network_scores = self._score_networks(conn, creator_scores)
            self._write_network_scores(conn, network_scores)
            self._append_history(conn, "creator", creator_scores)
            self._append_history(conn, "network", network_scores)
            conn.commit()
            duration = round(time.time() - started, 2)
            return {
                "status": "success",
                "creators_scored": len(creator_scores),
                "networks_scored": len(network_scores),
                "duration_seconds": duration,
            }
        except Exception:
            conn.rollback()
            logger.exception("[RiskScoringBuilder] failed")
            raise
        finally:
            conn.close()

    def score_creator_now(self, creator: str) -> dict:
        """Score a single creator immediately — used after migration/funding extraction."""
        # X78.5 -- found live: outer_command=risk_scoring_builder.py:124
        # was the real (previously misattributed to db_locking.py's
        # sqlite3.connect monkeypatch) source of a permanent write-lease
        # leak that stalled creator_funding_worker for 56+ minutes and
        # caused a crash-loop (X78.4's live sanity window). conn was
        # opened, and row_factory/PRAGMA journal_mode were set, BEFORE
        # the try block below -- so if EITHER of those two statements
        # raised (PRAGMA journal_mode=WAL does touch the database file
        # and can legitimately fail/be slow under contention), the
        # exception propagated straight out of this function WITHOUT
        # ever reaching `finally: conn.close()`, which is the only thing
        # that calls TrackedConnection._release_write_lane() and clears
        # this thread's _thread_write_lease.owner. conn is now opened
        # inside the try/finally so close() is guaranteed on every path,
        # matching every other connection in this class (run(), __init__
        # helpers) and the pattern already used throughout this codebase
        # since X78.0.
        #
        # X78.6 -- that fix was necessary but not sufficient. Measured
        # live and reproduced directly against the production DB:
        # apply_migration()'s own CREATE TABLE/ALTER statements acquire
        # the write lease immediately (they are write-shaped SQL), and
        # everything that ran after it on the SAME connection --
        # sync_infra_wallets() (three full SELECT DISTINCT scans of
        # token_analysis, ~1.4M rows each, measured ~48s combined) and
        # _build_context() (a full 1.57M-row scan for tokens_by_creator
        # alone plus five more full-table queries, measured ~20s+) --
        # ran entirely INSIDE that held write lease, for a call that
        # only ever scores ONE creator. 70+ seconds of read-only work
        # was performed under write ownership before the first actual
        # write (_write_creator_scores, a small executemany) even began.
        # Any other write dispatched to this worker's thread during that
        # window collided with NestedDatabaseWriteError -- which, under
        # sustained job throughput, recurred faster than each 70s window
        # could clear, looking indistinguishable from a permanent leak.
        #
        # Fix: read-only setup (migration schema check, infra-wallet
        # sync, context building, scoring) now runs on a connection that
        # is committed and closed -- releasing the write lease -- BEFORE
        # persistence begins. A second, freshly-opened connection is
        # held only for the brief _write_creator_scores + commit. This
        # does not change scoring semantics, migration behaviour, or
        # infra-wallet sync behaviour -- only WHEN the write lease is
        # held, per the same collect-then-persist discipline already
        # established elsewhere in this codebase (X77.x).
        # X78.7 -- see SYNC_INFRA_WALLETS_DEBOUNCE_SEC's module-level
        # comment: infra_wallets only grows monotonically, so re-scanning
        # ~1.6M token_analysis rows on every single-creator score call is
        # unnecessary. Debounced module-wide (not per-instance), same
        # pattern as creator_funding_worker.py's intel-refresh debounce.
        global _sync_infra_wallets_last_run
        now_monotonic = time.monotonic()
        should_sync_infra = (
            now_monotonic - _sync_infra_wallets_last_run >= SYNC_INFRA_WALLETS_DEBOUNCE_SEC
        )

        setup_conn = None
        try:
            setup_conn = sqlite3.connect(self.db_path, timeout=60)
            setup_conn.row_factory = sqlite3.Row
            setup_conn.execute("PRAGMA journal_mode=WAL")
            self.apply_migration(setup_conn)
            if should_sync_infra:
                sync_infra_wallets(setup_conn)
                _sync_infra_wallets_last_run = now_monotonic
            else:
                # sync_infra_wallets() itself calls this first -- but when
                # debounced-skipped, _build_context_for_creator's own
                # NOT IN (SELECT address FROM infra_wallets) subqueries
                # still require the table to exist (a correctness
                # precondition, independent of whether this call happens
                # to refresh its contents).
                ensure_infra_wallets_table(setup_conn)
            # X78.7 -- _build_context_for_creator is the single-creator-
            # optimized equivalent of _build_context(conn, [creator]):
            # identical output shape/semantics (verified in
            # tests/test_x78_7_query_optimization.py), but every query is
            # filtered by creator/funder address in SQL instead of
            # scanning full tables and filtering in Python.
            context = self._build_context_for_creator(setup_conn, creator)
            # apply_migration/sync_infra_wallets may have performed
            # writes (schema DDL, infra_wallets upserts) -- commit them
            # now so this connection's close() below releases the write
            # lease before the brief write-only connection reacquires it.
            setup_conn.commit()
        except Exception as e:
            if setup_conn is not None:
                try:
                    setup_conn.rollback()
                except Exception:
                    pass
            logger.warning(f"[RiskScoringBuilder] score_creator_now setup failed for {creator}: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            if setup_conn is not None:
                try:
                    setup_conn.close()
                except Exception:
                    pass

        score = self._score_creator_fast(creator, context)

        write_conn = None
        try:
            write_conn = sqlite3.connect(self.db_path, timeout=60)
            write_conn.row_factory = sqlite3.Row
            self._write_creator_scores(write_conn, [score])
            write_conn.commit()
            return {"status": "success", "creator": creator, "risk_level": score.get("risk_level")}
        except Exception as e:
            if write_conn is not None:
                try:
                    write_conn.rollback()
                except Exception:
                    pass
            logger.warning(f"[RiskScoringBuilder] score_creator_now write failed for {creator}: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            if write_conn is not None:
                try:
                    write_conn.close()
                except Exception:
                    pass

    @staticmethod
    def apply_migration(conn: sqlite3.Connection) -> None:
        migration = (
            Path(__file__).resolve().parent.parent.parent
            / "database"
            / "migrations"
            / "add_risk_scoring.sql"
        )
        for stmt in migration.read_text().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

    def _load_creator_universe(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute("""
            SELECT creator_address FROM network_membership
            UNION
            SELECT creator_address FROM creator_funders
            UNION
            SELECT earliest_tx_creator FROM token_analysis
            WHERE earliest_tx_creator IS NOT NULL AND earliest_tx_creator != ''
            UNION
            SELECT creator_address FROM creator_self_funding
            UNION
            SELECT creator_address FROM creator_outbound_classifications
        """).fetchall()
        return sorted({row[0] for row in rows if row[0]})

    def _build_context_for_creator(self, conn: sqlite3.Connection, creator: str) -> dict[str, Any]:
        """X78.7 -- single-creator-optimized equivalent of
        _build_context(conn, [creator]), used only by score_creator_now.

        _build_context() is also used by run() (the full-batch entry
        point, where scanning every table IS the correct plan -- it is
        scoring every creator at once). For a single creator, every one
        of _build_context()'s eight full-table scans was doing that same
        ecosystem-wide read just to filter down to one creator_set
        membership check in Python (measured against production:
        ~1.57M rows read for tokens_by_creator alone, ~18s; see
        docs/audits/x78_6_risk_scoring_runtime_reentrancy.md and
        x78_7's own benchmark for the full measurement). This produces
        the IDENTICAL dict shape and semantics as
        _build_context(conn, [creator]) would, verified in
        tests/test_x78_7_query_optimization.py, but pushes the creator
        filter into SQL via indexed/predicate-scoped queries instead of
        reading the whole table and filtering in Python.

        fanout_by_funder and wallet_cluster_funders are genuinely
        ecosystem-scoped BY DESIGN (_score_creator_fast looks up "how
        many OTHER creators does funder X also fund" for each of this
        creator's own funders) -- but they only ever need to be computed
        for the specific funder addresses that fund THIS creator, not
        every funder in the database. This queries this creator's own
        funders first (a small, indexed set), then scopes the
        ecosystem-wide aggregate queries to exactly those addresses via
        a parameterized IN clause -- same result, far smaller scan.
        """
        funders_by_creator: dict[str, list[dict[str, Any]]] = defaultdict(list)
        funder_rows = conn.execute("""
            SELECT cf.creator_address, cf.funder_address, COALESCE(cf.amount_sol, 0) AS amount_sol
            FROM creator_funders cf
            WHERE cf.creator_address = ?
              AND cf.is_cex = 0
              AND cf.funder_address IS NOT NULL
              AND cf.funder_address != ''
              AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)
        """, (creator,)).fetchall()
        funder_addresses = []
        for row in funder_rows:
            funders_by_creator[row["creator_address"]].append(dict(row))
            funder_addresses.append(row["funder_address"])

        fanout_by_funder: dict[str, int] = {}
        if funder_addresses:
            placeholders = ",".join("?" for _ in funder_addresses)
            for row in conn.execute(f"""
                SELECT funder_address, COUNT(DISTINCT creator_address) AS creators
                FROM creator_funders
                WHERE is_cex = 0
                  AND funder_address IN ({placeholders})
                  AND funder_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY funder_address
            """, funder_addresses).fetchall():
                fanout_by_funder[row["funder_address"]] = int(row["creators"] or 0)

        self_funding_row = conn.execute(
            "SELECT * FROM creator_self_funding WHERE creator_address = ?", (creator,)
        ).fetchone()
        self_funding = {creator: dict(self_funding_row)} if self_funding_row else {}

        outbound_by_creator: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in conn.execute("""
            SELECT creator_address, relationship_type, COUNT(*) AS n, SUM(COALESCE(amount_sol, 0)) AS sol
            FROM creator_outbound_classifications
            WHERE creator_address = ?
              AND recipient_address NOT IN (SELECT address FROM infra_wallets)
            GROUP BY creator_address, relationship_type
        """, (creator,)).fetchall():
            outbound_by_creator[row["creator_address"]][row["relationship_type"]] = dict(row)

        second_hop_by_creator = {}
        second_hop_row = conn.execute("""
            SELECT creator_address, COUNT(*) AS n, MAX(confidence_score) AS max_conf
            FROM creator_second_hop
            WHERE creator_address = ?
              AND upstream_address NOT IN (SELECT address FROM infra_wallets)
            GROUP BY creator_address
        """, (creator,)).fetchone()
        if second_hop_row:
            second_hop_by_creator[creator] = {
                "n": int(second_hop_row["n"] or 0), "max_conf": second_hop_row["max_conf"]
            }

        c2c_count: Counter[str] = Counter()
        for row in conn.execute("""
            SELECT source_creator, dest_creator FROM creator_c2c_edges
            WHERE source_creator = ? OR dest_creator = ?
        """, (creator, creator)).fetchall():
            c2c_count[row["source_creator"]] += 1
            c2c_count[row["dest_creator"]] += 1

        coord_by_creator: dict[str, dict[str, Any]] = defaultdict(lambda: {"edges": 0, "funders": set()})
        for row in conn.execute("""
            SELECT creator_a, creator_b, bridge_funder
            FROM coordinated_creator_edges
            WHERE (creator_a = ? OR creator_b = ?)
              AND bridge_funder NOT IN (SELECT address FROM infra_wallets)
        """, (creator, creator)).fetchall():
            a = row["creator_a"]
            b = row["creator_b"]
            funder = row["bridge_funder"]
            coord_by_creator[a]["edges"] += 1
            coord_by_creator[a]["funders"].add(funder)
            coord_by_creator[b]["edges"] += 1
            coord_by_creator[b]["funders"].add(funder)

        wallet_cluster_funders = set()
        if funder_addresses:
            placeholders = ",".join("?" for _ in funder_addresses)
            wallet_cluster_funders = {
                row["funder_wallet"]
                for row in conn.execute(f"""
                    SELECT funder_wallet
                    FROM wallet_clusters
                    WHERE funder_wallet IN ({placeholders})
                      AND funder_wallet NOT IN (SELECT address FROM infra_wallets)
                """, funder_addresses).fetchall()
            }

        farm_members = set()
        farm_row = conn.execute(
            "SELECT 1 FROM farm_cluster_members WHERE wallet_address = ? LIMIT 1", (creator,)
        ).fetchone()
        if farm_row:
            farm_members.add(creator)

        jito_creators = set()
        jito_row = conn.execute("""
            SELECT 1 FROM creator_tags
            WHERE creator_address = ? AND tag IN ('uses_jitotip', 'uses_jitotip_other')
            LIMIT 1
        """, (creator,)).fetchone()
        if jito_row:
            jito_creators.add(creator)

        tokens_by_creator: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conn.execute("""
            SELECT
                ta.earliest_tx_creator AS creator,
                ta.mint,
                COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                ta.market_cap_current,
                ta.created_at,
                ta.migrated_at,
                ta.market_cap_highest_at_ts,
                ta.lifecycle_stage,
                COALESCE(liq.liquidity_removed, 0) AS liquidity_removed,
                liq.liquidity_removed_at
            FROM token_analysis ta
            LEFT JOIN (
                SELECT mint,
                       MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed,
                       MAX(liquidity_removed_at) AS liquidity_removed_at
                FROM token_pool_accounts
                GROUP BY mint
            ) liq ON liq.mint = ta.mint
            WHERE ta.earliest_tx_creator = ?
        """, (creator,)).fetchall():
            item = dict(row)
            item.pop("creator")
            peak = item.get("peak_mc")
            item["g_class"] = _token_class_from_peak(peak) if peak and float(peak) > 0 else None
            tokens_by_creator[creator].append(item)

        return {
            "funders_by_creator": funders_by_creator,
            "fanout_by_funder": fanout_by_funder,
            "self_funding": self_funding,
            "outbound_by_creator": outbound_by_creator,
            "second_hop_by_creator": second_hop_by_creator,
            "c2c_count": c2c_count,
            "coord_by_creator": coord_by_creator,
            "wallet_cluster_funders": wallet_cluster_funders,
            "farm_members": farm_members,
            "jito_creators": jito_creators,
            "tokens_by_creator": tokens_by_creator,
        }

    def _build_context(self, conn: sqlite3.Connection, creators: list[str]) -> dict[str, Any]:
        creator_set = set(creators)
        funders_by_creator: dict[str, list[dict[str, Any]]] = defaultdict(list)
        fanout_by_funder: dict[str, int] = {}
        for row in conn.execute("""
            SELECT cf.creator_address, cf.funder_address, COALESCE(cf.amount_sol, 0) AS amount_sol
            FROM creator_funders cf
            WHERE cf.is_cex = 0
              AND cf.funder_address IS NOT NULL
              AND cf.funder_address != ''
              AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)
        """).fetchall():
            creator = row["creator_address"]
            if creator in creator_set:
                funders_by_creator[creator].append(dict(row))
        for row in conn.execute("""
            SELECT funder_address, COUNT(DISTINCT creator_address) AS creators
            FROM creator_funders
            WHERE is_cex = 0
              AND funder_address IS NOT NULL
              AND funder_address != ''
              AND funder_address NOT IN (SELECT address FROM infra_wallets)
            GROUP BY funder_address
        """).fetchall():
            fanout_by_funder[row["funder_address"]] = int(row["creators"] or 0)

        self_funding = {
            row["creator_address"]: dict(row)
            for row in conn.execute("SELECT * FROM creator_self_funding").fetchall()
        }

        outbound_by_creator: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in conn.execute("""
            SELECT creator_address, relationship_type, COUNT(*) AS n, SUM(COALESCE(amount_sol, 0)) AS sol
            FROM creator_outbound_classifications
            WHERE recipient_address NOT IN (SELECT address FROM infra_wallets)
            GROUP BY creator_address, relationship_type
        """).fetchall():
            outbound_by_creator[row["creator_address"]][row["relationship_type"]] = dict(row)

        second_hop_by_creator = {
            row["creator_address"]: {"n": int(row["n"] or 0), "max_conf": row["max_conf"]}
            for row in conn.execute("""
                SELECT creator_address, COUNT(*) AS n, MAX(confidence_score) AS max_conf
                FROM creator_second_hop
                WHERE upstream_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY creator_address
            """).fetchall()
        }

        c2c_count: Counter[str] = Counter()
        for row in conn.execute("SELECT source_creator, dest_creator FROM creator_c2c_edges").fetchall():
            c2c_count[row["source_creator"]] += 1
            c2c_count[row["dest_creator"]] += 1

        coord_by_creator: dict[str, dict[str, int]] = defaultdict(lambda: {"edges": 0, "funders": set()})
        for row in conn.execute("""
            SELECT creator_a, creator_b, bridge_funder
            FROM coordinated_creator_edges
            WHERE bridge_funder NOT IN (SELECT address FROM infra_wallets)
        """).fetchall():
            a = row["creator_a"]
            b = row["creator_b"]
            funder = row["bridge_funder"]
            coord_by_creator[a]["edges"] += 1
            coord_by_creator[a]["funders"].add(funder)
            coord_by_creator[b]["edges"] += 1
            coord_by_creator[b]["funders"].add(funder)

        wallet_cluster_funders = {
            row["funder_wallet"]
            for row in conn.execute("""
                SELECT funder_wallet
                FROM wallet_clusters
                WHERE funder_wallet NOT IN (SELECT address FROM infra_wallets)
            """).fetchall()
        }

        farm_members = {
            row["wallet_address"]
            for row in conn.execute("SELECT wallet_address FROM farm_cluster_members").fetchall()
        }

        jito_creators = {
            row["creator_address"]
            for row in conn.execute("""
                SELECT DISTINCT creator_address
                FROM creator_tags
                WHERE tag IN ('uses_jitotip', 'uses_jitotip_other')
            """).fetchall()
        }

        tokens_by_creator: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conn.execute("""
            SELECT
                ta.earliest_tx_creator AS creator,
                ta.mint,
                COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                ta.market_cap_current,
                ta.created_at,
                ta.migrated_at,
                ta.market_cap_highest_at_ts,
                ta.lifecycle_stage,
                COALESCE(liq.liquidity_removed, 0) AS liquidity_removed,
                liq.liquidity_removed_at
            FROM token_analysis ta
            LEFT JOIN (
                SELECT mint,
                       MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed,
                       MAX(liquidity_removed_at) AS liquidity_removed_at
                FROM token_pool_accounts
                GROUP BY mint
            ) liq ON liq.mint = ta.mint
            WHERE ta.earliest_tx_creator IS NOT NULL
              AND ta.earliest_tx_creator != ''
        """).fetchall():
            item = dict(row)
            creator = item.pop("creator")
            peak = item.get("peak_mc")
            item["g_class"] = _token_class_from_peak(peak) if peak and float(peak) > 0 else None
            tokens_by_creator[creator].append(item)

        return {
            "funders_by_creator": funders_by_creator,
            "fanout_by_funder": fanout_by_funder,
            "self_funding": self_funding,
            "outbound_by_creator": outbound_by_creator,
            "second_hop_by_creator": second_hop_by_creator,
            "c2c_count": c2c_count,
            "coord_by_creator": coord_by_creator,
            "wallet_cluster_funders": wallet_cluster_funders,
            "farm_members": farm_members,
            "jito_creators": jito_creators,
            "tokens_by_creator": tokens_by_creator,
        }

    def _score_creator_fast(self, creator: str, context: dict[str, Any]) -> dict[str, Any]:
        breakdown = ScoreBreakdown()
        funders = context["funders_by_creator"].get(creator, [])
        funder_addresses = [row["funder_address"] for row in funders]

        shared_funder_evidence = []
        for funder in funder_addresses:
            shared_count = context["fanout_by_funder"].get(funder, 0)
            if shared_count >= 6:
                breakdown.add("operator_score", 50, "shared_funder_6_plus", f"Shared non-infra funder reaches {shared_count} creators")
            elif shared_count >= 3:
                breakdown.add("operator_score", 35, "shared_funder_3_5", f"Shared non-infra funder reaches {shared_count} creators")
            elif shared_count >= 2:
                breakdown.add("operator_score", 20, "shared_funder_2", "Shared non-infra funder reaches 2 creators")
            if shared_count >= 2:
                shared_funder_evidence.append({"funder": funder, "creator_count": shared_count})

        sf = context["self_funding"].get(creator)
        is_self_funding = bool(sf and sf.get("is_self_funding"))
        if is_self_funding:
            breakdown.add("operator_score", 40, "self_funding_loop", "Self-funding loop detected")

        outbound = context["outbound_by_creator"].get(creator, {})
        if outbound.get("return_to_funder"):
            breakdown.add("operator_score", 40, "return_to_funder", "Creator returned SOL to a funder")
        if outbound.get("shared_payout_wallet"):
            breakdown.add("operator_score", 30, "shared_payout_wallet", "Shared payout wallet detected")
        if outbound.get("creator_to_upstream_hub"):
            breakdown.add("operator_score", 50, "creator_to_upstream_hub", "Creator links to an upstream hub")

        second_hop = context["second_hop_by_creator"].get(creator, {"n": 0, "max_conf": None})
        if second_hop["n"] > 0:
            breakdown.add("operator_score", 50, "second_hop_upstream", "2H upstream hub link detected")

        c2c_edges = context["c2c_count"].get(creator, 0)
        if c2c_edges:
            breakdown.add("operator_score", 25, "creator_funder_creator_chain", "Creator-to-creator chain detected")

        coord = context["coord_by_creator"].get(creator, {"edges": 0, "funders": set()})
        coord_edges = int(coord["edges"] or 0)
        if coord_edges:
            breakdown.add("operator_score", 20, "shared_secondary_non_infra", f"{coord_edges} non-infra coordination edges")

        if any(funder in context["wallet_cluster_funders"] for funder in funder_addresses):
            breakdown.add("operator_score", 15, "wallet_cluster", "Funder participates in wallet cluster")

        if creator in context["farm_members"]:
            breakdown.add("operator_score", 25, "farm_cluster", "Creator is associated with a farm cluster")

        if creator in context["jito_creators"] and breakdown.operator_score > 0:
            breakdown.add("operator_score", 10, "timing_jito_pattern", "Jito/timing pattern present alongside other evidence")

        tokens = context["tokens_by_creator"].get(creator, [])
        token_stats = self._score_token_history(breakdown, tokens)
        self._score_liquidity(breakdown, tokens)

        category = self._creator_category(breakdown, token_stats, is_self_funding)
        final_score = self._final_score(breakdown, critical_floor=is_self_funding)
        risk_level = self._risk_level(final_score)
        explanation = {
            "coordination_signals": {
                "funders": shared_funder_evidence[:20],
                "coord_edges": coord_edges,
                "coord_funders": len(coord.get("funders", set())),
                "outbound": outbound,
                "second_hop_count": second_hop["n"],
                "self_funding": sf,
            },
            "g_distribution": token_stats["g_distribution"],
            "token_history": token_stats,
            "liquidation_events": [t for t in tokens if t.get("liquidity_removed")],
            "reason_codes": breakdown.reason_codes,
            "reasons": breakdown.reasons,
        }
        return {
            "entity_id": creator,
            "creator_address": creator,
            "operator_score": self._dimension_score(breakdown.operator_score),
            "outcome_score": self._dimension_score(breakdown.outcome_score),
            "g_score": self._dimension_score(breakdown.g_score),
            "liquidation_score": self._dimension_score(breakdown.liquidation_score),
            "final_score": final_score,
            "category": category,
            "risk_level": risk_level,
            "migrated_tokens": token_stats["migrated_tokens"],
            "total_tokens": token_stats["total_tokens"],
            "g7_percentage": token_stats["g7_percentage"],
            "liquidation_count": token_stats["liquidity_removed_count"],
            "last_migrated_at": token_stats["last_migrated_at"],
            "top_reasons": breakdown.reasons[:6],
            "reason_codes": sorted(set(breakdown.reason_codes)),
            "explanation_json": explanation,
        }

    def _score_creator(self, conn: sqlite3.Connection, creator: str) -> dict[str, Any]:
        breakdown = ScoreBreakdown()

        funders = conn.execute("""
            SELECT cf.funder_address, COALESCE(cf.amount_sol, 0) AS amount_sol
            FROM creator_funders cf
            WHERE cf.creator_address = ?
              AND cf.is_cex = 0
              AND cf.funder_address IS NOT NULL
              AND cf.funder_address != ''
              AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)
        """, (creator,)).fetchall()
        funder_addresses = [row["funder_address"] for row in funders]

        shared_funder_evidence = []
        for funder in funder_addresses:
            row = conn.execute("""
                SELECT COUNT(DISTINCT creator_address) AS creators
                FROM creator_funders
                WHERE funder_address = ?
                  AND is_cex = 0
                  AND funder_address NOT IN (SELECT address FROM infra_wallets)
            """, (funder,)).fetchone()
            shared_count = int(row["creators"] or 0)
            if shared_count >= 6:
                breakdown.add("operator_score", 50, "shared_funder_6_plus", f"Shared non-infra funder reaches {shared_count} creators")
            elif shared_count >= 3:
                breakdown.add("operator_score", 35, "shared_funder_3_5", f"Shared non-infra funder reaches {shared_count} creators")
            elif shared_count >= 2:
                breakdown.add("operator_score", 20, "shared_funder_2", "Shared non-infra funder reaches 2 creators")
            if shared_count >= 2:
                shared_funder_evidence.append({"funder": funder, "creator_count": shared_count})

        sf = conn.execute("""
            SELECT is_self_funding, self_funding_percentage, self_funding_intermediates, total_funders
            FROM creator_self_funding
            WHERE creator_address = ?
        """, (creator,)).fetchone()
        is_self_funding = bool(sf and sf["is_self_funding"])
        if is_self_funding:
            breakdown.add("operator_score", 40, "self_funding_loop", "Self-funding loop detected")

        outbound_rows = conn.execute("""
            SELECT relationship_type, COUNT(*) AS n, SUM(COALESCE(amount_sol, 0)) AS sol
            FROM creator_outbound_classifications
            WHERE creator_address = ?
              AND recipient_address NOT IN (SELECT address FROM infra_wallets)
            GROUP BY relationship_type
        """, (creator,)).fetchall()
        outbound = {row["relationship_type"]: dict(row) for row in outbound_rows}
        if outbound.get("return_to_funder"):
            breakdown.add("operator_score", 40, "return_to_funder", "Creator returned SOL to a funder")
        if outbound.get("shared_payout_wallet"):
            breakdown.add("operator_score", 30, "shared_payout_wallet", "Shared payout wallet detected")
        if outbound.get("creator_to_upstream_hub"):
            breakdown.add("operator_score", 50, "creator_to_upstream_hub", "Creator links to an upstream hub")

        second_hop = conn.execute("""
            SELECT COUNT(*) AS n, MAX(confidence_score) AS max_conf
            FROM creator_second_hop
            WHERE creator_address = ?
              AND upstream_address NOT IN (SELECT address FROM infra_wallets)
        """, (creator,)).fetchone()
        if second_hop and int(second_hop["n"] or 0) > 0:
            breakdown.add("operator_score", 50, "second_hop_upstream", "2H upstream hub link detected")

        c2c = conn.execute("""
            SELECT COUNT(*) AS n
            FROM creator_c2c_edges
            WHERE source_creator = ? OR dest_creator = ?
        """, (creator, creator)).fetchone()
        if c2c and int(c2c["n"] or 0) > 0:
            breakdown.add("operator_score", 25, "creator_funder_creator_chain", "Creator-to-creator chain detected")

        coord = conn.execute("""
            SELECT COUNT(*) AS edges, COUNT(DISTINCT bridge_funder) AS funders
            FROM coordinated_creator_edges
            WHERE (creator_a = ? OR creator_b = ?)
              AND bridge_funder NOT IN (SELECT address FROM infra_wallets)
        """, (creator, creator)).fetchone()
        coord_edges = int(coord["edges"] or 0) if coord else 0
        if coord_edges:
            breakdown.add("operator_score", 20, "shared_secondary_non_infra", f"{coord_edges} non-infra coordination edges")

        wc = conn.execute("""
            SELECT COUNT(*) AS n
            FROM wallet_clusters
            WHERE funder_wallet IN ({})
        """.format(",".join("?" for _ in funder_addresses) or "NULL"), funder_addresses).fetchone()
        if wc and int(wc["n"] or 0) > 0:
            breakdown.add("operator_score", 15, "wallet_cluster", "Funder participates in wallet cluster")

        fc = conn.execute("""
            SELECT COUNT(*) AS n
            FROM farm_cluster_members
            WHERE wallet_address = ?
        """, (creator,)).fetchone()
        if fc and int(fc["n"] or 0) > 0:
            breakdown.add("operator_score", 25, "farm_cluster", "Creator is associated with a farm cluster")

        jito = conn.execute("""
            SELECT COUNT(*) AS n
            FROM creator_tags
            WHERE creator_address = ?
              AND tag IN ('uses_jitotip', 'uses_jitotip_other')
        """, (creator,)).fetchone()
        if jito and int(jito["n"] or 0) > 0 and breakdown.operator_score > 0:
            breakdown.add("operator_score", 10, "timing_jito_pattern", "Jito/timing pattern present alongside other evidence")

        tokens = self._load_creator_tokens(conn, creator)
        token_stats = self._score_token_history(breakdown, tokens)
        self._score_liquidity(breakdown, tokens)

        category = self._creator_category(breakdown, token_stats, is_self_funding)
        final_score = self._final_score(breakdown, critical_floor=is_self_funding)
        risk_level = self._risk_level(final_score)
        explanation = {
            "coordination_signals": {
                "funders": shared_funder_evidence[:20],
                "coord_edges": coord_edges,
                "outbound": outbound,
                "second_hop_count": int(second_hop["n"] or 0) if second_hop else 0,
                "self_funding": dict(sf) if sf else None,
            },
            "g_distribution": token_stats["g_distribution"],
            "token_history": token_stats,
            "liquidation_events": [t for t in tokens if t.get("liquidity_removed")],
            "reason_codes": breakdown.reason_codes,
            "reasons": breakdown.reasons,
        }
        return {
            "entity_id": creator,
            "creator_address": creator,
            "operator_score": self._dimension_score(breakdown.operator_score),
            "outcome_score": self._dimension_score(breakdown.outcome_score),
            "g_score": self._dimension_score(breakdown.g_score),
            "liquidation_score": self._dimension_score(breakdown.liquidation_score),
            "final_score": final_score,
            "category": category,
            "risk_level": risk_level,
            "migrated_tokens": token_stats["migrated_tokens"],
            "total_tokens": token_stats["total_tokens"],
            "g7_percentage": token_stats["g7_percentage"],
            "liquidation_count": token_stats["liquidity_removed_count"],
            "last_migrated_at": token_stats["last_migrated_at"],
            "top_reasons": breakdown.reasons[:6],
            "reason_codes": sorted(set(breakdown.reason_codes)),
            "explanation_json": explanation,
        }

    def _load_creator_tokens(self, conn: sqlite3.Connection, creator: str) -> list[dict[str, Any]]:
        rows = conn.execute("""
            SELECT
                ta.mint,
                COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                ta.market_cap_current,
                ta.created_at,
                ta.migrated_at,
                ta.market_cap_highest_at_ts,
                ta.lifecycle_stage,
                COALESCE(liq.liquidity_removed, 0) AS liquidity_removed,
                liq.liquidity_removed_at
            FROM token_analysis ta
            LEFT JOIN (
                SELECT mint,
                       MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed,
                       MAX(liquidity_removed_at) AS liquidity_removed_at
                FROM token_pool_accounts
                GROUP BY mint
            ) liq ON liq.mint = ta.mint
            WHERE ta.earliest_tx_creator = ?
        """, (creator,)).fetchall()
        tokens = []
        for row in rows:
            item = dict(row)
            peak = item.get("peak_mc")
            # Only classify tokens with a real peak — exclude tokens still in flight (no peak yet)
            item["g_class"] = _token_class_from_peak(peak) if peak and float(peak) > 0 else None
            tokens.append(item)
        return tokens

    def _score_token_history(self, breakdown: ScoreBreakdown, tokens: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(tokens)
        migrated = sum(1 for t in tokens if t.get("migrated_at") and t.get("lifecycle_stage") == "migrated")
        known_g = [t["g_class"] for t in tokens if t.get("g_class")]
        dist = dict(Counter(known_g))
        known_count = len(known_g)
        g7_count = dist.get("G7", 0)
        weak_count = sum(dist.get(g, 0) for g in ("G5", "G6", "G7"))
        strong_count = sum(dist.get(g, 0) for g in ("G1", "G2", "G3"))
        g7_pct = (g7_count / known_count * 100) if known_count else 0
        low_g_pct = (strong_count / known_count * 100) if known_count else 0
        avg_g = 0
        if known_g:
            avg_g = sum(int(g[1:]) for g in known_g) / known_count

        if migrated >= 50:
            breakdown.add("outcome_score", 70, "migrated_tokens_50_plus", f"{migrated} migrated tokens")
        elif migrated >= 25:
            breakdown.add("outcome_score", 50, "migrated_tokens_25_plus", f"{migrated} migrated tokens")
        elif migrated >= 10:
            breakdown.add("outcome_score", 35, "migrated_tokens_10_plus", f"{migrated} migrated tokens")
        elif migrated >= 3:
            breakdown.add("outcome_score", 20, "migrated_tokens_3_plus", f"{migrated} migrated tokens")

        if total >= 5 and weak_count / max(known_count, 1) >= 0.7:
            breakdown.add("outcome_score", 25, "repeated_low_peak_mc", "Repeated low peak market caps")
            breakdown.add("outcome_score", 25, "repeated_poor_outcomes", "Repeated poor outcomes")
        if total >= 5 and strong_count / max(known_count, 1) >= 0.5:
            breakdown.add("outcome_score", -30, "repeated_strong_performers", "Repeated strong performers")
        elif total >= 5 and 0 < strong_count < known_count:
            breakdown.add("outcome_score", -20, "mixed_healthy_outcomes", "Mixed or healthier outcomes")

        dump_60 = 0
        survival_seconds = []
        for token in tokens:
            created = _parse_ts(token.get("created_at"))
            peak_at = _parse_ts(token.get("market_cap_highest_at_ts"))
            if created and peak_at and 0 <= peak_at - created <= 60 and token.get("g_class") == "G7":
                dump_60 += 1
            if created and peak_at and peak_at >= created:
                survival_seconds.append(peak_at - created)
        if total and dump_60 / total >= 0.8:
            breakdown.add("outcome_score", 40, "dump_within_60s", "Most tokens peaked/dumped within 60 seconds")
        med_survival = median(survival_seconds) if survival_seconds else None
        if med_survival is not None and med_survival < 300 and total >= 5:
            breakdown.add("outcome_score", 30, "median_survival_under_5m", "Median peak/survival under 5 minutes")

        if known_count:
            if g7_pct >= 70:
                breakdown.add("g_score", 40, "g7_70_percent", f"{g7_pct:.0f}% tokens are G7")
            elif g7_pct >= 50:
                breakdown.add("g_score", 30, "g7_50_percent", f"{g7_pct:.0f}% tokens are G7")
            elif g7_pct >= 30:
                breakdown.add("g_score", 20, "g7_30_percent", f"{g7_pct:.0f}% tokens are G7")
            if avg_g >= 6:
                breakdown.add("g_score", 20, "high_average_g", f"Average G is {avg_g:.1f}")
            if g7_count >= 3:
                breakdown.add("g_score", 15, "consecutive_g7_streak", "Repeated G7 streak pattern")
            if low_g_pct >= 70:
                breakdown.add("g_score", -30, "strong_g1_g3_70_percent", "70%+ tokens are G1-G3")
            elif low_g_pct >= 50:
                breakdown.add("g_score", -20, "strong_g1_g3_50_percent", "50%+ tokens are G1-G3")

        migrated_timestamps = [
            _parse_ts(t.get("migrated_at"))
            for t in tokens
            if t.get("migrated_at") and t.get("lifecycle_stage") == "migrated"
        ]
        last_migrated_at = max((ts for ts in migrated_timestamps if ts), default=None)

        return {
            "total_tokens": total,
            "migrated_tokens": migrated,
            "g_distribution": dist,
            "g7_percentage": round(g7_pct, 2),
            "strong_g1_g3_percentage": round(low_g_pct, 2),
            "average_g": round(avg_g, 2),
            "dump_within_60s_count": dump_60,
            "median_survival_seconds": med_survival,
            "liquidity_removed_count": sum(1 for t in tokens if t.get("liquidity_removed")),
            "last_migrated_at": last_migrated_at,
        }

    def _score_liquidity(self, breakdown: ScoreBreakdown, tokens: list[dict[str, Any]]) -> None:
        migrated = [t for t in tokens if t.get("migrated_at") and t.get("lifecycle_stage") == "migrated"]
        liq = [t for t in tokens if t.get("liquidity_removed")]
        liq_count = len(liq)
        migrated_count = len(migrated)
        if liq_count:
            breakdown.add("liquidation_score", 25, "repeated_liquidation_behaviour", f"{liq_count} liquidity removals detected")
        soon = 0
        for token in liq:
            migrated_at = _parse_ts(token.get("migrated_at"))
            removed_at = _parse_ts(token.get("liquidity_removed_at"))
            if migrated_at and removed_at and 0 <= removed_at - migrated_at <= 3600:
                soon += 1
        if soon:
            breakdown.add("liquidation_score", 40, "liquidity_removed_shortly_after_migration", f"{soon} liquidity removals shortly after migration")
        if migrated_count:
            pct = liq_count / migrated_count
            if pct >= 0.5:
                breakdown.add("liquidation_score", 30, "high_liquidity_removal_percentage", f"{pct * 100:.0f}% migrated tokens had LP removal")
            elif migrated_count >= 10 and liq_count == 0:
                breakdown.add("liquidation_score", -30, "no_liquidation_many_launches", "No explicit liquidation across many launches")
            elif migrated_count >= 3 and liq_count == 0:
                breakdown.add("liquidation_score", -20, "lp_stable_across_tokens", "LP stable across multiple launches")

    def _creator_category(self, breakdown: ScoreBreakdown, stats: dict[str, Any], is_self_funding: bool) -> str:
        final = self._final_score(breakdown)
        g7_pct = stats.get("g7_percentage", 0)
        migrated = stats.get("migrated_tokens", 0)
        liq_count = stats.get("liquidity_removed_count", 0)
        if final >= 80:
            if is_self_funding:
                return "SELF_FUNDING_FARM"
            if g7_pct >= 50 and liq_count:
                return "LIQUIDITY_EXTRACTOR"
            return "CRITICAL_OPERATOR"
        if is_self_funding:
            return "SELF_FUNDING_FARM"
        if g7_pct >= 50 and liq_count:
            return "LIQUIDITY_EXTRACTOR"
        if migrated >= 10 and g7_pct >= 50:
            return "SERIAL_DUMPER"
        if breakdown.operator_score >= 80 and breakdown.outcome_score >= 50:
            return "HIGH_RISK_OPERATOR"
        if breakdown.operator_score >= 50:
            return "OPERATOR_MEMBER"
        if migrated >= 10:
            return "SERIAL_MIGRATOR"
        if breakdown.operator_score >= 20:
            return "COORDINATED_CREATOR"
        if final >= 20:
            return "WATCHLIST"
        return "LOW"

    def _score_networks(self, conn: sqlite3.Connection, creator_scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
        creator_map = {row["creator_address"]: row for row in creator_scores}
        networks = conn.execute("SELECT network_name, network_size, network_type FROM networks_release").fetchall()
        results = []
        for net in networks:
            members = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT creator_address FROM network_membership WHERE network_name=?",
                    (net["network_name"],),
                ).fetchall()
            ]
            scores = [creator_map[m] for m in members if m in creator_map]
            if scores:
                op = max(s["operator_score"] for s in scores)
                outcome = sum(s["outcome_score"] for s in scores) / len(scores)
                g = sum(s["g_score"] for s in scores) / len(scores)
                liq = sum(s["liquidation_score"] for s in scores) / len(scores)
                migrated = sum(s["migrated_tokens"] for s in scores)
                total = sum(s["total_tokens"] for s in scores)
                liq_count = sum(s["liquidation_count"] for s in scores)
                g7_pct = (
                    sum(s["g7_percentage"] * s["total_tokens"] for s in scores) / max(total, 1)
                    if total else 0
                )
                last_migrated_at = max(
                    (s["last_migrated_at"] for s in scores if s.get("last_migrated_at")),
                    default=None,
                )
            else:
                op = outcome = g = liq = 0
                migrated = total = liq_count = 0
                g7_pct = 0
                last_migrated_at = None

            coord = conn.execute("""
                SELECT COUNT(*) AS edges, COUNT(DISTINCT bridge_funder) AS funders
                FROM coordinated_creator_edges
                WHERE creator_a IN (SELECT creator_address FROM network_membership WHERE network_name=?)
                  AND creator_b IN (SELECT creator_address FROM network_membership WHERE network_name=?)
                  AND bridge_funder NOT IN (SELECT address FROM infra_wallets)
            """, (net["network_name"], net["network_name"])).fetchone()
            edges = int(coord["edges"] or 0) if coord else 0
            if edges >= 20:
                op += 20
            elif edges >= 1:
                op += 10

            breakdown = ScoreBreakdown(op, outcome, g, liq)
            for score in sorted(scores, key=lambda s: s["final_score"], reverse=True)[:8]:
                breakdown.reasons.extend(score["top_reasons"][:2])
                breakdown.reason_codes.extend(score["reason_codes"][:4])
            if edges:
                breakdown.reason_codes.append("network_coordination_edges")
                breakdown.reasons.append(f"{edges} intra-network coordination edges")

            final = self._final_score(breakdown)
            category = self._network_category(final, op, migrated, g7_pct, liq_count, net["network_type"])
            explanation = {
                "network_name": net["network_name"],
                "network_type": net["network_type"],
                "creator_count": len(members),
                "coordination_edges": edges,
                "creator_scores": sorted(
                    [
                        {
                            "creator": s["creator_address"],
                            "final_score": s["final_score"],
                            "category": s["category"],
                            "risk_level": s["risk_level"],
                        }
                        for s in scores
                    ],
                    key=lambda row: row["final_score"],
                    reverse=True,
                )[:50],
                "reason_codes": sorted(set(breakdown.reason_codes)),
                "reasons": list(dict.fromkeys(breakdown.reasons))[:12],
            }
            results.append({
                "entity_id": net["network_name"],
                "network_name": net["network_name"],
                "operator_score": self._dimension_score(op),
                "outcome_score": self._dimension_score(outcome),
                "g_score": self._dimension_score(g),
                "liquidation_score": self._dimension_score(liq),
                "final_score": final,
                "category": category,
                "risk_level": self._risk_level(final),
                "creator_count": len(members),
                "migrated_tokens": migrated,
                "total_tokens": total,
                "g7_percentage": round(g7_pct, 2),
                "liquidation_count": liq_count,
                "last_migrated_at": last_migrated_at,
                "top_reasons": list(dict.fromkeys(breakdown.reasons))[:6],
                "reason_codes": sorted(set(breakdown.reason_codes)),
                "explanation_json": explanation,
            })
        return results

    def _network_category(
        self,
        final_score: int,
        operator_score: float,
        migrated: int,
        g7_pct: float,
        liq_count: int,
        network_type: str | None,
    ) -> str:
        if network_type in ("cex_only", "infra_only"):
            return "NOISE_OR_INFRA"
        if final_score >= 80:
            return "CRITICAL_OPERATOR_GROUP"
        if operator_score >= 90 and (g7_pct >= 50 or liq_count):
            return "CONFIRMED_OPERATOR_GROUP"
        if final_score >= 60:
            return "HIGH_RISK_OPERATOR_GROUP"
        if operator_score >= 70:
            return "COORDINATED_OPERATOR"
        if migrated >= 3 or operator_score >= 30:
            return "EMERGING_SIGNAL"
        return "NOISE_OR_INFRA"

    def _dimension_score(self, value: float) -> float:
        return round(max(0, min(100, value)), 2)

    def _final_score(self, breakdown: ScoreBreakdown, critical_floor: bool = False) -> int:
        score = round(
            0.35 * self._dimension_score(breakdown.operator_score)
            + 0.25 * self._dimension_score(breakdown.outcome_score)
            + 0.25 * self._dimension_score(breakdown.g_score)
            + 0.15 * self._dimension_score(breakdown.liquidation_score)
        )
        if critical_floor:
            score = max(score, 80)
        return max(0, min(100, int(score)))

    def _risk_level(self, final_score: int) -> str:
        if final_score >= 80:
            return "CRITICAL"
        if final_score >= 60:
            return "HIGH"
        if final_score >= 40:
            return "MEDIUM"
        if final_score >= 20:
            return "WATCH"
        return "LOW"

    def _write_creator_scores(self, conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
        now = int(time.time())
        conn.executemany("""
            INSERT INTO creator_risk_scores (
                creator_address, operator_score, outcome_score, g_score, liquidation_score,
                final_score, category, risk_level, migrated_tokens, total_tokens,
                g7_percentage, liquidation_count, last_migrated_at, top_reasons, reason_codes,
                explanation_json, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(creator_address) DO UPDATE SET
                operator_score=excluded.operator_score,
                outcome_score=excluded.outcome_score,
                g_score=excluded.g_score,
                liquidation_score=excluded.liquidation_score,
                final_score=excluded.final_score,
                category=excluded.category,
                risk_level=excluded.risk_level,
                migrated_tokens=excluded.migrated_tokens,
                total_tokens=excluded.total_tokens,
                g7_percentage=excluded.g7_percentage,
                liquidation_count=excluded.liquidation_count,
                last_migrated_at=excluded.last_migrated_at,
                top_reasons=excluded.top_reasons,
                reason_codes=excluded.reason_codes,
                explanation_json=excluded.explanation_json,
                scored_at=excluded.scored_at
        """, [
            (
                row["creator_address"], row["operator_score"], row["outcome_score"],
                row["g_score"], row["liquidation_score"], row["final_score"],
                row["category"], row["risk_level"], row["migrated_tokens"],
                row["total_tokens"], row["g7_percentage"], row["liquidation_count"],
                row.get("last_migrated_at"),
                json.dumps(row["top_reasons"]), json.dumps(row["reason_codes"]),
                json.dumps(row["explanation_json"]), now,
            )
            for row in rows
        ])

    def _write_network_scores(self, conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
        now = int(time.time())
        conn.executemany("""
            INSERT INTO network_risk_scores (
                network_name, operator_score, outcome_score, g_score, liquidation_score,
                final_score, category, risk_level, creator_count, migrated_tokens,
                total_tokens, g7_percentage, liquidation_count, last_migrated_at,
                top_reasons, reason_codes, explanation_json, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network_name) DO UPDATE SET
                operator_score=excluded.operator_score,
                outcome_score=excluded.outcome_score,
                g_score=excluded.g_score,
                liquidation_score=excluded.liquidation_score,
                final_score=excluded.final_score,
                category=excluded.category,
                risk_level=excluded.risk_level,
                creator_count=excluded.creator_count,
                migrated_tokens=excluded.migrated_tokens,
                total_tokens=excluded.total_tokens,
                g7_percentage=excluded.g7_percentage,
                liquidation_count=excluded.liquidation_count,
                last_migrated_at=excluded.last_migrated_at,
                top_reasons=excluded.top_reasons,
                reason_codes=excluded.reason_codes,
                explanation_json=excluded.explanation_json,
                scored_at=excluded.scored_at
        """, [
            (
                row["network_name"], row["operator_score"], row["outcome_score"],
                row["g_score"], row["liquidation_score"], row["final_score"],
                row["category"], row["risk_level"], row["creator_count"],
                row["migrated_tokens"], row["total_tokens"], row["g7_percentage"],
                row["liquidation_count"], row.get("last_migrated_at"),
                json.dumps(row["top_reasons"]),
                json.dumps(row["reason_codes"]), json.dumps(row["explanation_json"]), now,
            )
            for row in rows
        ])

    def _append_history(self, conn: sqlite3.Connection, entity_type: str, rows: list[dict[str, Any]]) -> None:
        now = int(time.time())
        conn.executemany("""
            INSERT INTO risk_score_history (
                entity_type, entity_id, operator_score, outcome_score, g_score,
                liquidation_score, final_score, category, risk_level,
                reason_codes, explanation_json, actual_outcome_json, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                entity_type,
                row["entity_id"],
                row["operator_score"],
                row["outcome_score"],
                row["g_score"],
                row["liquidation_score"],
                row["final_score"],
                row["category"],
                row["risk_level"],
                json.dumps(row["reason_codes"]),
                json.dumps(row["explanation_json"]),
                json.dumps({
                    "migrated_tokens": row.get("migrated_tokens", 0),
                    "g7_percentage": row.get("g7_percentage", 0),
                    "liquidation_count": row.get("liquidation_count", 0),
                }),
                now,
            )
            for row in rows
        ])


def apply_migration(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        RiskScoringBuilder.apply_migration(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else "database/flex_complete_database.db"
    print(RiskScoringBuilder(db).run())
