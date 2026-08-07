"""X65.7 — Canonical Campaign classification for Discovery.

Implements the design frozen across X65.5/X65.6: a new, mutually-exclusive
`campaign` dimension answering "does this launch match the validated
WATCHTOWER provisioning fingerprint" -- independent of Behaviour Cohort,
Creator Identity, Topology, Funding Origin, Treasury, and Operation
Attribution, all of which remain completely unmodified.

Read-only aggregation layer only. Every fact this module reads is already
persisted by existing, unmodified modules:
  - wt_attribution_outcomes / wt_watchtower_launches (wrap-close provisioning
    evidence)
  - wt_candidate_websocket_watches (SubProv fan-out / single-use / not-reused
    confidence signals -- X65.4's identified, previously-unused evidence
    source)
  - src/ops/treasury_resolution.py (treasury tier -- consulted ONLY for
    confidence refinement, never as a membership gate, per X65.6 Phase 4)

No detection logic, RPC, or write path is introduced. This module performs
zero writes and zero network I/O.

Mandatory membership criteria (X65.5/X65.6 Phase 3), deliberately minimal:
  1. creator_identity == FRESH_CREATOR
  2. Direct evidence of wrap-close provisioning reaching the creator

Everything else (fan-out breadth, single-use, not-reused, treasury tier) is
optional and confidence-increasing ONLY -- absence never excludes a launch
from WATCHTOWER (X65.5/X65.6 Phase 3's core, tested requirement).

Exclusivity (X65.6 Phase 5A/5B): CAMPAIGN_ORDER's decision function returns
exactly one of WATCHTOWER / OTHER_CAMPAIGN / UNCLASSIFIED per launch --
never zero, never more than one. The returned `conserved` boolean asserts
this the same way canonical_behaviour_conserved (X65.0) and topology's own
`conserved` (X29.1) already do in this codebase.
"""
from __future__ import annotations

from src.ops.lineage_quarantine import eligible_session_relation

import sqlite3
from typing import Any, Optional

from src.ops.creator_identity import FRESH_CREATOR
from src.ops.treasury_resolution import (
    STATUS_KNOWN_TREASURY,
    STATUS_UNKNOWN_TREASURY_CANDIDATE,
)

WATCHTOWER = "WATCHTOWER"
OTHER_CAMPAIGN = "OTHER_CAMPAIGN"
UNCLASSIFIED = "UNCLASSIFIED"

CAMPAIGN_ORDER = (WATCHTOWER, OTHER_CAMPAIGN, UNCLASSIFIED)

CAMPAIGN_LABELS = {
    WATCHTOWER: "WATCHTOWER Provisioning",
    OTHER_CAMPAIGN: "Other Campaign",
    UNCLASSIFIED: "Unclassified",
}

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_BASELINE = "BASELINE"

# Treasury tier labels shown alongside a WATCHTOWER launch (X65.6 Phase 4) --
# descriptive only, never a membership gate.
TREASURY_TIER_CONFIRMED = "CONFIRMED"
TREASURY_TIER_PROBABLE = "PROBABLE"
TREASURY_TIER_NEW = "NEW"
TREASURY_TIER_UNKNOWN = "UNKNOWN"

