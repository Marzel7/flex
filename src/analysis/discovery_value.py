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

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
_BAND = {HIGH: 3, MEDIUM: 2, LOW: 1}


def band_rank(band: str) -> int:
    return _BAND.get(band, 0)


def _band_from_coverage(coverage: float, has_frontier: bool) -> str:
    """coverage 0..1 → band. No unexplored frontier at all → LOW (confirmation)."""
    if not has_frontier:
        return LOW
    if coverage <= 0.30:
        return HIGH
    if coverage <= 0.70:
        return MEDIUM
    return LOW


def _is_mapped_clause(conn) -> Optional[str]:
    """SQL fragment: a counterparty address is 'mapped' if it's known infra OR already
    a watchtower-attributed creator. Returns the WHERE-able subquery, or None if the
    supporting tables are absent."""
    has_infra = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='infra_wallets'").fetchone()
    if not has_infra:
        return None
    return ("(SELECT address FROM infra_wallets "
            "UNION SELECT creator_address FROM creator_risk_scores WHERE watchtower_related=1)")


def attribution_coverage(conn: sqlite3.Connection, creator_address: str) -> dict:
    """
    Coverage for an attributed creator = fraction of its UPSTREAM funders that are
    already mapped (known infra or already attributed). The frontier is the unmapped
    upstream funders + unexplored sibling creators those funders touch.

    Returns {coverage, total_upstream, unmapped_upstream, has_frontier}.
    """
    mapped = _is_mapped_clause(conn)
    has_cf = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_funders'").fetchone()
    if not has_cf or mapped is None:
        return {"coverage": 0.0, "total_upstream": 0, "unmapped_upstream": 0, "has_frontier": False}
    total = conn.execute(
        "SELECT COUNT(DISTINCT funder_address) FROM creator_funders WHERE creator_address=?",
        (creator_address,)).fetchone()[0]
    if total == 0:
        # zero upstream funders → clicking shows only creator→token→launch → CONFIRMATION
        return {"coverage": 1.0, "total_upstream": 0, "unmapped_upstream": 0, "has_frontier": False}
    unmapped = conn.execute(
        f"SELECT COUNT(DISTINCT funder_address) FROM creator_funders "
        f"WHERE creator_address=? AND funder_address NOT IN {mapped}",
        (creator_address,)).fetchone()[0]
    coverage = (total - unmapped) / total
    return {"coverage": round(coverage, 3), "total_upstream": total,
            "unmapped_upstream": unmapped, "has_frontier": unmapped > 0}


def attribution_discovery_value(conn, creator_address, linkage) -> tuple[str, str, dict]:
    """(band, reveal_text, coverage_info) for an attributed creator — coverage-driven."""
    cov = attribution_coverage(conn, creator_address)
    band = _band_from_coverage(cov["coverage"], cov["has_frontier"])
    if not cov["has_frontier"]:
        reveal = "upstream fully mapped — confirmation only (creator → token → launch)"
    else:
        n = cov["unmapped_upstream"]
        reveal = f"{n} unmapped upstream funder{'s' if n != 1 else ''} — may reveal new infra + sibling creators"
    return band, reveal, cov


def cluster_coverage(conn: sqlite3.Connection, cluster_id: int) -> dict:
    """
    Coverage for an emerging cluster = fraction of its members' counterparties
    (funders + recipients) that are already mapped. Unexplained shared funders /
    payout paths / corridors are the frontier.
    """
    mapped = _is_mapped_clause(conn)
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
    unmapped = set()
    for tbl, col in (("creator_funders", "funder_address"),
                     ("creator_outgoing_transfers", "recipient_address")):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone():
            continue
        for (addr,) in conn.execute(
            f"SELECT DISTINCT {col} FROM {tbl} WHERE creator_address IN ({ph})", creators):
            if not addr:
                continue
            cps.add(addr)
        for (addr,) in conn.execute(
            f"SELECT DISTINCT {col} FROM {tbl} WHERE creator_address IN ({ph}) "
            f"AND {col} NOT IN {mapped}", creators):
            if addr:
                unmapped.add(addr)
    total = len(cps)
    if total == 0:
        return {"coverage": 1.0, "total_cp": 0, "unmapped_cp": 0, "has_frontier": False}
    coverage = (total - len(unmapped)) / total
    return {"coverage": round(coverage, 3), "total_cp": total,
            "unmapped_cp": len(unmapped), "has_frontier": len(unmapped) > 0}


def cluster_discovery_value(conn, cluster_id, provisioners=0, growing=False) -> tuple[str, str, dict]:
    """Coverage-driven, with growth/provisioning as upgrades (a growing cluster with a
    provisioning signature is a stronger lead even at similar coverage)."""
    cov = cluster_coverage(conn, cluster_id)
    band = _band_from_coverage(cov["coverage"], cov["has_frontier"])
    # upgrades: active expansion signals push a MEDIUM toward HIGH
    if band == MEDIUM and (growing or provisioners):
        band = HIGH
    if not cov["has_frontier"]:
        reveal = "counterparties fully mapped — limited new ground"
    else:
        n = cov["unmapped_cp"]
        extra = []
        if provisioners: extra.append("provisioning signature")
        if growing: extra.append("growing")
        tail = (" · " + " · ".join(extra)) if extra else ""
        reveal = f"{n} unexplained counterpart{'ies' if n != 1 else 'y'} — may expose unattributed infra{tail}"
    return band, reveal, cov


def reservoir_discovery_value(dormant: int, converted: int) -> tuple[str, str, dict]:
    """
    Reservoir = genuinely forward-looking. The first conversion reveals
    relay → creator → funding lineage → new infrastructure, before provisioning-hub
    detection fires. Unconverted pool = 0% coverage = HIGH. This is by design one of
    the highest-value discovery objects in the system.
    """
    if dormant <= 0:
        return LOW, "no dormant wallets to convert", {"coverage": 1.0, "has_frontier": False}
    total = dormant + converted
    coverage = round(converted / total, 3) if total else 0.0
    if converted == 0:
        return HIGH, ("first conversion may reveal relay → creator → funding lineage → "
                      "new infrastructure"), {"coverage": 0.0, "has_frontier": True}
    band = _band_from_coverage(coverage, True)
    return band, "conversions in progress — each reveals a new launch lineage", \
        {"coverage": coverage, "has_frontier": True}


def evidence_expansion_potential() -> dict:
    """Per-evidence-type expansion potential (the *ceiling* a type can reach when its
    frontier is unmapped). Individual objects are still scored by actual coverage."""
    return {
        "direct_infrastructure": HIGH, "provisioning_hub": HIGH,
        "lineage": MEDIUM, "relay_funded": MEDIUM,
        "collector_flow": LOW, "fingerprint": LOW,
    }
