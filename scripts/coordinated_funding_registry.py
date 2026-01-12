#!/usr/bin/env python3
"""
Coordinated Funding Accounts Registry

Maintains a persistent registry of known coordinated funding accounts discovered
during risk analysis. These accounts are used to flag new tokens that link to
coordinated pump operations.

Structure:
- coordinated_accounts.json: Maps funding account -> list of creators it funds
"""

import json
from pathlib import Path
from typing import Dict, List, Set
from datetime import datetime

class CoordinatedFundingRegistry:
    """Track and query coordinated funding accounts"""
    
    def __init__(self, registry_file: str = "coordinated_accounts.json"):
        self.registry_path = Path(registry_file)
        self.accounts: Dict[str, List[str]] = {}  # account -> [creators]
        self.load()
    
    def load(self):
        """Load registry from file"""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, 'r') as f:
                    self.accounts = json.load(f)
            except Exception as e:
                print(f"⚠️  Could not load registry: {e}")
                self.accounts = {}
    
    def save(self):
        """Save registry to file"""
        try:
            with open(self.registry_path, 'w') as f:
                json.dump(self.accounts, f, indent=2)
        except Exception as e:
            print(f"❌ Error saving registry: {e}")
    
    def add_account(self, funding_account: str, creators: List[str]):
        """Register a coordinated funding account
        
        Args:
            funding_account: The funding account address
            creators: List of creators funded by this account
        """
        if len(creators) >= 2:  # Only register if funds 2+ creators
            existing = set(self.accounts.get(funding_account, []))
            existing.update(creators)
            self.accounts[funding_account] = list(existing)
            self.save()
            return True
        return False
    
    def is_coordinated(self, funding_account: str) -> bool:
        """Check if account is in coordinated registry"""
        return funding_account in self.accounts
    
    def get_linked_creators(self, funding_account: str) -> List[str]:
        """Get creators funded by this account"""
        return self.accounts.get(funding_account, [])
    
    def get_creator_risk(self, creator: str) -> Dict:
        """Get risk info for a creator
        
        Returns:
            Dict with:
            - is_coordinated: bool
            - coordinated_accounts: list of accounts that fund this creator
            - linked_creators: dict mapping account -> list of other creators
        """
        coordinated_accounts = []
        linked_creators = {}
        
        for account, creators in self.accounts.items():
            if creator in creators:
                coordinated_accounts.append(account)
                others = [c for c in creators if c != creator]
                if others:
                    linked_creators[account] = others
        
        return {
            'is_coordinated': len(coordinated_accounts) > 0,
            'coordinated_accounts': coordinated_accounts,
            'linked_creators': linked_creators,
            'account_count': len(coordinated_accounts),
            'total_linked_creators': sum(len(c) for c in linked_creators.values())
        }
    
    def get_all_coordinated_accounts(self) -> Dict[str, List[str]]:
        """Get all coordinated accounts"""
        return self.accounts.copy()
    
    def get_stats(self) -> Dict:
        """Get registry statistics"""
        total_accounts = len(self.accounts)
        total_creators = len(set(c for creators in self.accounts.values() for c in creators))
        avg_creators_per_account = total_creators / total_accounts if total_accounts > 0 else 0
        
        return {
            'total_coordinated_accounts': total_accounts,
            'total_unique_creators': total_creators,
            'avg_creators_per_account': round(avg_creators_per_account, 2),
            'largest_group': max((len(c), a) for a, c in self.accounts.items()) if self.accounts else (0, None)
        }

if __name__ == '__main__':
    registry = CoordinatedFundingRegistry()
    print("\nCoordinated Funding Accounts Registry")
    print("="*60)
    stats = registry.get_stats()
    print(f"\nTotal coordinated accounts: {stats['total_coordinated_accounts']}")
    print(f"Total unique creators: {stats['total_unique_creators']}")
    print(f"Average creators per account: {stats['avg_creators_per_account']}")
    
    if stats['largest_group'][1]:
        print(f"\nLargest coordinated group:")
        print(f"  Account: {stats['largest_group'][1][:40]}...")
        print(f"  Creators funded: {stats['largest_group'][0]}")
