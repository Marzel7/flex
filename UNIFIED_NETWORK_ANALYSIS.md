# Unified Bidirectional Creator Network Analysis

## Overview

We now have a **complete bidirectional funding flow analysis** that captures both pre- and post-migration funding patterns. This unified view reveals:

1. **PRE-MIGRATION** (Inbound): Who funds creators before token launch
2. **POST-MIGRATION** (Outbound): Where creators consolidate profits after launch
3. **NETWORK COORDINATION**: Creators sharing the same treasury addresses

## Key Findings

### Coordinated Networks (7 Total)

#### 🚨 Network #1 - CRITICAL (5 Creators)
- **Members**: 5 creators, 1 MALICIOUS, 4 SUSPICIOUS
- **Shared Treasuries**: 2 addresses
  - `gdtAELiTGwHY8gmhyXBN5FR5PyxNxGTbDoN3wF1XJ7v`
  - `hi5C6CNiKdZRSbPCMChu9LWE5Dq7oVRtjBA5T5RhFqh`
- **Total Outbound**: 0.07 SOL
- **Inbound Funding**: NONE (self-funded or external capital)
- **Status**: All 5 blocked, already identified as coordinated rug operation

**Members**:
1. 2NuAgVk3hcb7s4YvP4GjV5fD8eDvZQv5wuN6ZC8igRfV (MALICIOUS)
2. 8UwGyvVSLz9SV1qKFSu13xTvhqhdxDpiRjzrjByS8vFo (SUSPICIOUS)
3. 4cVkLoYBeVX6y38DY3XVC756CdfPm3XRd55dnHww6jo8 (SUSPICIOUS)
4. 8k7ixJ9Xou4mkT7zm3pFBFQFvqWkHrdbphiRXfd47T82 (SUSPICIOUS)
5. 4Er1AvGbfzsCtDa4z28aKcJ2oxnvT9kMocPGoR9vcWr4 (SUSPICIOUS)

#### Networks #2-7 (1-4 Creators Each)
- Various blocked creators with post-migration consolidation patterns
- Individual treasuries or shared across 2-5 destinations
- All members already in blocklist

### Individual Creators (16 Total)

#### Top Funded Creators (Pre-Migration)
1. **FNkq7bdnsaqwKmu51PpSNZ7fmmMM8rY23scCJ45** ✓ Safe
   - Inbound: 10 funders, 28.42 SOL (largest inbound funding)
   - Outbound: None
   - Status: External funding hub, no post-migration consolidation

2. **cwPG1BF4GqAPDF8p22zjcNMW65w3YPH3SkmS5Xv** ✓ Safe
   - Inbound: 3 funders, 7.34 SOL
   - Outbound: None

3. **npcP7WAHMXC5MzQbwN67pJtarFcsMqro5NUXZ1mn** 🚨 SUSPICIOUS
   - Inbound: 15 funders, 0.53 SOL (most funders, but low amounts)
   - Outbound: None
   - Status: Blocked creator with distributed funding sources

#### Creators with Post-Migration Activity
1. **7HVWy5o61LmnyYY1VJVXPdrVueVtghaH2qBos25** 🚨 SUSPICIOUS
   - Inbound: None
   - Outbound: 2 destinations, 0.81 SOL (highest single creator outbound)
   - Status: Self-funded, consolidates to 2 addresses

2. **7YmGbGBLMTVxPW17Kxr14VvuAecrbtpWAXCofn9** 🚨 SUSPICIOUS
   - Inbound: None
   - Outbound: 5 destinations, 0.39 SOL (most destinations)
   - Status: Distributed consolidation pattern

## Funding Flow Patterns

### Pattern 1: External Funding Hub
- High inbound (many funders, distributed amounts)
- No outbound consolidation
- Example: FNkq7bdnsaqwKmu51PpSNZ7fmmMM8rY23scCJ45 (28.42 SOL from 10 sources)
- **Interpretation**: Legitimate or semi-legitimate projects funded by external sources

