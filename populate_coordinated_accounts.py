#!/usr/bin/env python3
"""
Populate the coordinated funding accounts registry from database analysis.

Scans all creators in the database to find coordinated funding accounts
(accounts that fund multiple creators) and registers them.
"""

import sqlite3
from pathlib import Path
from coordinated_funding_registry import CoordinatedFundingRegistry

def populate_registry():
    """Scan database and populate coordinated accounts registry"""
    db_path = Path('pumpswap_tokens.db')
    
    if not db_path.exists():
        print("❌ Error: pumpswap_tokens.db not found")
        return False
    
    registry = CoordinatedFundingRegistry()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()
    
    print("\n" + "="*80)
    print("POPULATING COORDINATED FUNDING ACCOUNTS REGISTRY")
    print("="*80 + "\n")
    
    # Find all funding accounts and which creators they fund
    cursor.execute('''
        SELECT counterparty_address, COUNT(DISTINCT creator_address) as creator_count
        FROM creator_sol_transfers
        WHERE transfer_type = 'incoming'
        GROUP BY counterparty_address
        HAVING creator_count >= 2
        ORDER BY creator_count DESC
    ''')
    
    coordinated_accounts = cursor.fetchall()
    print(f"Found {len(coordinated_accounts)} coordinated funding accounts\n")
    
    registered = 0
    for account, creator_count in coordinated_accounts:
        # Get all creators funded by this account
        cursor.execute('''
            SELECT DISTINCT creator_address
            FROM creator_sol_transfers
            WHERE counterparty_address = ? AND transfer_type = 'incoming'
        ''', (account,))
        
        creators = [row[0] for row in cursor.fetchall()]
        
        if registry.add_account(account, creators):
            registered += 1
            print(f"✓ Registered: {account[:20]}... funds {len(creators)} creators")
    
    conn.close()
    
    print(f"\n{'='*80}")
    print(f"REGISTRY COMPLETE")
    print(f"{'='*80}\n")
    
    stats = registry.get_stats()
    print(f"Total coordinated accounts: {stats['total_coordinated_accounts']}")
    print(f"Total unique creators: {stats['total_unique_creators']}")
    print(f"Average creators per account: {stats['avg_creators_per_account']}")
    
    if stats['largest_group'][1]:
        print(f"\nLargest coordinated group:")
        print(f"  Account: {stats['largest_group'][1][:40]}...")
        print(f"  Creators funded: {stats['largest_group'][0]}")
    
    return True

if __name__ == '__main__':
    import sys
    success = populate_registry()
    sys.exit(0 if success else 1)
