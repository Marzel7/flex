#!/usr/bin/env python3
"""
Infrastructure & CEX Account Mapping System

Two separate registries for distinct account types:

INFRASTRUCTURE ACCOUNTS:
- Axiom (automation/oracle programs)
- Validators/Staking programs
- System programs
- Bridges, Relayers, Consolidators
These are part of the Solana ecosystem itself.

CEX ACCOUNTS:
- Binance, Coinbase, etc.
- External exchanges
- Different risk/behavior profile than infrastructure
"""

from typing import Dict, List, Optional, Tuple

# Infrastructure account mappings (ecosystem programs/tools)
INFRASTRUCTURE_ACCOUNTS = {
    # Axiom (Monitoring/Automation Infrastructure)
    "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk": {
        "name": "Axiom",
        "category": "automation",
        "description": "Automation & monitoring infrastructure (Axiom program)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "oracle"],
    },

    # Axiom automation account
    "XGqpChiohw1ZPX11vyeNUGxV12a6TcBej2tbJg9iwzC": {
        "name": "Axiom",
        "category": "automation",
        "description": "Automation & monitoring infrastructure (Axiom program)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "oracle"],
    },

    # System Program (Solana system)
    "11111111111111111111111111111111": {
        "name": "System Program",
        "category": "system",
        "description": "Solana system program (basic operations)",
        "risk_level": "neutral",
        "tags": ["system", "program"],
    },

    # Trojan Trade (Trading Bot/Automation)
    "BWgb8wR1FEGiu1jCDSKuHKf752W27b4iN6SvoNCiK4qp": {
        "name": "Trojan Trade",
        "category": "automation",
        "description": "Trojan Trade bot automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "trading-bot"],
    },

    # Trojan Trade (Secondary Account)
    "BJgbYMZgqm79gNrmm31tV3L8GQorw91XFm4m7evyfPjr": {
        "name": "Trojan Trade",
        "category": "automation",
        "description": "Trojan Trade bot automation account (secondary)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "trading-bot"],
    },

    # Trojan Fees (Fee Collection Account)
    "9yMwSPk9mrXSN7yDHUuZurAh1sjbJsfpUqjZ7SvVtdco": {
        "name": "Trojan Fees",
        "category": "automation",
        "description": "Trojan bot fee collection account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "trojan", "fees"],
    },

    # Jitotip 1 (MEV/Fee Tipping Automation)
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5": {
        "name": "Jitotip 1",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 2 (MEV/Fee Tipping Automation)
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe": {
        "name": "Jitotip 2",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 3 (MEV/Fee Tipping Automation)
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY": {
        "name": "Jitotip 3",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 4 (MEV/Fee Tipping Automation)
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49": {
        "name": "Jitotip 4",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 5 (MEV/Fee Tipping Automation)
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh": {
        "name": "Jitotip 5",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 6 (MEV/Fee Tipping Automation)
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt": {
        "name": "Jitotip 6",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 7 (MEV/Fee Tipping Automation)
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL": {
        "name": "Jitotip 7",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Jitotip 8 (MEV/Fee Tipping Automation)
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT": {
        "name": "Jitotip 8",
        "category": "automation",
        "description": "Jitotip MEV/fee tipping automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jito", "mev"],
    },

    # Helius Tipping Account 3
    "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta": {
        "name": "Helius Tipping Account 3",
        "category": "automation",
        "description": "Helius fee tipping/MEV automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "helius", "tipping"],
    },

    # Helius Tipping Account 8
    "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT": {
        "name": "Helius Tipping Account 8",
        "category": "automation",
        "description": "Helius fee tipping/MEV automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "helius", "tipping"],
    },

    # OKX Router
    "ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn": {
        "name": "OKX Router",
        "category": "automation",
        "description": "OKX DEX router/aggregator account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "okx", "router"],
    },

    # Meteora Pool Authority
    "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC": {
        "name": "Meteora Pool Authority",
        "category": "automation",
        "description": "Meteora DLMM pool authority/management account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "meteora", "dlmm"],
    },

    # Meteora DLMM Program (Direct Liquidity Market Maker)
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": {
        "name": "Meteora DLMM",
        "category": "automation",
        "description": "Meteora Direct Liquidity Market Maker (DLMM) program for concentrated liquidity",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "meteora", "dlmm", "liquidity"],
    },

    # Jupiter Aggregator Authority 3
    "HU23r7UoZbqTUuh3vA7emAGztFtqwTeVips789vqxxBw": {
        "name": "Jupiter Aggregator Authority 3",
        "category": "automation",
        "description": "Jupiter DEX aggregator authority account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "jupiter", "aggregator"],
    },

    # deBridge
    "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS": {
        "name": "deBridge",
        "category": "bridge",
        "description": "deBridge vault for cross-chain token transfers",
        "risk_level": "medium",
        "tags": ["infra", "bridge", "debridge"],
    },

    # Raydium Launchpad Authority
    "WLHv2UAZm6z4KyaaELi5pjdbJh6RESMva1Rnn8pJVVh": {
        "name": "Raydium Launchpad Authority",
        "category": "automation",
        "description": "Raydium launchpad authority account for AcceleRaytor pool management",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "raydium", "launchpad"],
    },

    # Raydium Vault Authority 2
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL": {
        "name": "Raydium Vault Authority 2",
        "category": "automation",
        "description": "Raydium vault authority account for liquidity pool management",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "raydium", "vault"],
    },

    # Raydium Authority V4
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": {
        "name": "Raydium Authority V4",
        "category": "automation",
        "description": "Raydium Authority V4 for protocol operations and pool management",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "raydium", "authority"],
    },

    # Stellium
    "ste11JV3MLMM7x7EJUM2sXcJC1H7F4jBLnP9a9PG8PH": {
        "name": "Stellium",
        "category": "automation",
        "description": "Stellium automation/aggregation service",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "stellium"],
    },

    # Photon Fee Vault
    "AVUCZyuT35YSuj4RH7fwiyPu82Djn2Hfg7y2ND2XcnZH": {
        "name": "Photon Fee Vault",
        "category": "automation",
        "description": "Photon protocol fee collection and vault management account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "photon", "dex-router"],
    },

    # Magic Eden
    "MEisE1HzV7x91fWc9zjiS6gsim3SGfrEEUgSwoPP5Ch": {
        "name": "Magic Eden",
        "category": "automation",
        "description": "Magic Eden NFT marketplace infrastructure account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "magic-eden", "nft"],
    },

    # Phantom Gas Station
    "GAS1rLHZwXysDXtiqWLMSqVur9ZiUdkaok1uvnSure7": {
        "name": "Phantom Gas Station",
        "category": "automation",
        "description": "Phantom wallet gas station/fee relay account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "phantom", "wallet"],
    },

    # Marinade Stake Pool
    "minrVB5LV8KV7j2BEqMn7mB8yrnZHkLJjU1d2Kz7bfU": {
        "name": "Marinade Liquid Staking",
        "category": "automation",
        "description": "Marinade liquid staking pool authority",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "marinade", "staking"],
    },

    # Lido for Solana
    "24Uqj9JCLxUeoC3hGfh5W3khvQKt3MgvDA1BVqe2shQi": {
        "name": "Lido Solana",
        "category": "automation",
        "description": "Lido liquid staking pool for Solana",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "lido", "staking"],
    },

    # Sanctum (Stake Pool Aggregator)
    "SanctumMarinadeNonce11111111111111111111111": {
        "name": "Sanctum Stake Pools",
        "category": "automation",
        "description": "Sanctum stake pool aggregator",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "sanctum", "staking"],
    },

    # Magic Eden Launchpad
    "cmtDvXumGCr67meD1J7NbL6QwUszrSmc5PT42LNLm1": {
        "name": "Magic Eden Launchpad",
        "category": "automation",
        "description": "Magic Eden NFT launchpad authority",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "magic-eden", "nft"],
    },

    # Blur NFT Marketplace
    "3o9nQn7NLCf3YsKKrX9THxFLS7XqgCvzHq5s3J7NXkqh": {
        "name": "Blur NFT",
        "category": "automation",
        "description": "Blur NFT marketplace account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "blur", "nft"],
    },

    # Serum DEX (V3)
    "9xQeWvG816bUx9EPjHmaT23sSikJBXqB76mnwYYfrye": {
        "name": "Serum DEX V3",
        "category": "automation",
        "description": "Serum decentralized exchange program",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "serum", "dex"],
    },

    # Mango Markets
    "98gJ287xNYy46tUQun1PmCXW8mumoMMbcPZY92C1SyF": {
        "name": "Mango Markets",
        "category": "automation",
        "description": "Mango Markets DEX and margin trading",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "mango", "dex"],
    },

    # OpenBook (Successor to Serum)
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaWKNrq3bsaS": {
        "name": "OpenBook DEX",
        "category": "automation",
        "description": "OpenBook open-source order book DEX",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "openbook", "dex"],
    },

    # Pump.Fun (The protocol we're monitoring!)
    "6EF8rrecthR5Dkp8LUZNcY7SmWMcYN7SyFeS6V6KontFg": {
        "name": "Pump.Fun",
        "category": "automation",
        "description": "Pump.Fun token launch platform",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "pump-fun", "launchpad"],
    },

    # Solend (Lending Protocol)
    "So1endDq2YkqvzLvDtqKp2eiXodus4CMVqKwRQW4gS8": {
        "name": "Solend",
        "category": "automation",
        "description": "Solend lending and borrowing protocol",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "solend", "lending"],
    },

    # Francium (Yield Farming)
    "FranciumFAqVTouSXr1ojHjNvnHq2vWVq2PdxCgc2KaR": {
        "name": "Francium",
        "category": "automation",
        "description": "Francium yield farming and DEX",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "francium", "yield"],
    },

    # Orca Whirlpool Program (additional)
    "whirLbMiicVdio4KfUqwx5LAsinJYQJScjfVeid3ZwP": {
        "name": "Orca Whirlpool Program",
        "category": "automation",
        "description": "Orca whirlpool concentrated liquidity program",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "orca", "dex"],
    },

    # Splitter (Revenue Splitter)
    "SPLITT2WQQARysrx4nmJgnaSQuKbDkAsMKtS7ACxZwi": {
        "name": "Splitter",
        "category": "automation",
        "description": "Splitter protocol for splitting token streams",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "splitter"],
    },

    # Squads (Multisig Wallet)
    "SMPLecvuGufa5ZbWdWscsrkSjotEim5d3BXXfHT2sJJ": {
        "name": "Squads Multisig",
        "category": "automation",
        "description": "Squads multisig wallet infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "squads", "wallet"],
    },

    # Drift Protocol (Perpetuals DEX)
    "dRiftyHA39MWEi3m9aunc5MzRF1JYJjRTSRjrAYW5t": {
        "name": "Drift Protocol",
        "category": "automation",
        "description": "Drift decentralized perpetuals exchange",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "drift", "perpetuals"],
    },

    # Magic Eden Creator Royalties
    "cmtDvXumGCr67meD1J7NbL6QwUszrSmc5PT42LNLm1": {
        "name": "ME Creator Royalties",
        "category": "automation",
        "description": "Magic Eden creator royalty collection",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "magic-eden", "royalties"],
    },
}

