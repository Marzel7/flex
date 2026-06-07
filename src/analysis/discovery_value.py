"""
discovery_value.py — score discovery objects by EXPECTED NETWORK-EXPANSION VALUE.

Discovery Value = expected ability to reveal PREVIOUSLY UNKNOWN WATCHTOWER
infrastructure. NOT confidence, NOT attribution strength, NOT token/launch counts —
only network expansion potential.

The precise definition (the fix):
    Discovery value is driven by INFRASTRUCTURE COVERAGE — how much of the object's
    reachable upstream/counterparty infrastructure is ALREADY mapped.

      coverage = mapped_counterparties / total_counterparties
      discovery_value ∝ (1 - coverage)

    A direct-infra creator whose upstream funders are all already known is
    CONFIRMATION (creator → token → launch — nothing new), so LOW. The same creator
    with 11 unmapped upstream funders is DISCOVERY, so HIGH. Linkage TYPE no longer
    sets the band on its own — what matters is whether unexplored infrastructure
    actually exists behind the object.

The test (from the spec):
    Clicking it mostly shows creator → token → launch  → CONFIRMATION → LOW
    Clicking it likely reveals new funder / sibling / relay / corridor → DISCOVERY → HIGH
"""
from __future__ import annotations
import sqlite3
from typing import Optional

VERY_HIGH, HIGH, MEDIUM, LOW = "VERY_HIGH", "HIGH", "MEDIUM", "LOW"
_BAND = {VERY_HIGH: 4, HIGH: 3, MEDIUM: 2, LOW: 1}

# Sentinel/placeholder "addresses" that are not real wallets — they falsely bind
# clusters and inflate yield (Cluster #75 audit: 93/111 members were glued ONLY by
# 'SYSTEM'). Excluded from yield, coverage, and quality everywhere.
PLACEHOLDERS = {"SYSTEM", "UNKNOWN", "", "NONE", "null", "None"}

# Frontier Quality bands — how much COORDINATION STRUCTURE the frontier contains.
# A large pile of one-off funders is LOW; a small tightly-shared infra set is HIGH.
VERY_LOW = "VERY_LOW"
_QUALITY = {VERY_HIGH: 4, HIGH: 3, MEDIUM: 2, LOW: 1, VERY_LOW: 0}


def quality_rank(q: str) -> int:
    return _QUALITY.get(q, 0)


def band_rank(band: str) -> int:
    return _BAND.get(band, 0)


def _band(coverage: float, has_frontier: bool, yield_count: int) -> str:
    """
    Band from BOTH coverage (how unexplored) and yield (how MANY unknown entities
    are reachable). A low-coverage object with a large frontier is VERY_HIGH — the
    biggest network-expansion opportunity. No frontier → LOW (confirmation only).
    """
    if not has_frontier or yield_count <= 0:
        return LOW
    if coverage <= 0.30 and yield_count >= 15:
        return VERY_HIGH        # mostly unexplored AND a large frontier
    if coverage <= 0.30:
        return HIGH
    if coverage <= 0.70:
        return HIGH if yield_count >= 15 else MEDIUM
    return MEDIUM if yield_count >= 8 else LOW


