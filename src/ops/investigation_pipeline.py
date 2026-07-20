"""X27.2/X27.5 — Investigation Reduction Pipeline: reduces today's migrated
launches into a sequence of MUTUALLY-EXCLUSIVE investigative buckets.

This module performs no detection, attribution, or classification of its
own. It is a pure presentation-layer reduction over the ALREADY-PERSISTED
`wt_attribution_outcomes.outcome_type` (src/ops/attribution_outcome.py),
re-ranked by investigative usefulness rather than attribution-confidence
order, plus two independently-evaluated additions: Repeat Creator (the
platform's own vetted launcher-profile classifier, evaluate_launcher_profile)
and, as of X27.5, the two behavioural archetypes from src/ops/behaviour_queue.py
(Rapid Birth -> Launch, Burst Launches) -- never a new/looser threshold,
never a second classification system.

Why re-rank at all: `_classify()` in attribution_outcome.py already
assigns exactly one `outcome_type` per mint, but its internal priority
order optimises for ATTRIBUTION COMPLETENESS (e.g. a CEX/relay boundary
check runs before the multi-token-creator check), not for INVESTIGATIVE
VALUE. A launch whose creator is an established repeat creator (per
evaluate_launcher_profile, which already guards against the shared/bot
-wallet false-positive case via canonical_operator_linked /
fresh_provisioning_evidence / material_infrastructure_change) is
"understood" from an analyst's standpoint even if it also happens to
terminate at a CEX. Measured live 2026-07-16 (24h window, 519 outcomes):
of the 85 unique creators meeting the real `established` gate, their
launches were previously scattered across INSUFFICIENT_EVIDENCE (100),
LINEAGE_GAP (25), UNKNOWN_INFRASTRUCTURE (15), KNOWN_RELAY_REACHED (3),
and KNOWN_CEX_REACHED (1) -- 144 launches total that Pipeline Health was
counting as still needing investigation, when the creator was already
understood. This module removes them from the queue exactly once.

X27.5 — why behavioural archetypes were merged in rather than kept as a
separate Behaviour Queue: X27.4 built Rapid Birth -> Launch and Burst
Launches as an independent second dashboard, answering "what kind of
launch is this" alongside X27.2's "why has this launch left the pipeline."
Measured live 2026-07-17 (24h window, 580 investigation-pipeline launches,
585 behaviour-queue launches): Burst Launches (133 matched launches) was
spread across ALL FIVE investigation buckets simultaneously (16 in Known
Infrastructure, 34 in Repeat Creator, 50 in Insufficient Evidence, 17 in
Lineage Gap, 16 in Unknown Infrastructure) -- exactly the double-counting
the governing principle warns against. There is no scenario where an
analyst benefits from seeing the same 133 launches counted once in
Pipeline Health and again in a separate Behaviour Queue panel. Folding the
two behavioural archetypes into this module's own priority walk, at a
position between Repeat Creator and Unknown Infrastructure, gives every
launch exactly one bucket across the whole platform, not per-panel.

Priority order (derived from "which bucket removes a launch from future
human investigation", Phase 2, extended in X27.5):
  1. KNOWN_OPERATION       -- CANONICAL_OPERATOR_REACHED: fully resolved.
  2. KNOWN_INFRASTRUCTURE  -- KNOWN_CEX/BRIDGE/RELAY_REACHED: reviewed
                              terminal boundary, no further attribution
                              expected.
  3. REPEAT_CREATOR        -- KNOWN_MULTI_TOKEN_CREATOR outcome_type, OR
                              (independently) evaluate_launcher_profile()
                              .established for any other outcome_type --
                              creator already understood from prior
                              investigation.
  4. RAPID_BIRTH_LAUNCH    -- (X27.5) high-confidence behavioural
                              fingerprint (birth->launch <=5s, on-chain
                              timing only); consumes launches BEFORE
                              Unknown Infrastructure/Lineage Gap/
                              Insufficient Evidence, since a matched
                              launch is a high-priority investigation
                              candidate regardless of its attribution
                              state.
  5. BURST_LAUNCH          -- (X27.5) coordinated migration-timing
                              cluster (>=3 co-migrations within 60s,
                              token_analysis.migrated_at only).
  6. UNKNOWN_INFRASTRUCTURE -- investigation required; candidate for
                              emerging-operator monitoring.
  7. LINEAGE_GAP           -- investigation required; evidence incomplete.
  8. INSUFFICIENT_EVIDENCE -- pipeline improvement; weakest evidence tier.
  (AMBIGUOUS_BRANCH / MAX_DEPTH fold into LINEAGE_GAP -- both are "stopped,
  retry only on new evidence" states with no separate investigative action.)

Priority is data, not branching logic (Phase 4/8): `BUCKET_ORDER` is an
ordered tuple; the assignment loop is a single generic first-match walk.
Inserting a new bucket (Coordinated Migration, Treasury Burst, Shared
Provisioning, Creator Recycling, Migration Waves, ...) means adding one
predicate at the desired priority position -- no rewrite of the walk
itself, and no new dashboard.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable

KNOWN_OPERATION = "KNOWN_OPERATION"
KNOWN_INFRASTRUCTURE = "KNOWN_INFRASTRUCTURE"
REPEAT_CREATOR = "REPEAT_CREATOR"
RAPID_BIRTH_LAUNCH = "RAPID_BIRTH_LAUNCH"
BURST_LAUNCH = "BURST_LAUNCH"
UNKNOWN_INFRASTRUCTURE = "UNKNOWN_INFRASTRUCTURE"
LINEAGE_GAP = "LINEAGE_GAP"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

BUCKET_ORDER = (
    KNOWN_OPERATION,
    KNOWN_INFRASTRUCTURE,
    REPEAT_CREATOR,
    RAPID_BIRTH_LAUNCH,
    BURST_LAUNCH,
    UNKNOWN_INFRASTRUCTURE,
    LINEAGE_GAP,
    INSUFFICIENT_EVIDENCE,
)

# Phase 3 -- canonical bucket semantics. Each answers "why has this launch
# left the investigation pipeline?" These are investigative dispositions,
# not attribution outcomes; wording deliberately avoids re-asserting
# attribution facts already stated elsewhere (Attribution Outcome).
BUCKET_LABELS = {
    KNOWN_OPERATION: "Known Operation",
    KNOWN_INFRASTRUCTURE: "Known Infrastructure",
    REPEAT_CREATOR: "Repeat Creators",
    RAPID_BIRTH_LAUNCH: "Rapid Birth → Launch",
    BURST_LAUNCH: "Burst Launches",
    UNKNOWN_INFRASTRUCTURE: "Unknown Infrastructure",
    LINEAGE_GAP: "Lineage Gap",
    INSUFFICIENT_EVIDENCE: "Insufficient Evidence",
}

BUCKET_REASONS = {
    KNOWN_OPERATION: "Investigation complete. Attribution reached a confirmed canonical operator.",
    KNOWN_INFRASTRUCTURE: "Investigation complete. Funding terminates at reviewed infrastructure; no further attribution expected.",
    REPEAT_CREATOR: "No investigation required. Creator already understood from prior investigation. (Priority may change in a future sprint.)",
    RAPID_BIRTH_LAUNCH: "Investigation priority. High-confidence behavioural fingerprint: creator birth to launch within 5 seconds, verified on-chain timing only.",
    BURST_LAUNCH: "Investigation priority. Coordinated migration-timing cluster (3+ launches migrating within 60 seconds of each other).",
    UNKNOWN_INFRASTRUCTURE: "Investigation required. Potential new infrastructure.",
    LINEAGE_GAP: "Investigation required. Evidence incomplete.",
    INSUFFICIENT_EVIDENCE: "Pipeline improvement opportunity. Insufficient evidence currently exists.",
}

_OUTCOME_TO_BUCKET = {
    "CANONICAL_OPERATOR_REACHED": KNOWN_OPERATION,
    "KNOWN_CEX_REACHED": KNOWN_INFRASTRUCTURE,
    "KNOWN_BRIDGE_REACHED": KNOWN_INFRASTRUCTURE,
    "KNOWN_RELAY_REACHED": KNOWN_INFRASTRUCTURE,
    "KNOWN_MULTI_TOKEN_CREATOR": REPEAT_CREATOR,
    "UNKNOWN_INFRASTRUCTURE": UNKNOWN_INFRASTRUCTURE,
    "LINEAGE_GAP": LINEAGE_GAP,
    "AMBIGUOUS_BRANCH": LINEAGE_GAP,
    "MAX_DEPTH": LINEAGE_GAP,
    "INSUFFICIENT_EVIDENCE": INSUFFICIENT_EVIDENCE,
}


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


def assign_bucket(
    ops_conn: sqlite3.Connection,
    core_conn: sqlite3.Connection,
    mint: str,
    outcome_type: str | None,
    *,
    creator: str | None = None,
    now: int | None = None,
    rapid_birth_evidence: dict[str, Any] | None = None,
    burst_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assigns exactly one bucket to a single migrated launch (Phase 4).

    Priority walk: KNOWN_OPERATION -> KNOWN_INFRASTRUCTURE -> REPEAT_CREATOR
    -> RAPID_BIRTH_LAUNCH -> BURST_LAUNCH -> UNKNOWN_INFRASTRUCTURE ->
    LINEAGE_GAP -> INSUFFICIENT_EVIDENCE. The first three map straight off
    outcome_type (already mutually exclusive at the attribution layer);
    REPEAT_CREATOR additionally re-claims any lower-priority outcome_type
    whose creator independently satisfies evaluate_launcher_profile()
    .established -- the same, already-vetted definition used by
    KNOWN_MULTI_TOKEN_CREATOR itself, never a looser one (a naive
    launch_count>1 rule was measured live to sweep in 77% of launches,
    dominated by shared/bot-wallet false positives; the real `established`
    gate measured 28%, in line with a genuine repeat-creator population).

    X27.5 -- RAPID_BIRTH_LAUNCH/BURST_LAUNCH are evaluated next, using the
    SAME evidence dicts src/ops/behaviour_queue.py's lookups produce
    (rapid_birth_launch_lookup/burst_launch_lookup) -- this function never
    recomputes behavioural evidence itself, only consumes it, so there is
    exactly one place in the codebase that decides whether a launch
    exhibits either archetype."""
    mapped = _OUTCOME_TO_BUCKET.get(outcome_type)
    if mapped in (KNOWN_OPERATION, KNOWN_INFRASTRUCTURE, REPEAT_CREATOR):
        return {"bucket": mapped, "label": BUCKET_LABELS[mapped], "reason": BUCKET_REASONS[mapped]}

    resolved_creator = creator if creator is not None else _resolve_creator(core_conn, mint)
    if resolved_creator:
        from src.ops.attribution_outcome import evaluate_launcher_profile
        profile = evaluate_launcher_profile(ops_conn, core_conn, resolved_creator, now=now)
        if profile["established"]:
            return {"bucket": REPEAT_CREATOR, "label": BUCKET_LABELS[REPEAT_CREATOR], "reason": BUCKET_REASONS[REPEAT_CREATOR]}

    if rapid_birth_evidence and rapid_birth_evidence.get("matched"):
        return {"bucket": RAPID_BIRTH_LAUNCH, "label": BUCKET_LABELS[RAPID_BIRTH_LAUNCH], "reason": BUCKET_REASONS[RAPID_BIRTH_LAUNCH]}

    if burst_evidence and burst_evidence.get("matched"):
        return {"bucket": BURST_LAUNCH, "label": BUCKET_LABELS[BURST_LAUNCH], "reason": BUCKET_REASONS[BURST_LAUNCH]}

    bucket = mapped or INSUFFICIENT_EVIDENCE
    return {"bucket": bucket, "label": BUCKET_LABELS[bucket], "reason": BUCKET_REASONS[bucket]}


