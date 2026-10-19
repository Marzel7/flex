"""X27.1 — Creator Activity Intelligence: read-only composition of the
creator WALLET's own historical launch behaviour, independent of funding
provenance, attribution, infrastructure, or operation identity.

Answers exactly one question: "What do we already know about this
creator?" It never discusses treasury, sub-provisioner, infrastructure,
attribution, or operator concepts -- those already have dedicated
Discovery sections (Funding Walkback, Attribution Outcome, Operational
Behaviour, Operation Identity / Canonical Operator respectively).

Metrics are derived exclusively from `token_analysis` (the core DB's own
per-token analysis table), grouped by the creator's resolved wallet
address. `token_analysis` was chosen over `wt_creator_launches` /
`wt_watchtower_launches` because both of those are WATCHTOWER-detection-
scoped (staged-wallet-only and live-cascade-only respectively) and would
silently undercount any creator not caught by those specific detection
paths -- `token_analysis` is the platform's most complete per-token
record, live-updated as tokens are observed.

Creator identity resolution follows the same trust rule already
established by src/ops/attribution_outcome.py's evaluate_launcher_profile
(): `pf_ws_creator` is the authoritative launch-creator column when
present; `earliest_tx_creator` is only used as a fallback, never merged
in when `pf_ws_creator` exists, because it can be a shared transaction
authority common to many unrelated launches (confirmed live: several
`earliest_tx_creator`/`pf_ws_creator` values are shared across 5,000+
rows -- almost certainly a shared program/relay address, not a genuine
individual creator).

Every field is either:
  - a Fact: a value/count read directly from persisted token_analysis rows, or
  - explicitly labelled when the supporting data is absent (e.g. peak
    market cap "not captured" for launches with no market_cap_highest).
Never a probability/confidence label -- classification thresholds
(Phase 5) are fixed, measurable counts/durations, not heuristics.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

# X43.1 — reuse the SAME dual-format (ISO-8601 / epoch-as-text) timestamp
# parser X43.0 already introduced for this exact ambiguity in
# token_analysis.created_at/migrated_at (see operational_behaviour_tags.py's
# docstring for the two formats observed live). This module's own SQL-side
# _epoch_expr() CASE conversion was believed to always yield a numeric
# value, but the production crash trace (TypeError: unsupported operand
# type(s) for -: 'str' and 'str' at the last_activity - first_observed
# subtraction) shows a string can still reach Python here under some
# concurrent-write/coercion condition not reproduced by a static read-only
# scan. Normalizing defensively in Python, right before every arithmetic
# use, closes that gap regardless of the SQL layer's behaviour.
from src.ops.operational_behaviour_tags import _parse_token_analysis_timestamp

# X27.1 Phase 5 — repeat-creator classification thresholds. Measurable,
# fixed counts -- not arbitrary labels. A single-launch creator has
# launched exactly once; a repeat creator has launched multiple times;
# a highly active creator crosses both a volume and a recency bar (many
# launches AND recent activity), distinguishing a currently active serial
# launcher from a repeat creator whose activity is entirely historical.
SINGLE_LAUNCH_THRESHOLD = 1
HIGHLY_ACTIVE_LAUNCH_COUNT = 10
HIGHLY_ACTIVE_RECENT_WINDOW_SECONDS = 7 * 86400

STATUS_SINGLE_LAUNCH = "SINGLE_LAUNCH"
STATUS_REPEAT_CREATOR = "REPEAT_CREATOR"
STATUS_HIGHLY_ACTIVE = "HIGHLY_ACTIVE_CREATOR"

STATUS_LABELS = {
    STATUS_SINGLE_LAUNCH: "Single launch",
    STATUS_REPEAT_CREATOR: "Repeat creator",
    STATUS_HIGHLY_ACTIVE: "Highly active creator",
}


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _safe_created_ats(rows, has_created_at: bool, creator: Optional[str]) -> list[float]:
    """X43.1 — parse each row's created_at defensively, even though the
    caller's SQL CASE expression (_epoch_expr) should already have produced
    a numeric value for every real column format seen live. A value that
    survives as neither a number nor a parseable ISO-8601 timestamp is
    excluded here (never allowed to reach a later subtraction as a string,
    which is exactly the class of production crash --
    TypeError: unsupported operand type(s) for -: 'str' and 'str' -- this
    function exists to close) and logged at WARNING with the offending
    creator/mint/raw value so a future occurrence is diagnosable rather
    than silent or fatal."""
    import logging
    out: list[float] = []
    for r in rows:
        if not has_created_at or r["created_at"] is None:
            continue
        parsed = _parse_token_analysis_timestamp(r["created_at"])
        if parsed is None:
            logging.getLogger("creator_activity").warning(
                "unparseable created_at for creator=%r mint=%r raw_value=%r",
                creator, r["mint"] if "mint" in r.keys() else None, r["created_at"],
            )
            continue
        out.append(parsed)
    return out


class CreatorActivityService:
    """Composes the Discovery Creator Activity card for one creator wallet.
    Read-only against the core DB (token_analysis lives there), following
    the same connection/PRAGMA pattern as OperationalBehaviourService."""

    def __init__(self, core_db_path: str) -> None:
        self.core_db_path = core_db_path

    def build(self, creator: Optional[str]) -> Optional[dict[str, Any]]:
        """Returns None only when no creator address is known at all, or
        token_analysis itself is unavailable. Otherwise always returns a
        report; if the creator has no matching rows (e.g. a wallet that
        turned out not to be a creator), returns a report with
        launches_created=0 rather than None, so callers can distinguish
        "no creator resolved" from "creator resolved, zero launches found"."""
        if not creator:
            return None

        conn = _connect(self.core_db_path)
        try:
            if not _table_exists(conn, "token_analysis"):
                return None
            columns = _columns(conn, "token_analysis")

            # X27.1 — pf_ws_creator is the authoritative launch-creator
            # column (same trust rule as evaluate_launcher_profile(),
            # attribution_outcome.py:165-171): prefer it exclusively when
            # present; only fall back to earliest_tx_creator when
            # pf_ws_creator doesn't exist as a column at all. Never merge
            # the two via COALESCE -- earliest_tx_creator alone can be a
            # shared transaction authority common to thousands of
            # unrelated launches, which would inflate an individual
            # creator's counts with unrelated tokens.
            if "pf_ws_creator" in columns:
                creator_column = "pf_ws_creator"
            elif "earliest_tx_creator" in columns:
                creator_column = "earliest_tx_creator"
            else:
                return None

            has_market_cap = "market_cap_highest" in columns
            has_migrated_at = "migrated_at" in columns
            has_created_at = "created_at" in columns

            # created_at/migrated_at are unix-epoch numbers for most rows but
            # ISO-8601 strings ("2026-07-16T21:02:32Z") for some (~1.8% of
            # token_analysis) -- CAST(... AS REAL) silently truncates a
            # string like "2026-07-16..." to 2026.0 rather than erroring, so
            # a blind cast produces a wrong-but-plausible-looking timestamp.
            # Same normalisation precedent as attribution_outcome.py's
            # creator_funders observed_expr.
            def _epoch_expr(col: str) -> str:
                # A numeric value can still read typeof()='text' if the
                # declared column affinity is TEXT (SQLite coerces on
                # insert) -- check for a value that is ENTIRELY digits
                # (GLOB '[0-9]*' alone would also match "2026-07-16...",
                # since that also starts with a digit) rather than trusting
                # typeof() alone, so a plain epoch string doesn't get
                # misrouted into unixepoch() (which returns NULL for
                # non-date strings) and an ISO date string doesn't get
                # truncated by a numeric cast.
                return (
                    f"CASE WHEN typeof({col}) IN ('integer','real') "
                    f"OR {col} NOT GLOB '*[^0-9]*' THEN CAST({col} AS REAL) "
                    f"ELSE unixepoch({col}) END"
                )

            select_cols = ["mint"]
            if has_created_at:
                select_cols.append(f"{_epoch_expr('created_at')} AS created_at")
            if has_migrated_at:
                select_cols.append(f"{_epoch_expr('migrated_at')} AS migrated_at")
            if has_market_cap:
                select_cols.append("market_cap_highest")

            rows = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM token_analysis WHERE {creator_column}=?",
                (creator,),
            ).fetchall()
        finally:
            conn.close()

        launches_created = len(rows)
        if launches_created == 0:
            return {
                "creator": creator,
                "launches_created": 0,
                "successful_migrations": 0,
                "migration_rate_pct": None,
                "first_observed": None,
                "last_activity": None,
                "active_lifetime_seconds": None,
                "average_launch_cadence_seconds": None,
                "peak_market_cap": None,
                "status": None,
                "status_label": None,
                "coverage_note": "No launches found for this creator address in token_analysis.",
            }

        created_ats = _safe_created_ats(rows, has_created_at, creator)
        first_observed = min(created_ats) if created_ats else None
        last_activity = max(created_ats) if created_ats else None
        active_lifetime_seconds = (
            int(last_activity - first_observed)
            if first_observed is not None and last_activity is not None
            else None
        )
        average_cadence = (
            active_lifetime_seconds / (launches_created - 1)
            if active_lifetime_seconds is not None and launches_created > 1
            else None
        )

        successful_migrations = sum(
            1 for r in rows if has_migrated_at and r["migrated_at"] is not None
        ) if has_migrated_at else None
        migration_rate_pct = (
            round(successful_migrations / launches_created * 100, 1)
            if successful_migrations is not None else None
        )

        peak_values = [r["market_cap_highest"] for r in rows if has_market_cap and r["market_cap_highest"]]
        peak_market_cap = max(peak_values) if peak_values else None
        peak_coverage = len(peak_values)

        status, status_label = self._classify(launches_created, last_activity)

        return {
            "creator": creator,
            "launches_created": launches_created,
            "successful_migrations": successful_migrations,
            "migration_rate_pct": migration_rate_pct,
            "first_observed": int(first_observed) if first_observed is not None else None,
            "last_activity": int(last_activity) if last_activity is not None else None,
            "active_lifetime_seconds": active_lifetime_seconds,
            "average_launch_cadence_seconds": average_cadence,
            "peak_market_cap": peak_market_cap,
            "peak_market_cap_coverage": f"{peak_coverage} of {launches_created} launches" if has_market_cap else None,
            "status": status,
            "status_label": status_label,
            "coverage_note": "Reflects launches present in token_analysis for this creator address; not a chain-wide guarantee of every launch ever made.",
        }

    @staticmethod
    def _classify(launches_created: int, last_activity: Optional[float]) -> tuple[Optional[str], Optional[str]]:
        """X27.1 Phase 5 -- fixed, measurable thresholds; no heuristic
        scoring. HIGHLY_ACTIVE requires both a volume bar (>= 10 launches)
        and a recency bar (activity within the last 7 days) so a creator
        whose 10+ launches all happened long ago reads as REPEAT_CREATOR,
        not HIGHLY_ACTIVE -- "highly active" should mean currently active,
        not merely historically prolific."""
        import time
        if launches_created <= SINGLE_LAUNCH_THRESHOLD:
            return STATUS_SINGLE_LAUNCH, STATUS_LABELS[STATUS_SINGLE_LAUNCH]
        is_recent = (
            last_activity is not None
            and (time.time() - last_activity) <= HIGHLY_ACTIVE_RECENT_WINDOW_SECONDS
        )
        if launches_created >= HIGHLY_ACTIVE_LAUNCH_COUNT and is_recent:
            return STATUS_HIGHLY_ACTIVE, STATUS_LABELS[STATUS_HIGHLY_ACTIVE]
        return STATUS_REPEAT_CREATOR, STATUS_LABELS[STATUS_REPEAT_CREATOR]
