"""
Funder Watchlist Builder

Identifies and scores funders for webhook monitoring based on:
1. Rugged creator funding (funded creators that later rugged)
2. Multi-creator funding (funds many creators in short time)
3. Fingerprint cluster membership (in cluster with malicious wallets)
4. CEX/infra wallet detection (penalize known CEX)

Run periodically (e.g., every 6 hours) to update watchlist.
Usage: python funder_watchlist_builder.py
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

DB_PATH = "flex_complete_database.db"

# Risk tier assignments (webhook grouping)
RISK_TIERS = {
    "CRITICAL": (800, 1000),
    "HIGH": (500, 799),
    "MEDIUM": (200, 499),
    "LOW": (0, 199),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_funder_risk_score(conn: sqlite3.Connection, funder_address: str) -> Tuple[int, List[str]]:
    """
    Compute risk score for a funder (0-1000).
    Returns: (score, list of reasons)
    """
    reasons = []
    score = 0

    # Rule 1: Check if funder funded rugged creators
    cursor = conn.execute("""
        SELECT COUNT(DISTINCT cf.creator_address) as rugged_creator_count
        FROM creator_funders cf
        JOIN creator_blocklist cb ON cf.creator_address = cb.creator_address
        WHERE cf.funder_address = ?
    """, (funder_address,))

    row = cursor.fetchone()
    rugged_count = row[0] if row else 0

    if rugged_count >= 6:
        score += 400
        reasons.append(f"Funded {rugged_count} rugged creators")
    elif rugged_count >= 3:
        score += 200
        reasons.append(f"Funded {rugged_count} rugged creators")
    elif rugged_count >= 1:
        score += 80
        reasons.append(f"Funded {rugged_count} rugged creator(s)")

    # Rule 2: Check multi-creator funding (many creators)
    cursor = conn.execute("""
        SELECT COUNT(DISTINCT creator_address) as creator_count
        FROM creator_funders
        WHERE funder_address = ?
    """, (funder_address,))

    row = cursor.fetchone()
    creator_count = row[0] if row else 0

    if creator_count >= 50:
        score += 300
        reasons.append(f"Funds {creator_count} distinct creators (hub behavior)")
    elif creator_count >= 20:
        score += 150
        reasons.append(f"Funds {creator_count} distinct creators")
    elif creator_count >= 10:
        score += 60
        reasons.append(f"Funds {creator_count} creators")

    # Rule 3: Check fingerprint cluster membership (in cluster with malicious)
    try:
        cursor = conn.execute("""
            SELECT COUNT(*) as malicious_in_cluster
            FROM super_clusters sc1
            JOIN creator_blocklist cb ON sc1.creator_address = cb.creator_address
            WHERE sc1.cluster_id = (
                SELECT cluster_id FROM super_clusters WHERE creator_address = ? LIMIT 1
            ) AND sc1.creator_address != ?
        """, (funder_address, funder_address))

        row = cursor.fetchone()
        malicious_count = row[0] if row else 0

        if malicious_count >= 3:
            score += 250
            reasons.append(f"In cluster with {malicious_count} blocklisted creators")
    except:
        pass  # super_clusters may not exist yet

    # Rule 4: Check if funder is marked as CEX/infra (reduce score)
    try:
        cursor = conn.execute("""
            SELECT is_cex FROM creator_funders WHERE funder_address = ? LIMIT 1
        """, (funder_address,))
        row = cursor.fetchone()
        if row and row[0]:
            score = max(0, score - 200)  # Penalize CEX wallets
            reasons.append("(CEX/infra wallet - score reduced)")
    except:
        pass

    # Cap score at 1000
    score = min(score, 1000)

    return score, reasons


def assign_to_webhook_group(score: int) -> str:
    """Assign funder to webhook group based on risk score."""
    for tier, (min_score, max_score) in RISK_TIERS.items():
        if min_score <= score <= max_score:
            return tier
    return "LOW"


def ensure_webhook_groups(conn: sqlite3.Connection):
    """Ensure webhook groups exist."""
    cursor = conn.cursor()
    for tier in RISK_TIERS.keys():
        cursor.execute("""
            INSERT OR IGNORE INTO funder_webhook_groups
            (webhook_group_id, description, is_active)
            VALUES (?, ?, 1)
        """, (tier, f"{tier} Risk Funder Tier"))
    conn.commit()


def rebuild_funder_watchlist():
    """
    Rebuild funder watchlist from scratch.
    Called periodically (e.g., every 6 hours) to update scores and assignments.
    """
    conn = get_db()
    cursor = conn.cursor()

    print("[WATCHLIST_BUILDER] 🚀 Starting watchlist rebuild...", flush=True)

    # Ensure webhook groups exist
    ensure_webhook_groups(conn)

    # Get all known funders
    cursor.execute("SELECT DISTINCT funder_address FROM creator_funders")
    funders = [row[0] for row in cursor.fetchall()]

    print(f"[WATCHLIST_BUILDER] Scoring {len(funders)} funders...", flush=True)

    updated_count = 0
    added_count = 0

    for funder_address in funders:
        score, reasons = compute_funder_risk_score(conn, funder_address)

        # Only add if score is above threshold (> 50)
        if score > 50:
            webhook_group = assign_to_webhook_group(score)

            # Check if already exists
            cursor.execute("""
                SELECT funder_address FROM funder_watchlist WHERE funder_address = ?
            """, (funder_address,))
            exists = cursor.fetchone()

            if exists:
                cursor.execute("""
                    UPDATE funder_watchlist
                    SET risk_score = ?, risk_reasons = ?, webhook_group_id = ?,
                        last_updated_at = CURRENT_TIMESTAMP
                    WHERE funder_address = ?
                """, (score, json.dumps(reasons), webhook_group, funder_address))
                updated_count += 1
            else:
                cursor.execute("""
                    INSERT INTO funder_watchlist
                    (funder_address, risk_score, risk_reasons, webhook_group_id, is_active)
                    VALUES (?, ?, ?, ?, 1)
                """, (funder_address, score, json.dumps(reasons), webhook_group))
                added_count += 1

    conn.commit()
    print(f"[WATCHLIST_BUILDER] ✅ Watchlist rebuilt: {added_count} added, {updated_count} updated", flush=True)

    # Summarize by tier
    for tier in RISK_TIERS.keys():
        cursor.execute("""
            SELECT COUNT(*) FROM funder_watchlist
            WHERE webhook_group_id = ? AND is_active = 1
        """, (tier,))
        count = cursor.fetchone()[0]
        print(f"[WATCHLIST_BUILDER]   {tier}: {count} funders", flush=True)

    conn.close()


if __name__ == "__main__":
    rebuild_funder_watchlist()