def build_pipeline_health(
    ops_db_path: str,
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Reduces every migrated launch (wt_attribution_outcomes row) in the
    window into exactly one bucket. Read-only; makes zero writes. Returns
    per-bucket counts (Phase 6) plus the full mint->bucket assignment map
    (used for drill-down exclusivity, Phase 7) and a conservation check
    (Phase 5): sum(bucket counts) must equal total launches, always."""
    now = int(now or time.time())
    since = now - window_seconds

    # X27.5 -- behavioural evidence is computed once, up front, via the
    # SAME functions src/ops/behaviour_queue.py exposes (no reimplementation,
    # no second classification system). These lookups are independent of
    # the window used below (rapid_birth_launch_lookup is not windowed at
    # all -- it reflects whatever is currently in wt_watchtower_launches;
    # burst_launch_lookup is computed over this exact window so cluster
    # membership matches the population being classified).
    from src.ops.behaviour_queue import rapid_birth_launch_lookup, burst_launch_lookup
    rapid_birth_lookup = rapid_birth_launch_lookup(ops_db_path)
    burst_lookup = burst_launch_lookup(core_db_path, window_seconds=window_seconds, now=now)

    ops_conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops_conn.row_factory = sqlite3.Row
    ops_conn.execute("PRAGMA query_only=ON")
    core_conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True, timeout=5)
    core_conn.row_factory = sqlite3.Row
    core_conn.execute("PRAGMA query_only=ON")
    try:
        rows = ops_conn.execute(
            "SELECT mint, outcome_type FROM wt_attribution_outcomes WHERE completed_at >= ?",
            (since,),
        ).fetchall()
        mints = [r["mint"] for r in rows]
        creator_of: dict[str, str | None] = {}
        if mints and _table_exists(core_conn, "token_analysis"):
            placeholders = ",".join("?" for _ in mints)
            for r in core_conn.execute(
                f"SELECT mint, pf_ws_creator, earliest_tx_creator FROM token_analysis WHERE mint IN ({placeholders})",
                mints,
            ):
                creator_of[r["mint"]] = r["pf_ws_creator"] or r["earliest_tx_creator"]

        assignments: dict[str, dict[str, Any]] = {}
        counts = {b: 0 for b in BUCKET_ORDER}
        for r in rows:
            mint, outcome_type = r["mint"], r["outcome_type"]
            result = assign_bucket(
                ops_conn, core_conn, mint, outcome_type,
                creator=creator_of.get(mint), now=now,
                rapid_birth_evidence=rapid_birth_lookup.get(mint),
                burst_evidence=burst_lookup.get(mint),
            )
            result["creator"] = creator_of.get(mint)
            # X27.9 Phase 8 — supplementary behavioural evidence must remain visible
            # even when a higher-priority bucket (e.g. Repeat Creator) wins the
            # exclusive assignment; this is drill-down metadata only, never a
            # second bucket count or dashboard.
            rb = rapid_birth_lookup.get(mint)
            bl = burst_lookup.get(mint)
            result["secondary_evidence"] = {
                "rapid_birth_launch": rb if rb and rb.get("matched") else None,
                "burst_launch": bl if bl and bl.get("matched") else None,
            }
            assignments[mint] = result
            counts[result["bucket"]] += 1

        total = len(rows)
        conserved = sum(counts.values()) == total
    finally:
        ops_conn.close()
        core_conn.close()

    return {
        "window_seconds": window_seconds,
        "generated_at": now,
        "total_launches": total,
        "buckets": [
            {
                "bucket": b,
                "label": BUCKET_LABELS[b],
                "reason": BUCKET_REASONS[b],
                "count": counts[b],
            }
            for b in BUCKET_ORDER
        ],
        "conserved": conserved,
        "assignments": assignments,
    }


def launches_in_bucket(pipeline: dict[str, Any], bucket: str) -> list[str]:
    """Phase 7 drill-down: mints assigned to exactly this bucket."""
    return [m for m, a in pipeline["assignments"].items() if a["bucket"] == bucket]


def secondary_evidence_for(pipeline: dict[str, Any], mint: str) -> dict[str, Any] | None:
    """X27.9 Phase 8 — supplementary behavioural evidence for one launch,
    independent of which bucket won the priority walk. A Repeat Creator
    launch that also matched Burst Launch/Rapid Birth keeps that evidence
    visible here rather than losing it once a higher-priority bucket claims
    the exclusive assignment. Not a second bucket count, not a second
    dashboard -- purely additive drill-down detail."""
    assignment = pipeline["assignments"].get(mint)
    return assignment.get("secondary_evidence") if assignment else None


def creators_in_bucket(pipeline: dict[str, Any], bucket: str) -> list[dict[str, Any]]:
    """Groups a bucket's mints by creator wallet (highest launch count
    first). REPEAT_CREATOR in particular reads far better grouped by
    creator than as a flat token list -- the whole point of that bucket
    is "this creator is already understood," so the creator identity is
    the natural drill-down unit; a token list is one click further down
    from there, not the entry point. Mints with no resolved creator are
    grouped under a single `creator: None` bucket, listed last."""
    grouped: dict[str | None, list[str]] = {}
    for mint, assignment in pipeline["assignments"].items():
        if assignment["bucket"] != bucket:
            continue
        grouped.setdefault(assignment.get("creator"), []).append(mint)

    rows = [
        {"creator": creator, "mints": mints, "launch_count": len(mints)}
        for creator, mints in grouped.items()
        if creator is not None
    ]
    rows.sort(key=lambda r: -r["launch_count"])
    unresolved = grouped.get(None)
    if unresolved:
        rows.append({"creator": None, "mints": unresolved, "launch_count": len(unresolved)})
    return rows
