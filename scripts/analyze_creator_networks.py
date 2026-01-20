#!/usr/bin/env python3
"""
Analyze creator networks to detect coordinated rug-pulling rings.

Identifies:
1. Creators sharing SOL destination wallets (likely same person/team)
2. Network sizes and connection strength
3. Risk levels based on connected malicious creators
4. Flags creators connected to known ruggers
"""

import sqlite3
import json
from collections import defaultdict
from pathlib import Path

DB_PATH = "pumpswap_tokens.db"


def get_all_blocked_creators():
    """Get all blocked creators and their reputations"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT creator_address, reputation, rug_count
            FROM creator_blocklist
            ORDER BY rug_count DESC
        """)
        blocked = {row[0]: {"reputation": row[1], "rug_count": row[2]} for row in cursor.fetchall()}
        conn.close()
        return blocked
    except Exception as e:
        print(f"[ERROR] Failed to get blocked creators: {e}")
        return {}


def build_creator_networks():
    """Analyze SOL transfer patterns to build creator networks"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Get all SOL transfer destinations by creator
        cursor.execute("""
            SELECT creator_address, destination_address
            FROM creator_sol_transfers
            WHERE transfer_count > 0
            ORDER BY creator_address
        """)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("[NETWORK] No SOL transfer data available yet")
            return {}

        # Build network graph - creators sharing destinations
        creator_destinations = defaultdict(set)
        destination_creators = defaultdict(set)

        for creator, destination in rows:
            creator_destinations[creator].add(destination)
            destination_creators[destination].add(creator)

        # Find connected creators (sharing at least one destination)
        networks = {}
        visited = set()

        for creator in creator_destinations:
            if creator in visited:
                continue

            # BFS to find all connected creators
            network = set()
            queue = [creator]
            visited.add(creator)

            while queue:
                current = queue.pop(0)
                network.add(current)

                # Find creators sharing destinations with current
                for dest in creator_destinations[current]:
                    for connected in destination_creators[dest]:
                        if connected not in visited:
                            visited.add(connected)
                            queue.append(connected)

            if len(network) > 1:
                networks[creator] = list(network)

        return networks

    except Exception as e:
        print(f"[ERROR] Failed to build networks: {e}")
        return {}


def analyze_networks_risk():
    """Analyze networks and assign risk levels based on blocked creators"""
    blocked_creators = get_all_blocked_creators()
    networks = build_creator_networks()

    if not networks:
        print("\n[NETWORK] No creator networks detected yet")
        print("[NETWORK] SOL transfer tracking will be enabled in future versions")
        return

    print(f"\n[NETWORK] Found {len(networks)} creator networks")
    print("=" * 120)

    malicious_networks = []
    suspicious_networks = []
    clean_networks = []

    for creator, connected_creators in networks.items():
        # Check if any connected creators are blocked
        blocked_in_network = [c for c in connected_creators if c in blocked_creators]
        malicious_in_network = [c for c in blocked_in_network
                               if blocked_creators[c]["reputation"] == "MALICIOUS"]

        # Determine risk level
        if blocked_in_network:
            if malicious_in_network:
                risk_level = "CRITICAL"
                malicious_networks.append((creator, len(connected_creators), len(blocked_in_network)))
                flag = "🚨 CRITICAL"
            else:
                risk_level = "HIGH"
                suspicious_networks.append((creator, len(connected_creators), len(blocked_in_network)))
                flag = "⚠️ HIGH"
        else:
            risk_level = "CLEAN"
            clean_networks.append((creator, len(connected_creators)))
            flag = "🟢 CLEAN"

        # Display network info
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        print(f"\n{flag} {creator_short}")
        print(f"   Network Size: {len(connected_creators)} creators")
        if blocked_in_network:
            print(f"   Blocked Members: {len(blocked_in_network)}")
            print(f"   Risk Level: {risk_level}")

        # Update database with network info
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Update creator_networks table
            cursor.execute("""
                INSERT OR REPLACE INTO creator_networks
                (creator_address, connected_creators, shared_destinations, network_size, network_risk_level, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                creator,
                json.dumps(connected_creators),
                json.dumps([]),  # Will populate with shared destinations in future
                len(connected_creators),
                risk_level
            ))

            # Update creator_blocklist if creator is blocked
            if creator in blocked_creators:
                cursor.execute("""
                    UPDATE creator_blocklist
                    SET connected_to_malicious = ?,
                        network_members = ?
                    WHERE creator_address = ?
                """, (
                    1 if blocked_in_network else 0,
                    json.dumps(blocked_in_network),
                    creator
                ))

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"   [DB ERROR] {e}")

    # Summary
    print("\n" + "=" * 120)
    print(f"\n[SUMMARY] Network Analysis Results:")
    print(f"  Total Networks: {len(networks)}")
    print(f"  🚨 CRITICAL (connected to malicious): {len(malicious_networks)}")
    for creator, size, blocked_count in malicious_networks:
        print(f"     - {creator[:8]}...: {size} creators, {blocked_count} blocked")

    print(f"  ⚠️ HIGH RISK (connected to suspicious): {len(suspicious_networks)}")
    for creator, size, blocked_count in suspicious_networks:
        print(f"     - {creator[:8]}...: {size} creators, {blocked_count} suspicious")

    print(f"  🟢 CLEAN (no blocked members): {len(clean_networks)}")


if __name__ == "__main__":
    print("[NETWORK] Starting creator network analysis...")
    analyze_networks_risk()
    print("\n[NETWORK] Analysis complete")
