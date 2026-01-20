#!/usr/bin/env python3
"""
Creator Address Tracking System

Tracks all addresses that have SOL interactions (sent/received) with creators.
Filters out dust (< 0.025 SOL) and identifies addresses used by multiple creators.

Tables:
- creator_address_interactions: All addresses interacting with creators
- multi_creator_addresses: Addresses linked to multiple creators
"""

import sqlite3
import json
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"
MIN_SOL = 0.01  # Lower threshold to catch coordinated activity

def create_tracking_tables():
    """Create tables for address tracking"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()

    # Table 1: All address-creator interactions (filtered)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS creator_address_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_address TEXT NOT NULL,
            external_address TEXT NOT NULL,
            direction TEXT NOT NULL,  -- 'outbound' (creator→address) or 'inbound' (address→creator)
            total_amount REAL NOT NULL,
            transaction_count INTEGER DEFAULT 1,
            min_sol REAL DEFAULT 0.025,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(creator_address, external_address, direction)
        )
    """)

    # Table 2: Addresses linked to multiple creators
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS multi_creator_addresses (
            external_address TEXT PRIMARY KEY,
            creator_count INTEGER,
            total_creators TEXT,  -- JSON array of creator addresses
            total_sol_volume REAL,
            interaction_patterns TEXT,  -- JSON: {creator: [outbound, inbound, total]}
            risk_level TEXT,  -- HIGH, MEDIUM, LOW
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Created tracking tables")

def populate_address_interactions():
    """Populate all address-creator interactions from existing data"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()

    # Clear existing data
    cursor.execute("DELETE FROM creator_address_interactions")

    # Get all outbound transfers (creator → address)
    cursor.execute("""
        SELECT creator_address, destination_address, total_amount
        FROM creator_sol_transfers
        WHERE total_amount >= ?
    """, (MIN_SOL,))

    outbound_count = 0
    for creator, address, amount in cursor.fetchall():
        cursor.execute("""
            INSERT OR REPLACE INTO creator_address_interactions
            (creator_address, external_address, direction, total_amount, min_sol)
            VALUES (?, ?, 'outbound', ?, ?)
        """, (creator, address, amount, MIN_SOL))
        outbound_count += 1

    # Get all inbound transfers (funder → creator)
    cursor.execute("""
        SELECT creator_address, funder_address, amount_sol
        FROM creator_funders
        WHERE amount_sol >= ?
    """, (MIN_SOL,))

    inbound_count = 0
    for creator, funder, amount in cursor.fetchall():
        cursor.execute("""
            INSERT OR REPLACE INTO creator_address_interactions
            (creator_address, external_address, direction, total_amount, min_sol)
            VALUES (?, ?, 'inbound', ?, ?)
        """, (creator, funder, amount, MIN_SOL))
        inbound_count += 1

    conn.commit()

    print(f"✅ Populated address interactions:")
    print(f"   Outbound (creator→address): {outbound_count}")
    print(f"   Inbound (funder→creator): {inbound_count}")
    print(f"   Total: {outbound_count + inbound_count}")

    conn.close()

def identify_multi_creator_addresses():
    """Find addresses interacting with multiple creators"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Clear existing multi-creator data
    cursor.execute("DELETE FROM multi_creator_addresses")

    # Find all addresses and count how many creators they interact with
    cursor.execute("""
        SELECT
            external_address,
            COUNT(DISTINCT creator_address) as creator_count,
            ROUND(SUM(total_amount), 4) as total_volume,
            GROUP_CONCAT(DISTINCT creator_address) as creators
        FROM creator_address_interactions
        GROUP BY external_address
        HAVING COUNT(DISTINCT creator_address) > 1
        ORDER BY creator_count DESC, total_volume DESC
    """)

    multi_creator_addrs = cursor.fetchall()

    print(f"\n🔗 Found {len(multi_creator_addrs)} addresses linked to multiple creators\n")

    # Process each multi-creator address
    for i, row in enumerate(multi_creator_addrs, 1):
        addr = row['external_address']
        creator_count = row['creator_count']
        total_volume = row['total_volume']
        creators = row['creators'].split(',')

        # Get interaction details for each creator
        cursor.execute("""
            SELECT
                creator_address,
                direction,
                ROUND(SUM(total_amount), 4) as amount
            FROM creator_address_interactions
            WHERE external_address = ?
            GROUP BY creator_address, direction
        """, (addr,))

        patterns = {}
        for detail_row in cursor.fetchall():
            creator = detail_row[0]
            direction = detail_row[1]
            amount = detail_row[2]

            if creator not in patterns:
                patterns[creator] = {'outbound': 0, 'inbound': 0, 'total': 0}
            patterns[creator][direction] = amount
            patterns[creator]['total'] += amount

        # Determine risk level
        if creator_count >= 5:
            risk_level = "HIGH"
        elif creator_count >= 3:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # Insert into multi_creator_addresses
        cursor.execute("""
            INSERT INTO multi_creator_addresses
            (external_address, creator_count, total_creators, total_sol_volume, interaction_patterns, risk_level)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (addr, creator_count, json.dumps(creators), total_volume, json.dumps(patterns), risk_level))

        print(f"{i}. {addr}")
        print(f"   Creators: {creator_count}")
        print(f"   Total SOL: {total_volume:.4f}")
        print(f"   Risk Level: {risk_level}")
        print()

    conn.commit()
    conn.close()

def display_multi_creator_addresses():
    """Display multi-creator addresses with details"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("\n" + "="*100)
    print("MULTI-CREATOR ADDRESS ANALYSIS")
    print("="*100)

    cursor.execute("""
        SELECT *
        FROM multi_creator_addresses
        ORDER BY creator_count DESC, total_sol_volume DESC
    """)

    results = cursor.fetchall()

    if not results:
        print("\nNo multi-creator addresses found above 0.025 SOL threshold")
        conn.close()
        return

    print(f"\nFound {len(results)} addresses used by multiple creators:\n")

    for i, row in enumerate(results, 1):
        print(f"{i}. Address: {row['external_address']}")
        print(f"   Risk Level: {row['risk_level']}")
        print(f"   Creator Count: {row['creator_count']}")
        print(f"   Total SOL Volume: {row['total_sol_volume']:.4f}")

        creators = json.loads(row['total_creators'])
        patterns = json.loads(row['interaction_patterns'])

        print(f"   Creators ({len(creators)}):")
        for creator in creators[:5]:  # Show first 5
            if creator in patterns:
                p = patterns[creator]
                out = f"{p.get('outbound', 0):.4f}" if p.get('outbound', 0) > 0 else "0"
                inp = f"{p.get('inbound', 0):.4f}" if p.get('inbound', 0) > 0 else "0"
                print(f"     • {creator[:35]}... → {out} SOL out | {inp} SOL in")

        if len(creators) > 5:
            print(f"     ... and {len(creators) - 5} more")

        print()

    # Summary statistics
    print("="*100)
    print("SUMMARY STATISTICS")
    print("="*100)

    cursor.execute("""
        SELECT
            COUNT(*) as total_addresses,
            SUM(creator_count) as total_creator_links,
            AVG(creator_count) as avg_creators_per_address,
            MAX(creator_count) as max_creators,
            ROUND(SUM(total_sol_volume), 4) as total_volume,
            SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END) as high_risk_count,
            SUM(CASE WHEN risk_level = 'MEDIUM' THEN 1 ELSE 0 END) as medium_risk_count,
            SUM(CASE WHEN risk_level = 'LOW' THEN 1 ELSE 0 END) as low_risk_count
        FROM multi_creator_addresses
    """)

    summary = cursor.fetchone()

    print(f"\nTotal multi-creator addresses: {summary['total_addresses']}")
    print(f"Total creator-address links: {summary['total_creator_links']}")
    print(f"Average creators per address: {summary['avg_creators_per_address']:.1f}")
    print(f"Max creators for single address: {summary['max_creators']}")
    print(f"Total SOL volume: {summary['total_volume']:.4f}")
    print(f"\nRisk Distribution:")
    print(f"  HIGH: {summary['high_risk_count']}")
    print(f"  MEDIUM: {summary['medium_risk_count']}")
    print(f"  LOW: {summary['low_risk_count']}")

    conn.close()

def get_creators_for_address(address):
    """Get all creators interacting with a specific address"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            creator_address,
            direction,
            ROUND(total_amount, 4) as amount,
            transaction_count
        FROM creator_address_interactions
        WHERE external_address = ?
        ORDER BY total_amount DESC
    """, (address,))

    results = cursor.fetchall()
    conn.close()

    return results

if __name__ == "__main__":
    print("\n" + "="*100)
    print("CREATOR ADDRESS TRACKING SYSTEM")
    print("="*100)

    # Step 1: Create tables
    print("\n📊 Step 1: Creating tracking tables...")
    create_tracking_tables()

    # Step 2: Populate interactions
    print("\n📥 Step 2: Populating address interactions (min: 0.025 SOL)...")
    populate_address_interactions()

    # Step 3: Identify multi-creator addresses
    print("\n🔍 Step 3: Identifying multi-creator addresses...")
    identify_multi_creator_addresses()

    # Step 4: Display results
    print("\n📈 Step 4: Displaying results...")
    display_multi_creator_addresses()

    print("\n✅ Analysis complete!")
