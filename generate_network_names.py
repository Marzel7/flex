#!/usr/bin/env python3
"""
Generate random display names for all super_clusters.
Uses memorable, colorful names instead of IDs.
"""

import sqlite3
import random
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

# Random name components - create memorable, unique names
ADJECTIVES = [
    "Swift", "Crimson", "Golden", "Ethereal", "Shadow", "Blazing", "Frozen",
    "Silent", "Raging", "Mystic", "Vibrant", "Stellar", "Lunar", "Solar",
    "Phantom", "Radiant", "Hollow", "Storm", "Thunder", "Cosmic", "Primal",
    "Quantum", "Nexus", "Apex", "Iron", "Crystal", "Obsidian", "Emerald",
    "Amber", "Sapphire", "Pearl", "Ruby", "Onyx", "Jade", "Ivory"
]

NOUNS = [
    "Phoenix", "Dragon", "Falcon", "Tiger", "Wolf", "Raven", "Serpent",
    "Eagle", "Hawk", "Viper", "Panther", "Lion", "Bear", "Mantis",
    "Scorpion", "Sphinx", "Kraken", "Leviathan", "Basilisk", "Gargoyle",
    "Chimera", "Cerberus", "Titan", "Giant", "Golem", "Wraith", "Specter",
    "Lich", "Reaper", "Sentinel", "Guardian", "Warden", "Oracle", "Prophet"
]

def generate_unique_name(cursor, used_names):
    """Generate a unique name not yet in database"""
    while True:
        name = f"{random.choice(ADJECTIVES)}{random.choice(NOUNS)}"
        if name not in used_names:
            return name

def main():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cursor = conn.cursor()

    # Get all super_clusters
    cursor.execute("SELECT super_cluster_id FROM super_clusters ORDER BY super_cluster_id")
    clusters = [row[0] for row in cursor.fetchall()]

    # Get existing names
    cursor.execute("SELECT display_name FROM network_names")
    used_names = set(row[0] for row in cursor.fetchall())

    # Generate and insert names for new clusters
    inserted = 0
    skipped = 0

    for cluster_id in clusters:
        cursor.execute("SELECT display_name FROM network_names WHERE super_cluster_id = ?", (cluster_id,))
        if cursor.fetchone():
            skipped += 1
            continue

        name = generate_unique_name(cursor, used_names)
        used_names.add(name)

        cursor.execute(
            "INSERT INTO network_names (super_cluster_id, display_name) VALUES (?, ?)",
            (cluster_id, name)
        )
        inserted += 1

        print(f"✓ {cluster_id} → {name}")

    conn.commit()
    conn.close()

    print(f"\n{'='*60}")
    print(f"Generated: {inserted} names")
    print(f"Skipped (existing): {skipped} names")
    print(f"Total clusters: {len(clusters)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
