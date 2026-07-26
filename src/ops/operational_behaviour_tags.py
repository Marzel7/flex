"""X29.1 Stage 2 — Operational Behaviour tag framework.

Answers "how does the operation behave?" Zero or more additive tags per
launch (X29.0 Part 2, Dimension 2) -- never mutually exclusive, never used
to determine topology.

This module performs NO new behavioural detection. Per X29.0 Part 2's
Behaviour table, it wires together the already-additive, already-correct
logic that exists today:

  - Rapid Birth->Migration, Burst Launcher: src/ops/behaviour_queue.py's
    rapid_birth_launch_lookup()/burst_launch_lookup(), reused verbatim.
    behaviour_queue.py already returns archetypes_matched as a list per
    mint (its own docstring: "a launch CAN legitimately exhibit more than
    one archetype at once") -- this module does not recompute that logic,
    it only re-exposes it as one of the three tag sources.
  - Repeat Creator: src/ops/attribution_outcome.py's
    evaluate_launcher_profile(), the platform's one vetted creator-history
    classifier -- reused verbatim, same as investigation_pipeline.py already
    does, just no longer force-overriding a topology bucket in the process
    (X29.0 Part 1's identified defect).

High Migration Success and Slow Burn (X29.0 Gap 4) have no existing
implementation to reuse and are NOT implemented in this module -- they are
explicitly left as future work, not stubbed with an invented threshold.

X43.0 -- read-only classification expansion (observational only, never
consulted by attribution/treasury-confirmation/merge/Operator/Operation
logic, per the frozen X39-X41 architecture):

  - RAPID_MIGRATION / MIGRATION_5_TO_15M / DELAYED_MIGRATION: bucket
    token_analysis.migrated_at - token_analysis.created_at into the three
    fixed thresholds the task specifies (<300s / 300-900s / >=900s). No new
    table, no new column -- both timestamps already exist on every migrated
    token row. created_at is stored in two formats in this table (ISO-8601
    strings and, for a real subset of rows, epoch-as-text) -- both are
    parsed defensively; a row that parses as neither is simply excluded
    from migration-timing classification (never guessed).
  - CREATOR_RECYCLING: same creator wallet (the SAME creator_of lookup this
    module already builds for Repeat Creator) appears across >1 DISTINCT
    mint in the current population. Exact wallet-address reuse only -- no
    clustering, no inferred identity, per the task's explicit instruction.
  - PROVISIONING_BURST is deliberately NOT implemented: no canonical
    "provisioning burst" definition exists anywhere else in this codebase
    (grepped for PROVISIONING_BURST/provisioning_burst across src/ --
    zero hits). The task explicitly says to leave it hidden rather than
    invent a threshold, so it is omitted entirely, not stubbed with a zero
    count.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from src.ops.behaviour_queue import rapid_birth_launch_lookup, burst_launch_lookup, RAPID_BIRTH_LAUNCH, BURST_LAUNCH

REPEAT_CREATOR = "REPEAT_CREATOR"
RAPID_MIGRATION = "RAPID_MIGRATION"
MIGRATION_5_TO_15M = "MIGRATION_5_TO_15M"
DELAYED_MIGRATION = "DELAYED_MIGRATION"
CREATOR_RECYCLING = "CREATOR_RECYCLING"
QUICK_BIRTH_MIGRATION = "QUICK_BIRTH_MIGRATION"
UNKNOWN_BEHAVIOUR = "UNKNOWN_BEHAVIOUR"

# X43.0 thresholds -- fixed exactly as specified by the task, not tuned.
RAPID_MIGRATION_MAX_SECONDS = 300
MIGRATION_5_TO_15M_MAX_SECONDS = 900

BEHAVIOUR_LABELS = {
    RAPID_BIRTH_LAUNCH: "Rapid Birth→Migration",
    BURST_LAUNCH: "Burst Launcher",
    REPEAT_CREATOR: "Repeat Creator",
    RAPID_MIGRATION: "Rapid Migration (<5m)",
    MIGRATION_5_TO_15M: "Migration (5–15m)",
    DELAYED_MIGRATION: "Delayed Migration (>15m)",
    CREATOR_RECYCLING: "Creator Recycling",
    QUICK_BIRTH_MIGRATION: "Quick Birth ≤5s → Migration",
    UNKNOWN_BEHAVIOUR: "Unknown",
}

BEHAVIOUR_ORDER = (
    RAPID_BIRTH_LAUNCH, BURST_LAUNCH,
    RAPID_MIGRATION, MIGRATION_5_TO_15M, DELAYED_MIGRATION, CREATOR_RECYCLING,
)

# X65.0 -- canonical (exclusive) precedence order, derived from measured
# specificity, NOT popularity (see docs/design/x65_0/x65_0_precedence.md).
# RAPID_MIGRATION is the single most "popular" tag (93.5% coverage measured
# live) yet ranks LAST among the matchable rules -- it requires only one,
# broad, single-launch condition. QUICK_BIRTH_MIGRATION was measured as a
# strict subset of RAPID_MIGRATION (100% overlap live) and must therefore
# outrank it. Read top-to-bottom: the FIRST rule a launch matches wins;
# every other rule it also satisfies is simply not consulted (still
# available, unchanged, via the additive `behaviours` list for filtering).
CANONICAL_BEHAVIOUR_ORDER = (
    RAPID_BIRTH_LAUNCH,
    QUICK_BIRTH_MIGRATION,
    BURST_LAUNCH,
    CREATOR_RECYCLING,
    RAPID_MIGRATION,
    MIGRATION_5_TO_15M,
    DELAYED_MIGRATION,
    UNKNOWN_BEHAVIOUR,
)


def _parse_token_analysis_timestamp(value) -> float | None:
    """token_analysis.created_at holds either an ISO-8601 string
    ('2026-04-11T10:27:27Z') or, for a real subset of rows, an epoch
    value stored as text ('1775915651.24585'). Both are parsed; anything
    else returns None so the caller can skip that row rather than guess."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        import datetime
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def _migration_behaviour_tag(migration_seconds: float) -> str:
    if migration_seconds < RAPID_MIGRATION_MAX_SECONDS:
        return RAPID_MIGRATION
    if migration_seconds < MIGRATION_5_TO_15M_MAX_SECONDS:
        return MIGRATION_5_TO_15M
    return DELAYED_MIGRATION


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _resolve_creator(core_conn: sqlite3.Connection, mint: str) -> str | None:
    if not _table_exists(core_conn, "token_analysis"):
        return None
    row = core_conn.execute(
        "SELECT pf_ws_creator, earliest_tx_creator FROM token_analysis WHERE mint=?", (mint,)
    ).fetchone()
    if not row:
        return None
    return row[0] or row[1]


