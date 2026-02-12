#!/usr/bin/env python3
"""
Detect coordinated funder behavior by analyzing shared counterparty addresses.

When multiple funders for the same creator share counterparty addresses in their
transaction histories, it suggests coordinated fund movement or shared infrastructure.

This script analyzes:
1. All funders for a creator
2. All their incoming/outgoing counterparty addresses
3. Finds overlaps - addresses that appear in multiple funders' histories
4. Reports on the coordination network
"""

import sqlite3
from typing import Dict, List, Set, Tuple
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"


def get_creator_funders(creator_address: str) -> List[Tuple[str, float]]:
    """Get all funders for a creator ordered by amount."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT funder_address, amount_sol
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
        """, (creator_address,))

        funders = [(row[0], row[1]) for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error getting funders: {e}")
        return []


def get_funder_counterparties(funder_address: str) -> Tuple[Set[str], Set[str]]:
    """Get all incoming senders and outgoing recipients for a funder."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Get inflows (who sent to this funder)
        cursor.execute("""
            SELECT DISTINCT sender_address FROM funder_incoming_transfers
            WHERE funder_address = ?
        """, (funder_address,))
        inflows = {row[0] for row in cursor.fetchall()}

        # Get outflows (who this funder sent to)
        cursor.execute("""
            SELECT DISTINCT recipient_address FROM funder_outgoing_transfers
            WHERE funder_address = ?
        """, (funder_address,))
        outflows = {row[0] for row in cursor.fetchall()}

        conn.close()
        return inflows, outflows
    except Exception as e:
        print(f"[DB] Error getting counterparties: {e}")
        return set(), set()


def analyze_creator_coordination(creator_address: str) -> None:
    """Analyze coordination between all funders for a creator."""
    funders = get_creator_funders(creator_address)

    if len(funders) < 2:
        print(f"[ANALYSIS] Creator {creator_address} has {len(funders)} funder(s) - need at least 2 for coordination analysis")
        return

    print(f"\n[COORDINATION ANALYSIS] Creator: {creator_address}")
    print(f"[COORDINATION ANALYSIS] Analyzing {len(funders)} funders\n")

    # Build a map of all counterparties and which funders they're connected to
    counterparty_to_funders: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)

    funder_data = {}

    for funder_addr, amount_sol in funders:
        inflows, outflows = get_funder_counterparties(funder_addr)
        funder_data[funder_addr] = {
            "amount_sol": amount_sol,
            "inflows": inflows,
            "outflows": outflows,
            "all_counterparties": inflows | outflows
        }

        # Track which funders connect to each counterparty
        for counterparty in inflows:
            counterparty_to_funders[counterparty].append((funder_addr, "inflow", amount_sol))

        for counterparty in outflows:
            counterparty_to_funders[counterparty].append((funder_addr, "outflow", amount_sol))

    # Find shared counterparties (coordination indicators)
    shared_counterparties = {
        addr: funders for addr, funders in counterparty_to_funders.items()
        if len(funders) >= 2
    }

    if not shared_counterparties:
        print("✓ No shared counterparties found - funders appear independent")
        return

    print(f"🔗 Found {len(shared_counterparties)} shared counterparty addresses\n")

    # Sort by number of funders connected
    sorted_shared = sorted(shared_counterparties.items(), key=lambda x: len(x[1]), reverse=True)

    for counterparty_addr, connected_funders in sorted_shared[:10]:
        funder_count = len(connected_funders)
        print(f"[SHARED] {counterparty_addr}")
        print(f"         Connected to {funder_count} funders:")

        for funder_addr, direction, funder_amount in connected_funders:
            arrow = "←" if direction == "inflow" else "→"
            print(f"         {arrow} {funder_addr[:20]}... ({funder_amount:.4f} SOL)")

        print()

    # Funder pairs analysis
    print(f"\n[FUNDER PAIRS] Funders with shared counterparties:\n")

    funder_list = list(funder_data.keys())
    pair_connections = defaultdict(list)

    for i, funder1 in enumerate(funder_list):
        for funder2 in funder_list[i + 1:]:
            shared = funder_data[funder1]["all_counterparties"] & funder_data[funder2]["all_counterparties"]
            if shared:
                pair_connections[(funder1, funder2)] = shared

    for (funder1, funder2), shared_addrs in sorted(pair_connections.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
        amount1 = funder_data[funder1]["amount_sol"]
        amount2 = funder_data[funder2]["amount_sol"]
        shared_count = len(shared_addrs)

        print(f"[PAIR] Funder 1: {funder1[:20]}... ({amount1:.4f} SOL)")
        print(f"[PAIR] Funder 2: {funder2[:20]}... ({amount2:.4f} SOL)")
        print(f"[PAIR] Shared counterparties: {shared_count}")

        # Show first 3 shared addresses
        for shared_addr in list(shared_addrs)[:3]:
            print(f"       - {shared_addr}")

        if len(shared_addrs) > 3:
            print(f"       ... and {len(shared_addrs) - 3} more")

        print()

    # Risk assessment
    print(f"\n[RISK ASSESSMENT]\n")

    if len(shared_counterparties) >= 3:
        print("🔴 HIGH RISK: Multiple shared counterparties suggest coordinated infrastructure")
    elif len(shared_counterparties) >= 1:
        print("🟡 MEDIUM RISK: Some shared counterparties detected")
    else:
        print("🟢 LOW RISK: Funders appear independent")

    # Check for hub patterns (one counterparty connecting many funders)
    hub_counterparties = {
        addr: funders for addr, funders in shared_counterparties.items()
        if len(funders) >= 3
    }

    if hub_counterparties:
        print(f"\n🌐 HUB PATTERN DETECTED: {len(hub_counterparties)} addresses act as hubs")
        print("   (connecting 3+ funders together)\n")

        for hub_addr, connected in sorted(hub_counterparties.items(), key=lambda x: len(x[1]), reverse=True)[:3]:
            print(f"   HUB: {hub_addr}")
            print(f"   Connected funders: {len(connected)}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python detect_coordinated_funders.py <creator_address>")
        sys.exit(1)

    creator_address = sys.argv[1]
    analyze_creator_coordination(creator_address)
