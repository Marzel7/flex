"""
FLEX Webhook Creator Ranker
Scores creators based on webhook-extracted activity and traditional signals

Feeds real-time SOL transfer data into creator ranking system:
- Activity metrics (tx frequency, SOL volumes)
- Risk patterns (self-funding, distribution)
- Network membership (coordinated, C2C)
- Token creation behavior
- Historical data integration

Author: Claude Code
Date: 2026-03-03
"""

import sqlite3
from src.utils.db_locking import db_connect
import os
import time
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta


DB_PATH = os.getenv("FLEX_DB_PATH", "flex_complete_database.db")


# ============================================================================
# SCORING CONSTANTS
# ============================================================================

SCORING_WEIGHTS = {
    # Activity scores
    "active_5m": 40,
    "active_1h": 25,
    "high_volume_tx": 15,
    "high_value_sol": 20,

    # Risk patterns (negative/red flags)
    "self_funding": -30,
    "distribution_pattern": -25,
    "high_concentration": -20,

    # Network/coordination
    "coordinated_edge": -35,
    "c2c_network": -30,
    "network_member": -20,
    "funding_chain": -15,

    # Token behavior
    "multi_token_creator": -25,  # Launched multiple tokens
    "rapid_token_launches": -40,  # Multiple tokens in short time
    "token_with_issues": -20,  # Token has risk flags

    # Wallet behavior
    "dust_transfers": -10,
    "high_recipient_diversity": 10,
    "consistent_patterns": -15,
}

RISK_THRESHOLDS = {
    "critical": 80,    # Score >= 80: Critical risk
    "elevated": 60,    # Score >= 60: Elevated risk
    "moderate": 40,    # Score >= 40: Moderate risk
    "low": 0,          # Score < 40: Low risk
}


# ============================================================================
# DATABASE UTILITIES
# ============================================================================

def get_ranker_db():
    """Create optimized database connection for ranking."""
    conn = db_connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# ACTIVITY SCORING
# ============================================================================

