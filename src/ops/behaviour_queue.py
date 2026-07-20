"""X27.4 — Behavioural Discovery Engine: classifies launches by
behavioural archetype, independent of attribution, funding, walkback,
treasury, operator identity, or infrastructure.

Purpose (X27.4 reframe): this is NOT a WATCHTOWER detector. WATCHTOWER's
confirmed corpus is used only to VALIDATE that a behavioural fingerprint
is high-precision (Phase 4/replay) -- it is not the target. Treasuries
rotate, provisioning wallets change, infrastructure evolves; a launch
exhibiting the same operational tempo as the historical WATCHTOWER corpus
is worth investigating regardless of which treasury (if any) turns out to
have funded it. Classification functions in this module never take a
treasury, operator, or infrastructure identity as an input, and never
query any table keyed by those concepts (wt_confirmed_treasuries,
operator_entities, wt_discovered_subprovs, infra_mapping, etc.) --
enforced structurally (see the function signatures below) and by test
(tests/test_x27_4_behaviour_queue.py's independence tests).

This is Stage 2 of the Investigation Queue (Stage 1 = X27.2's mutually
-exclusive investigation-pipeline reduction). Stage 2 does not remove
launches from consideration; it answers "what kind of launch is this"
for the population that still needs investigation, using ONLY
independently-verified-trustworthy behavioural signals. The intended
workflow (Phase 5): Migration -> Behavioural Discovery -> Walkback ->
Treasury -> Operational Pattern -> Known Operation. Behaviour identifies
candidates; walkback and treasury analysis explain them afterwards.

Governing principle (X27.4 brief, informed by X27.3.2's timing-integrity
audit): a behavioural archetype represents POSITIVE evidence only.
Failure to match an archetype never implies the opposite behaviour --
it means the required evidence was unavailable, and the launch remains
UNCLASSIFIED. No inference, no estimation, no coverage-widening via a
looser or substitute signal.

Why archetypes are NOT mutually-priority-ordered like X27.2's buckets:
a launch's behavioural facts are independent observations (its birth
-to-launch timing is a separate fact from its migration-clustering
context) -- a launch CAN legitimately exhibit more than one archetype
at once. Each archetype is evaluated independently; a launch's overall
disposition for the summary counts is "highest-value archetype matched,
else Unclassified" purely for a single-number UI count, but the full
per-launch result exposes every archetype matched, not just one.

Signal trust basis (X27.3.2, "Lifecycle Timing Integrity Audit"):
  - wt_watchtower_launches.create_time -- ON-CHAIN (tx blockTime), reliable,
    but only 43 rows total (live-cascade-scoped).
  - wt_watchtower_launches.birth_to_launch_seconds -- derived from the
    above; reliable wherever create_time exists (41/43 rows).
  - token_analysis.migrated_at -- ingestion time, but verified live within
    ~6s of true on-chain blockTime; effectively 100% coverage of migrated
    launches. Usable for migration-CLUSTERING (relative timing between
    launches), which does not depend on any single absolute timestamp
    being exact, only on relative ordering being correct.
  - token_analysis.created_at -- REJECTED (X27.3.2): unreliable for ~96%
    of rows (ingestion-time fallback masquerading as birth time). Never
    used here, for any archetype.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

RAPID_BIRTH_LAUNCH = "RAPID_BIRTH_LAUNCH"
BURST_LAUNCH = "BURST_LAUNCH"
UNCLASSIFIED = "UNCLASSIFIED_BEHAVIOUR"

ARCHETYPE_ORDER = (RAPID_BIRTH_LAUNCH, BURST_LAUNCH, UNCLASSIFIED)

ARCHETYPE_LABELS = {
    RAPID_BIRTH_LAUNCH: "Confirmed Rapid Birth → Launch",
    BURST_LAUNCH: "Burst Launches",
    UNCLASSIFIED: "Unclassified Behaviour",
}

# Phase 2 -- Rapid Birth -> Launch. Measured live against the confirmed
# WATCHTOWER corpus (wt_watchtower_launches, create_time IS NOT NULL):
# 40 of 41 launches with genuine on-chain birth_to_launch_seconds were
# <=5 seconds (97.6%). This threshold is the corpus's own natural
# clustering point, not an arbitrary round number -- the single outlier
# was 98 seconds, an order of magnitude beyond the cluster.
RAPID_BIRTH_LAUNCH_THRESHOLD_SECONDS = 5
RAPID_BIRTH_LAUNCH_MEASURED_PRECISION = 40 / 41  # 0.9756...

# Phase 3 -- Burst Launches. Measured live against 24h of migrated launches
# (token_analysis.migrated_at, the one signal X27.3.2 confirmed as reliable
# and near-universally covered): a 60-second sliding window's neighbour
# -count distribution has p90=2 (background rate) and p99=8 -- >=3
# co-migrating launches (self + >=2 others) within 60s is a natural cut
# well above ordinary background clustering, not an arbitrary constant.
BURST_WINDOW_SECONDS = 60
BURST_MIN_CLUSTER_SIZE = 3


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def rapid_birth_launch_lookup(ops_db_path: str) -> dict[str, dict[str, Any]]:
    """Phase 2: returns {mint: {matched, birth_to_launch_seconds}} ONLY for
    mints with genuine on-chain create_time AND birth_to_launch_seconds in
    wt_watchtower_launches. A mint absent from this dict has no canonical
    lifecycle timing at all -- callers MUST treat absence as "evidence
    unavailable," never as "did not match." This function never estimates
    or substitutes a value for a missing signal."""
    conn = _connect(ops_db_path)
    try:
        if not _table_exists(conn, "wt_watchtower_launches"):
            return {}
        rows = conn.execute(
            "SELECT mint, birth_to_launch_seconds FROM wt_watchtower_launches "
            "WHERE create_time IS NOT NULL AND birth_to_launch_seconds IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {
        r["mint"]: {
            "matched": r["birth_to_launch_seconds"] <= RAPID_BIRTH_LAUNCH_THRESHOLD_SECONDS,
            "birth_to_launch_seconds": r["birth_to_launch_seconds"],
        }
        for r in rows
    }


def burst_launch_lookup(
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Phase 3: returns {mint: {matched, cluster_size}} for every migrated
    launch in the window, using ONLY token_analysis.migrated_at (the
    signal X27.3.2 confirmed reliable/near-universal). Never touches
    created_at. A launch's cluster_size counts itself plus every other
    migration within +/- BURST_WINDOW_SECONDS; matched=True when
    cluster_size >= BURST_MIN_CLUSTER_SIZE."""
    now = int(now or time.time())
    since = now - window_seconds
    conn = _connect(core_db_path)
    try:
        if not _table_exists(conn, "token_analysis"):
            return {}
        rows = conn.execute(
            "SELECT mint, CAST(migrated_at AS REAL) AS m FROM token_analysis "
            "WHERE migrated_at IS NOT NULL AND CAST(migrated_at AS REAL) >= ? "
            "ORDER BY m",
            (since,),
        ).fetchall()
    finally:
        conn.close()

    mints = [r["mint"] for r in rows]
    ts = [r["m"] for r in rows]
    n = len(ts)

    import bisect
    result: dict[str, dict[str, Any]] = {}
    for i, t in enumerate(ts):
        lo = bisect.bisect_left(ts, t - BURST_WINDOW_SECONDS)
        hi = bisect.bisect_right(ts, t + BURST_WINDOW_SECONDS)
        cluster_size = hi - lo
        result[mints[i]] = {
            "matched": cluster_size >= BURST_MIN_CLUSTER_SIZE,
            "cluster_size": cluster_size,
        }
    return result