### Pattern 2: Self-Funded Consolidation
- No inbound funding
- High outbound to shared treasuries
- Example: 7HVWy5o61LmnyYY1VJVXPdrVueVtghaH2qBos25 (0.81 SOL consolidated)
- **Interpretation**: Personal capital, consolidating profits to own addresses

### Pattern 3: Coordinated Network
- Multiple creators sharing same treasuries
- All members blocked/suspicious
- Example: The 5-creator MALICIOUS network
- **Interpretation**: Professional rug-pulling organization

### Pattern 4: Distributed Consolidation
- No inbound funding
- Outbound to multiple addresses (5+ destinations)
- Example: 7YmGbGBLMTVxPW17Kxr14VvuAecrbtpWAXCofn9 (0.39 SOL to 5 destinations)
- **Interpretation**: Possible money laundering or sophisticated splitting

## Database Tables Created

### creator_unified_network
```
creator_address TEXT PRIMARY KEY
inbound_funders_count INTEGER
inbound_total_sol REAL
outbound_destinations_count INTEGER
outbound_total_sol REAL
is_coordinated INTEGER (0 or 1)
network_id INTEGER (references creator_network_group)
co_creator_count INTEGER (how many co-creators in network)
reputation TEXT ('MALICIOUS', 'SUSPICIOUS', or NULL)
```

### creator_network_group
```
network_id INTEGER PRIMARY KEY
network_size INTEGER
risk_level TEXT ('CRITICAL', 'HIGH', 'MEDIUM')
blocked_member_count INTEGER
malicious_member_count INTEGER
total_outbound_sol REAL
treasury_addresses TEXT (JSON array)
```

## Key Insights

### The Discrepancy Resolved
**Question**: "Why did we find multi-funder accounts before but not now?"

**Answer**:
- **Before**: Identified COORDINATED CREATOR NETWORKS (5 creators → 2 shared treasuries)
- **Now**: Identified EXTERNAL FUNDING SOURCES (46 different funders → 12 creators)
- **Result**: 0 external funders supply multiple creators (distributed model)
- **BUT**: 7 networks where multiple creators send to same treasury (consolidation model)

### Funding Model Insights
1. **Pre-launch funding is DISTRIBUTED**: Each external funder supplies 1 creator
2. **Post-launch consolidation is CENTRALIZED**: Multiple creators → shared treasuries
3. **No funding cartels detected**: Unlike creators (who coordinate), external funders don't fund multiple creators
4. **Professional operations self-fund**: The malicious 5-creator network had no inbound funding, only internal consolidation

## Risk Scoring Integration Points

### Use Inbound Funding For:
1. **Legitimacy Score**: Creators with external funding are slightly less suspicious
   - High inbound = better legitimacy signal
   - Multiple funders = distributed trust

2. **Funder Reputation**: Track individual funders
   - Funders who fund multiple creators → higher scrutiny
   - Funders associated with rugs → blacklist

### Use Outbound Consolidation For:
1. **Organization Detection**: Multiple creators → same address = network flag
2. **Post-Launch Behavior**: Heavy consolidation after quick peaks = rug signal
3. **Treasury Tracking**: Monitor treasury addresses for coordinated dumps

## Implementation Status

### ✅ Completed
- Bidirectional analysis (`unified_creator_network_view.py`)
- Database tables created (`creator_unified_network`, `creator_network_group`)
- Query tool (`query_unified_network.py`)
- 21 creators analyzed with full funding flows

### ⏳ Next Steps
1. Integrate bidirectional funding into risk scoring
2. Weight external funding for legitimacy signals
3. Flag CRITICAL networks with -90% safety score
4. Monitor treasury addresses for coordinated activity

## Statistics Summary

- **Total creators analyzed**: 21
- **In coordinated networks**: 9
- **Blocked/suspicious**: 13
- **Safe unblocked**: 8
- **Total inbound SOL** (external): 36.60 SOL
- **Total outbound SOL** (consolidation): 1.28 SOL
- **Network consolidation detected**: YES (7 networks)
- **Multi-creator external funders**: NO (distributed model)

