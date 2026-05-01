"""
Token-level prediction scoring.

Creator/network risk predicts what the token is likely to do.
Real-time token data tells us whether the prediction was right.

This is a risk estimate, not a guarantee. Labels like LIKELY_DUMP
describe expected behaviour based on historical operator patterns.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PREDICTION_LABELS = [
    "CRITICAL_RISK",
    "SELF_FUNDED_TOKEN",
    "SERIAL_OPERATOR_TOKEN",
    "LIQUIDATION_RISK",
    "NETWORK_RISK_TOKEN",
    "LIKELY_DUMP",
    "WATCH",
    "LOW_RISK",
    "PENDING_CREATOR",
    "PENDING_FUNDING",
    "PENDING_RISK_SCORE",
    "INSUFFICIENT_HISTORY",
]

OUTCOME_LABELS = ["FAST_DUMP", "SLOW_DUMP", "LIQUIDATED", "SURVIVED", "STRONG_PERFORMER", "UNKNOWN"]
PENDING_STATUSES = {"PENDING_CREATOR", "PENDING_FUNDING", "PENDING_RISK_SCORE", "INSUFFICIENT_HISTORY"}


def _risk_level(score: int, label: str | None = None) -> str:
    # Label overrides when it implies higher risk than score alone
    if label in ("LIKELY_DUMP", "SELF_FUNDED_TOKEN", "LIQUIDATION_RISK", "NETWORK_RISK_TOKEN"):
        if score >= 80:
            return "CRITICAL"
        if score >= 60:
            return "HIGH"
        return "MEDIUM"  # minimum MEDIUM for these labels
    if label in ("CRITICAL_RISK", "SERIAL_OPERATOR_TOKEN"):
        return "CRITICAL"
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    if score >= 20:
        return "WATCH"
    return "LOW"


def _prediction_label(
    score: int,
    creator_category: str | None,
    network_category: str | None,
    funding_score: int,
    liquidation_score: int,
    g7_pct: float,
) -> str:
    if score >= 80:
        if creator_category in ("SELF_FUNDING_FARM",):
            return "SELF_FUNDED_TOKEN"
        if creator_category in ("CRITICAL_OPERATOR", "HIGH_RISK_OPERATOR"):
            return "SERIAL_OPERATOR_TOKEN"
        if liquidation_score >= 60:
            return "LIQUIDATION_RISK"
        return "CRITICAL_RISK"
    if creator_category in ("SELF_FUNDING_FARM",):
        return "SELF_FUNDED_TOKEN"
    if liquidation_score >= 50:
        return "LIQUIDATION_RISK"
    if network_category in ("CRITICAL_OPERATOR_GROUP", "CONFIRMED_OPERATOR_GROUP", "HIGH_RISK_OPERATOR_GROUP"):
        return "NETWORK_RISK_TOKEN"
    if g7_pct >= 70 or score >= 60:
        return "LIKELY_DUMP"
    if score >= 20:
        return "WATCH"
    return "LOW_RISK"


@dataclass
class TokenScore:
    mint: str
    creator_address: str | None
    network_name: str | None
    prediction_status: str = "COMPLETE"
    prediction_confidence: str = "HIGH"
    data_completeness: float = 1.0
    creator_score: int = 0
    network_score: int = 0
    funding_score: int = 0
    outcome_history_score: int = 0
    liquidation_score: int = 0
    reason_codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    explanation: dict[str, Any] = field(default_factory=dict)

    def add(self, dimension: str, points: int, code: str, reason: str) -> None:
        current = getattr(self, dimension)
        setattr(self, dimension, min(100, current + points))
        self.reason_codes.append(code)
        self.reasons.append(reason)

    @property
    def prediction_score(self) -> int | None:
        if self.prediction_status != "COMPLETE":
            return None
        # If no network membership, redistribute network weight to creator
        creator_w = 0.55 if self.network_score == 0 else 0.40
        network_w = 0.00 if self.network_score == 0 else 0.15
        return min(100, round(
            creator_w * min(100, self.creator_score) +
            network_w * min(100, self.network_score) +
            0.25 * min(100, self.funding_score) +
            0.15 * min(100, self.outcome_history_score) +
            0.05 * min(100, self.liquidation_score)
        ))


_context_cache_by_db: dict[str, dict[str, Any]] = {}
_context_cache_ts_by_db: dict[str, float] = {}
_CONTEXT_TTL = 300.0  # rebuild context every 5 minutes
_migration_applied_paths: set[str] = set()


class TokenPredictionBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_cached_context(self, conn: sqlite3.Connection) -> dict[str, Any]:
        db_key = str(Path(self.db_path).resolve())
        if (
            time.time() - _context_cache_ts_by_db.get(db_key, 0) > _CONTEXT_TTL
            or db_key not in _context_cache_by_db
        ):
            _context_cache_by_db[db_key] = self._build_context(conn)
            _context_cache_ts_by_db[db_key] = time.time()
        return _context_cache_by_db[db_key]

    def run(self) -> dict[str, Any]:
        started = time.time()
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            self._apply_migration(conn)
            # Tokens already scored live (BIRTH/MIGRATED event) keep their prediction frozen.
            # Batch only scores tokens that have never been live-scored, and resolves outcomes.
            live_scored = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT mint FROM token_prediction_events WHERE event_type IN ('BIRTH','MIGRATED')"
                ).fetchall()
            }
            context = self._build_context(conn)
            tokens = [t for t in self._load_tokens(conn) if t["mint"] not in live_scored]
            scores = [self._score_token(t, context) for t in tokens]
            if scores:
                self._write_scores(conn, scores)
            rescored = self._process_rescore_queue(conn, context)
            backfilled = self._rescore_incomplete_candidates(conn, context)
            self._resolve_outcomes(conn)
            conn.commit()
            return {
                "status": "success",
                "tokens_scored": len(scores),
                "queued_rescored": rescored,
                "pending_backfilled": backfilled,
                "duration_seconds": round(time.time() - started, 2),
            }
        except Exception:
            conn.rollback()
            logger.exception("[TokenPredictionBuilder] failed")
            raise
        finally:
            conn.close()

    def score_single(self, conn: sqlite3.Connection, mint: str, event_type: str = "prediction_updated") -> dict[str, Any] | None:
        """Score one token and write result. Used for real-time updates."""
        self._apply_migration(conn)
        conn.row_factory = sqlite3.Row
        conn.commit()
        row = conn.execute("""
            SELECT ta.mint, COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) AS earliest_tx_creator,
                   COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                   ta.market_cap_current, ta.migrated_at, ta.lifecycle_stage,
                   0 AS liquidity_removed
            FROM token_analysis ta
            WHERE ta.mint = ?
        """, (mint,)).fetchone()
        if not row:
            return None
        context = self._get_cached_context(conn)
        score = self._score_token(dict(row), context)
        self._write_scores(conn, [score])
        self._write_events(conn, [score], event_type)
        conn.commit()
        self._schedule_outcome_checks(mint)
        label = self._label_for_score(score)
        risk_level = self._risk_level_for_score(score, label)
        return {
            "prediction_score": score.prediction_score,
            "risk_level": risk_level,
            "label": label,
            "prediction_status": score.prediction_status,
            "prediction_confidence": score.prediction_confidence,
            "data_completeness": score.data_completeness,
        }

    def _schedule_outcome_checks(self, mint: str) -> None:
        import threading
        for delay in (300, 1800, 7200):  # 5m, 30m, 2h
            def _check(m=mint):
                try:
                    c = sqlite3.connect(self.db_path, timeout=30)
                    c.row_factory = sqlite3.Row
                    c.execute("PRAGMA journal_mode=WAL")
                    self._resolve_outcomes(c)
                    c.commit()
                    c.close()
                except Exception as e:
                    logger.warning(f"[PREDICTION] outcome check failed for {m[:16]}: {e}")
            timer = threading.Timer(delay, _check)
            timer.daemon = True
            timer.start()

    def _apply_migration(self, conn: sqlite3.Connection) -> None:
        db_key = str(Path(self.db_path).resolve())
        if db_key in _migration_applied_paths:
            return
        migration = (
            Path(__file__).resolve().parent.parent.parent
            / "database" / "migrations" / "add_token_predictions.sql"
        )
        self._preensure_legacy_columns(conn)
        conn.executescript(migration.read_text())
        try:
            from src.utils.infra_mapping import sync_infra_wallets
            sync_infra_wallets(conn)
        except Exception:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS infra_wallets (
                    address TEXT PRIMARY KEY,
                    type TEXT,
                    label TEXT
                )
            """)
        self._ensure_prediction_schema(conn)
        _migration_applied_paths.add(db_key)

    def _preensure_legacy_columns(self, conn: sqlite3.Connection) -> None:
        table = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='token_prediction_scores'
        """).fetchone()
        if not table:
            return
        cols = {row[1] for row in conn.execute("PRAGMA table_info(token_prediction_scores)").fetchall()}
        for name, ddl in {
            "prediction_status": "ALTER TABLE token_prediction_scores ADD COLUMN prediction_status TEXT NOT NULL DEFAULT 'COMPLETE'",
            "prediction_confidence": "ALTER TABLE token_prediction_scores ADD COLUMN prediction_confidence TEXT NOT NULL DEFAULT 'HIGH'",
            "data_completeness": "ALTER TABLE token_prediction_scores ADD COLUMN data_completeness REAL NOT NULL DEFAULT 1.0",
        }.items():
            if name not in cols:
                conn.execute(ddl)

    def _ensure_prediction_schema(self, conn: sqlite3.Connection) -> None:
        cols = {row[1]: {"notnull": row[3]} for row in conn.execute("PRAGMA table_info(token_prediction_scores)").fetchall()}
        for name, ddl in {
            "prediction_status": "ALTER TABLE token_prediction_scores ADD COLUMN prediction_status TEXT NOT NULL DEFAULT 'COMPLETE'",
            "prediction_confidence": "ALTER TABLE token_prediction_scores ADD COLUMN prediction_confidence TEXT NOT NULL DEFAULT 'HIGH'",
            "data_completeness": "ALTER TABLE token_prediction_scores ADD COLUMN data_completeness REAL NOT NULL DEFAULT 1.0",
        }.items():
            if name not in cols:
                conn.execute(ddl)
        cols = {row[1]: {"notnull": row[3]} for row in conn.execute("PRAGMA table_info(token_prediction_scores)").fetchall()}
        if cols.get("prediction_score", {}).get("notnull") or cols.get("risk_level", {}).get("notnull"):
            self._rebuild_prediction_scores_nullable(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_status ON token_prediction_scores(prediction_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_confidence ON token_prediction_scores(prediction_confidence)")

    def _rebuild_prediction_scores_nullable(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute("ALTER TABLE token_prediction_scores RENAME TO token_prediction_scores_old")
        conn.execute("""
            CREATE TABLE token_prediction_scores (
                mint TEXT PRIMARY KEY,
                creator_address TEXT,
                network_name TEXT,
                prediction_score INTEGER,
                risk_level TEXT,
                prediction_label TEXT NOT NULL DEFAULT 'PENDING_CREATOR',
                prediction_status TEXT NOT NULL DEFAULT 'COMPLETE',
                prediction_confidence TEXT NOT NULL DEFAULT 'HIGH',
                data_completeness REAL NOT NULL DEFAULT 1.0,
                reason_codes TEXT,
                explanation_json TEXT,
                creator_score INTEGER NOT NULL DEFAULT 0,
                network_score INTEGER NOT NULL DEFAULT 0,
                funding_score INTEGER NOT NULL DEFAULT 0,
                outcome_history_score INTEGER NOT NULL DEFAULT 0,
                liquidation_score INTEGER NOT NULL DEFAULT 0,
                predicted_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        old_cols = {row[1] for row in conn.execute("PRAGMA table_info(token_prediction_scores_old)").fetchall()}
        status_expr = "prediction_status" if "prediction_status" in old_cols else "'COMPLETE'"
        confidence_expr = "prediction_confidence" if "prediction_confidence" in old_cols else "'HIGH'"
        completeness_expr = "data_completeness" if "data_completeness" in old_cols else "1.0"
        conn.execute(f"""
            INSERT INTO token_prediction_scores (
                mint, creator_address, network_name, prediction_score, risk_level,
                prediction_label, prediction_status, prediction_confidence, data_completeness,
                reason_codes, explanation_json, creator_score, network_score,
                funding_score, outcome_history_score, liquidation_score,
                predicted_at, last_updated_at
            )
            SELECT
                mint, creator_address, network_name, prediction_score, risk_level,
                prediction_label, {status_expr}, {confidence_expr}, {completeness_expr},
                reason_codes, explanation_json, creator_score, network_score,
                funding_score, outcome_history_score, liquidation_score,
                predicted_at, last_updated_at
            FROM token_prediction_scores_old
        """)
        conn.execute("DROP TABLE token_prediction_scores_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_prediction_score ON token_prediction_scores(prediction_score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_risk_level ON token_prediction_scores(risk_level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_creator ON token_prediction_scores(creator_address)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_network ON token_prediction_scores(network_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tps_label ON token_prediction_scores(prediction_label)")

    def _build_context(self, conn: sqlite3.Connection) -> dict[str, Any]:
        # Creator risk scores
        creator_scores = {
            r["creator_address"]: dict(r)
            for r in conn.execute("""
                SELECT creator_address, final_score, category, risk_level,
                       operator_score, outcome_score, g_score, liquidation_score,
                       g7_percentage, migrated_tokens, liquidation_count
                FROM creator_risk_scores
            """).fetchall()
        }

        # Network risk scores
        network_scores = {
            r["network_name"]: dict(r)
            for r in conn.execute("""
                SELECT network_name, final_score, category, risk_level,
                       operator_score, g7_percentage
                FROM network_risk_scores
            """).fetchall()
        }

        # Network membership
        creator_network: dict[str, str] = {}
        for r in conn.execute("SELECT creator_address, network_name FROM network_membership").fetchall():
            creator_network[r["creator_address"]] = r["network_name"]

        # Self-funding
        self_funding = {
            r["creator_address"]: dict(r)
            for r in conn.execute("SELECT creator_address, is_self_funding, self_funding_percentage FROM creator_self_funding").fetchall()
        }

        # Funder fanout
        fanout: dict[str, int] = {
            r["funder_address"]: int(r["creators"] or 0)
            for r in conn.execute("""
                SELECT funder_address, COUNT(DISTINCT creator_address) AS creators
                FROM creator_funders
                WHERE is_cex = 0
                  AND funder_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY funder_address
            """).fetchall()
        }

        # Funders per creator
        funders_by_creator: dict[str, list[str]] = defaultdict(list)
        funding_rows_by_creator: dict[str, int] = defaultdict(int)
        for r in conn.execute("""
            SELECT creator_address, COUNT(*) AS n
            FROM creator_funders
            GROUP BY creator_address
        """).fetchall():
            funding_rows_by_creator[r["creator_address"]] = int(r["n"] or 0)

        for r in conn.execute("""
            SELECT creator_address, funder_address
            FROM creator_funders
            WHERE is_cex = 0 AND funder_address NOT IN (SELECT address FROM infra_wallets)
        """).fetchall():
            funders_by_creator[r["creator_address"]].append(r["funder_address"])

        # Second hop
        second_hop = {
            r["creator_address"]: int(r["n"] or 0)
            for r in conn.execute("""
                SELECT creator_address, COUNT(*) AS n
                FROM creator_second_hop
                WHERE upstream_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY creator_address
            """).fetchall()
        }

        # Outbound classifications
        outbound: dict[str, set[str]] = defaultdict(set)
        for r in conn.execute("""
            SELECT creator_address, relationship_type
            FROM creator_outbound_classifications
            WHERE recipient_address NOT IN (SELECT address FROM infra_wallets)
        """).fetchall():
            outbound[r["creator_address"]].add(r["relationship_type"])

        return {
            "creator_scores": creator_scores,
            "network_scores": network_scores,
            "creator_network": creator_network,
            "self_funding": self_funding,
            "fanout": fanout,
            "funders_by_creator": funders_by_creator,
            "funding_rows_by_creator": funding_rows_by_creator,
            "second_hop": second_hop,
            "outbound": outbound,
        }

    def _load_tokens(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute("""
            SELECT
                ta.mint,
                COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) AS creator_address,
                COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                ta.market_cap_current,
                ta.migrated_at,
                ta.lifecycle_stage,
                COALESCE(liq.liquidity_removed, 0) AS liquidity_removed
            FROM token_analysis ta
            LEFT JOIN (
                SELECT mint, MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed
                FROM token_pool_accounts GROUP BY mint
            ) liq ON liq.mint = ta.mint
        """).fetchall()
        return [dict(r) for r in rows]

    def _load_tokens_by_mint(self, conn: sqlite3.Connection, mints: list[str]) -> list[dict[str, Any]]:
        if not mints:
            return []
        placeholders = ",".join("?" for _ in mints)
        rows = conn.execute(f"""
            SELECT
                ta.mint,
                COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) AS creator_address,
                COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                ta.market_cap_current,
                ta.migrated_at,
                ta.lifecycle_stage,
                COALESCE(liq.liquidity_removed, 0) AS liquidity_removed
            FROM token_analysis ta
            LEFT JOIN (
                SELECT mint, MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed
                FROM token_pool_accounts GROUP BY mint
            ) liq ON liq.mint = ta.mint
            WHERE ta.mint IN ({placeholders})
        """, mints).fetchall()
        return [dict(r) for r in rows]

    def _process_rescore_queue(self, conn: sqlite3.Connection, context: dict[str, Any], limit: int = 500) -> int:
        rows = conn.execute("""
            SELECT mint
            FROM token_rescore_queue
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        mints = [row["mint"] for row in rows]
        if not mints:
            return 0
        scores = [self._score_token(token, context) for token in self._load_tokens_by_mint(conn, mints)]
        if scores:
            self._write_scores(conn, scores)
            self._write_events(conn, scores, "RESCORE")
        conn.executemany("DELETE FROM token_rescore_queue WHERE mint = ?", [(mint,) for mint in mints])
        return len(scores)

    def _rescore_incomplete_candidates(self, conn: sqlite3.Connection, context: dict[str, Any], limit: int = 1000) -> int:
        rows = conn.execute("""
            SELECT mint
            FROM token_prediction_scores
            WHERE prediction_status != 'COMPLETE'
               OR (
                    COALESCE(prediction_score, 0) = 0
                AND COALESCE(risk_level, 'LOW') = 'LOW'
                AND COALESCE(prediction_label, 'LOW_RISK') = 'LOW_RISK'
               )
            ORDER BY last_updated_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        mints = [row["mint"] for row in rows]
        if not mints:
            return 0
        scores = [self._score_token(token, context) for token in self._load_tokens_by_mint(conn, mints)]
        if scores:
            self._write_scores(conn, scores)
            self._write_events(conn, scores, "RESCORE")
        return len(scores)

    def _score_token(self, token: dict[str, Any], ctx: dict[str, Any]) -> TokenScore:
        mint = token["mint"]
        creator = token.get("creator_address") or token.get("earliest_tx_creator")
        network = ctx["creator_network"].get(creator) if creator else None

        ts = TokenScore(mint=mint, creator_address=creator, network_name=network)

        pending = self._pending_state(creator, ctx)
        if pending:
            status, completeness, reason = pending
            ts.prediction_status = status
            ts.prediction_confidence = "LOW"
            ts.data_completeness = completeness
            ts.reason_codes.append(status.lower())
            ts.reasons.append(reason)
            ts.explanation = {
                "prediction_status": status,
                "prediction_confidence": "LOW",
                "data_completeness": completeness,
                "missing_reason": reason,
                "creator_address": creator,
                "network_name": network,
                "reasons": ts.reasons,
                "reason_codes": ts.reason_codes,
            }
            return ts

        # ── Creator score ──────────────────────────────────────
        cs = ctx["creator_scores"].get(creator, {}) if creator else {}
        ts.creator_score = int(cs.get("final_score") or 0)
        creator_category = cs.get("category")
        if creator_category:
            ts.add("creator_score", 0, "creator_scored", f"Creator category: {creator_category}")

        # ── Network score ──────────────────────────────────────
        ns = ctx["network_scores"].get(network, {}) if network else {}
        ts.network_score = int(ns.get("final_score") or 0)
        network_category = ns.get("category")

        # ── Funding score ──────────────────────────────────────
        sf = ctx["self_funding"].get(creator, {}) if creator else {}
        if sf.get("is_self_funding"):
            ts.add("funding_score", 40, "self_funding_loop", "Creator has self-funding loop")

        funders = ctx["funders_by_creator"].get(creator, []) if creator else []
        for funder in funders:
            fanout = ctx["fanout"].get(funder, 0)
            if fanout >= 6:
                ts.add("funding_score", 15, "shared_funder_6_plus", f"Funder reaches {fanout} creators")
                break
            elif fanout >= 2:
                ts.add("funding_score", 15, "shared_funder_multi", f"Funder reaches {fanout} creators")
                break

        if ctx["second_hop"].get(creator, 0) > 0:
            ts.add("funding_score", 25, "second_hop_upstream", "Creator linked to 2H upstream hub")

        outbound = ctx["outbound"].get(creator, set()) if creator else set()
        if "shared_payout_wallet" in outbound:
            ts.add("funding_score", 20, "shared_payout_wallet", "Shared payout wallet detected")
        if "return_to_funder" in outbound:
            ts.add("funding_score", 15, "return_to_funder", "Creator returned SOL to a funder")
        if "creator_to_upstream_hub" in outbound:
            ts.add("funding_score", 20, "creator_to_upstream_hub", "Creator links to upstream hub")

        if network_category in ("CRITICAL_OPERATOR_GROUP", "CONFIRMED_OPERATOR_GROUP"):
            ts.add("funding_score", 20, "confirmed_operator_group", "Creator in confirmed operator group")
        elif network_category in ("HIGH_RISK_OPERATOR_GROUP",):
            ts.add("funding_score", 10, "high_risk_operator_group", "Creator in high-risk operator group")

        # ── Outcome history score ──────────────────────────────
        g7_pct = float(cs.get("g7_percentage") or 0)
        migrated = int(cs.get("migrated_tokens") or 0)
        liq_count = int(cs.get("liquidation_count") or 0)

        if migrated >= 10 and g7_pct >= 70:
            ts.add("outcome_history_score", 60, "repeated_g7", f"Creator: {g7_pct:.0f}% tokens are G7")
        elif migrated >= 5 and g7_pct >= 50:
            ts.add("outcome_history_score", 40, "majority_g7", f"Creator: {g7_pct:.0f}% tokens are G7")
        elif migrated >= 3 and g7_pct >= 30:
            ts.add("outcome_history_score", 20, "some_g7", f"Creator: {g7_pct:.0f}% tokens are G7")

        if migrated >= 50:
            ts.add("outcome_history_score", 30, "serial_migrator_50", f"{migrated} migrated tokens")
        elif migrated >= 20:
            ts.add("outcome_history_score", 20, "serial_migrator_20", f"{migrated} migrated tokens")
        elif migrated >= 10:
            ts.add("outcome_history_score", 10, "serial_migrator_10", f"{migrated} migrated tokens")

        # ── Liquidation score ──────────────────────────────────
        liq_score_raw = int(cs.get("liquidation_score") or 0)
        ts.liquidation_score = liq_score_raw

        if liq_count >= 5:
            ts.add("liquidation_score", 20, "repeated_liquidation", f"Creator has {liq_count} liquidation events")

        # ── Build explanation ──────────────────────────────────
        ts.explanation = {
            "prediction_status": "COMPLETE",
            "prediction_confidence": None,
            "data_completeness": 1.0,
            "creator_category": creator_category,
            "network_category": network_category,
            "network_name": network,
            "creator_final_score": ts.creator_score,
            "network_final_score": ts.network_score,
            "funding_score": ts.funding_score,
            "outcome_history_score": ts.outcome_history_score,
            "liquidation_score": ts.liquidation_score,
            "creator_g7_pct": g7_pct,
            "creator_migrated_tokens": migrated,
            "self_funding": bool(sf.get("is_self_funding")),
            "second_hop": ctx["second_hop"].get(creator, 0) if creator else 0,
            "reasons": ts.reasons,
            "reason_codes": ts.reason_codes,
        }
        ts.prediction_confidence = self._complete_confidence(ts, migrated)
        ts.explanation["prediction_confidence"] = ts.prediction_confidence

        return ts

    def _pending_state(self, creator: str | None, ctx: dict[str, Any]) -> tuple[str, float, str] | None:
        if not creator:
            return ("PENDING_CREATOR", 0.2, "Creator address has not been resolved yet")
        if ctx["funding_rows_by_creator"].get(creator, 0) == 0:
            return ("PENDING_FUNDING", 0.45, "Creator exists, but funding extraction has not produced funder rows yet")
        creator_score = ctx["creator_scores"].get(creator)
        if not creator_score:
            return ("PENDING_RISK_SCORE", 0.65, "Creator funding exists, but creator risk score has not been built yet")
        if int(creator_score.get("migrated_tokens") or 0) < 2:
            return ("INSUFFICIENT_HISTORY", 0.8, "Creator has fewer than 2 migrated tokens in history")
        return None

    def _complete_confidence(self, score: TokenScore, migrated: int) -> str:
        if migrated >= 5 and (
            score.creator_score >= 60
            or score.network_score >= 60
            or score.funding_score >= 40
            or score.outcome_history_score >= 40
        ):
            return "HIGH"
        return "MEDIUM"

    def _write_scores(self, conn: sqlite3.Connection, scores: list[TokenScore]) -> None:
        now = int(time.time())
        conn.executemany("""
            INSERT INTO token_prediction_scores (
                mint, creator_address, network_name,
                prediction_score, risk_level, prediction_label,
                prediction_status, prediction_confidence, data_completeness,
                reason_codes, explanation_json,
                creator_score, network_score, funding_score,
                outcome_history_score, liquidation_score,
                predicted_at, last_updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mint) DO UPDATE SET
                creator_address=excluded.creator_address,
                network_name=excluded.network_name,
                prediction_score=excluded.prediction_score,
                risk_level=excluded.risk_level,
                prediction_label=excluded.prediction_label,
                prediction_status=excluded.prediction_status,
                prediction_confidence=excluded.prediction_confidence,
                data_completeness=excluded.data_completeness,
                reason_codes=excluded.reason_codes,
                explanation_json=excluded.explanation_json,
                creator_score=excluded.creator_score,
                network_score=excluded.network_score,
                funding_score=excluded.funding_score,
                outcome_history_score=excluded.outcome_history_score,
                liquidation_score=excluded.liquidation_score,
                last_updated_at=excluded.last_updated_at
        """, [
            self._score_row(s, now) for s in scores
        ])

    def _score_row(self, s: TokenScore, now: int) -> tuple:
        score = s.prediction_score
        label = self._label_for_score(s)
        rl = self._risk_level_for_score(s, label)
        return (
            s.mint, s.creator_address, s.network_name,
            score, rl, label, s.prediction_status, s.prediction_confidence, s.data_completeness,
            json.dumps(list(dict.fromkeys(s.reason_codes))),
            json.dumps(s.explanation),
            s.creator_score, s.network_score, s.funding_score,
            s.outcome_history_score, s.liquidation_score,
            now, now,
        )

    def _label_for_score(self, s: TokenScore) -> str:
        if s.prediction_status != "COMPLETE":
            return s.prediction_status
        score = s.prediction_score or 0
        creator_cat = s.explanation.get("creator_category")
        network_cat = s.explanation.get("network_category")
        g7_pct = float(s.explanation.get("creator_g7_pct") or 0)
        return _prediction_label(score, creator_cat, network_cat, s.funding_score, s.liquidation_score, g7_pct)

    def _risk_level_for_score(self, s: TokenScore, label: str | None = None) -> str | None:
        if s.prediction_status != "COMPLETE":
            return None
        label = label or self._label_for_score(s)
        return _risk_level(s.prediction_score or 0, label)

    def _write_events(self, conn: sqlite3.Connection, scores: list[TokenScore], event_type: str) -> None:
        now = int(time.time())
        rows = conn.execute("""
            SELECT ta.mint,
                   COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                   ta.market_cap_current,
                   COALESCE(liq.liquidity_removed, 0) AS liquidity_removed
            FROM token_analysis ta
            LEFT JOIN (
                SELECT mint, MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed
                FROM token_pool_accounts GROUP BY mint
            ) liq ON liq.mint = ta.mint
        """).fetchall()
        token_data = {r["mint"]: dict(r) for r in rows}

        conn.executemany("""
            INSERT INTO token_prediction_events (
                mint, creator_address, event_type,
                prediction_score, risk_level, prediction_label,
                market_cap, peak_market_cap, liquidity_removed, event_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """, [
            (
                s.mint, s.creator_address, event_type,
                s.prediction_score,
                self._risk_level_for_score(s),
                self._label_for_score(s),
                token_data.get(s.mint, {}).get("market_cap_current"),
                token_data.get(s.mint, {}).get("peak_mc"),
                1 if token_data.get(s.mint, {}).get("liquidity_removed") else 0,
                now,
            )
            for s in scores
        ])

    def _resolve_outcomes(self, conn: sqlite3.Connection) -> None:
        """Resolve outcomes for tokens that have enough data to judge."""
        now = int(time.time())
        rows = conn.execute("""
            SELECT
                tps.mint, tps.prediction_score, tps.prediction_label, tps.risk_level,
                ta.migrated_at, ta.created_at, ta.market_cap_current,
                COALESCE(ta.market_cap_highest, ta.market_cap_current, 0) AS peak_mc,
                ta.market_cap_highest_at_ts,
                COALESCE(liq.liquidity_removed, 0) AS liquidity_removed
            FROM token_prediction_scores tps
            JOIN token_analysis ta ON ta.mint = tps.mint
            LEFT JOIN (
                SELECT mint, MAX(COALESCE(liquidity_removed, 0)) AS liquidity_removed
                FROM token_pool_accounts GROUP BY mint
            ) liq ON liq.mint = tps.mint
            WHERE ta.migrated_at IS NOT NULL
              AND ta.lifecycle_stage = 'migrated'
              AND tps.prediction_status = 'COMPLETE'
              AND ta.mint NOT IN (SELECT mint FROM token_prediction_outcomes WHERE prediction_correct IS NOT NULL)
              AND tps.mint IN (
                  SELECT DISTINCT mint FROM token_prediction_events
                  WHERE event_type IN ('BIRTH', 'MIGRATED')
              )
        """).fetchall()

        outcomes = []
        for r in rows:
            migrated_at = r["migrated_at"]
            if isinstance(migrated_at, str):
                try:
                    from datetime import datetime
                    migrated_at = int(datetime.fromisoformat(migrated_at.replace("Z", "+00:00")).timestamp())
                except Exception:
                    continue
            elif migrated_at:
                migrated_at = int(float(migrated_at))
            else:
                continue

            age_seconds = now - migrated_at
            if age_seconds < 1800:  # wait at least 30 min before resolving
                continue

            peak_mc = float(r["peak_mc"] or 0)
            current_mc = float(r["market_cap_current"] or 0)
            liq_removed = bool(r["liquidity_removed"])
            peak_at = r["market_cap_highest_at_ts"]
            try:
                peak_at = int(float(peak_at)) if peak_at else None
            except Exception:
                peak_at = None

            dump_60s = bool(peak_at and peak_at - migrated_at <= 60 and peak_mc < 75000)
            dump_5m = bool(peak_at and peak_at - migrated_at <= 300 and peak_mc < 150000)
            survived_30m = age_seconds >= 1800 and current_mc > 5000
            survived_2h = age_seconds >= 7200 and current_mc > 5000

            if liq_removed:
                actual_outcome = "LIQUIDATED"
            elif dump_60s:
                actual_outcome = "FAST_DUMP"
            elif dump_5m:
                actual_outcome = "SLOW_DUMP"
            elif peak_mc >= 500000 and survived_30m:
                actual_outcome = "STRONG_PERFORMER"
            elif survived_2h:
                actual_outcome = "SURVIVED"
            else:
                actual_outcome = "UNKNOWN"

            predicted_high = r["risk_level"] in ("HIGH", "CRITICAL")
            bad_outcome = actual_outcome in ("FAST_DUMP", "SLOW_DUMP", "LIQUIDATED")
            good_outcome = actual_outcome in ("SURVIVED", "STRONG_PERFORMER")

            if predicted_high and bad_outcome:
                correct = 1
            elif not predicted_high and good_outcome:
                correct = 1
            elif predicted_high and good_outcome:
                correct = 0  # false positive
            elif not predicted_high and bad_outcome:
                correct = 0  # false negative
            else:
                correct = None

            outcomes.append((
                r["mint"], r["prediction_score"], r["prediction_label"], r["risk_level"],
                actual_outcome, None,
                1 if dump_60s else 0,
                1 if dump_5m else 0,
                1 if liq_removed else 0,
                1 if survived_30m else 0,
                1 if survived_2h else 0,
                peak_mc, current_mc, correct, now,
            ))

        if outcomes:
            conn.executemany("""
                INSERT INTO token_prediction_outcomes (
                    mint, prediction_score, prediction_label, predicted_risk_level,
                    actual_outcome, actual_g_level,
                    dumped_within_60s, dumped_within_5m, liquidity_removed,
                    survived_30m, survived_2h,
                    peak_market_cap, final_market_cap, prediction_correct, resolved_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    actual_outcome=excluded.actual_outcome,
                    dumped_within_60s=excluded.dumped_within_60s,
                    dumped_within_5m=excluded.dumped_within_5m,
                    liquidity_removed=excluded.liquidity_removed,
                    survived_30m=excluded.survived_30m,
                    survived_2h=excluded.survived_2h,
                    peak_market_cap=excluded.peak_market_cap,
                    final_market_cap=excluded.final_market_cap,
                    prediction_correct=excluded.prediction_correct,
                    resolved_at=excluded.resolved_at
            """, outcomes)


class TokenPredictionRescoreWorker:
    """Process tokens whose creator/funding/risk/network data changed after first scoring."""

    def __init__(self, db_path: str, limit: int = 500):
        self.db_path = db_path
        self.limit = limit

    def run(self) -> dict[str, Any]:
        started = time.time()
        builder = TokenPredictionBuilder(self.db_path)
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            builder._apply_migration(conn)
            context = builder._build_context(conn)
            processed = builder._process_rescore_queue(conn, context, self.limit)
            conn.commit()
            return {
                "status": "success",
                "processed": processed,
                "duration_seconds": round(time.time() - started, 2),
            }
        except Exception:
            conn.rollback()
            logger.exception("[TokenPredictionRescoreWorker] failed")
            raise
        finally:
            conn.close()


def apply_migration(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        builder = TokenPredictionBuilder(db_path)
        builder._apply_migration(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "database/flex_complete_database.db"
    result = TokenPredictionBuilder(db).run()
    print(result)