def build_behaviour_queue(
    ops_db_path: str,
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Builds the full Behaviour Queue over the Stage-1 investigation
    population (every migrated launch in the window). Read-only, zero
    writes. Unlike X27.2's exclusive bucket assignment, a launch may match
    zero, one, or both archetypes -- archetypes are independent positive
    -evidence facts, not priority-ranked dispositions. The single-archetype
    `primary_archetype` per launch (used for the summary counts / UI rows)
    picks the first ARCHETYPE_ORDER entry the launch matches, defaulting to
    UNCLASSIFIED when none match."""
    now = int(now or time.time())

    rapid = rapid_birth_launch_lookup(ops_db_path)
    burst = burst_launch_lookup(core_db_path, window_seconds=window_seconds, now=now)

    conn = _connect(core_db_path)
    try:
        since = now - window_seconds
        rows = conn.execute(
            "SELECT mint FROM token_analysis WHERE migrated_at IS NOT NULL "
            "AND CAST(migrated_at AS REAL) >= ?",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    all_mints = [r["mint"] for r in rows]
    total = len(all_mints)

    assignments: dict[str, dict[str, Any]] = {}
    counts = {a: 0 for a in ARCHETYPE_ORDER}
    for mint in all_mints:
        archetypes_matched = []
        rapid_evidence = rapid.get(mint)
        if rapid_evidence and rapid_evidence["matched"]:
            archetypes_matched.append(RAPID_BIRTH_LAUNCH)
        burst_evidence = burst.get(mint)
        if burst_evidence and burst_evidence["matched"]:
            archetypes_matched.append(BURST_LAUNCH)

        primary = archetypes_matched[0] if archetypes_matched else UNCLASSIFIED
        counts[primary] += 1
        assignments[mint] = {
            "archetypes_matched": archetypes_matched,
            "primary_archetype": primary,
            "rapid_birth_launch_evidence": rapid_evidence,
            "burst_launch_evidence": burst_evidence,
        }

    rapid_coverage = len(rapid)
    burst_coverage = len(burst)

    return {
        "window_seconds": window_seconds,
        "generated_at": now,
        "total_launches": total,
        "conserved": sum(counts.values()) == total,
        "archetypes": [
            {
                "archetype": RAPID_BIRTH_LAUNCH,
                "label": ARCHETYPE_LABELS[RAPID_BIRTH_LAUNCH],
                "count": counts[RAPID_BIRTH_LAUNCH],
                "coverage_pct": round(rapid_coverage / total * 100, 1) if total else 0.0,
                "coverage_note": f"{rapid_coverage} of {total} launches have canonical lifecycle timing available",
                "confidence": "HIGH",
                "confidence_note": (
                    f"Measured {RAPID_BIRTH_LAUNCH_MEASURED_PRECISION*100:.1f}% precision "
                    "against the confirmed WATCHTOWER corpus (40 of 41 launches with genuine "
                    "on-chain birth-to-launch timing were <=5s)"
                ),
                "evidence_source": "wt_watchtower_launches.create_time + birth_to_launch_seconds",
            },
            {
                "archetype": BURST_LAUNCH,
                "label": ARCHETYPE_LABELS[BURST_LAUNCH],
                "count": counts[BURST_LAUNCH],
                "coverage_pct": round(burst_coverage / total * 100, 1) if total else 0.0,
                "coverage_note": f"{burst_coverage} of {total} launches have a reliable migration timestamp",
                "confidence": "MEDIUM",
                "confidence_note": (
                    f">= {BURST_MIN_CLUSTER_SIZE} launches migrating within "
                    f"{BURST_WINDOW_SECONDS}s of each other (p90 background rate is 2 co-migrations "
                    "in the same window; this threshold sits above ordinary background clustering)"
                ),
                "evidence_source": "token_analysis.migrated_at",
            },
            {
                "archetype": UNCLASSIFIED,
                "label": ARCHETYPE_LABELS[UNCLASSIFIED],
                "count": counts[UNCLASSIFIED],
                "coverage_pct": 100.0,
                "coverage_note": "Always available -- the default when no archetype's evidence is present or matched",
                "confidence": "N/A",
                "confidence_note": "Absence of a match is not evidence of absence of behaviour; it means no archetype's required evidence was available or satisfied",
                "evidence_source": None,
            },
        ],
        "assignments": assignments,
    }


def launches_in_archetype(queue: dict[str, Any], archetype: str) -> list[str]:
    """Phase 4/7 drill-down: mints whose primary_archetype is exactly this
    archetype (i.e. the highest-priority archetype they matched)."""
    return [m for m, a in queue["assignments"].items() if a["primary_archetype"] == archetype]


def rapid_birth_launch_candidates_for_treasury_discovery(
    ops_db_path: str, core_db_path: str, *, window_seconds: int = 86400, now: int | None = None,
) -> list[str]:
    """Phase 5 -- demonstrates the treasury-agnostic discovery workflow
    described in the brief:

        Rapid Birth -> Launch
          -> Unknown treasury
          -> Repeated treasury
          -> Repeated provisioning
          -> Emerging operation

    This function performs ONLY the first step -- it returns the mint list
    that a downstream walkback/treasury-discovery process (already built:
    src/core/walkback_worker.py, src/ops/treasury_expansion_resolver.py,
    src/ops/emerging_operator_service.py) should prioritise for
    investigation. It does not itself resolve, query, or reference any
    treasury, operator, or infrastructure table -- the boundary between
    "behaviour identifies candidates" and "walkback/treasury explains them"
    is enforced by this function calling nothing but
    build_behaviour_queue()/launches_in_archetype() and returning a bare
    mint list, with no funding-side lookups performed on this module's own
    behalf. A caller wiring this into an actual discovery pipeline is
    expected to feed the returned mints into the EXISTING walkback/treasury
    stages -- this function's job ends at "these mints are worth
    investigating," never "here is who funded them."""
    queue = build_behaviour_queue(ops_db_path, core_db_path, window_seconds=window_seconds, now=now)
    return launches_in_archetype(queue, RAPID_BIRTH_LAUNCH)