def build_behaviour_classification(
    ops_db_path: str,
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Classifies every mint in the Stage-1 population
    (wt_attribution_outcomes, matching funding_topology.py's and
    investigation_pipeline.py's own population) by additive Behaviour tags.
    Read-only, zero writes. Reuses existing evidence functions verbatim --
    no new thresholds, no new logic."""
    now = int(now or time.time())
    since = now - window_seconds

    rapid_lookup = rapid_birth_launch_lookup(ops_db_path)
    burst_lookup = burst_launch_lookup(core_db_path, window_seconds=window_seconds, now=now)

    ops_conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops_conn.row_factory = sqlite3.Row
    ops_conn.execute("PRAGMA query_only=ON")
    core_conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True, timeout=5)
    core_conn.row_factory = sqlite3.Row
    core_conn.execute("PRAGMA query_only=ON")
    try:
        # X65.35 -- same fix as funding_topology.py's build_topology_
        # classification: window by each mint's resolved launch create
        # time, not by wt_attribution_outcomes.completed_at (walkback
        # completion time). Both classifiers must share the same
        # population/window basis since build_operational_intelligence
        # zips their results together by mint.
        from src.ops.discovery_window import launch_create_times_for_mints

        all_rows = ops_conn.execute("SELECT mint FROM wt_attribution_outcomes").fetchall()
        all_mints_unfiltered = [r["mint"] for r in all_rows]
        create_times = launch_create_times_for_mints(ops_conn, core_conn, all_mints_unfiltered)
        mints = [
            m for m in all_mints_unfiltered
            if m in create_times and create_times[m] >= since
        ]

        creator_of: dict[str, str | None] = {}
        migration_seconds_of: dict[str, float] = {}
        if mints and _table_exists(core_conn, "token_analysis"):
            placeholders = ",".join("?" for _ in mints)
            for r in core_conn.execute(
                f"SELECT mint, pf_ws_creator, earliest_tx_creator, created_at, migrated_at "
                f"FROM token_analysis WHERE mint IN ({placeholders})",
                mints,
            ):
                creator_of[r["mint"]] = r["pf_ws_creator"] or r["earliest_tx_creator"]
                # X43.0 -- migrated_at is already a unix epoch (per this
                # table's existing idx_ta_migrated_at usage elsewhere);
                # created_at needs the dual-format parse (see
                # _parse_token_analysis_timestamp's docstring).
                created_ts = _parse_token_analysis_timestamp(r["created_at"])
                migrated_ts = r["migrated_at"]
                if created_ts is not None and migrated_ts is not None:
                    delta = float(migrated_ts) - created_ts
                    if delta >= 0:
                        migration_seconds_of[r["mint"]] = delta
                    # a negative delta means the two timestamps are not
                    # comparable for this row (bad data) -- excluded, never
                    # coerced to zero or guessed.

        # X43.0 CREATOR_RECYCLING -- exact wallet reuse only, computed over
        # the SAME creator_of map Repeat Creator already builds above. A
        # creator counts as "recycling" only if it appears on >1 DISTINCT
        # mint within this population -- no clustering, no heuristic.
        creator_mint_counts: dict[str, int] = {}
        for _mint, _creator in creator_of.items():
            if _creator:
                creator_mint_counts[_creator] = creator_mint_counts.get(_creator, 0) + 1

        assignments: dict[str, dict[str, Any]] = {}
        counts = {b: 0 for b in BEHAVIOUR_ORDER}
        for mint in mints:
            tags: list[str] = []
            rb = rapid_lookup.get(mint)
            if rb and rb.get("matched"):
                tags.append(RAPID_BIRTH_LAUNCH)
            bl = burst_lookup.get(mint)
            if bl and bl.get("matched"):
                tags.append(BURST_LAUNCH)
            creator = creator_of.get(mint)
            if creator:
                if creator_mint_counts.get(creator, 0) > 1:
                    tags.append(CREATOR_RECYCLING)
            migration_seconds = migration_seconds_of.get(mint)
            if migration_seconds is not None:
                tags.append(_migration_behaviour_tag(migration_seconds))
            for t in tags:
                counts[t] += 1
            assignments[mint] = {
                "behaviours": tags,
                "labels": [BEHAVIOUR_LABELS[t] for t in tags],
                "creator": creator,
            }

        total = len(mints)
    finally:
        ops_conn.close()
        core_conn.close()

    return {
        "window_seconds": window_seconds,
        "generated_at": now,
        "total_launches": total,
        "behaviours": [
            {
                "behaviour": b,
                "label": BEHAVIOUR_LABELS[b],
                "count": counts[b],
                "coverage_pct": round(counts[b] / total * 100, 1) if total else 0.0,
            }
            for b in BEHAVIOUR_ORDER
        ],
        "assignments": assignments,
    }


def launches_with_behaviour(classification: dict[str, Any], behaviour: str) -> list[str]:
    """Drill-down: mints carrying this behaviour tag (additive -- a mint may
    appear under more than one behaviour's drill-down list)."""
    return [m for m, a in classification["assignments"].items() if behaviour in a["behaviours"]]


def canonical_behaviour_for(behaviours: list[str], *, is_quick_birth_migration: bool = False) -> str:
    """X65.0 -- exactly ONE canonical behaviour per launch, using the most
    specific matching rule (see docs/design/x65_0/x65_0_precedence.md for
    the full derivation). This does NOT replace or mutate the additive
    `behaviours` list -- that list, and QUICK_BIRTH_MIGRATION's own
    separate `is_quick_birth_migration` flag, remain fully intact and
    unchanged for filtering/cross-dimensional-query use
    (src.ops.operational_intelligence.query()'s explicit "no hierarchy
    should prevent cross-dimensional searching" requirement is untouched
    by this function). This function is purely additive: it derives one
    more field from facts that already exist, using CANONICAL_BEHAVIOUR_
    ORDER's precedence, and is the ONLY thing the Discovery Cohort
    Report's Behaviour Cohort UI should read for attribution purposes.

    `behaviours` may be empty, one entry, or several (as already produced
    by build_behaviour_classification) -- QUICK_BIRTH_MIGRATION is passed
    separately since it is computed by a different module
    (src.ops.operational_intelligence.classify_quick_birth_migration) from
    a different timestamp triple and was never part of the `behaviours`
    list to begin with (see the X65.0 overlap report's finding that it is
    a strict subset of RAPID_MIGNATION, not an independent tag)."""
    tag_set = set(behaviours)
    if is_quick_birth_migration:
        tag_set.add(QUICK_BIRTH_MIGRATION)
    for candidate in CANONICAL_BEHAVIOUR_ORDER:
        if candidate == UNKNOWN_BEHAVIOUR:
            continue
        if candidate in tag_set:
            return candidate
    return UNKNOWN_BEHAVIOUR
