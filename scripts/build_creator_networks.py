#!/usr/bin/env python3
"""
Build creator networks from SOL transfer patterns.

This script:
1. Reads SOL transfer data from creator_sol_transfers
2. Identifies creators sharing SOL destinations
3. Builds network graphs (creators → shared destinations)
4. Flags networks containing malicious creators
5. Updates blocklist with network information
"""

import sqlite3
import json
from collections import defaultdict
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"


class CreatorNetworkBuilder:
    """Build creator networks from SOL transfers"""

    def __init__(self):
        self.creator_destinations = defaultdict(set)
        self.destination_creators = defaultdict(set)
        self.networks = {}
        self.blocked_creators = {}

    def load_sol_transfers(self) -> bool:
        """Load SOL transfer data from database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='creator_sol_transfers'
            """)
            if not cursor.fetchone():
                print("[NETWORK] ⏳ creator_sol_transfers table not found")
                print("[NETWORK]    Run: python scripts/extract_sol_transfers.py")
                conn.close()
                return False

            # Load transfers
            cursor.execute("""
                SELECT creator_address, destination_address
                FROM creator_sol_transfers
                WHERE transfer_count > 0
            """)

            transfers = cursor.fetchall()
            conn.close()

            if not transfers:
                print("[NETWORK] ⏳ No SOL transfer data found")
                return False

            print(f"[NETWORK] Loaded {len(transfers)} SOL transfer records")

            # Build index
            for creator, destination in transfers:
                self.creator_destinations[creator].add(destination)
                self.destination_creators[destination].add(creator)

            return True

        except Exception as e:
            print(f"[ERROR] Failed to load SOL transfers: {e}")
            return False

    def load_blocked_creators(self):
        """Load blocked creators from database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT creator_address, reputation, rug_count
                FROM creator_blocklist
            """)

            for creator, reputation, rug_count in cursor.fetchall():
                self.blocked_creators[creator] = {
                    "reputation": reputation,
                    "rug_count": rug_count
                }

            conn.close()
            print(f"[NETWORK] Loaded {len(self.blocked_creators)} blocked creators")

        except Exception as e:
            print(f"[ERROR] Failed to load blocked creators: {e}")

    def build_networks(self):
        """Build creator networks using BFS"""
        print(f"[NETWORK] Building creator networks...")

        visited = set()

        for creator in self.creator_destinations:
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
                for dest in self.creator_destinations[current]:
                    for connected in self.destination_creators[dest]:
                        if connected not in visited:
                            visited.add(connected)
                            queue.append(connected)

            # Store network if size > 1
            if len(network) > 1:
                self.networks[creator] = list(network)

        print(f"[NETWORK] Found {len(self.networks)} creator networks")

    def analyze_network_risk(self):
        """Analyze networks for risk levels"""
        print(f"[NETWORK] Analyzing network risks...")

        high_risk_networks = []
        critical_networks = []

        for creator, members in self.networks.items():
            # Check if any members are blocked
            blocked_members = [m for m in members if m in self.blocked_creators]

            if not blocked_members:
                continue

            # Check if any are malicious (2+ rugs)
            malicious_members = [
                m for m in blocked_members
                if self.blocked_creators[m]["rug_count"] >= 2
            ]

            if malicious_members:
                risk_level = "CRITICAL"
                critical_networks.append((creator, len(members), blocked_members))
            else:
                risk_level = "HIGH"
                high_risk_networks.append((creator, len(members), blocked_members))

            # Store network info
            self._store_network(creator, members, risk_level, blocked_members)

        print(f"[NETWORK] 🚨 CRITICAL networks: {len(critical_networks)}")
        for creator, size, blocked in critical_networks:
            creator_short = f"{creator[:8]}...{creator[-4:]}"
            print(f"  {creator_short}: {size} creators, {len(blocked)} blocked")

        print(f"[NETWORK] ⚠️ HIGH RISK networks: {len(high_risk_networks)}")
        print(f"  {len(high_risk_networks)} networks with suspicious members")

    def _store_network(self, creator: str, members: list, risk_level: str, blocked_members: list):
        """Store network information in database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Store in creator_networks if table exists
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, shared_destinations, network_size, network_risk_level, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    creator,
                    json.dumps(members),
                    json.dumps(list(self.creator_destinations[creator])),
                    len(members),
                    risk_level
                ))
            except sqlite3.OperationalError:
                # Table doesn't exist - create it
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_networks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        creator_address TEXT NOT NULL,
                        connected_creators TEXT NOT NULL,
                        shared_destinations TEXT NOT NULL,
                        network_size INTEGER,
                        network_risk_level TEXT,
                        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(creator_address)
                    )
                """)

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, shared_destinations, network_size, network_risk_level)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    creator,
                    json.dumps(members),
                    json.dumps(list(self.creator_destinations[creator])),
                    len(members),
                    risk_level
                ))

            # Update creator_blocklist with network info if creator is blocked
            if creator in self.blocked_creators:
                cursor.execute("""
                    UPDATE creator_blocklist
                    SET connected_to_malicious = ?,
                        network_members = ?
                    WHERE creator_address = ?
                """, (
                    1 if len(blocked_members) > 0 else 0,
                    json.dumps(blocked_members),
                    creator
                ))

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[ERROR] Failed to store network: {e}")

    def display_networks(self):
        """Display network information"""
        print(f"\n[NETWORKS] Creator Networks Summary:")
        print("=" * 100)

        for creator, members in sorted(self.networks.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
            creator_short = f"{creator[:8]}...{creator[-4:]}"
            blocked = [m for m in members if m in self.blocked_creators]

            if blocked:
                blocked_str = f", {len(blocked)} blocked"
            else:
                blocked_str = ""

            print(f"\n{creator_short}: {len(members)} creators{blocked_str}")

            if blocked:
                for b in blocked[:3]:
                    b_short = f"{b[:8]}...{b[-4:]}"
                    rep = self.blocked_creators[b]["reputation"]
                    print(f"  - {b_short} ({rep})")


async def main():
    print(f"[NETWORK] Starting network builder at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    builder = CreatorNetworkBuilder()

    # Load data
    builder.load_blocked_creators()
    if not builder.load_sol_transfers():
        print("[NETWORK] ⏳ Waiting for SOL transfer data...")
        print("[NETWORK] Run: python scripts/extract_sol_transfers.py")
        return

    # Build networks
    builder.build_networks()

    # Analyze risks
    builder.analyze_network_risk()

    # Display results
    builder.display_networks()

    print("\n" + "=" * 100)
    print(f"[NETWORK] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
