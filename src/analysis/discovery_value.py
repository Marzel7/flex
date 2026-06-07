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
    cps = set()
    for tbl, col in (("creator_funders", "funder_address"),
                     ("creator_outgoing_transfers", "recipient_address")):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone():
            continue
        for (addr,) in conn.execute(
            f"SELECT DISTINCT {col} FROM {tbl} WHERE creator_address IN ({ph})", creators):
            if addr:
                cps.add(addr)
    total = len(cps)
    if total == 0:
        return {"coverage": 1.0, "total_cp": 0, "unmapped_cp": 0, "has_frontier": False}
    unmapped = sum(1 for cp in cps if cp not in mapped)
    coverage = (total - unmapped) / total
    return {"coverage": round(coverage, 3), "total_cp": total,
            "unmapped_cp": unmapped, "has_frontier": unmapped > 0}


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


def evidence_expansion_potential() -> dict:
    """Per-evidence-type expansion potential (the *ceiling* a type can reach when its
    frontier is unmapped). Individual objects are still scored by actual coverage."""
    return {
        "direct_infrastructure": HIGH, "provisioning_hub": HIGH,
        "lineage": MEDIUM, "relay_funded": MEDIUM,
        "collector_flow": LOW, "fingerprint": LOW,
    }
