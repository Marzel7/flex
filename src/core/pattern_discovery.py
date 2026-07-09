"""Known operator baseline report.

Clusters confirmed WATCHTOWER launches by behavioural fingerprint to produce
a reference library of known operator signatures (seed band, capital band,
mechanism, migration rate, upstream treasury).

This is NOT discovery — it describes infrastructure already attributed.
Discovery mode (clustering wt_discovered_subprovs by immediate_funder) will
be added when WT-LIKE subprovs accumulate enough session/walkback data.

No writes, no schema changes, no background processing.
Called on-demand by both the CLI report and the dashboard API.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any


# ── Bucketing helpers ─────────────────────────────────────────────────────────

def seed_band(sol: float | None) -> str:
    if sol is None:
        return "unknown"
    if sol < 0.05:
        return "<0.05"
    if sol < 0.15:
        return "0.05–0.15"
    if sol < 0.25:
        return "0.15–0.25"
    if sol < 1.0:
        return "0.25–1"
    if sol < 5.0:
        return "1–5"
    if sol < 10.0:
        return "5–10"
    return "≥10"


def capital_band(sol: float | None) -> str:
    if sol is None:
        return "unknown"
    if sol < 10:
        return "<10"
    if sol < 100:
        return "10–100"
    if sol < 500:
        return "100–500"
    if sol < 800:
        return "500–800"
    if sol < 1200:
        return "800–1200"
    return "≥1200"


# ── Data loading ──────────────────────────────────────────────────────────────

def load_launches(conn: sqlite3.Connection) -> list[dict]:
    """Load all launches with lifecycle state. Single query, LIMIT 2000 safety ceiling."""
    rows = conn.execute("""
        SELECT wl.mint, wl.subprov_wallet, wl.treasury_wallet,
               wl.wrap_close_sol, wl.subprov_funding_sol,
               wl.fanout_count, wl.fanout_to_create_secs,
               wl.create_to_migration_secs, wl.funding_mechanism,
               wl.create_time,
               CASE WHEN tlc.lifecycle_state IN ('MIGRATED','RECYCLED') THEN 1 ELSE 0 END AS migrated
        FROM wt_watchtower_launches wl
        LEFT JOIN wt_token_lifecycle tlc ON tlc.mint = wl.mint
        ORDER BY wl.create_time ASC
        LIMIT 2000
    """).fetchall()
    return [dict(r) for r in rows]


# ── Scoring ───────────────────────────────────────────────────────────────────

_MAX_SCORE = 16  # 3+3+2+2+2+2+2


def score_cluster(members: set[str], launches: list[dict]) -> dict:
    """Score a cluster on behavioural signals. Returns scoring dict."""
    cluster_launches = [l for l in launches if l["subprov_wallet"] in members]

    mechs = [l["funding_mechanism"] for l in cluster_launches if l["funding_mechanism"]]
    seeds = [l["wrap_close_sol"] for l in cluster_launches if l["wrap_close_sol"]]
    capitals = [l["subprov_funding_sol"] for l in cluster_launches if l["subprov_funding_sol"]]
    fanouts = [l["fanout_count"] for l in cluster_launches if l["fanout_count"]]
    migrations = [l["migrated"] for l in cluster_launches]
    treasuries = {l["treasury_wallet"] for l in cluster_launches if l["treasury_wallet"]}

    breakdown: dict[str, tuple[int, str]] = {}
    score = 0

    # Signal 1: Funding mechanism identical (+3)
    if mechs:
        if len(set(mechs)) == 1:
            breakdown["mechanism_identical"] = (3, f"all {mechs[0]}")
            score += 3
        else:
            breakdown["mechanism_mixed"] = (0, f"mixed: {sorted(set(mechs))}")
    else:
        breakdown["mechanism_unknown"] = (0, "no data")

    # Signal 2: Creator seed amount consistency (+3 tight, +1 moderate)
    if len(seeds) >= 2:
        lo, hi = min(seeds), max(seeds)
        spread = (hi - lo) / hi if hi else 0
        if spread <= 0.15:
            breakdown["seed_tight"] = (3, f"{lo:.4f}–{hi:.4f}◎ (±{spread*100:.0f}%)")
            score += 3
        elif spread <= 0.30:
            breakdown["seed_moderate"] = (1, f"{lo:.4f}–{hi:.4f}◎ (±{spread*100:.0f}%)")
            score += 1
        else:
            breakdown["seed_wide"] = (0, f"{lo:.4f}–{hi:.4f}◎ (wide)")
    elif seeds:
        breakdown["seed_single"] = (1, f"{seeds[0]:.4f}◎")
        score += 1

    # Signal 3: Session capital consistency (+2)
    if len(capitals) >= 2:
        lo, hi = min(capitals), max(capitals)
        spread = (hi - lo) / hi if hi else 0
        if spread <= 0.20:
            breakdown["capital_consistent"] = (2, f"{lo:.0f}–{hi:.0f}◎")
            score += 2
        else:
            breakdown["capital_variable"] = (0, f"{lo:.0f}–{hi:.0f}◎ (variable)")
    elif capitals:
        breakdown["capital_single"] = (1, f"{capitals[0]:.0f}◎")
        score += 1

    # Signal 4: Fan-out width ±2 (+2)
    if len(fanouts) >= 2:
        lo, hi = min(fanouts), max(fanouts)
        if hi - lo <= 2:
            breakdown["fanout_consistent"] = (2, f"{lo}–{hi} creators/session")
            score += 2
        else:
            breakdown["fanout_variable"] = (0, f"{lo}–{hi} (wide spread)")
    elif fanouts:
        breakdown["fanout_single"] = (1, f"{fanouts[0]} creators")
        score += 1

    # Signal 5: Migration rate (+2 ≥80%, +1 ≥50%)
    if migrations:
        mig_count = sum(migrations)
        mig_rate = mig_count / len(migrations)
        if mig_rate >= 0.80:
            breakdown["migration_high"] = (2, f"{mig_rate*100:.0f}% ({mig_count}/{len(migrations)})")
            score += 2
        elif mig_rate >= 0.50:
            breakdown["migration_moderate"] = (1, f"{mig_rate*100:.0f}%")
            score += 1
        else:
            breakdown["migration_low"] = (0, f"{mig_rate*100:.0f}%")

    # Signal 6: Shared upstream funder (+2 single, +1 few)
    if len(treasuries) == 1:
        breakdown["single_treasury"] = (2, list(treasuries)[0])
        score += 2
    elif len(treasuries) <= 3:
        breakdown["few_treasuries"] = (1, f"{len(treasuries)} distinct funders")
        score += 1
    else:
        breakdown["many_treasuries"] = (0, f"{len(treasuries)} distinct funders")

    # Signal 7: Cluster size (+2 ≥5 members, +1 ≥3)
    n = len(members)
    if n >= 5:
        breakdown["large_cluster"] = (2, f"{n} subprovs")
        score += 2
    elif n >= 3:
        breakdown["medium_cluster"] = (1, f"{n} subprovs")
        score += 1

    pct = round(score / _MAX_SCORE * 100)
    if pct >= 75:
        confidence = "HIGH"
    elif pct >= 50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "score": score,
        "max_score": _MAX_SCORE,
        "score_pct": pct,
        "confidence": confidence,
        "breakdown": {k: {"points": v[0], "note": v[1]} for k, v in breakdown.items()},
        "n_launches": len(cluster_launches),
        "migration_count": sum(migrations),
        "migration_rate": round(sum(migrations) / len(migrations) * 100) if migrations else 0,
        "mechs": sorted(set(mechs)),
        "seeds": seeds,
        "seed_min": min(seeds) if seeds else None,
        "seed_max": max(seeds) if seeds else None,
        "seed_band": seed_band(seeds[0] if len(set(seeds)) == 1 else None) if seeds else "unknown",
        "capitals": capitals,
        "capital_min": min(capitals) if capitals else None,
        "capital_max": max(capitals) if capitals else None,
        "capital_band": capital_band(sum(capitals) / len(capitals)) if capitals else "unknown",
        "fanouts": fanouts,
        "treasuries": sorted(treasuries),
        "members": sorted(members),
    }


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_by_treasury(launches: list[dict]) -> dict[str, set[str]]:
    """Group subprovs by shared treasury_wallet."""
    by_treasury: dict[str, set[str]] = defaultdict(set)
    for l in launches:
        if l["treasury_wallet"]:
            by_treasury[l["treasury_wallet"]].add(l["subprov_wallet"])
    return dict(by_treasury)


def cluster_ungrouped_by_fingerprint(
    launches: list[dict], already_grouped: set[str]
) -> dict[tuple[str, str], set[str]]:
    """Group remaining subprovs by (seed_band, mechanism)."""
    sp_fp: dict[str, tuple[str, str]] = {}
    for l in launches:
        sp = l["subprov_wallet"]
        if sp in already_grouped or sp in sp_fp:
            continue
        sp_fp[sp] = (
            seed_band(l["wrap_close_sol"]),
            l["funding_mechanism"] or "unknown",
        )
    by_fp: dict[tuple[str, str], set[str]] = defaultdict(set)
    for sp, fp in sp_fp.items():
        by_fp[fp].add(sp)
    return dict(by_fp)


# ── Per-subprov detail ────────────────────────────────────────────────────────

def subprov_detail(sp: str, launches: list[dict]) -> dict:
    sp_launches = [l for l in launches if l["subprov_wallet"] == sp]
    mig = sum(1 for l in sp_launches if l["migrated"])
    return {
        "subprov": sp,
        "launches": len(sp_launches),
        "migrated": mig,
        "seed_sol": sp_launches[0]["wrap_close_sol"] if sp_launches else None,
        "capital_sol": sp_launches[0]["subprov_funding_sol"] if sp_launches else None,
        "mechanism": sp_launches[0]["funding_mechanism"] if sp_launches else None,
        "treasury": sp_launches[0]["treasury_wallet"] if sp_launches else None,
    }


# ── Main report ───────────────────────────────────────────────────────────────

def build_report(conn: sqlite3.Connection, min_members: int = 2) -> dict[str, Any]:
    """Build the full pattern report dict. No writes. Safe to call from API or CLI."""
    launches = load_launches(conn)
    if not launches:
        return {"launches_total": 0, "primary_clusters": [], "secondary_clusters": [],
                "singletons": [], "summary": {}}

    total = len(launches)
    migrated_total = sum(1 for l in launches if l["migrated"])
    treasury_set = {l["treasury_wallet"] for l in launches if l["treasury_wallet"]}

    # Primary: shared treasury
    treasury_clusters = cluster_by_treasury(launches)
    treasury_subprovs: set[str] = set()
    for members in treasury_clusters.values():
        treasury_subprovs.update(members)

    primary = []
    for treasury, members in sorted(treasury_clusters.items(), key=lambda kv: -len(kv[1])):
        if len(members) < min_members:
            continue
        scored = score_cluster(members, launches)
        scored["cluster_type"] = "PRIMARY"
        scored["label"] = f"treasury={treasury[:20]}…"
        scored["treasury_key"] = treasury
        scored["member_detail"] = [subprov_detail(sp, launches) for sp in sorted(members)]
        primary.append(scored)

    # Sort primary by confidence desc then member count desc
    _conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    primary.sort(key=lambda c: (_conf_order.get(c["confidence"], 9), -c["n_launches"]))

    # Secondary: ungrouped by seed_band + mechanism
    secondary_raw = cluster_ungrouped_by_fingerprint(launches, treasury_subprovs)
    secondary = []
    for (sb, mech), members in sorted(secondary_raw.items(), key=lambda kv: -len(kv[1])):
        if len(members) < min_members:
            continue
        scored = score_cluster(members, launches)
        scored["cluster_type"] = "SECONDARY"
        scored["label"] = f"seed={sb}◎  mech={mech}"
        scored["treasury_key"] = None
        scored["member_detail"] = [subprov_detail(sp, launches) for sp in sorted(members)]
        secondary.append(scored)
    secondary.sort(key=lambda c: (_conf_order.get(c["confidence"], 9), -c["n_launches"]))

    # Singletons
    all_grouped = treasury_subprovs | {sp for grp in secondary_raw.values() for sp in grp}
    singleton_sps = {l["subprov_wallet"] for l in launches} - all_grouped
    singletons = [subprov_detail(sp, launches) for sp in sorted(singleton_sps)]

    return {
        "launches_total": total,
        "migrated_total": migrated_total,
        "migration_rate": round(migrated_total / total * 100) if total else 0,
        "upstream_funders": len(treasury_set),
        "primary_clusters": primary,
        "secondary_clusters": secondary,
        "singletons": singletons,
        "summary": {
            "primary_count": len(primary),
            "secondary_count": len(secondary),
            "singleton_count": len(singletons),
            "high_confidence": sum(1 for c in primary + secondary if c["confidence"] == "HIGH"),
            "medium_confidence": sum(1 for c in primary + secondary if c["confidence"] == "MEDIUM"),
        },
    }