def score_creator_activity(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score creator based on recent activity from webhook data.

    Checks address_activity table for:
    - Transaction frequency (5m, 1h)
    - SOL volumes (in/out)
    - Recency

    Returns:
        Tuple of (score, reasons)
    """
    score = 0
    reasons = []

    cur = conn.cursor()

    cur.execute("""
        SELECT
            last_seen_at,
            tx_5m, tx_1h,
            sol_in_5m, sol_in_1h,
            sol_out_5m, sol_out_1h
        FROM address_activity
        WHERE address = ?
    """, (address,))

    row = cur.fetchone()
    if not row:
        return (0, ["no_recent_activity"])

    tx_5m = row[1]
    tx_1h = row[2]
    sol_in_1h = row[4]
    sol_out_1h = row[6]

    # Recency bonus
    if tx_5m and tx_5m > 0:
        score += SCORING_WEIGHTS["active_5m"]
        reasons.append(f"active_5m({tx_5m}tx)")

    if tx_1h and tx_1h >= 5:
        score += SCORING_WEIGHTS["high_volume_tx"]
        reasons.append(f"high_volume({tx_1h}tx/1h)")

    # Volume bonus
    if (sol_in_1h + sol_out_1h) > 10.0:
        score += SCORING_WEIGHTS["high_value_sol"]
        reasons.append(f"high_value({(sol_in_1h + sol_out_1h):.2f}SOL)")

    return (score, reasons)


# ============================================================================
# RISK PATTERN SCORING
# ============================================================================

def score_self_funding_risk(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score self-funding risk pattern.

    High self-funding percentage = suspicious behavior
    """
    score = 0
    reasons = []

    cur = conn.cursor()

    cur.execute("""
        SELECT
            is_self_funding,
            self_funding_percentage,
            self_funding_intermediates,
            total_funders
        FROM creator_self_funding
        WHERE creator_address = ?
    """, (address,))

    row = cur.fetchone()
    if row and row[0]:  # is_self_funding
        pct = row[1] or 0
        intermediates = row[2] or 0

        score += SCORING_WEIGHTS["self_funding"]

        # Scale penalty by percentage
        if pct > 80:
            score -= 30  # Extra penalty for very high self-funding
            reasons.append(f"self_funding_extreme({pct:.0f}%)")
        elif pct > 50:
            reasons.append(f"self_funding_high({pct:.0f}%)")
        else:
            reasons.append(f"self_funding_moderate({pct:.0f}%)")

    return (score, reasons)


def score_distribution_pattern(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score distribution pattern risk.

    Many recipients from few sources = money laundering risk
    """
    score = 0
    reasons = []

    cur = conn.cursor()

    # Get outgoing transfer stats
    cur.execute("""
        SELECT
            COUNT(*) as outgoing_count,
            COUNT(DISTINCT destination) as recipient_count
        FROM sol_transfers
        WHERE source = ?
    """, (address,))

    row = cur.fetchone()
    if row:
        outgoing_count = row[0]
        recipient_count = row[1]

        # Ratio: many recipients from few transfers
        if outgoing_count > 0:
            recipient_ratio = recipient_count / outgoing_count
            if recipient_ratio > 0.8 and outgoing_count > 5:
                score += SCORING_WEIGHTS["distribution_pattern"]
                reasons.append(f"distribution({recipient_count}recipients/{outgoing_count}transfers)")

    return (score, reasons)


def score_concentration_risk(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score concentration risk.

    Heavy concentration in single addresses = suspicious
    """
    score = 0
    reasons = []

    cur = conn.cursor()

    # Find concentration of transfers
    cur.execute("""
        SELECT
            destination,
            SUM(amount_sol) as total_sent
        FROM sol_transfers
        WHERE source = ?
        GROUP BY destination
        ORDER BY total_sent DESC
        LIMIT 1
    """, (address,))

    top_recipient = cur.fetchone()

    if top_recipient:
        # Get total sent
        cur.execute("""
            SELECT SUM(amount_sol) as total FROM sol_transfers WHERE source = ?
        """, (address,))
        total_sent = cur.fetchone()[0] or 0

        if total_sent > 0:
            top_concentration = (top_recipient[1] or 0) / total_sent
            if top_concentration > 0.5:  # >50% to single address
                score += SCORING_WEIGHTS["high_concentration"]
                reasons.append(f"concentration({top_concentration:.0%} to one addr)")

    return (score, reasons)


# ============================================================================
# NETWORK SCORING
# ============================================================================

def score_network_membership(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score based on network membership and coordination.

    Membership in coordinated networks = higher risk
    """
    score = 0
    reasons = []

    cur = conn.cursor()

    # Check coordinated edges (optional - may not exist in webhook-only systems)
    try:
        cur.execute("""
            SELECT COUNT(*) as coordinated_count
            FROM coordinated_creator_edges
            WHERE creator_a = ? OR creator_b = ?
        """, (address, address))

        coordinated_count = cur.fetchone()[0] or 0
        if coordinated_count > 0:
            score += SCORING_WEIGHTS["coordinated_edge"]
            reasons.append(f"coordinated({coordinated_count}edges)")
    except:
        pass

    # Check C2C networks (optional - may not exist in webhook-only systems)
    try:
        cur.execute("""
            SELECT COUNT(DISTINCT network_name) as network_count
            FROM creator_to_creator_networks
            WHERE creator_address = ?
        """, (address,))

        c2c_count = cur.fetchone()[0] or 0
        if c2c_count > 0:
            score += SCORING_WEIGHTS["c2c_network"]
            reasons.append(f"c2c_network({c2c_count})")
    except:
        pass

    # Check funding network membership (optional - may not exist in webhook-only systems)
    try:
        cur.execute("""
            SELECT COUNT(DISTINCT network_id) as network_count
            FROM funding_network_members
            WHERE funder_address = ?
        """, (address,))

        funding_net_count = cur.fetchone()[0] or 0
        if funding_net_count > 0:
            score += SCORING_WEIGHTS["network_member"]
            reasons.append(f"funding_network({funding_net_count})")
    except:
        pass

    # Check funding chains (optional - may not exist in webhook-only systems)
    try:
        cur.execute("""
            SELECT COUNT(*) as chain_count FROM funding_chains
            WHERE source_creator = ? OR dest_creator = ?
        """, (address, address))

        chain_count = cur.fetchone()[0] or 0
        if chain_count > 0:
            score += SCORING_WEIGHTS["funding_chain"]
            reasons.append(f"funding_chain({chain_count})")
    except:
        pass

    return (score, reasons)


# ============================================================================
# TOKEN BEHAVIOR SCORING
# ============================================================================

def score_token_behavior(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score based on token creation behavior.

    Multiple token launches = potential pump-and-dump pattern
    """
    score = 0
    reasons = []

    cur = conn.cursor()

    # Count tokens created
    cur.execute("""
        SELECT COUNT(DISTINCT mint) as token_count FROM token_analysis
        WHERE earliest_tx_creator = ?
    """, (address,))

    token_count = cur.fetchone()[0] or 0

    if token_count >= 2:
        score += SCORING_WEIGHTS["multi_token_creator"]
        reasons.append(f"multi_token({token_count})")

        # Check if launches were rapid (all within 24h)
        cur.execute("""
            SELECT
                MIN(analyzed_at) as first_launch,
                MAX(analyzed_at) as latest_launch
            FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (address,))

        launch_row = cur.fetchone()
        if launch_row and launch_row[0] and launch_row[1]:
            first = launch_row[0]
            latest = launch_row[1]
            time_diff = latest - first

            if time_diff < 86400 and token_count >= 3:  # < 1 day
                score += SCORING_WEIGHTS["rapid_token_launches"]
                reasons.append(f"rapid_launches({token_count}in{time_diff}s)")

    # Check token risk levels
    cur.execute("""
        SELECT COUNT(*) as risky_count FROM token_analysis
        WHERE earliest_tx_creator = ? AND risk_level IN ('critical', 'high')
    """, (address,))

    risky_token_count = cur.fetchone()[0] or 0
    if risky_token_count > 0:
        score += SCORING_WEIGHTS["token_with_issues"]
        reasons.append(f"risky_tokens({risky_token_count})")

    return (score, reasons)


# ============================================================================
# COMPREHENSIVE SCORING
# ============================================================================

def compute_creator_risk_score(conn: sqlite3.Connection, address: str) -> Dict:
    """
    Compute comprehensive risk score for a creator.

    Combines:
    - Activity metrics
    - Self-funding risk
    - Distribution patterns
    - Network membership
    - Token behavior

    Returns:
        Dict with score, risk_level, component_scores, reasons
    """
    total_score = 0
    all_reasons = []
    component_scores = {}

    # ---- Activity Scoring ----
    activity_score, activity_reasons = score_creator_activity(conn, address)
    total_score += activity_score
    component_scores["activity"] = activity_score
    all_reasons.extend(activity_reasons)

    # ---- Risk Pattern Scoring ----
    self_fund_score, self_fund_reasons = score_self_funding_risk(conn, address)
    total_score += self_fund_score
    component_scores["self_funding"] = self_fund_score
    all_reasons.extend(self_fund_reasons)

    distribution_score, dist_reasons = score_distribution_pattern(conn, address)
    total_score += distribution_score
    component_scores["distribution"] = distribution_score
    all_reasons.extend(dist_reasons)

    concentration_score, conc_reasons = score_concentration_risk(conn, address)
    total_score += concentration_score
    component_scores["concentration"] = concentration_score
    all_reasons.extend(conc_reasons)

    # ---- Network Scoring ----
    network_score, network_reasons = score_network_membership(conn, address)
    total_score += network_score
    component_scores["network"] = network_score
    all_reasons.extend(network_reasons)

    # ---- Token Behavior Scoring ----
    token_score, token_reasons = score_token_behavior(conn, address)
    total_score += token_score
    component_scores["token_behavior"] = token_score
    all_reasons.extend(token_reasons)

    # Determine risk level
    risk_level = "low"
    for level, threshold in sorted(RISK_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
        if total_score >= threshold:
            risk_level = level
            break

    return {
        "address": address,
        "score": total_score,
        "risk_level": risk_level,
        "component_scores": component_scores,
        "reasons": all_reasons,
        "computed_at": datetime.utcnow().isoformat(),
    }


# ============================================================================
# CREATOR ENRICHMENT (for API responses)
# ============================================================================

def enrich_creator_with_risk_score(conn: sqlite3.Connection, creator_data: Dict) -> Dict:
    """
    Add risk score and components to creator data dict.

    Used to enrich /api/creator-recent-checks responses
    """
    address = creator_data.get("creator_address")
    if not address:
        return creator_data

    risk_score = compute_creator_risk_score(conn, address)

    creator_data["risk_score"] = risk_score["score"]
    creator_data["risk_level"] = risk_score["risk_level"]
    creator_data["component_scores"] = risk_score["component_scores"]
    creator_data["risk_reasons"] = risk_score["reasons"]

    return creator_data


def get_top_risk_creators(limit: int = 20) -> List[Dict]:
    """
    Get top-scoring (riskiest) creators based on webhook activity.

    Returns:
        List of creator dicts with risk scores, sorted by score DESC
    """
    conn = get_ranker_db()
    cur = conn.cursor()

    # Get creators with recent activity (from sol_transfers)
    cur.execute("""
        SELECT DISTINCT source as creator_address
        FROM sol_transfers
        WHERE received_at > datetime('now', '-1 hour')
        UNION
        SELECT DISTINCT destination
        FROM sol_transfers
        WHERE received_at > datetime('now', '-1 hour')
    """)

    addresses = [row[0] for row in cur.fetchall()]

    creators = []
    for addr in addresses:
        risk_score = compute_creator_risk_score(conn, addr)

        # Get token count
        cur.execute("""
            SELECT COUNT(DISTINCT mint) FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (addr,))
        token_count = cur.fetchone()[0] or 0

        creators.append({
            "creator_address": addr,
            "risk_score": risk_score["score"],
            "risk_level": risk_score["risk_level"],
            "token_count": token_count,
            "component_scores": risk_score["component_scores"],
            "risk_reasons": risk_score["reasons"],
        })

    conn.close()

    # Sort by score (highest/riskiest first)
    creators.sort(key=lambda x: x["risk_score"], reverse=True)

    return creators[:limit]


# ============================================================================
# RANKING UPDATE (called by webhook worker)
# ============================================================================

def update_creator_rankings_batch(addresses: List[str]) -> List[Dict]:
    """
    Update rankings for a batch of addresses.

    Called by webhook_worker.py after processing addresses

    Returns:
        List of updated creator risk profiles
    """
    conn = get_ranker_db()

    results = []
    for addr in addresses:
        try:
            risk_score = compute_creator_risk_score(conn, addr)
            results.append(risk_score)
            print(f"[RANKER] {addr[:8]}... - score={risk_score['score']}, level={risk_score['risk_level']}", flush=True)
        except Exception as e:
            print(f"[RANKER] Error scoring {addr[:8]}...: {e}", flush=True)
            continue

    conn.close()

    return results


# ============================================================================
# API ENDPOINT HELPER
# ============================================================================

def enrich_creator_recent_checks(recent_checks_list: List[Dict]) -> List[Dict]:
    """
    Enrich the /api/creator-recent-checks response with risk scores.

    Takes the output from api_creator_recent_checks() and adds risk data
    """
    conn = get_ranker_db()

    enriched = []
    for creator_data in recent_checks_list:
        enriched_data = enrich_creator_with_risk_score(conn, creator_data)
        enriched.append(enriched_data)

    conn.close()

    # Sort by risk score (highest first)
    enriched.sort(key=lambda x: x.get("risk_score", 0), reverse=True)

    return enriched


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    """Test the ranker"""
    conn = get_ranker_db()

    # Test a sample address
    test_addr = "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ"

    result = compute_creator_risk_score(conn, test_addr)

    print(f"\nCreator Risk Score Analysis")
    print(f"=" * 60)
    print(f"Address: {result['address']}")
    print(f"Score: {result['score']}")
    print(f"Risk Level: {result['risk_level']}")
    print(f"\nComponent Scores:")
    for component, score in result["component_scores"].items():
        print(f"  {component}: {score:+d}")
    print(f"\nReasons:")
    for reason in result["reasons"]:
        print(f"  - {reason}")

    conn.close()
