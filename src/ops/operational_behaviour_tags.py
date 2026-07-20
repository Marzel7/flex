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
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from src.ops.behaviour_queue import rapid_birth_launch_lookup, burst_launch_lookup, RAPID_BIRTH_LAUNCH, BURST_LAUNCH
from src.ops.attribution_outcome import evaluate_launcher_profile

REPEAT_CREATOR = "REPEAT_CREATOR"

BEHAVIOUR_LABELS = {
    RAPID_BIRTH_LAUNCH: "Rapid Birth→Migration",
    BURST_LAUNCH: "Burst Launcher",
    REPEAT_CREATOR: "Repeat Creator",
}

BEHAVIOUR_ORDER = (RAPID_BIRTH_LAUNCH, BURST_LAUNCH, REPEAT_CREATOR)


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
        rows = ops_conn.execute(
            "SELECT mint FROM wt_attribution_outcomes WHERE completed_at >= ?",
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
                profile = evaluate_launcher_profile(ops_conn, core_conn, creator, now=now)
                if profile["established"]:
                    tags.append(REPEAT_CREATOR)
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