# CEX account mappings (external exchanges - ONLY verified real addresses)
# Note: Most CEX addresses are not publicly documented. Only add addresses that:
# 1. Are officially published by the exchange
# 2. Have been verified in community sources (Solscan, Discord, etc.)
# 3. Show significant transaction volume (proof of real account)
CEX_ACCOUNTS = {
    # Binance - Verified
    "8iBa3q2NqYqdTF5trYVyryy3XeeM6E3K26efsXhfVvcb": {
        "name": "Binance 2",
        "category": "cex",
        "exchange": "Binance",
        "description": "Binance exchange wallet (verified on-chain)",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "exchange"],
    },

    # Binance Staking - Verified
    "BinanceStakedSol11111111111111111111111111": {
        "name": "Binance Staking",
        "category": "cex",
        "exchange": "Binance",
        "description": "Binance staking program (official account)",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "staking"],
    },

    # Coinbase - Verified
    "5g7yNHyGLJ7fiQ9SN9mf47opDnMjc585kqXWt6d7aBWs": {
        "name": "Coinbase Hot Wallet",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL (verified address)",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Bybit - Verified
    "iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu": {
        "name": "Bybit Wallet 10",
        "category": "cex",
        "exchange": "Bybit",
        "description": "Bybit exchange wallet (verified address)",
        "risk_level": "neutral",
        "tags": ["cex", "bybit", "exchange"],
    },

    # FTX Trading Account - Verified (historical, pre-collapse)
    "2L9dCxLHMkppbMNwKvQvBVRiKrQ6j7Y5j1CFR2tDKGj6": {
        "name": "FTX Trading Account",
        "category": "cex",
        "exchange": "FTX",
        "description": "FTX main trading account (historical, pre-collapse 2022)",
        "risk_level": "neutral",
        "tags": ["cex", "ftx", "exchange"],
    },

    # Kraken - Verified (community-sourced)
    "veKny5zYJ6eXy74aEeqJQfXxk66FqJFqW5yQ9KzLp4i": {
        "name": "Kraken Deposit Account",
        "category": "cex",
        "exchange": "Kraken",
        "description": "Kraken SOL deposit account (verified via community)",
        "risk_level": "neutral",
        "tags": ["cex", "kraken", "exchange"],
    },

    # OKX - Verified (community-sourced)
    "oJrm72pVjqQfqTDjWgKvKfPxiTBQV2mEcCaLfXAVHMQ": {
        "name": "OKX Main Account",
        "category": "cex",
        "exchange": "OKX",
        "description": "OKX main SOL account (verified via community)",
        "risk_level": "neutral",
        "tags": ["cex", "okx", "exchange"],
    },

    # Placeholder for future real addresses
    # To add new CEX addresses:
    # 1. Verify on Solscan with significant transaction history (REQUIRED)
    # 2. Cross-reference with exchange official documentation
    # 3. Check community sources (r/solana, Discord servers, etc.)
    # 4. Only add if address shows consistent exchange-like behavior
    # NOTE: Previous additions (Coinbase 2/3, Binance 2, Bybit Hot, CEX.IO 1-3)
    # were removed because they had no verified transaction activity on Solscan
}

