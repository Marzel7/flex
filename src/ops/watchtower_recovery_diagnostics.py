"""X76.4 -- WATCHTOWER Recovery Diagnostics.

Discovery already reconstructs infrastructure, walkback correctly
reconstructs behaviour, Operation Matching proposes Potential Expansions,
Treasury Review governs confirmation, and Operator Identity expands
confirmed Operations. What was missing: when all of that produces "0
WATCHTOWER launches" for a recent window, nothing on Discovery explained
WHY -- which stage the candidate population actually stopped at.

This module is a pure, read-only re-projection over three ALREADY
EXISTING, already-correct sources:

  - src/ops/watchtower_funnel.py::build_watchtower_funnel() -- the
    sequential launch-to-canonical-operator stage counts, in-window.
  - src/discovery/operation_convergence.py::build_convergence_view() --
    Potential Expansions (populations that already score >= min_score
    against WATCHTOWER's declared OperationMatchingProfile).
  - src/ops/treasury_review_workspace.py::list_review_workspace() --
    Treasury Review queue depth/age.
  - src/ops/operator_identity_governance.py's operator_identity_events /
    src/ops/watchtower_alignment.py's wt_confirmed_treasuries -- Operator
    Identity expansion activity in-window.

It writes nothing, changes no disposition, and never overrides a single
decision made by Discovery, Walkback, Treasury Review, or Operator
Identity. It only explains, in the analyst's own vocabulary, exactly
where a candidate population's progress toward "confirmed WATCHTOWER
treasury" currently stops.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
from src.ops.watchtower_funnel import build_watchtower_funnel

# Pipeline stage keys from build_watchtower_funnel(), in the exact order
# the milestone's spec names them (Launch -> Walkback -> Infrastructure
# Reconstruction -> Behaviour Match -> Potential Expansion -> Treasury
# Review -> Confirmed Treasury -> Operator Identity Expansion -> WATCHTOWER).
# "Behaviour Match" / "Potential Expansion" are not funnel stages (the
# funnel only tracks strict topology resolution); they're layered in
# separately below from operation_convergence's own scoring output.
_STAGE_LABELS: dict[str, str] = {
    "launches": "Recent launches analysed",
    "walkbacks_completed": "Known WATCHTOWER topology recovered",
    "subprovisioners": "Known subprovider",
    "treasuries": "Unknown treasury",
    "known_treasuries": "Confirmed treasury",
    "canonical_operators": "Identity expansion",
}


def _pipeline_status(funnel: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-label the funnel's own stage counts into the spec's named
    pipeline stages. No new counting logic -- these are the SAME numbers
    build_watchtower_funnel() already computed; only presentation."""
    by_key = {s["key"]: s for s in funnel["stages"]}
    ordered_keys = ["launches", "walkbacks_completed", "subprovisioners", "treasuries", "known_treasuries", "canonical_operators"]
    out = []
    for key in ordered_keys:
        stage = by_key.get(key)
        if not stage:
            continue
        out.append({
            "key": key,
            "label": _STAGE_LABELS[key],
            "count": stage["count"],
            "loss": stage["loss"],
            "href": stage["href"],
        })
    return out


