#!/usr/bin/env python3
"""
Infrastructure Mapping System

Maps known infrastructure accounts to their categories:
- Exchanges (CEX)
- Validators/Staking
- Bridges
- Relayers
- Consolidators
- Pools/Liquidity

Allows tagging and highlighting when these accounts appear in funding flows.
"""

from typing import Dict, List, Optional, Tuple

# Infrastructure account mappings
INFRASTRUCTURE_ACCOUNTS = {
    # Axiom (Monitoring/Automation Infrastructure)
    "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk": {
        "name": "Axiom",
        "category": "automation",
        "description": "Automation & monitoring infrastructure (Axiom program)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "oracle"],
    },

    # CEX Accounts - Binance
    "8iBa3q2NqYqdTF5trYVyryy3XeeM6E3K26efsXhfVvcb": {
        "name": "Binance 2",
        "category": "cex",
        "description": "Binance exchange wallet",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "exchange"],
    },

    # Axiom automation account
    "XGqpChiohw1ZPX11vyeNUGxV12a6TcBej2tbJg9iwzC": {
        "name": "Axiom",
        "category": "automation",
        "description": "Automation & monitoring infrastructure (Axiom program)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "oracle"],
    },

    # Generic CEX staking (if needed)
    "BinanceStakedSol11111111111111111111111111": {
        "name": "Binance Staking",
        "category": "cex",
        "description": "Binance staking program",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "staking"],
    },

    # Consolidation/Relay Addresses (commonly used for internal transfers)
    "11111111111111111111111111111111": {
        "name": "System Program",
        "category": "system",
        "description": "Solana system program (basic operations)",
        "risk_level": "neutral",
        "tags": ["system", "program"],
    },
}

# Risk-based categories
CATEGORY_RISK_MAPPING = {
    "automation": "neutral",      # Neutral infrastructure
    "cex": "neutral",             # Neutral (institutional)
    "system": "neutral",          # Neutral (system)
    "validator": "low",           # Low risk
    "bridge": "medium",           # Medium risk (cross-chain)
    "relay": "medium",            # Medium risk (intermediary)
    "consolidator": "medium",     # Medium risk (intermediary)
    "unknown": "unknown",         # Unknown
}

def get_account_info(address: str) -> Optional[Dict]:
    """Get infrastructure info for an account if it exists"""
    return INFRASTRUCTURE_ACCOUNTS.get(address)

def is_infrastructure_account(address: str) -> bool:
    """Check if account is known infrastructure"""
    return address in INFRASTRUCTURE_ACCOUNTS

def get_category(address: str) -> str:
    """Get category for an address"""
    info = get_account_info(address)
    return info["category"] if info else "unknown"

def get_tags(address: str) -> List[str]:
    """Get tags for an address"""
    info = get_account_info(address)
    return info.get("tags", []) if info else []

def get_risk_level(address: str) -> str:
    """Get risk level based on category"""
    category = get_category(address)
    return CATEGORY_RISK_MAPPING.get(category, "unknown")

def format_funder_with_tags(address: str, amount_sol: float) -> Dict:
    """
    Format funder information with tags and highlights

    Returns dict with:
    - address
    - amount_sol
    - is_infrastructure: bool
    - category: str (if infrastructure)
    - tags: list
    - display_name: str
    - risk_level: str
    """
    info = get_account_info(address)

    if info:
        return {
            "address": address,
            "amount_sol": amount_sol,
            "is_infrastructure": True,
            "category": info["category"],
            "tags": info.get("tags", []),
            "display_name": info["name"],
            "description": info["description"],
            "risk_level": info["risk_level"],
        }
    else:
        return {
            "address": address,
            "amount_sol": amount_sol,
            "is_infrastructure": False,
            "category": "unknown",
            "tags": [],
            "display_name": address[:16] + "...",
            "description": None,
            "risk_level": "unknown",
        }

def add_infrastructure_account(address: str, name: str, category: str,
                               description: str = "", tags: List[str] = None,
                               risk_level: str = "neutral"):
    """
    Add a new infrastructure account to the mapping

    Args:
        address: Account address
        name: Display name
        category: Category (automation, cex, system, validator, bridge, etc.)
        description: Human-readable description
        tags: List of tags for filtering/highlighting
        risk_level: neutral, low, medium, high, unknown
    """
    if tags is None:
        tags = []

    INFRASTRUCTURE_ACCOUNTS[address] = {
        "name": name,
        "category": category,
        "description": description,
        "risk_level": risk_level,
        "tags": tags,
    }

def get_accounts_by_category(category: str) -> Dict[str, Dict]:
    """Get all accounts in a specific category"""
    return {addr: info for addr, info in INFRASTRUCTURE_ACCOUNTS.items()
            if info["category"] == category}

def get_accounts_by_tag(tag: str) -> Dict[str, Dict]:
    """Get all accounts with a specific tag"""
    return {addr: info for addr, info in INFRASTRUCTURE_ACCOUNTS.items()
            if tag in info.get("tags", [])}

def highlight_infra_in_funding(funders: List[Dict]) -> List[Dict]:
    """
    Add infrastructure highlighting to a list of funders

    Input: List of dicts with 'funder_address' and 'amount_sol'
    Output: Same list with added 'is_infrastructure', 'category', 'tags' fields
    """
    result = []
    for funder in funders:
        address = funder.get("funder_address") or funder.get("address")
        amount = funder.get("amount_sol") or funder.get("amount")

        enriched = format_funder_with_tags(address, amount)

        # Merge with original funder data
        enriched.update(funder)
        result.append(enriched)

    return result

# Example usage in database queries:
"""
# In main.py, when retrieving funders:

from infra_mapping import highlight_infra_in_funding

# Get raw funders from DB
cursor.execute('''
    SELECT funder_address, SUM(amount_sol) as amount_sol
    FROM creator_funders
    WHERE creator_address = ?
    GROUP BY funder_address
    ORDER BY amount_sol DESC
''', (creator_address,))

raw_funders = [dict(row) for row in cursor.fetchall()]

# Add infrastructure highlighting
funders_with_tags = highlight_infra_in_funding(raw_funders)

# In UI template, check for infrastructure:
# {% if funder.is_infrastructure %}
#   <span class="infra-tag">{{ funder.category|upper }}</span>
# {% endif %}
"""

if __name__ == "__main__":
    # Test the infrastructure mapping
    print("Infrastructure Mapping Test")
    print("=" * 80)

    # Test Axiom
    axiom = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"
    print(f"\nAXIOM Test:")
    print(f"  Address: {axiom}")
    print(f"  Is Infrastructure: {is_infrastructure_account(axiom)}")
    print(f"  Info: {get_account_info(axiom)}")
    print(f"  Tags: {get_tags(axiom)}")
    print(f"  Risk Level: {get_risk_level(axiom)}")

    # Test unknown
    unknown = "111111111111111111111111111111111111111111"
    print(f"\nUnknown Account Test:")
    print(f"  Address: {unknown}")
    print(f"  Is Infrastructure: {is_infrastructure_account(unknown)}")
    print(f"  Category: {get_category(unknown)}")
    print(f"  Risk Level: {get_risk_level(unknown)}")

    # Test formatting
    print(f"\nFormatted Output:")
    formatted = format_funder_with_tags(axiom, 10.5)
    for key, value in formatted.items():
        print(f"  {key}: {value}")

    # Test by category
    print(f"\nAccounts by category 'automation':")
    infra = get_accounts_by_category("automation")
    for addr, info in infra.items():
        print(f"  {info['name']}: {addr[:16]}...")

    print("\n" + "=" * 80)