def load_mapped_set(conn: sqlite3.Connection) -> Optional[set]:
    """
    Load the 'mapped infrastructure' address set ONCE per request (known infra +
    already-attributed creators), for in-memory membership checks. infra_wallets has
    ~560k rows, so a per-object `NOT IN (...union...)` subquery is O(seconds) each and
    times the endpoint out — pulling the set once and checking in Python is the fix.
    Returns None if infra_wallets is absent.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='infra_wallets'").fetchone():
        return None
    s = {r[0] for r in conn.execute("SELECT address FROM infra_wallets") if r[0]}
    s.update(r[0] for r in conn.execute(
        "SELECT creator_address FROM creator_risk_scores WHERE watchtower_related=1") if r[0])
    return s


def attribution_coverage(conn: sqlite3.Connection, creator_address: str, mapped: set) -> dict:
    """
    Coverage for an attributed creator = fraction of its UPSTREAM funders already mapped
    (known infra or already attributed). `mapped` is the precomputed set (load_mapped_set).
    Returns {coverage, total_upstream, unmapped_upstream, has_frontier}.
    """
    if mapped is None or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_funders'").fetchone():
        return {"coverage": 0.0, "total_upstream": 0, "unmapped_upstream": 0, "has_frontier": False}
    funders = {r[0] for r in conn.execute(
        "SELECT DISTINCT funder_address FROM creator_funders WHERE creator_address=?",
        (creator_address,)) if r[0]}
    total = len(funders)
    if total == 0:
        # zero upstream funders → clicking shows only creator→token→launch → CONFIRMATION
        return {"coverage": 1.0, "total_upstream": 0, "unmapped_upstream": 0, "has_frontier": False}
    unmapped = sum(1 for f in funders if f not in mapped)
    coverage = (total - unmapped) / total
    return {"coverage": round(coverage, 3), "total_upstream": total,
            "unmapped_upstream": unmapped, "has_frontier": unmapped > 0}


def attribution_discovery_value(conn, creator_address, linkage, mapped) -> tuple[str, str, dict]:
    """(band, reveal_text, info) for an attributed creator. info carries coverage AND
    potential_yield = count of unknown upstream entities investigation could reveal."""
    cov = attribution_coverage(conn, creator_address, mapped)
    y = cov["unmapped_upstream"]
    cov["potential_yield"] = y
    band = _band(cov["coverage"], cov["has_frontier"], y)
    if not cov["has_frontier"]:
        reveal = "upstream fully mapped — confirmation only (creator → token → launch)"
    else:
        reveal = f"{y} unmapped upstream funder{'s' if y != 1 else ''} — may reveal new infra + sibling creators"
    return band, reveal, cov


def cluster_coverage(conn: sqlite3.Connection, cluster_id: int, mapped: set) -> dict:
    """
    Coverage for an emerging cluster = fraction of its members' counterparties
    (funders + recipients) already mapped. `mapped` is the precomputed set. Unexplained
    shared funders / payout paths are the frontier.
    """
    if mapped is None or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_cluster_members'").fetchone():
        return {"coverage": 0.0, "total_cp": 0, "unmapped_cp": 0, "has_frontier": False}
    creators = [r[0] for r in conn.execute(
        "SELECT creator_wallet FROM wt_cluster_members WHERE cluster_id=?", (cluster_id,)).fetchall()
        if r and r[0]]
    if not creators:
        return {"coverage": 0.0, "total_cp": 0, "unmapped_cp": 0, "has_frontier": False}
    ph = ",".join("?" * len(creators))
    funders, recipients = set(), set()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_funders'").fetchone():
        funders = {a for (a,) in conn.execute(
            f"SELECT DISTINCT funder_address FROM creator_funders WHERE creator_address IN ({ph})",
            creators) if a and a not in PLACEHOLDERS}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_outgoing_transfers'").fetchone():
        recipients = {a for (a,) in conn.execute(
            f"SELECT DISTINCT recipient_address FROM creator_outgoing_transfers WHERE creator_address IN ({ph})",
            creators) if a and a not in PLACEHOLDERS}
    cps = funders | recipients
    total = len(cps)
    if total == 0:
        return {"coverage": 1.0, "total_cp": 0, "unmapped_cp": 0, "has_frontier": False,
                "breakdown": {}}
    unmapped_set = {cp for cp in cps if cp not in mapped}
    unmapped = len(unmapped_set)
    coverage = (total - unmapped) / total
    # decompose the UNMAPPED frontier so the analyst knows what it contains: a funder
    # set vs a recipient set imply different investigation strategies.
    u_funders = len(unmapped_set & funders)
    u_recipients = len(unmapped_set & recipients)
    breakdown = {
        "funders": u_funders,
        "recipients": u_recipients,
        "both": len(unmapped_set & funders & recipients),
    }
    return {"coverage": round(coverage, 3), "total_cp": total,
            "unmapped_cp": unmapped, "has_frontier": unmapped > 0,
            "breakdown": breakdown}


def cluster_discovery_value(conn, cluster_id, mapped, provisioners=0, growing=False) -> tuple[str, str, dict]:
    """Coverage + yield driven; growth/provisioning upgrade the band."""
    cov = cluster_coverage(conn, cluster_id, mapped)
    y = cov["unmapped_cp"]
    cov["potential_yield"] = y
    band = _band(cov["coverage"], cov["has_frontier"], y)
    # active expansion signals push one band higher (capped at VERY_HIGH)
    if (growing or provisioners) and band in (MEDIUM, HIGH):
        band = VERY_HIGH if band == HIGH else HIGH
    if not cov["has_frontier"]:
        reveal = "counterparties fully mapped — limited new ground"
    else:
        extra = []
        if provisioners: extra.append("provisioning signature")
        if growing: extra.append("growing")
        tail = (" · " + " · ".join(extra)) if extra else ""
        reveal = f"{y} unexplained infrastructure node{'s' if y != 1 else ''} — may expose unattributed infra{tail}"
    return band, reveal, cov


def reservoir_discovery_value(dormant: int, converted: int) -> tuple[str, str, dict]:
    """
    Reservoir = genuinely forward-looking. The first conversion reveals
    relay → creator → funding lineage → new infrastructure, before provisioning-hub
    detection fires. Unconverted pool = 0% coverage = HIGH. This is by design one of
    the highest-value discovery objects in the system.
    """
    if dormant <= 0:
        return LOW, "no dormant wallets to convert", {"coverage": 1.0, "has_frontier": False,
                                                      "potential_yield": 0}
    total = dormant + converted
    coverage = round(converted / total, 3) if total else 0.0
    # yield is UNKNOWN-but-large: each of the dormant wallets is a potential lineage
    # (relay → creator → provisioning → operator). We surface the dormant count as the
    # frontier size — its true yield is unbounded until the first conversion.
    if converted == 0:
        return VERY_HIGH, ("first conversion may reveal relay → creator → lineage → "
                           "provisioning → operator — earliest known interception point"), \
               {"coverage": 0.0, "has_frontier": True, "potential_yield": dormant, "yield_unknown": True}
    band = _band(coverage, True, dormant)
    return band, "conversions in progress — each reveals a new launch lineage", \
        {"coverage": coverage, "has_frontier": True, "potential_yield": dormant}


def attribution_yield_summary(conn: sqlite3.Connection, creator_addresses: list[str],
                              mapped: set) -> dict:
    """
    Aggregate network-expansion view across attributed creators: fully/partially/
    unexplored + TOTAL potential yield (sum of unknown upstream entities). Makes the
    attribution counts actionable — "143 nodes reachable" > "124 confirmed".

    One pass: pull every (creator, funder) edge for attributed creators in a single
    query, then bucket per-creator in Python against the in-memory `mapped` set. Avoids
    both the 284-query loop AND the per-row NOT IN (560k union) — both timed out.
    """
    if mapped is None or not creator_addresses or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_funders'").fetchone():
        return {"fully_explored": 0, "partially_explored": 0, "unexplored": 0, "potential_yield": 0}
    addr_set = set(creator_addresses)
    per: dict[str, list[int]] = {}   # creator → [total, unmapped]
    for ca, fa in conn.execute(
        "SELECT creator_address, funder_address FROM creator_funders "
        "WHERE creator_address IN (SELECT creator_address FROM creator_risk_scores "
        "WHERE watchtower_related=1)"):
        if not fa:
            continue
        rec = per.setdefault(ca, [0, 0])
        rec[0] += 1
        if fa not in mapped:
            rec[1] += 1
    fully = partial = unexplored = total_yield = 0
    for ca in addr_set:
        total, unm = per.get(ca, [0, 0])
        total_yield += unm
        if total == 0 or unm == 0:
            fully += 1
        elif (total - unm) / total >= 0.5:
            partial += 1
        else:
            unexplored += 1
    return {"fully_explored": fully, "partially_explored": partial,
            "unexplored": unexplored, "potential_yield": total_yield}


# ── Frontier classification + Expected Yield ─────────────────────────────────
# CURRENT = infrastructure already active/observable (clusters, attributions) — the
#   relationships exist now, so discovery probability is relatively high.
# FUTURE  = not yet active, may generate infrastructure later (reservoir) — yield is
#   real but unbounded/uncertain, so discovery probability is unknown.
CURRENT, FUTURE = "CURRENT", "FUTURE"

# Discovery probability per object kind/signal. Heuristic until conversion-rate data
# exists. Used for Expected Yield = potential_yield × probability. Reservoir is
# deliberately UNKNOWN (None) — we don't yet have a conversion rate to estimate it.
_PROB = {"HIGH": 0.7, "MEDIUM": 0.4, "LOW": 0.15}


def discovery_probability(kind: str, band: str, has_baseline: bool = True):
    """Probability that investigating this object actually yields new infra.
    Returns a float, or None when genuinely unknown (reservoir before first conversion)."""
    if kind == "reservoir":
        return None                      # unknown until the first conversion happens
    p = _PROB.get(band, 0.3)
    # active/observable infrastructure (clusters/attributions) is more convertible than
    # a soft signal; baseline-less clusters are slightly less certain.
    if kind == "cluster" and not has_baseline:
        p *= 0.85
    return round(p, 2)


def expected_yield(potential_yield, probability):
    """Expected Yield = potential × probability. None when probability is unknown —
    the UI shows the raw potential with an 'unknown probability' flag instead."""
    if probability is None or potential_yield is None:
        return None
    return round(potential_yield * probability, 1)


def frontier_type(kind: str) -> str:
    """reservoir → FUTURE (pre-launch staging); everything else → CURRENT (active)."""
    return FUTURE if kind == "reservoir" else CURRENT


def cluster_frontier_quality(conn, cluster_id, mapped, provisioners=0) -> tuple[str, dict]:
    """
    FRONTIER QUALITY = how much COORDINATION STRUCTURE the frontier contains, vs being
    a large pile of unrelated wallets. Rewards shared structure; penalises one-off
    counterparties, placeholder-binding, and duplicate-member inflation.

    Returns (band, signals) where signals explains the score (for the audit/UI).
    """
    rows = conn.execute(
        "SELECT creator_wallet FROM wt_cluster_members WHERE cluster_id=?", (cluster_id,)
    ).fetchall()
    stored = len(rows)
    members = {r[0] for r in rows if r and r[0]}
    n = len(members)
    if n == 0:
        return VERY_LOW, {"reason": "no members"}
    dup_inflation = stored - n
    creators = list(members)
    ph = ",".join("?" * len(creators))

    # shared recipients (the real clustering glue), excluding placeholders
    shared = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_outgoing_transfers'").fetchone():
        for r, mc in conn.execute(
            f"SELECT recipient_address, COUNT(DISTINCT creator_address) FROM creator_outgoing_transfers "
            f"WHERE creator_address IN ({ph}) GROUP BY recipient_address", creators):
            if r and r not in PLACEHOLDERS and r not in mapped and mc >= 2:
                shared[r] = mc
    # shared funders (coordination on the funding side)
    shared_funders = 0
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_funders'").fetchone():
        for r, mc in conn.execute(
            f"SELECT funder_address, COUNT(DISTINCT creator_address) FROM creator_funders "
            f"WHERE creator_address IN ({ph}) GROUP BY funder_address", creators):
            if r and r not in PLACEHOLDERS and r not in mapped and mc >= 2:
                shared_funders += 1

    # how many members are connected by ANY real shared node (the coherent core)
    coherent_members = 0
    if shared:
        sph = ",".join("?" * len(shared))
        coherent_members = conn.execute(
            f"SELECT COUNT(DISTINCT creator_address) FROM creator_outgoing_transfers "
            f"WHERE creator_address IN ({ph}) AND recipient_address IN ({sph})",
            creators + list(shared.keys())).fetchone()[0]
    coherence = coherent_members / n if n else 0      # fraction bound by REAL structure

    # placeholder binding: members glued only by SYSTEM/UNKNOWN
    placeholder_bound = conn.execute(
        f"SELECT COUNT(DISTINCT creator_address) FROM creator_outgoing_transfers "
        f"WHERE creator_address IN ({ph}) AND recipient_address IN "
        f"({','.join('?'*len(PLACEHOLDERS))})", creators + list(PLACEHOLDERS)).fetchone()[0]

    # ── score ──
    score = 0
    score += min(40, len(shared) * 4)          # real shared recipients
    score += min(25, shared_funders * 5)       # shared funders
    score += min(20, (provisioners or 0) * 10) # provisioning signature
    score += int(coherence * 30)               # fraction bound by real structure
    # penalties
    if coherence < 0.25: score -= 25           # most members not really connected
    if placeholder_bound > n * 0.4: score -= 30  # majority glued by sentinels
    if dup_inflation > n * 0.2: score -= 10    # heavy duplicate inflation

    band = (VERY_HIGH if score >= 70 else HIGH if score >= 45 else
            MEDIUM if score >= 25 else LOW if score >= 10 else VERY_LOW)
    return band, {
        "score": score, "members": n, "stored_rows": stored, "dup_inflation": dup_inflation,
        "shared_recipients": len(shared), "shared_funders": shared_funders,
        "provisioners": provisioners or 0,
        "coherent_members": coherent_members, "coherence": round(coherence, 2),
        "placeholder_bound": placeholder_bound,
    }


def reservoir_frontier_quality(dormant, relays, uniform=True) -> tuple[str, dict]:
    """Reservoir quality = coordination of the relay-funded cohort. A synchronized,
    uniformly-funded cohort from known relays is HIGH structure even at small size."""
    if dormant <= 0:
        return VERY_LOW, {"reason": "empty"}
    score = 30                                  # relay-funded by construction
    if relays and relays <= 3: score += 30      # few relays = concentrated, coordinated
    if uniform: score += 25                     # uniform funding = synchronized cohort
    if dormant >= 20: score += 15               # a real cohort, not a couple of wallets
    band = (VERY_HIGH if score >= 70 else HIGH if score >= 45 else MEDIUM)
    return band, {"score": score, "dormant": dormant, "relays": relays, "uniform": uniform}


def discovery_priority(potential_yield, probability, quality_band) -> float:
    """
    Discovery Priority = Potential Yield × Discovery Probability × Frontier Quality.
    Quality is the multiplier that collapses large-but-incoherent frontiers and lifts
    small-but-structured ones. Probability None (reservoir) → treated as a mid 0.5 so
    a high-quality unknown still ranks, but its quality carries it, not raw size.
    """
    py = potential_yield or 0
    p = 0.5 if probability is None else probability
    qmult = {VERY_HIGH: 1.0, HIGH: 0.7, MEDIUM: 0.4, LOW: 0.15, VERY_LOW: 0.03}.get(quality_band, 0.3)
    return round(py * p * qmult, 1)


def evidence_expansion_potential() -> dict:
    """Per-evidence-type expansion potential (the *ceiling* a type can reach when its
    frontier is unmapped). Individual objects are still scored by actual coverage."""
    return {
        "direct_infrastructure": HIGH, "provisioning_hub": HIGH,
        "lineage": MEDIUM, "relay_funded": MEDIUM,
        "collector_flow": LOW, "fingerprint": LOW,
    }