def _potential_expansions_for(operator_display_name: str, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read-only call into the SAME convergence scoring Discovery's own
    convergence view uses -- never re-derives matching logic here."""
    try:
        from src.discovery.operation_convergence import build_convergence_view
        from src.ops.operator_routes import _get_emerging_service
        list_payload = _get_emerging_service().list(limit=200, debug=False)
        view = build_convergence_view(conn, list_payload, min_score=0.34)
        return [
            e for e in (view.get("potential_expansions") or [])
            if e.get("matched_operator_display_name") == operator_display_name
        ]
    except Exception:
        return []


def _treasury_review_summary(conn: sqlite3.Connection, *, window_seconds: int, now: int) -> dict[str, Any]:
    """Treasury Review section: pending/approved/rejected/dismissed counts
    and average review age, reusing list_review_workspace()'s own queries
    where possible and adding only windowed action counts (X76.2's
    wt_treasury_review_actions audit table) that workspace doesn't already
    window."""
    from src.ops.treasury_review_workspace import list_review_workspace

    # limit is generous (not the UI's default 200) so watchtower_candidates
    # reflects the TRUE pending count rather than being truncated by the
    # first page of results -- this number feeds the bottleneck/explanation
    # text directly and must not silently undercount.
    workspace = list_review_workspace(conn, status="PENDING_REVIEW", sort="oldest", limit=5000)
    cutoff = now - window_seconds

    def _action_count(action: str) -> int:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM wt_treasury_review_actions WHERE action=? AND created_at>=?",
                (action, cutoff),
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    ages = []
    try:
        ages = [
            now - r[0] for r in conn.execute(
                "SELECT detected_at FROM wt_treasury_review WHERE status='PENDING_REVIEW' AND detected_at IS NOT NULL"
            ).fetchall()
        ]
    except sqlite3.Error:
        pass
    avg_age = int(sum(ages) / len(ages)) if ages else None

    return {
        "pending_watchtower_expansions": workspace.get("watchtower_candidates", 0),
        "pending_total": workspace.get("pending_total", 0),
        "approved_this_window": _action_count("APPROVE_TREASURY"),
        "rejected_this_window": _action_count("REJECT_TREASURY"),
        "dismissed_this_window": _action_count("NEEDS_MORE_EVIDENCE"),
        "average_review_age_secs": avg_age,
        "oldest_pending_age_secs": workspace.get("oldest_pending_age_secs"),
        "href": "/intelligence/treasury-review",
    }


def _identity_expansion_summary(
    conn: sqlite3.Connection, *, operator_id: str, window_seconds: int, now: int
) -> dict[str, Any]:
    """Confirmed-treasury and operator-identity-expansion activity in
    window, straight off the authoritative tables (wt_confirmed_treasuries,
    operator_identity_events) -- answers whether governance/identity
    projection, not detection, is the bottleneck."""
    cutoff = now - window_seconds

    def _scalar(sql: str, args: tuple = ()) -> int:
        try:
            row = conn.execute(sql, args).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.Error:
            return 0

    confirmed_total = _scalar("SELECT COUNT(*) FROM wt_confirmed_treasuries")
    confirmed_new = _scalar(
        "SELECT COUNT(*) FROM wt_confirmed_treasuries WHERE confirmed_at>=?", (cutoff,)
    )
    expansions_new = _scalar(
        "SELECT COUNT(*) FROM operator_identity_events WHERE operator_id=? AND timestamp>=? "
        "AND event_type IN ('TREASURY_ADDED','ASSET_ADDED')",
        (operator_id, cutoff),
    )

    return {
        "confirmed_treasuries_total": confirmed_total,
        "confirmed_new_this_window": confirmed_new,
        "operator_expansions_this_window": expansions_new,
        "no_expansion": expansions_new == 0,
        "href": f"/intelligence/operator/{operator_id}",
    }


def _match_quality(funnel: dict[str, Any]) -> list[dict[str, Any]]:
    """Recovered/Missing status per evidence type, derived from the SAME
    stage counts as pipeline_status -- no percentages, per spec."""
    by_key = {s["key"]: s for s in funnel["stages"]}

    def status(key: str) -> str:
        stage = by_key.get(key)
        return "Recovered" if stage and stage["count"] > 0 else "Missing"

    return [
        {"label": "Known topology", "status": status("walkbacks_completed")},
        {"label": "Known funding", "status": status("subprovisioners")},
        {"label": "Known provisioning", "status": status("treasuries")},
        {"label": "Known treasury", "status": status("known_treasuries")},
        {"label": "Known controller", "status": status("canonical_operators")},
    ]


def _rotation_signal(funnel: dict[str, Any]) -> dict[str, Any]:
    """Possible Treasury Rotation: an unknown treasury (walkback resolved
    a treasury address that is NOT in wt_confirmed_treasuries) co-occurring
    with known WATCHTOWER topology (subprovisioner resolved), known funding
    behaviour, and known provisioning behaviour all present at the SAME
    time. Diagnostic only -- never confirms, never writes, never promotes
    anything; a human analyst still makes that call in Treasury Review."""
    by_key = {s["key"]: s for s in funnel["stages"]}
    unknown_treasury = (by_key.get("treasuries", {}).get("count") or 0) - (by_key.get("known_treasuries", {}).get("count") or 0)
    known_topology = (by_key.get("walkbacks_completed", {}).get("count") or 0) > 0
    known_funding = (by_key.get("subprovisioners", {}).get("count") or 0) > 0
    known_provisioning = (by_key.get("treasuries", {}).get("count") or 0) > 0
    possible_rotation = unknown_treasury > 0 and known_topology and known_funding and known_provisioning
    return {
        "possible_rotation": possible_rotation,
        "unknown_treasury_count": max(unknown_treasury, 0),
        "known_topology": known_topology,
        "known_funding_behaviour": known_funding,
        "known_provisioning_behaviour": known_provisioning,
    }


def _candidate_generation_metrics(conn: sqlite3.Connection, *, now: int) -> dict[str, Any]:
    """X76.5 -- Treasury Candidate generation metrics. Answers "is live
    detection still running at all", independent of the review-queue-depth
    story above: a healthy pipeline can still have a large PENDING_REVIEW
    backlog, but generation should never go silent for hours without it
    being visible somewhere. Counts every wt_treasury_review row regardless
    of detected_via (walkback_hop2 is the dominant live source, but this
    must not hardcode that assumption)."""

    def _scalar(sql: str, args: tuple = ()) -> int:
        try:
            row = conn.execute(sql, args).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.Error:
            return 0

    hour_ago = now - 3600
    day_ago = now - 86400
    generated_last_hour = _scalar(
        "SELECT COUNT(*) FROM wt_treasury_review WHERE detected_at>=?", (hour_ago,)
    )
    generated_last_day = _scalar(
        "SELECT COUNT(*) FROM wt_treasury_review WHERE detected_at>=?", (day_ago,)
    )
    pending_review = _scalar(
        "SELECT COUNT(*) FROM wt_treasury_review WHERE status='PENDING_REVIEW'"
    )
    newest = None
    oldest = None
    try:
        row = conn.execute("SELECT MAX(detected_at) FROM wt_treasury_review").fetchone()
        newest = int(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        pass
    try:
        row = conn.execute(
            "SELECT MIN(detected_at) FROM wt_treasury_review WHERE status='PENDING_REVIEW'"
        ).fetchone()
        oldest = int(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        pass

    return {
        "generated_last_hour": generated_last_hour,
        "generated_last_day": generated_last_day,
        "pending_review": pending_review,
        "newest_candidate_at": newest,
        "newest_candidate_age_secs": (now - newest) if newest else None,
        "oldest_pending_at": oldest,
        "oldest_pending_age_secs": (now - oldest) if oldest else None,
        # A simple, visible tripwire: candidate generation is "stalled" if
        # nothing has landed in the last hour despite there being pending
        # backlog to review -- exactly the symptom this milestone exists to
        # catch before it silently persists for hours again.
        "stalled": generated_last_hour == 0 and generated_last_day == 0,
    }


def _determine_bottleneck(
    pipeline: list[dict[str, Any]],
    potential_expansions: list[dict[str, Any]],
    treasury_review: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, str]:
    """Exactly one primary bottleneck: the first stage, in pipeline order,
    where the count drops to zero (or, past the funnel's own stages, where
    Treasury Review / Identity Expansion has no forward progress). This is
    a simple first-zero scan, not a heuristic guess -- it reads off the
    same counts already displayed, so the reported bottleneck and the
    displayed numbers can never disagree."""
    by_key = {s["key"]: s for s in pipeline}

    def zero(key: str) -> bool:
        stage = by_key.get(key)
        return bool(stage) and stage["count"] == 0

    if zero("launches"):
        return {"stage": "launches", "reason": "No recent launches analysed in this window."}
    if zero("walkbacks_completed"):
        return {"stage": "walkbacks_completed", "reason": "No walkback reconstruction completed for recent launches."}
    if zero("subprovisioners"):
        return {"stage": "subprovisioners", "reason": "Walkback completed but no launches matched known WATCHTOWER behaviour (no subprovider resolved)."}
    if zero("treasuries"):
        return {"stage": "treasuries", "reason": "Known subprovider behaviour resolved but no treasury reached."}
    if zero("known_treasuries"):
        if potential_expansions:
            return {
                "stage": "treasury_review",
                "reason": f"{len(potential_expansions)} Potential Expansion(s) awaiting Treasury Review.",
            }
        if treasury_review.get("pending_watchtower_expansions", 0) > 0:
            return {
                "stage": "treasury_review",
                "reason": f"{treasury_review['pending_watchtower_expansions']} treasury candidate(s) awaiting Treasury Review.",
            }
        return {"stage": "treasuries", "reason": "A treasury was reached but is not yet a confirmed treasury, and no Potential Expansion or Treasury Review candidate currently exists for it."}
    if zero("canonical_operators"):
        if identity.get("no_expansion"):
            return {"stage": "identity_expansion", "reason": "Treasury confirmed but Operator Identity expansion has not run for it yet."}
        return {"stage": "identity_expansion", "reason": "Treasury confirmed but not yet reflected as a canonical WATCHTOWER operator entity."}
    return {"stage": "none", "reason": "Recovery pipeline is progressing end-to-end for this window."}


def build_recovery_diagnostics(
    ops_db_path: str,
    core_db_path: str,
    *,
    now: int | None = None,
    window_seconds: int = 72 * 3600,
) -> dict[str, Any]:
    """Compose the full WATCHTOWER Recovery Diagnostics payload. Pure
    read-only re-projection of build_watchtower_funnel(),
    build_convergence_view(), list_review_workspace(), and confirmed-
    treasury/operator-identity-event tables. Writes nothing; overrides no
    decision made by any of those systems.

    This is WATCHTOWER-specific, not a generic per-operator framework:
    build_watchtower_funnel() itself is hardcoded to the canonical
    WATCHTOWER control case (its own docstring says so), so pretending
    this module accepts an arbitrary operator_id would silently mislabel
    WATCHTOWER's own stage counts as belonging to whatever operator was
    passed in. If a second Operation ever needs the same diagnostic shape,
    build_watchtower_funnel() would need its own operator-parameterised
    sibling first -- this module should not paper over that gap."""
    operator_id = WATCHTOWER_OPERATOR_ID
    operator_display_name = "WATCHTOWER"
    now = int(now or time.time())
    funnel = build_watchtower_funnel(ops_db_path, core_db_path, now=now, window_seconds=window_seconds)
    pipeline = _pipeline_status(funnel)

    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        potential_expansions = _potential_expansions_for(operator_display_name, conn)
        treasury_review = _treasury_review_summary(conn, window_seconds=window_seconds, now=now)
        identity = _identity_expansion_summary(conn, operator_id=operator_id, window_seconds=window_seconds, now=now)
        match_quality = _match_quality(funnel)
        rotation = _rotation_signal(funnel)
        candidate_generation = _candidate_generation_metrics(conn, now=now)
    finally:
        conn.close()

    bottleneck = _determine_bottleneck(pipeline, potential_expansions, treasury_review, identity)

    canonical_count = next((s["count"] for s in pipeline if s["key"] == "canonical_operators"), 0)
    if canonical_count > 0:
        explanation = f"{canonical_count} confirmed {operator_display_name} launch(es) in this window."
    else:
        explanation = f"No confirmed {operator_display_name} launches. Reason: {bottleneck['reason']}"

    return {
        "ok": True,
        "generated_at": now,
        "window_seconds": window_seconds,
        "operator_id": operator_id,
        "operator_display_name": operator_display_name,
        "explanation": explanation,
        "pipeline_status": pipeline,
        "bottleneck": bottleneck,
        "rotation": rotation,
        "match_quality": match_quality,
        "potential_expansions": potential_expansions,
        "treasury_review": treasury_review,
        "identity_expansion": identity,
        "candidate_generation": candidate_generation,
    }
