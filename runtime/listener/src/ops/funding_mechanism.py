"""X29.1 Stage 3 — Funding Mechanism classifier.

Answers "how is capital technically moved?" Zero or more additive tags per
launch (X29.0 Part 2, Dimension 3) -- never mutually exclusive, never used
to define topology or behaviour.

Verified real persisted values (checked directly against wt_ops_v2.db before
building this, not assumed): wt_provisioning_edges.funding_mechanism is one
of 'WSOL_WRAP_CLOSE' / 'PLAIN_XFER'; wt_watchtower_launches.funding_mechanism
is one of 'WSOL_WRAP_CLOSE' / 'SEEDED_ACCOUNT_CLOSE'. There is no persisted
'PLAIN_TRANSFER' string anywhere -- the code-level constant name differs
from the on-chain-derived value actually written to wt_provisioning_edges
(PLAIN_XFER). This module maps both onto one canonical tag set for display.

MIXED is not a new detection -- it is a rollup rule over already-observed,
already-persisted per-edge mechanism values (wt_provisioning_edges already
records TREASURY_TO_SUBPROV and SUBPROV_TO_CREATOR mechanisms separately per
mint via source_mint, and those two edges can already legitimately differ
today with zero code changes required to observe it).
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

WSOL_WRAP_CLOSE = "WSOL_WRAP_CLOSE"
PLAIN_TRANSFER = "PLAIN_TRANSFER"
SEEDED_ACCOUNT_CLOSE = "SEEDED_ACCOUNT_CLOSE"
MIXED = "MIXED"

MECHANISM_LABELS = {
    WSOL_WRAP_CLOSE: "WSOL Wrap-Close",
    PLAIN_TRANSFER: "Plain Transfer",
    SEEDED_ACCOUNT_CLOSE: "Seeded Account Close",
    MIXED: "Mixed",
}

# Maps every raw, on-chain-persisted mechanism string this codebase actually
# writes (across wt_provisioning_edges and wt_watchtower_launches) onto one
# canonical tag. PLAIN_XFER (edges table) and PLAIN_TRANSFER (a value used
# elsewhere in this codebase, e.g. ws_cascade.py's funding_mechanism='PLAIN_TRANSFER'
# writes to wt_active_subprov_sessions/wt_candidate_websocket_watches) are the
# SAME real-world mechanism under two different literal strings -- this is a
# display-normalization mapping, not a reclassification.
_RAW_TO_CANONICAL = {
    "WSOL_WRAP_CLOSE": WSOL_WRAP_CLOSE,
    "SEEDED_ACCOUNT_CLOSE": SEEDED_ACCOUNT_CLOSE,
    "PLAIN_XFER": PLAIN_TRANSFER,
    "PLAIN_TRANSFER": PLAIN_TRANSFER,
}

MECHANISM_ORDER = (WSOL_WRAP_CLOSE, PLAIN_TRANSFER, SEEDED_ACCOUNT_CLOSE, MIXED)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _canonical(raw: str | None) -> str | None:
    if not raw:
        return None
    return _RAW_TO_CANONICAL.get(raw)


def classify_mechanisms_for_launch(
    *,
    launch_mechanism: str | None,
    edge_mechanisms: list[str] | None = None,
) -> dict[str, Any]:
    """Zero or more mechanism tags for ONE launch.

    launch_mechanism: wt_watchtower_launches.funding_mechanism (the creator's
    own wrap-close/seed mechanism -- the SUBPROV_TO_CREATOR edge, in effect).
    edge_mechanisms: every distinct raw funding_mechanism value observed
    across this mint's wt_provisioning_edges rows (both TREASURY_TO_SUBPROV
    and SUBPROV_TO_CREATOR edges, if recorded) -- the richer source when
    available, since it can span more of the chain than the single
    launch-row mechanism.
    """
    raws: set[str] = set()
    if launch_mechanism:
        raws.add(launch_mechanism)
    for m in (edge_mechanisms or []):
        if m:
            raws.add(m)

    canonical_tags = sorted({c for c in (_canonical(r) for r in raws) if c})
    if not canonical_tags:
        return {"mechanisms": [], "labels": []}

    tags = list(canonical_tags)
    if len(canonical_tags) > 1:
        tags.append(MIXED)

    return {
        "mechanisms": tags,
        "labels": [MECHANISM_LABELS[t] for t in tags],
    }


def build_mechanism_classification(
    ops_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Classifies every mint present in wt_provisioning_edges (source_mint)
    or wt_watchtower_launches by Funding Mechanism. Read-only, zero writes.
    Additive: counts sum to MORE than total_launches whenever any launch
    carries >1 tag -- this is expected and reported explicitly (X29.0:
    'behaviour and mechanisms are additive tags', not exclusive)."""
    now = int(now or time.time())

    ops_conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops_conn.row_factory = sqlite3.Row
    ops_conn.execute("PRAGMA query_only=ON")
    try:
        launch_mechanism_by_mint: dict[str, str] = {}
        if _table_exists(ops_conn, "wt_watchtower_launches"):
            for r in ops_conn.execute(
                "SELECT mint, funding_mechanism FROM wt_watchtower_launches WHERE mint IS NOT NULL"
            ).fetchall():
                launch_mechanism_by_mint[r["mint"]] = r["funding_mechanism"]

        edge_mechanisms_by_mint: dict[str, list[str]] = {}
        if _table_exists(ops_conn, "wt_provisioning_edges"):
            for r in ops_conn.execute(
                "SELECT source_mint, funding_mechanism FROM wt_provisioning_edges "
                "WHERE source_mint IS NOT NULL AND funding_mechanism IS NOT NULL"
            ).fetchall():
                edge_mechanisms_by_mint.setdefault(r["source_mint"], []).append(r["funding_mechanism"])

        all_mints = set(launch_mechanism_by_mint) | set(edge_mechanisms_by_mint)

        assignments: dict[str, dict[str, Any]] = {}
        counts = {m: 0 for m in MECHANISM_ORDER}
        for mint in all_mints:
            result = classify_mechanisms_for_launch(
                launch_mechanism=launch_mechanism_by_mint.get(mint),
                edge_mechanisms=edge_mechanisms_by_mint.get(mint),
            )
            assignments[mint] = result
            for tag in result["mechanisms"]:
                counts[tag] += 1

        total = len(all_mints)
    finally:
        ops_conn.close()

    return {
        "generated_at": now,
        "total_launches": total,
        "mechanisms": [
            {
                "mechanism": m,
                "label": MECHANISM_LABELS[m],
                "count": counts[m],
                "coverage_pct": round(counts[m] / total * 100, 1) if total else 0.0,
            }
            for m in MECHANISM_ORDER
        ],
        "assignments": assignments,
    }


def launches_with_mechanism(classification: dict[str, Any], mechanism: str) -> list[str]:
    """Drill-down: mints carrying this mechanism tag (additive -- a mint may
    appear under more than one mechanism's drill-down list)."""
    return [m for m, a in classification["assignments"].items() if mechanism in a["mechanisms"]]
