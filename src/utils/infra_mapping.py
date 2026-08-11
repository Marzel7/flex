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

    # RapidLaunch (Token Launch Platform/Automation)
    "rapidXMVLw5uBieKHDGvF9k4xSSDXyD2FC5wLTAajaJ": {
        "name": "RapidLaunch",
        "category": "automation",
        "description": "RapidLaunch token launch platform & automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "rapidlaunch", "launch-platform"],
    },
    # Axiom (Monitoring/Automation Infrastructure)
    "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk": {
        "name": "Axiom",
        "category": "automation",
        "description": "Automation & monitoring infrastructure (Axiom program)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "oracle"],
    },
    # Axiom Trade (trading platform / launchpad funding wallet) — many unrelated users route
    # launches through it, so clusters funded by it are PLATFORM, not a single operator.
    "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9": {
        "name": "Axiom Trade",
        "category": "platform",
        "platform": "Axiom Trade",
        "description": "Axiom Trade — trading platform / launchpad funding wallet",
        "risk_level": "neutral",
        "tags": ["infra", "platform", "axiom", "trade", "launchpad"],
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

    # Pump.fun Fee Account
    "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM": {
        "name": "Pump.fun Fee Account",
        "category": "automation",
        "description": "Pump.fun platform fee collection account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "pumpfun", "fees"],
    },

    # Phantom Fees Account
    "8psNvWTrdNTiVRNzAgsou9kETXNJm2SXZyaKuJraVRtf": {
        "name": "Phantom Fees",
        "category": "automation",
        "description": "Phantom wallet fee collection account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "phantom", "fees"],
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

    # Bloxroute (MEV/Routing Infrastructure)
    "95cfoy472fcQHaw4tPGBTKpn6ZQnfEPfBgDQx6gcRmRg": {
        "name": "Bloxroute",
        "category": "automation",
        "description": "Bloxroute MEV routing & transaction optimization (bloxroute1.sol)",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "bloxroute", "mev", "routing"],
    },

    # OKX Router
    "ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn": {
        "name": "OKX Router",
        "category": "automation",
        "description": "OKX DEX router/aggregator account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "okx", "router"],
    },

    # OKX DEX Authority
    "HV1KXxWFaSeriyFvXyx48FqG9BoFbfinB8njCJonqP7K": {
        "name": "OKX DEX Authority",
        "category": "automation",
        "description": "OKX DEX liquidity and swap authority account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "okx", "dex", "authority"],
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

    # deBridge Bridge Vault
    "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS": {
        "name": "deBridge Bridge Vault",
        "category": "bridge",
        "description": "deBridge bridge vault for cross-chain token transfers and liquidity management",
        "risk_level": "neutral",
        "tags": ["infra", "bridge", "debridge", "cross-chain"],
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

    # Phantom Fees
    "9yj3zvLS3fDMqi1F8zhkaWfq8TZpZWHe6cz1Sgt7djXf": {
        "name": "Phantom Fees",
        "category": "automation",
        "description": "Phantom wallet fee collection account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "phantom", "wallet", "fees"],
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

    # Pump.Fun Migration Account
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg": {
        "name": "Pump.Fun Migration",
        "category": "automation",
        "description": "Pump.Fun token migration and transition account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "pump-fun", "migration"],
    },

    # Rollbit Treasury
    "RBHdGVfDfMjfU6iUfCb1LczMJcQLx7hGnxbzRsoDNvx": {
        "name": "Rollbit Treasury",
        "category": "protocol",
        "description": "Rollbit protocol treasury account - exclude from network analysis",
        "risk_level": "neutral",
        "tags": ["infra", "protocol", "rollbit", "treasury"],
    },

    # PYUSD (PayPal Stablecoin)
    "76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp": {
        "name": "PYUSD",
        "category": "automation",
        "description": "PayPal USD stablecoin - bulk consolidation/sweeper bot activity",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "pyusd", "stablecoin", "sweeper"],
    },

    # SolCasino
    "6qkh2JcHt3ctFeiL4HBn1e9NU5aPw25XNhtgEv6ZCJ4U": {
        "name": "SolCasino",
        "category": "protocol",
        "description": "SolCasino protocol treasury/distribution account - exclude from network analysis",
        "risk_level": "neutral",
        "tags": ["infra", "protocol", "solcasino", "treasury"],
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

    # Privacy Cash Pool
    "4AV2Qzp3N4c9RfzyEbNZs2wqWfW4EwKnnxFAZCndvfGh": {
        "name": "Privacy Cash Pool",
        "category": "automation",
        "description": "Privacy Cash Pool protocol account for privacy-enhanced transactions",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "privacy", "protocol"],
    },

    # Hyperunit Hot Wallet (Funding Intermediary)
    "9SLPTL41SPsYkgdsMzdfJsxymEANKr5bYoBsQzJyKpKS": {
        "name": "Hyperunit Hot Wallet",
        "category": "automation",
        "description": "Hyperunit funding intermediary/distribution account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "hyperunit", "wallet"],
    },

    # Terminal (Padre) Program - Infrastructure
    "term9YPb9mzAsABaqN71A4xdbxHmpBNZavpBiQKZzN3": {
        "name": "Terminal (Padre) Program",
        "category": "automation",
        "description": "Terminal/Padre program for transaction processing",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "terminal", "padre"],
    },

    # Padre Fee Wallet 1
    "J5XGHmzrRmnYWbmw45DbYkdZAU2bwERFZ11qCDXPvFB5": {
        "name": "Padre Fee Wallet 1",
        "category": "automation",
        "description": "Padre fee collection wallet 1",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "padre", "fees"],
    },

    # Padre Fee Wallet 2
    "DoAsxPQgiyAxyaJNvpAAUb2ups6rbJRdYrCPyWxwRxBb": {
        "name": "Padre Fee Wallet 2",
        "category": "automation",
        "description": "Padre fee collection wallet 2",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "padre", "fees"],
    },

    # Fireblocks Custody (Institutional Custody Infrastructure)
    "HVRcXaCFyUFG7iZLm3T1Qn8ZGDMHj3P3BpezUfWfRf2x": {
        "name": "Fireblocks Custody",
        "category": "automation",
        "description": "Fireblocks institutional custody and blockchain security platform",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "fireblocks", "custody", "institutional"],
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

    # Binance 2
    # Binance 2 - Verified
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": {
        "name": "Binance 2",
        "category": "cex",
        "exchange": "Binance",
        "description": "Binance exchange wallet for SOL trading and withdrawals",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "exchange"],
    },

    # Binance Deposit - Verified
    "21wG4F3ZR8gwGC47CkpD6ySBUgH9AABtYMBWFiYdTTgv": {
        "name": "Binance Deposit",
        "category": "cex",
        "exchange": "Binance",
        "description": "Binance exchange wallet for SOL deposits",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "exchange", "deposit"],
    },

    # Binance Deposit - user-confirmed
    "5dQL3XafngCoq5s1tDatNzjdjfXv63Si9LU6K9gjPeqV": {
        "name": "Binance Deposit",
        "category": "cex",
        "exchange": "Binance",
        "description": "Binance deposit wallet (user-confirmed)",
        "risk_level": "neutral",
        "tags": ["cex", "binance", "exchange", "deposit"],
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

    # Coinbase - Hot Wallet 1
    "FpwQQhQQoEaVu3WU2qZMfF1hx48YyfwsLoRgXG83E99Q": {
        "name": "Coinbase Hot Wallet 1",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Hot Wallet 2
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE": {
        "name": "Coinbase Hot Wallet 2",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Wallet 2
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm": {
        "name": "Coinbase Wallet 2",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Hot Wallet 3
    "D89hHJT5Aqyx1trP6EnGY9jJUB3whgnq3aUvvCqedvzf": {
        "name": "Coinbase Hot Wallet 3",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Hot Wallet 1
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": {
        "name": "Coinbase Hot Wallet 1",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Hot Wallet 4
    "4NyK1AdJBNbgaJ9EsKz3J4rfeHsuYdjkTPg3JaNdLeFw": {
        "name": "Coinbase Hot Wallet 4",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Hot Wallet 4 (Old)
    "DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo": {
        "name": "Coinbase Hot Wallet 4 (Old)",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Wallet 4
    "9obNtb5GyUegcs3a1CbBkLuc5hEWynWfJC6gjz5uWQkE": {
        "name": "Coinbase Wallet 4",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Wallet 5
    "59L2oxymiQQ9Hvhh92nt8Y7nDYjsauFkdb3SybdnsG6h": {
        "name": "Coinbase Wallet 5",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange"],
    },

    # Coinbase - Prime
    "3vxheE5C46XzK4XftziRhwAf8QAfipD7HXXWj25mgkom": {
        "name": "Coinbase Prime",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase Prime institutional custody account",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange", "institutional"],
    },

    # Coinbase - Cold Wallet 1
    "CKy3KzEMSL1PQV6Wppggoqi2nGA7teE4L7JipEK89yqj": {
        "name": "Coinbase Cold Wallet 1",
        "category": "cex",
        "exchange": "Coinbase",
        "description": "Coinbase cold storage wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "coinbase", "exchange", "cold-storage"],
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

    # OKX Hot Wallet
    "is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV": {
        "name": "OKX Hot Wallet",
        "category": "cex",
        "exchange": "OKX",
        "description": "OKX exchange hot wallet for trading and withdrawals",
        "risk_level": "neutral",
        "tags": ["cex", "okx", "exchange", "hot-wallet"],
    },

    # Kraken - Hot Wallet
    "6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF": {
        "name": "Kraken Hot Wallet",
        "category": "cex",
        "exchange": "Kraken",
        "description": "Kraken exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "kraken", "exchange"],
    },

    # HitBTC - Hot Wallet
    "DQ5JWbJyWdJeyBxZuuyu36sUBud6L6wo3aN1QC1bRmsR": {
        "name": "HitBTC Hot Wallet",
        "category": "cex",
        "exchange": "HitBTC",
        "description": "HitBTC exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "hitbtc", "exchange"],
    },

    # Moonpay - Verified
    "Cc3bpPzUvgAzdW9Nv7dUQ8cpap8Xa7ujJgLdpqGrTCu6": {
        "name": "Moonpay",
        "category": "cex",
        "exchange": "Moonpay",
        "description": "Moonpay crypto exchange wallet",
        "risk_level": "neutral",
        "tags": ["cex", "moonpay", "exchange"],
    },

    # KuCoin - Verified
    "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6": {
        "name": "KuCoin 2",
        "category": "cex",
        "exchange": "KuCoin",
        "description": "KuCoin exchange wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "kucoin", "exchange"],
    },

    # Fireblocks Custody - Verified
    "2nvBrX3EdKqhpqUMAhCu3LcEDno1tkbn8A95aGAocUNQ": {
        "name": "Fireblocks Custody",
        "category": "cex",
        "exchange": "Fireblocks",
        "description": "Fireblocks institutional custody wallet",
        "risk_level": "neutral",
        "tags": ["cex", "fireblocks", "custody", "institutional"],
    },

    # Bidget Exchange - Verified
    "A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR": {
        "name": "Bidget Exchange",
        "category": "cex",
        "exchange": "Bidget",
        "description": "Bidget exchange wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "bidget", "exchange"],
    },

    # ChangeNow - Verified
    "G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t": {
        "name": "ChangeNow",
        "category": "cex",
        "exchange": "ChangeNow",
        "description": "ChangeNow crypto exchange wallet",
        "risk_level": "neutral",
        "tags": ["cex", "changenow", "exchange"],
    },

    # MEXC Hot Wallet
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ": {
        "name": "MEXC Hot Wallet",
        "category": "cex",
        "exchange": "MEXC",
        "description": "MEXC exchange hot wallet for SOL trading and withdrawals",
        "risk_level": "neutral",
        "tags": ["cex", "mexc", "exchange"],
    },

    # HTX Hot Wallet (formerly Huobi)
    "BY4StcU9Y2BpgH8quZzorg31EGE4L1rjomN8FNsCBEcx": {
        "name": "HTX Hot Wallet",
        "category": "cex",
        "exchange": "HTX",
        "description": "HTX exchange hot wallet for SOL (formerly Huobi)",
        "risk_level": "neutral",
        "tags": ["cex", "htx", "exchange"],
    },

    # Robinhood Hot Wallet 1
    "EMXJqHznGSnSzeMyigBGQNEFw4EeaNDbj1UwaFTpp3sg": {
        "name": "Robinhood Hot Wallet 1",
        "category": "cex",
        "exchange": "Robinhood",
        "description": "Robinhood exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "robinhood", "exchange"],
    },

    # FixedFloat Exchange
    "5ndLnEYqSFiA5yUFHo6LVZ1eWc6Rhh11K5CfJNkoHEPs": {
        "name": "FixedFloat Exchange",
        "category": "cex",
        "exchange": "FixedFloat",
        "description": "FixedFloat exchange wallet for SOL swaps",
        "risk_level": "neutral",
        "tags": ["cex", "fixedfloat", "exchange"],
    },

    # Bybit Hot Wallet
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": {
        "name": "Bybit Hot Wallet",
        "category": "cex",
        "exchange": "Bybit",
        "description": "Bybit exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "bybit", "exchange"],
    },

    # Crypto.com Hot Wallet 2
    "AobVSwdW9BbpMdJvTqeCN4hPAmh4rHm7vwLnQ5ATSyrS": {
        "name": "Crypto.com Hot Wallet 2",
        "category": "cex",
        "exchange": "Crypto.com",
        "description": "Crypto.com exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "cryptocom", "exchange"],
    },

    # Stake.com Hot Wallet
    "G9X7F4JzLzbSGMCndiBdWNi5YzZZakmtkdwq7xS3Q3FE": {
        "name": "Stake.com Hot Wallet",
        "category": "cex",
        "exchange": "Stake.com",
        "description": "Stake.com exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "stakecom", "exchange"],
    },

    # Robinhood Hot Wallet 2
    "4xLpwxgYuPwPvtQjE94RLS4WZ4aD8NJYYKr2AJk99Qdg": {
        "name": "Robinhood Hot Wallet 2",
        "category": "cex",
        "exchange": "Robinhood",
        "description": "Robinhood exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "robinhood", "exchange"],
    },

    # Revolut Hot Wallet
    "Biw4eeaiYYYq6xSqEd7GzdwsrrndxA8mqdxfAtG3PTUU": {
        "name": "Revolut Hot Wallet",
        "category": "cex",
        "exchange": "Revolut",
        "description": "Revolut exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "revolut", "exchange"],
    },

    # Robinhood Hot Wallet 3
    "5HQZd9ovzAF1TLnHRAq1zcSnXC9HAp3EwhoxMHvo8rxB": {
        "name": "Robinhood Hot Wallet 3",
        "category": "cex",
        "exchange": "Robinhood",
        "description": "Robinhood exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "robinhood", "exchange"],
    },

    # Robinhood Hot Wallet 4
    "DwdrYTtTWHfnfJBiN2RH6EgPbquDQLjZTfTwpykPEq1g": {
        "name": "Robinhood Hot Wallet 4",
        "category": "cex",
        "exchange": "Robinhood",
        "description": "Robinhood exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "robinhood", "exchange"],
    },

    # Robinhood Hot Wallet 5
    "HjuvLWkeoRVuiDCVjZeWrj3cYzG2KV9CExqEJqJQJqJA": {
        "name": "Robinhood Hot Wallet 5",
        "category": "cex",
        "exchange": "Robinhood",
        "description": "Robinhood exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "robinhood", "exchange"],
    },

    # Robinhood Hot Wallet 6
    "GrwuVxpLsTPcvASi3HGLtwsQpzYCyxQgu4DQ9tpmVAW8": {
        "name": "Robinhood Hot Wallet 6",
        "category": "cex",
        "exchange": "Robinhood",
        "description": "Robinhood exchange hot wallet for SOL",
        "risk_level": "neutral",
        "tags": ["cex", "robinhood", "exchange"],
    },

    # BingX - Verified
    "J1BDJEdvTmmcjeTMVTHLPaaNvuQ3mdxeuWEM1YyMksLy": {
        "name": "BingX",
        "category": "cex",
        "exchange": "BingX",
        "description": "BingX exchange wallet (verified on-chain)",
        "risk_level": "neutral",
        "tags": ["cex", "bingx", "exchange"],
    },

    # Nexo Hot Wallet (Lending Platform/Exchange)
    "jmLtKSEWyQhCbHgTa52XZwcfLQ2ze9J8VN3nfSTEjt4": {
        "name": "Nexo Hot Wallet",
        "category": "cex",
        "exchange": "Nexo",
        "description": "Nexo lending platform hot wallet for customer deposits/withdrawals",
        "risk_level": "neutral",
        "tags": ["cex", "nexo", "lending", "exchange"],
    },

    # Placeholder for future real addresses
    # TO ADD NEW CEX ADDRESSES - STRICT VERIFICATION REQUIRED:
    # 1. Must check Solscan.io directly for confirmed transaction history
    # 2. Must have visible SOL transfers or token activity
    # 3. Must cross-reference with official exchange documentation
    # 4. Community sources alone are NOT sufficient (need Solscan proof)
    # 5. Verify address naming matches exchange branding
}

# User-verified external labels added from Arkham/manual review.
INFRASTRUCTURE_ACCOUNTS.update({
    "NexTBLockJYZ7QD7p2byrUa6df8ndV2WSd8GkbWqfbb": {
        "name": "NextBlock",
        "category": "automation",
        "description": "NextBlock transaction routing / block-building infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "nextblock", "routing"],
    },
    "F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe": {
        "name": "Relay.link Solver",
        "category": "relay",
        "description": "Relay.link solver / cross-chain routing infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "relay", "solver", "relay-link"],
    },
    "CreQJ2t94QK5dsxUZGXfPJ8Nx7wA9LHr5chxjSMkbNft": {
        "name": "MEV Bot (CreQJ)",
        "category": "automation",
        "description": "MEV bot / automated trading infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "mev", "bot"],
    },
    "2Hgx1GjKRuH9H21Fzz7uGiqh1Fcz3wMP5PmgpDbyDDYp": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "95ipm63sgxs1oHtn9rHfuvPKRRA6ZGeL7yBni8aynDW5": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "57bLoghjgoLtiUQpLiL78fP4VeVUqFc3sYzKoUhvvZZ2": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "8d9FNC7AgKLTCPKNd3MMkLLXZYLmiYFYR3vfXMBNJVNx": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "DhEsUaJkT1DzkFUWLCkU21VruJQZk1es4zBRhU9QjK9R": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "GwyFkQiUgR54nJf6kYV6e1zVj5zvwmPUUG2TzvhBPEiv": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "AkHnjaCgLdE8Rko25hju7qeSWf1Caf9tXpBzgCtij5Ld": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "435eCQn4AqX2nDHLWdkfaXxJC6y8cHuhSv8VF3cTp52S": {
        "name": "Fireblocks Custody",
        "category": "custody",
        "description": "Fireblocks institutional custody infrastructure",
        "risk_level": "neutral",
        "tags": ["infra", "custody", "fireblocks", "institutional"],
    },
    "84DGj4Qpypcaa5uxdzphYzkutEUTvxLWSuWq3BoJLxkp": {
        "name": "BonkBot Rewards",
        "category": "automation",
        "description": "BonkBot rewards / automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "bonkbot", "rewards"],
    },
    "7HeD6sLLqAnKVRuSfc1Ko3BSPMNKWgGTiWLKXJF31vKM": {
        "name": "BloomBot Fees",
        "category": "automation",
        "description": "BloomBot fee collection / automation account",
        "risk_level": "neutral",
        "tags": ["infra", "automation", "bloombot", "fees"],
    },
    "HWjmoUNYckccg9Qrwi43JTzBcGcM1nbdAtATf9GXmz16": {
        "name": "Near Intents Hot Wallet",
        "category": "bridge",
        "description": "Near Intents cross-chain settlement / routing wallet",
        "risk_level": "neutral",
        "tags": ["infra", "bridge", "near", "intents", "hot-wallet"],
    },
})

CEX_ACCOUNTS.update({
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w": {
        "name": "Gate Hot Wallet",
        "category": "cex",
        "exchange": "Gate",
        "description": "Gate exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "gate", "exchange", "hot-wallet"],
    },
    "6dUaYX9Z6aQPY66BgD4yzu1saifGAZLrhQqF9BugJxJ1": {
        "name": "Cobo Hot Wallet",
        "category": "cex",
        "exchange": "Cobo",
        "description": "Cobo hot wallet / custody service",
        "risk_level": "neutral",
        "tags": ["cex", "cobo", "custody", "hot-wallet"],
    },
    "8mowmVCEewZ9W2cEaQyQeQEeSxhGr1hvRviLwozwNtBt": {
        "name": "WhiteBIT Hot Wallet",
        "category": "cex",
        "exchange": "WhiteBIT",
        "description": "WhiteBIT exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "whitebit", "exchange", "hot-wallet"],
    },
    "2UL8hbNaoErAYNePqgQYPP9yDCDRZGWbzKW4krLqkhNL": {
        "name": "Bitvavo Hot Wallet",
        "category": "cex",
        "exchange": "Bitvavo",
        "description": "Bitvavo exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "bitvavo", "exchange", "hot-wallet"],
    },
    "35dszeQQQzkMvjcmyrPWPnN5ZyK9ZjYkNp9kKXZWMvji": {
        "name": "Bitget Wallet",
        "category": "cex",
        "exchange": "Bitget",
        "description": "Bitget exchange wallet",
        "risk_level": "neutral",
        "tags": ["cex", "bitget", "exchange"],
    },
    "8YxUm2CgA67gSxu3Vdeij1cEpfbVRurCFHQnUC2sytFz": {
        "name": "Star Bright Media Hot Wallet",
        "category": "cex",
        "exchange": "Star Bright Media",
        "description": "Star Bright Media hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "star-bright-media", "hot-wallet"],
    },
    "5F1seMKUqSNhv45f6FhB2cFmgJbk8U1avJw7M6TexUq1": {
        "name": "MoonPay Hot Wallet",
        "category": "cex",
        "exchange": "MoonPay",
        "description": "MoonPay hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "moonpay", "exchange", "hot-wallet"],
    },
    "53unSgGWqEWANcPYRF35B2Bgf8BkszUtcccKiXwGGLyr": {
        "name": "Binance US Hot Wallet",
        "category": "cex",
        "exchange": "Binance US",
        "description": "Binance US exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "binance-us", "exchange", "hot-wallet"],
    },
    "4iieWMwLjxHpZLfChMTwJaaBnRUVAFQFpFfTuZ7rFLSb": {
        "name": "Transak Hot Wallet",
        "category": "cex",
        "exchange": "Transak",
        "description": "Transak on/off-ramp hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "transak", "onramp", "hot-wallet"],
    },
    "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD": {
        "name": "OKX Hot Wallet",
        "category": "cex",
        "exchange": "OKX",
        "description": "OKX exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "okx", "exchange", "hot-wallet"],
    },
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5": {
        "name": "Kraken Hot Wallet",
        "category": "cex",
        "exchange": "Kraken",
        "description": "Kraken exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "kraken", "exchange", "hot-wallet"],
    },
    "ABNL41SwMLVRaCq6okEWsWyvLwm56nazUNwMrTHgmuyf": {
        "name": "SideShift Hot Wallet",
        "category": "cex",
        "exchange": "SideShift",
        "description": "SideShift exchange hot wallet",
        "risk_level": "neutral",
        "tags": ["cex", "sideshift", "exchange", "hot-wallet"],
    },
})

# PumpFun Token Creator accounts
# IMPORTANT: These are NOT excluded from suspicious analysis
# They are tracked for identification purposes only
PUMPFUN_TOKEN_CREATORS = {
    # PumpFun Token Creator
    "CfumDPwfYn6m3W6fyzCMhsYkS2Uxpeu1npxZPUasV5nX": {
        "name": "PumpFun Token Creator",
        "category": "platform",
        "platform": "PumpFun",
        "description": "PumpFun token creator/launcher account",
        "risk_level": "unknown",
        "tags": ["pumpfun", "creator", "launcher"],
    },

    # PumpFun Token Creator
    "GwpcTgEagp7gjmdVs4jumvaHhDzrr9QdYVVYvzb6AZT": {
        "name": "PumpFun Token Creator",
        "category": "platform",
        "platform": "PumpFun",
        "description": "PumpFun token creator/launcher account",
        "risk_level": "unknown",
        "tags": ["pumpfun", "creator", "launcher"],
    },

    # PumpFun Token Creator
    "DuGezKLZp8UL2aQMHthoUibEC7WSbpNiKFJLTtK1QHjx": {
        "name": "PumpFun Token Creator",
        "category": "platform",
        "platform": "PumpFun",
        "description": "PumpFun token creator/launcher account",
        "risk_level": "unknown",
        "tags": ["pumpfun", "creator", "launcher"],
    },

    # PumpFun Token Creator
    "CTSvhyNscqSA6NRHDycxR3dsgn3ErMJzgsXpQyDrCbDi": {
        "name": "PumpFun Token Creator",
        "category": "platform",
        "platform": "PumpFun",
        "description": "PumpFun token creator/launcher account",
        "risk_level": "unknown",
        "tags": ["pumpfun", "creator", "launcher"],
    },

    # PumpFun Token Creator
    "Fsss6uvqNeapk2zrouXeb8VXYyVUxLR2Yke7VfKxVujB": {
        "name": "PumpFun Token Creator",
        "category": "platform",
        "platform": "PumpFun",
        "description": "PumpFun token creator/launcher account",
        "risk_level": "unknown",
        "tags": ["pumpfun", "creator", "launcher"],
    },
}

# Suspicious wallet types (NOT excluded from suspicious analysis)
SUSPICIOUS_WALLET_TYPES = {
    # Unknown Ops Wallets
    "8mowmVCEewZ9W2cEaQyQeQEeSxhGr1hvRviLwozwNtBt": {
        "name": "Unknown Ops",
        "category": "suspicious",
        "wallet_type": "unknown_ops",
        "description": "Unknown operations wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "unknown_ops", "operations"],
    },

    "76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp": {
        "name": "Unknown Ops",
        "category": "suspicious",
        "wallet_type": "unknown_ops",
        "description": "Unknown operations wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "unknown_ops", "operations"],
    },

    "8d9FNC7AgKLTCPKNd3MMkLLXZYLmiYFYR3vfXMBNJVNx": {
        "name": "Unknown Ops",
        "category": "suspicious",
        "wallet_type": "unknown_ops",
        "description": "Unknown operations wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "unknown_ops", "operations"],
    },

    # Active Trading Wallets
    "AaZkwhkiDStDcgrU37XAj9fpNLrD8Erz5PNkdm4k5hjy": {
        "name": "Active Trading",
        "category": "suspicious",
        "wallet_type": "active_trading",
        "description": "Active trading wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "active_trading", "trading"],
    },

    "69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk": {
        "name": "Active Trading",
        "category": "suspicious",
        "wallet_type": "active_trading",
        "description": "Active trading wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "active_trading", "trading"],
    },

    "GLdaJ4YFRJ4cLuAQ1KbQfLmnfus33nKPsjMNgCxvNsBj": {
        "name": "Active Trading",
        "category": "suspicious",
        "wallet_type": "active_trading",
        "description": "Active trading wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "active_trading", "trading"],
    },

    "J8AJAD5VkTMA1ycAJkDhT44jyxnZLhJjPWyPb51gV68n": {
        "name": "Active Trading",
        "category": "suspicious",
        "wallet_type": "active_trading",
        "description": "Active trading wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "active_trading", "trading"],
    },

    "RZa9cGkpk71NJ2A2KP19mMMGxYABRhqMmsxYHip1aPm": {
        "name": "Active Trading",
        "category": "suspicious",
        "wallet_type": "active_trading",
        "description": "Active trading wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "active_trading", "trading"],
    },

    "GaYwwwSjjFe5iuvKCZHn9E4ypL9QmC4foJLRUw28cuTr": {
        "name": "Active Trading",
        "category": "suspicious",
        "wallet_type": "active_trading",
        "description": "Active trading wallet - suspicious funding pattern",
        "risk_level": "high",
        "tags": ["suspicious", "active_trading", "trading"],
    },

    # Bidget Exchange
    "A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR": {
        "name": "Bidget Exchange",
        "category": "cex",
        "exchange": "Bidget",
        "description": "Bidget exchange wallet (CEX account)",
        "risk_level": "neutral",
        "tags": ["cex", "bidget", "exchange"],
    },
}

# Custom user-labeled accounts (NOT infrastructure, NOT CEX)
CUSTOM_ACCOUNTS = {
    # User-labeled accounts for tracking/monitoring purposes
    "GZVSEAajExLJEvACHHQcujBw7nJq98GWUEZtood9LM9b": {
        "name": "mrdevvvv",
        "category": "user",
        "description": "Custom user-labeled account (mrdevvvv)",
        "risk_level": "neutral",
        "tags": ["custom", "user-labeled", "mrdevvvv"],
    },
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


def get_funder_label(address: str) -> Optional[Dict]:
    """Unified lookup for funder classification: CEX, then platform/infrastructure. Returns
    {name, kind} where kind = 'CEX' | 'PLATFORM' | 'INFRA', or None for an unknown wallet
    (a candidate operator). Used by the farm detector to tag who a cluster's funder really is."""
    c = CEX_ACCOUNTS.get(address)
    if c:
        return {"name": c.get("name") or c.get("exchange"), "kind": "CEX"}
    i = INFRASTRUCTURE_ACCOUNTS.get(address)
    if i:
        cat = (i.get("category") or "").lower()
        kind = "PLATFORM" if cat in ("platform",) else "INFRA"
        return {"name": i.get("name"), "kind": kind}
    return None

def get_pumpfun_creator_info(address: str) -> Optional[Dict]:
    """Get PumpFun token creator info (NOT excluded from suspicious analysis)"""
    return PUMPFUN_TOKEN_CREATORS.get(address)

def is_pumpfun_token_creator(address: str) -> bool:
    """Check if account is a known PumpFun token creator"""
    return address in PUMPFUN_TOKEN_CREATORS

def get_suspicious_wallet_info(address: str) -> Optional[Dict]:
    """Get suspicious wallet type info (NOT excluded from suspicious analysis)"""
    return SUSPICIOUS_WALLET_TYPES.get(address)

def is_suspicious_wallet_type(address: str) -> bool:
    """Check if account is a known suspicious wallet type"""
    return address in SUSPICIOUS_WALLET_TYPES

def is_infrastructure_account(address: str) -> bool:
    """Check if account is known infrastructure (not CEX)"""
    return address in INFRASTRUCTURE_ACCOUNTS

def is_cex_account(address: str) -> bool:
    """Check if account is a known CEX"""
    return address in CEX_ACCOUNTS

def is_custom_account(address: str) -> bool:
    """Check if account is a user-labeled custom account"""
    return address in CUSTOM_ACCOUNTS

def get_custom_info(address: str) -> Optional[Dict]:
    """Get custom account info"""
    return CUSTOM_ACCOUNTS.get(address)

def is_known_account(address: str) -> bool:
    """Check if account is infrastructure, CEX, or custom"""
    return address in INFRASTRUCTURE_ACCOUNTS or address in CEX_ACCOUNTS or address in CUSTOM_ACCOUNTS

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

def classify_wallet(address: str, db_conn=None) -> Dict:
    """
    Single authoritative wallet classification.

    Priority:
      1. Static INFRASTRUCTURE_ACCOUNTS registry  → wallet_type='infra'
      2. Static CEX_ACCOUNTS registry             → wallet_type='cex'
      3. cex_wallets DB table (if conn provided)  → wallet_type='cex'
      4. Everything else                          → wallet_type='unknown'

    Returns:
      {'wallet_type': 'cex'|'infra'|'unknown', 'label': str, 'source': str}
    """
    if address in INFRASTRUCTURE_ACCOUNTS:
        info = INFRASTRUCTURE_ACCOUNTS[address]
        return {'wallet_type': 'infra', 'label': info.get('name', ''), 'source': 'static_registry'}
    if address in CEX_ACCOUNTS:
        info = CEX_ACCOUNTS[address]
        return {'wallet_type': 'cex', 'label': info.get('name', ''), 'source': 'static_registry'}
    if db_conn is not None:
        try:
            row = db_conn.execute(
                "SELECT 1 FROM cex_wallets WHERE cex_address=? AND is_active=1 LIMIT 1",
                (address,)
            ).fetchone()
            if row:
                return {'wallet_type': 'cex', 'label': '', 'source': 'cex_wallets'}
        except Exception:
            pass
    return {'wallet_type': 'unknown', 'label': '', 'source': 'unknown'}


def ensure_infra_wallets_table(db_conn) -> None:
    """Create the reusable infra wallet exclusion table."""
    db_conn.execute("""
        CREATE TABLE IF NOT EXISTS infra_wallets (
            address TEXT PRIMARY KEY,
            type TEXT,
            label TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    db_conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_infra_wallets_type
        ON infra_wallets(type)
    """)


def collect_infra_wallet_rows(db_conn, include_cex: bool = True) -> list:
    """
    Read-only scan: gather the full desired (address, type, label) state from
    static registries and observed DB tables (including the three full
    token_analysis SELECT DISTINCT scans). Performs no writes and does not
    require a write lease -- callers should run this on a read-only
    connection so the ~2min scan never blocks concurrent writers.
    """
    rows = []

    for address, info in INFRASTRUCTURE_ACCOUNTS.items():
        rows.append((
            address,
            info.get("category") or "infra",
            info.get("name") or "Infrastructure",
        ))

    if include_cex:
        for address, info in CEX_ACCOUNTS.items():
            rows.append((
                address,
                "cex",
                info.get("name") or info.get("exchange") or "CEX",
            ))

    try:
        for row in db_conn.execute("SELECT funder_address, note FROM infra_funders_observed").fetchall():
            rows.append((row[0], "observed_infra", row[1] or "Observed Infrastructure"))
    except Exception:
        pass

    if include_cex:
        try:
            for row in db_conn.execute("""
                SELECT cex_address, exchange_name, wallet_type
                FROM cex_wallets
                WHERE is_active = 1
            """).fetchall():
                label = row[1] if row[2] in ("Hot Wallet", "Exchange Wallet") else f"{row[1]} {row[2]}"
                rows.append((row[0], "cex", label))
        except Exception:
            pass

    # Dynamic protocol accounts discovered from token records. These are
    # protocol mechanics, never coordination evidence.
    dynamic_sources = [
        ("bonding_curve_pda", "bonding_curve", "Pump.fun Bonding Curve"),
        ("pool_address", "pool", "Token Pool"),
        ("pumpswap_pool_address", "pumpswap_pool", "PumpSwap Pool"),
    ]
    for column, wallet_type, label in dynamic_sources:
        try:
            for row in db_conn.execute(f"""
                SELECT DISTINCT {column}
                FROM token_analysis
                WHERE {column} IS NOT NULL AND {column} != ''
            """).fetchall():
                rows.append((row[0], wallet_type, label))
        except Exception:
            pass

    return [(address, wallet_type, label) for address, wallet_type, label in rows if address]


def write_infra_wallet_deltas(db_conn, rows: list) -> int:
    """
    Short write phase: given the desired state from collect_infra_wallet_rows,
    diff against what's currently persisted and upsert only changed rows.
    Callers should open the write connection immediately before this call
    and commit/close immediately after, so the write lease is held only for
    the handful of actual changes, not the full scan.
    """
    ensure_infra_wallets_table(db_conn)

    current = {
        row[0]: (row[1], row[2])
        for row in db_conn.execute("SELECT address, type, label FROM infra_wallets").fetchall()
    }

    written = 0
    for address, wallet_type, label in rows:
        if current.get(address) == (wallet_type, label):
            continue
        db_conn.execute("""
            INSERT INTO infra_wallets (address, type, label, updated_at)
            VALUES (?, ?, ?, strftime('%s','now'))
            ON CONFLICT(address) DO UPDATE SET
                type = excluded.type,
                label = excluded.label,
                updated_at = excluded.updated_at
        """, (address, wallet_type, label))
        written += 1
    return written


def sync_infra_wallets(db_conn, include_cex: bool = True) -> int:
    """
    Seed/update infra_wallets from static registries and observed DB tables.

    Raw transfer tables remain untouched. This table is the interpretation-layer
    exclusion list used by network, coordination, 2H, and fingerprint builders.

    Kept as a single-connection convenience wrapper for existing callers that
    don't care about read/write lease separation (e.g. one-off scripts). The
    scheduler path (infra_sync_scheduler.run_once) uses
    collect_infra_wallet_rows + write_infra_wallet_deltas directly on
    separate connections instead, since it's the caller whose ~2min scan
    duration actually matters for write-lease contention.
    """
    ensure_infra_wallets_table(db_conn)
    rows = collect_infra_wallet_rows(db_conn, include_cex=include_cex)
    return write_infra_wallet_deltas(db_conn, rows)


def build_excluded_set(
    db_conn=None, candidate_addresses=None, include_token_analysis: bool = True
) -> frozenset:
    """
    Build a complete set of all CEX + infra addresses for bulk exclusion.
    Combines static registry with the live cex_wallets table.
    Safe to call with db_conn=None (returns static-only set).
    """
    excluded = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())
    if db_conn is not None:
        try:
            rows = db_conn.execute("SELECT address FROM infra_wallets").fetchall()
            excluded.update(r[0] for r in rows)
        except Exception:
            pass
        try:
            rows = db_conn.execute(
                "SELECT cex_address FROM cex_wallets WHERE is_active=1"
            ).fetchall()
            excluded.update(r[0] for r in rows)
        except Exception:
            pass
        try:
            rows = db_conn.execute("SELECT funder_address FROM infra_funders_observed").fetchall()
            excluded.update(r[0] for r in rows)
        except Exception:
            pass
        candidates = tuple(dict.fromkeys(candidate_addresses or ()))
        dynamic_columns = (
            ("bonding_curve_pda", "pool_address", "pumpswap_pool_address")
            if include_token_analysis else ()
        )
        for column in dynamic_columns:
            try:
                if candidates:
                    placeholders = ",".join("?" for _ in candidates)
                    rows = db_conn.execute(
                        f"SELECT DISTINCT {column} FROM token_analysis "
                        f"WHERE {column} IN ({placeholders})",
                        candidates,
                    ).fetchall()
                else:
                    rows = db_conn.execute(f"""
                        SELECT DISTINCT {column}
                        FROM token_analysis
                        WHERE {column} IS NOT NULL AND {column} != ''
                    """).fetchall()
                excluded.update(r[0] for r in rows)
            except Exception:
                pass
    return frozenset(excluded)


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

from src.utils.infra_mapping import highlight_infra_in_funding

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