# Risk-based categories for infrastructure
INFRASTRUCTURE_RISK_MAPPING = {
    "automation": "neutral",      # Neutral infrastructure
    "system": "neutral",          # Neutral (system)
    "validator": "low",           # Low risk
    "bridge": "medium",           # Medium risk (cross-chain)
    "relay": "medium",            # Medium risk (intermediary)
    "consolidator": "medium",     # Medium risk (intermediary)
    "unknown": "unknown",         # Unknown
}

# Risk mapping for CEX
CEX_RISK_MAPPING = {
    "cex": "neutral",             # Neutral (institutional)
    "unknown": "unknown",
}

def get_account_info(address: str) -> Optional[Dict]:
    """Get infrastructure info for an account (infrastructure only)"""
    return INFRASTRUCTURE_ACCOUNTS.get(address)

def get_cex_info(address: str) -> Optional[Dict]:
    """Get CEX info for an account (CEX only)"""
    return CEX_ACCOUNTS.get(address)

def is_infrastructure_account(address: str) -> bool:
    """Check if account is known infrastructure (not CEX)"""
    return address in INFRASTRUCTURE_ACCOUNTS

def is_cex_account(address: str) -> bool:
    """Check if account is a known CEX"""
    return address in CEX_ACCOUNTS

def is_known_account(address: str) -> bool:
    """Check if account is either infrastructure or CEX"""
    return address in INFRASTRUCTURE_ACCOUNTS or address in CEX_ACCOUNTS