TREASURY_TIER_LABELS = {
    TREASURY_TIER_CONFIRMED: "Confirmed Treasury",
    TREASURY_TIER_PROBABLE: "Probable Treasury",
    TREASURY_TIER_NEW: "New Treasury",
    TREASURY_TIER_UNKNOWN: "Unknown Treasury",
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _wrap_close_evidence_by_mint(ops_conn: sqlite3.Connection, mints: list[str]) -> dict[str, dict[str, Any]]:
    """Direct evidence of wrap-close provisioning reaching each mint's
    creator -- mandatory criterion 2. Two sources, either sufficient:

      1. wt_watchtower_launches -- the live cascade's own confirmed record
         (subprov_wallet, wrap_close_signature).
      2. wt_attribution_outcomes.evidence_json.subprovisioners -- the
         walkback-resolved population's own subprov evidence, cross-checked
         against wt_active_subprov_sessions for a real funding_signature
         (mirroring treasury_resolution.py's own CONFIRMED/PROBABLE
         distinction so this module never treats a bare, unconfirmed
         mention of a wallet as sufficient evidence).

    Returns {mint: {"subprov_wallet": str, "source": str}} for every mint
    with real evidence; mints without it are simply absent from the dict.
    """
    if not mints:
        return {}
    placeholders = ",".join("?" for _ in mints)
    evidence: dict[str, dict[str, Any]] = {}

    if _table_exists(ops_conn, "wt_watchtower_launches"):
        for row in ops_conn.execute(
            f"SELECT mint, subprov_wallet, wrap_close_signature FROM wt_watchtower_launches "
            f"WHERE mint IN ({placeholders}) AND subprov_wallet IS NOT NULL", mints,
        ):
            evidence[row["mint"]] = {
                "subprov_wallet": row["subprov_wallet"],
                "source": "wt_watchtower_launches",
            }

    remaining = [m for m in mints if m not in evidence]
    if remaining and _table_exists(ops_conn, "wt_attribution_outcomes"):
        import json
        remaining_placeholders = ",".join("?" for _ in remaining)
        candidate_subprov_by_mint: dict[str, str] = {}
        for row in ops_conn.execute(
            f"SELECT mint, evidence_json FROM wt_attribution_outcomes "
            f"WHERE mint IN ({remaining_placeholders})", remaining,
        ):
            try:
                ev = json.loads(row["evidence_json"] or "{}")
            except (TypeError, ValueError):
                continue
            subprovs = ev.get("subprovisioners") or []
            if subprovs:
                candidate_subprov_by_mint[row["mint"]] = subprovs[0]

        # Batched session-existence check across every distinct candidate
        # subprov in ONE query, instead of one round trip per mint -- a
        # real performance requirement at the live population's scale.
        # Mirrors treasury_resolution.py's own CONFIRMED/PROBABLE
        # discipline: a bare wallet mention in evidence_json is not, by
        # itself, treated as confirmed wrap-close provisioning evidence.
        sessioned_subprovs: set[str] = set()
        distinct_candidates = list(set(candidate_subprov_by_mint.values()))
        if distinct_candidates and _table_exists(ops_conn, "wt_active_subprov_sessions"):
            candidate_placeholders = ",".join("?" for _ in distinct_candidates)
            session_relation = eligible_session_relation(ops_conn)
            sessioned_subprovs = {
                r[0] for r in ops_conn.execute(
                    f"SELECT DISTINCT subprov_wallet FROM {session_relation} "
                    f"WHERE subprov_wallet IN ({candidate_placeholders})", distinct_candidates,
                )
            }

        for mint, subprov_wallet in candidate_subprov_by_mint.items():
            if subprov_wallet in sessioned_subprovs:
                evidence[mint] = {
                    "subprov_wallet": subprov_wallet,
                    "source": "wt_attribution_outcomes.evidence_json",
                }
    return evidence


def _fanout_evidence_for_subprovs(ops_conn: sqlite3.Connection, subprov_wallets: list[str]) -> dict[str, dict[str, Any]]:
    """Batched version of the per-subprov fan-out/single-use/not-reused
    check -- computes the same three confidence-increasing signals
    (X65.5/X65.6 Phase 3) for every distinct subprov wallet in ONE pass
    of aggregate SQL each, instead of one round trip per subprov. This
    is a performance fix only; the signals and their meaning are
    unchanged from the per-subprov version.

    Absence of data (a subprov with zero wt_candidate_websocket_watches
    rows) returns all False for that subprov -- the correct, honest
    default per X65.4 Phase 5's finding that most of the Discovery
    population has no coverage in this table; never a negative signal,
    only "no corroborating evidence available" (Baseline confidence).
    """
    result = {sp: {"fan_out_observed": False, "single_use_confirmed": False, "not_reused_confirmed": False}
              for sp in subprov_wallets}
    if not subprov_wallets or not _table_exists(ops_conn, "wt_candidate_websocket_watches"):
        return result

    placeholders = ",".join("?" for _ in subprov_wallets)

    for row in ops_conn.execute(
        f"SELECT subprov_wallet, COUNT(*) AS n FROM wt_candidate_websocket_watches "
        f"WHERE subprov_wallet IN ({placeholders}) GROUP BY subprov_wallet", subprov_wallets,
    ):
        if row["subprov_wallet"] in result:
            result[row["subprov_wallet"]]["fan_out_observed"] = row["n"] > 1

    # For every wrap_wallet belonging to any of these subprovs, compute how
    # many distinct candidates it funded and how many distinct subprovs it
    # was used by -- in two aggregate passes total, not one per wrap wallet.
    wrap_to_subprov: dict[str, set[str]] = {}
    for row in ops_conn.execute(
        f"SELECT DISTINCT wrap_wallet, subprov_wallet FROM wt_candidate_websocket_watches "
        f"WHERE subprov_wallet IN ({placeholders}) AND wrap_wallet IS NOT NULL", subprov_wallets,
    ):
        wrap_to_subprov.setdefault(row["wrap_wallet"], set()).add(row["subprov_wallet"])

    if wrap_to_subprov:
        wrap_wallets = list(wrap_to_subprov)
        wrap_placeholders = ",".join("?" for _ in wrap_wallets)
        funded_counts: dict[str, int] = {}
        used_by_counts: dict[str, int] = {}
        for row in ops_conn.execute(
            f"SELECT wrap_wallet, COUNT(DISTINCT candidate_wallet) AS n "
            f"FROM wt_candidate_websocket_watches WHERE wrap_wallet IN ({wrap_placeholders}) "
            f"GROUP BY wrap_wallet", wrap_wallets,
        ):
            funded_counts[row["wrap_wallet"]] = row["n"]
        for row in ops_conn.execute(
            f"SELECT wrap_wallet, COUNT(DISTINCT subprov_wallet) AS n "
            f"FROM wt_candidate_websocket_watches WHERE wrap_wallet IN ({wrap_placeholders}) "
            f"GROUP BY wrap_wallet", wrap_wallets,
        ):
            used_by_counts[row["wrap_wallet"]] = row["n"]

        for wrap_wallet, subprovs_using_it in wrap_to_subprov.items():
            funded = funded_counts.get(wrap_wallet, 0)
            used_by = used_by_counts.get(wrap_wallet, 0)
            for sp in subprovs_using_it:
                if sp not in result:
                    continue
                if funded == 1:
                    result[sp]["single_use_confirmed"] = True
                if used_by == 1:
                    result[sp]["not_reused_confirmed"] = True
    return result


def treasury_tier_for_resolution(resolution: dict[str, Any]) -> str:
    """Maps an existing treasury_resolution.py status onto the four
    Discovery-facing tiers (X65.6 Phase 4) -- presentation mapping only,
    the underlying treasury_resolution.py statuses/values are unchanged."""
    status = resolution.get("status")
    if status == STATUS_KNOWN_TREASURY:
        return TREASURY_TIER_CONFIRMED
    if status == STATUS_UNKNOWN_TREASURY_CANDIDATE:
        return TREASURY_TIER_NEW
    return TREASURY_TIER_UNKNOWN


def _treasury_tiers_for_mints(ops_conn: sqlite3.Connection, mints: list[str]) -> dict[str, str]:
    """Batched equivalent of calling treasury_resolution.py's
    resolve_treasury_for_launch() once per mint -- this module never
    modifies treasury_resolution.py; it replicates its documented
    KNOWN_TREASURY / UNKNOWN_TREASURY_CANDIDATE / else decision using the
    exact same tables (wt_attribution_outcomes.terminal_entity,
    wt_active_subprov_sessions.treasury_wallet, wt_confirmed_treasuries),
    in a handful of aggregate queries instead of one multi-query round
    trip per mint. This is a performance fix only -- the classification
    reached for any given mint is identical to what
    resolve_treasury_for_launch() would return, mapped through
    treasury_tier_for_resolution()'s same three-way rule.

    Used ONLY for confidence-tier display within this module -- never as
    a Campaign membership gate (X65.6 Phase 4).
    """
    tiers: dict[str, str] = {}
    if not mints or not _table_exists(ops_conn, "wt_attribution_outcomes"):
        return {m: TREASURY_TIER_UNKNOWN for m in mints}

    placeholders = ",".join("?" for _ in mints)
    funder_by_mint: dict[str, str] = {}
    for row in ops_conn.execute(
        f"SELECT mint, terminal_entity FROM wt_attribution_outcomes "
        f"WHERE mint IN ({placeholders}) AND terminal_entity IS NOT NULL", mints,
    ):
        funder_by_mint[row["mint"]] = row["terminal_entity"]

    if not funder_by_mint:
        return {m: TREASURY_TIER_UNKNOWN for m in mints}

    confirmed_treasuries: set[str] = set()
    if _table_exists(ops_conn, "wt_confirmed_treasuries"):
        confirmed_treasuries = {r[0] for r in ops_conn.execute("SELECT treasury FROM wt_confirmed_treasuries")}

    funders = list(set(funder_by_mint.values()))
    funder_placeholders = ",".join("?" for _ in funders)

    # A funder that is itself already a confirmed treasury -> direct
    # treasury funding, no subprov hop (mirrors classify_creator_funder's
    # SUBPROV_DIRECT_TREASURY branch).
    direct_treasury_funders = {f for f in funders if f in confirmed_treasuries}

    # A funder with a wt_active_subprov_sessions row -> its OWN
    # treasury_wallet is the second-hop candidate to check.
    subprov_treasury_by_funder: dict[str, str] = {}
    if _table_exists(ops_conn, "wt_active_subprov_sessions"):
        session_relation = eligible_session_relation(ops_conn)
        for row in ops_conn.execute(
            f"SELECT subprov_wallet, treasury_wallet FROM {session_relation} "
            f"WHERE subprov_wallet IN ({funder_placeholders}) AND treasury_wallet IS NOT NULL", funders,
        ):
            subprov_treasury_by_funder.setdefault(row["subprov_wallet"], row["treasury_wallet"])

    for mint in mints:
        funder = funder_by_mint.get(mint)
        if not funder:
            tiers[mint] = TREASURY_TIER_UNKNOWN
            continue
        if funder in direct_treasury_funders:
            tiers[mint] = TREASURY_TIER_CONFIRMED
            continue
        treasury_wallet = subprov_treasury_by_funder.get(funder)
        if not treasury_wallet:
            tiers[mint] = TREASURY_TIER_UNKNOWN
            continue
        tiers[mint] = TREASURY_TIER_CONFIRMED if treasury_wallet in confirmed_treasuries else TREASURY_TIER_NEW

    for mint in mints:
        tiers.setdefault(mint, TREASURY_TIER_UNKNOWN)
    return tiers


def classify_campaign_for_launch(
    *,
    creator_identity: Optional[str],
    wrap_close_evidence: Optional[dict[str, Any]],
    has_other_funding_lineage: bool,
    fanout_evidence: Optional[dict[str, Any]] = None,
    treasury_tier: Optional[str] = None,
) -> dict[str, Any]:
    """Pure per-launch decision function. Exactly one of WATCHTOWER /
    OTHER_CAMPAIGN / UNCLASSIFIED is returned -- never zero, never more
    than one (X65.6 Phase 5A/5B exclusivity requirement).

    Mandatory criteria (both required for WATCHTOWER):
      1. creator_identity == FRESH_CREATOR
      2. wrap_close_evidence is not None (direct provisioning evidence)

    Confidence (WATCHTOWER launches only; never affects membership):
      HIGH:     fan-out observed AND (single-use confirmed AND not-reused confirmed)
      MEDIUM:   at least one of {fan-out observed, single-use confirmed,
                not-reused confirmed, treasury tier != UNKNOWN} is true
      BASELINE: mandatory criteria only, no further corroborating evidence
    """
    fanout_evidence = fanout_evidence or {}

    if creator_identity != FRESH_CREATOR or wrap_close_evidence is None:
        campaign = OTHER_CAMPAIGN if has_other_funding_lineage else UNCLASSIFIED
        return {
            "campaign": campaign,
            "label": CAMPAIGN_LABELS[campaign],
            "confidence": None,
            "confidence_label": None,
            "evidence": {
                "creator_identity": creator_identity,
                "wrap_close_evidence": False,
                "other_funding_lineage": has_other_funding_lineage,
            },
        }

    fan_out_observed = bool(fanout_evidence.get("fan_out_observed"))
    single_use_confirmed = bool(fanout_evidence.get("single_use_confirmed"))
    not_reused_confirmed = bool(fanout_evidence.get("not_reused_confirmed"))
    treasury_known = treasury_tier not in (None, TREASURY_TIER_UNKNOWN)

    if fan_out_observed and single_use_confirmed and not_reused_confirmed:
        confidence = CONFIDENCE_HIGH
    elif fan_out_observed or single_use_confirmed or not_reused_confirmed or treasury_known:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_BASELINE

    return {
        "campaign": WATCHTOWER,
        "label": CAMPAIGN_LABELS[WATCHTOWER],
        "confidence": confidence,
        "confidence_label": confidence.title(),
        "evidence": {
            "creator_identity": creator_identity,
            "wrap_close_evidence": True,
            "subprov_wallet": wrap_close_evidence.get("subprov_wallet"),
            "wrap_close_source": wrap_close_evidence.get("source"),
            "fan_out_observed": fan_out_observed,
            "single_use_confirmed": single_use_confirmed,
            "not_reused_confirmed": not_reused_confirmed,
            "treasury_tier": treasury_tier,
        },
    }


def build_campaign_classification(
    ops_db_path: str,
    records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Batch entry point, mirroring build_topology_classification()'s and
    build_behaviour_classification()'s own shape. `records` is the
    already-computed Discovery records map from build_operational_
    intelligence() -- this function reads records[mint]["creator_identity"]
    (already attached by enrich_creator_identity(), called earlier in the
    same pipeline) but does not otherwise mutate `records`.

    Read-only, zero writes, zero RPC. Every fact is drawn from
    wt_watchtower_launches / wt_attribution_outcomes / wt_active_subprov_
    sessions / wt_candidate_websocket_watches / wt_confirmed_treasuries /
    wt_ops_v2_wallets -- all already-existing, already-populated tables.

    Returns:
      {
        "campaign_summary": [{"campaign":..., "label":..., "count":..., "coverage_pct":...}, ...],
        "campaign_conserved": bool,   # sum(bucket counts) == total_launches, always
        "assignments": {mint: {"campaign":..., "confidence":..., "treasury_tier":..., "evidence": {...}}},
      }
    """
    mints = list(records)
    total = len(mints)
    assignments: dict[str, dict[str, Any]] = {}
    counts = {c: 0 for c in CAMPAIGN_ORDER}

    if not mints:
        return {
            "campaign_summary": [
                {"campaign": c, "label": CAMPAIGN_LABELS[c], "count": 0, "coverage_pct": 0.0}
                for c in CAMPAIGN_ORDER
            ],
            "campaign_conserved": True,
            "assignments": {},
        }

    ops_conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops_conn.row_factory = sqlite3.Row
    ops_conn.execute("PRAGMA query_only=ON")
    try:
        wrap_close_by_mint = _wrap_close_evidence_by_mint(ops_conn, mints)

        # Other-funding-lineage check: any wt_attribution_outcomes row at
        # all (regardless of whether it produced wrap-close-specific
        # evidence above) counts as "some funding lineage exists" for the
        # OTHER_CAMPAIGN vs UNCLASSIFIED distinction (X65.6 Phase 8).
        has_lineage: set[str] = set()
        if _table_exists(ops_conn, "wt_attribution_outcomes"):
            placeholders = ",".join("?" for _ in mints)
            has_lineage = {
                row[0] for row in ops_conn.execute(
                    f"SELECT mint FROM wt_attribution_outcomes "
                    f"WHERE mint IN ({placeholders}) AND terminal_entity IS NOT NULL", mints,
                )
            }

        # Batched confidence-signal lookups -- computed ONLY for mints that
        # already satisfy the mandatory criteria (have wrap-close evidence),
        # in a handful of aggregate queries total rather than one round
        # trip per mint/subprov (a real performance requirement at the
        # ~4,000-launch scale of the live Discovery population).
        watchtower_mints = [m for m in mints if wrap_close_by_mint.get(m)]
        distinct_subprovs = sorted({
            wrap_close_by_mint[m]["subprov_wallet"] for m in watchtower_mints
            if wrap_close_by_mint[m].get("subprov_wallet")
        })
        fanout_by_subprov = _fanout_evidence_for_subprovs(ops_conn, distinct_subprovs)
        treasury_tier_by_mint = _treasury_tiers_for_mints(ops_conn, watchtower_mints)

        for mint in mints:
            creator_identity = records[mint].get("creator_identity")
            wrap_close_evidence = wrap_close_by_mint.get(mint)

            fanout_evidence = None
            treasury_tier = None
            if wrap_close_evidence:
                subprov_wallet = wrap_close_evidence.get("subprov_wallet")
                if subprov_wallet:
                    fanout_evidence = fanout_by_subprov.get(subprov_wallet)
                treasury_tier = treasury_tier_by_mint.get(mint)

            result = classify_campaign_for_launch(
                creator_identity=creator_identity,
                wrap_close_evidence=wrap_close_evidence,
                has_other_funding_lineage=mint in has_lineage,
                fanout_evidence=fanout_evidence,
                treasury_tier=treasury_tier,
            )
            assignments[mint] = result
            counts[result["campaign"]] += 1
    finally:
        ops_conn.close()

    return {
        "campaign_summary": [
            {
                "campaign": c,
                "label": CAMPAIGN_LABELS[c],
                "count": counts[c],
                "coverage_pct": round(counts[c] / total * 100, 1) if total else 0.0,
            }
            for c in CAMPAIGN_ORDER
        ],
        "campaign_conserved": sum(counts.values()) == total,
        "assignments": assignments,
    }