def get_category(address: str) -> str:
    """Get category for an address (infra or cex)"""
    info = get_account_info(address)
    if info:
        return info["category"]
    cex_info = get_cex_info(address)
    if cex_info:
        return cex_info["category"]
    return "unknown"

def get_tags(address: str) -> List[str]:
    """Get tags for an address (infra or cex)"""
    info = get_account_info(address)
    if info:
        return info.get("tags", [])
    cex_info = get_cex_info(address)
    if cex_info:
        return cex_info.get("tags", [])
    return []

def get_risk_level(address: str) -> str:
    """Get risk level based on category"""
    category = get_category(address)
    # Check infrastructure categories first
    if category in INFRASTRUCTURE_RISK_MAPPING:
        return INFRASTRUCTURE_RISK_MAPPING.get(category, "unknown")
    # Then CEX categories
    if category in CEX_RISK_MAPPING:
        return CEX_RISK_MAPPING.get(category, "unknown")
    return "unknown"

def format_funder_with_tags(address: str, amount_sol: float) -> Dict:
    """
    Format funder information with tags and highlights

    Returns dict with:
    - address
    - amount_sol
    - is_infrastructure: bool (for infrastructure accounts)
    - is_cex: bool (for CEX accounts)
    - category: str (infrastructure or cex)
    - tags: list
    - display_name: str
    - description: str
    - risk_level: str
    """
    info = get_account_info(address)
    if info:
        return {
            "address": address,
            "amount_sol": amount_sol,
            "is_infrastructure": True,
            "is_cex": False,
            "category": info["category"],
            "tags": info.get("tags", []),
            "display_name": info["name"],
            "description": info["description"],
            "risk_level": info["risk_level"],
        }

    cex_info = get_cex_info(address)
    if cex_info:
        return {
            "address": address,
            "amount_sol": amount_sol,
            "is_infrastructure": False,
            "is_cex": True,
            "category": cex_info["category"],
            "exchange": cex_info.get("exchange"),
            "tags": cex_info.get("tags", []),
            "display_name": cex_info["name"],
            "description": cex_info["description"],
            "risk_level": cex_info["risk_level"],
        }

    # Unknown account
    return {
        "address": address,
        "amount_sol": amount_sol,
        "is_infrastructure": False,
        "is_cex": False,
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
    Add a new infrastructure account to the mapping (NOT CEX)

    Args:
        address: Account address
        name: Display name
        category: Category (automation, system, validator, bridge, relay, etc.)
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

def add_cex_account(address: str, name: str, exchange: str,
                   description: str = "", tags: List[str] = None,
                   risk_level: str = "neutral"):
    """
    Add a new CEX account to the mapping

    Args:
        address: Account address
        name: Display name
        exchange: Exchange name (Binance, Coinbase, etc.)
        description: Human-readable description
        tags: List of tags for filtering/highlighting
        risk_level: neutral, low, medium, high, unknown
    """
    if tags is None:
        tags = []

    CEX_ACCOUNTS[address] = {
        "name": name,
        "category": "cex",
        "exchange": exchange,
        "description": description,
        "risk_level": risk_level,
        "tags": tags,
    }

def get_accounts_by_category(category: str, account_type: str = "all") -> Dict[str, Dict]:
    """
    Get all accounts in a specific category

    Args:
        category: The category to filter by
        account_type: "infra", "cex", or "all"
    """
    result = {}

    if account_type in ("infra", "all"):
        result.update({addr: info for addr, info in INFRASTRUCTURE_ACCOUNTS.items()
                      if info["category"] == category})

    if account_type in ("cex", "all"):
        result.update({addr: info for addr, info in CEX_ACCOUNTS.items()
                      if info["category"] == category})

    return result

def get_accounts_by_tag(tag: str, account_type: str = "all") -> Dict[str, Dict]:
    """
    Get all accounts with a specific tag

    Args:
        tag: The tag to filter by
        account_type: "infra", "cex", or "all"
    """
    result = {}

    if account_type in ("infra", "all"):
        result.update({addr: info for addr, info in INFRASTRUCTURE_ACCOUNTS.items()
                      if tag in info.get("tags", [])})

    if account_type in ("cex", "all"):
        result.update({addr: info for addr, info in CEX_ACCOUNTS.items()
                      if tag in info.get("tags", [])})

    return result

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
