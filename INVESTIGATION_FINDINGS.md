# Investigation: Multi-Funder Accounts Discovery

## The Question

> "How come when we did this before we found accounts that were multfunding"

## The Investigation

You asked why the current extraction found **0 multi-creator external funders** when previous work apparently found accounts that funded multiple creators.

## The Answer: Two Different Funding Patterns

### What We Found Before (POST-Launch)
The previous analysis identified a **COORDINATED CREATOR NETWORK**:
- **5 creators** all sending SOL to **2 shared treasury addresses**
- From the treasury's perspective: receives from multiple creators (looks like "multi-funding")
- Pattern: Professional rug-pulling organization consolidating profits
- Status: All 5 creators already blocked

**Treasury Addresses** (receiving from multiple creators):
- `hi5C6CNiKdZRSbPCMChu9LWE5Dq7oVRtjBA5T5RhFqh` ← receives from 4 creators
- `gdtAELiTGwHY8gmhyXBN5FR5PyxNxGTbDoN3wF1XJ7v` ← receives from 3 creators

### What We Found Now (PRE-Launch)
The current extraction identified **DISTRIBUTED EXTERNAL FUNDING**:
- **46 different external funders** supplying SOL to creators before token launch
- Each funder supplies **only 1 creator** (no multi-creator funders)
- Pattern: Individual capital providers, not coordinated
- Total: 36.60 SOL distributed across 6 creators

## Why the Discrepancy?

The key insight is **two separate funding phases**:

```
PHASE 1: PRE-MIGRATION (Preparation)
├─ External funders → Individual creators
├─ 46 funders → 6 creators  
├─ 36.60 SOL total
└─ RESULT: 0 multi-creator funders (distributed model)

     ↓ TOKEN LAUNCHES ↓

PHASE 2: POST-MIGRATION (Consolidation)
├─ Individual creators → Shared treasuries
├─ 5 creators → 2 addresses
├─ 0.07 SOL consolidated
└─ RESULT: Multi-creator consolidation (DETECTED!)
```

## Key Findings

### External Funding is Distributed
- No funding cartels were detected
- Each external funder supplies 1 creator
- No pattern of external entities funding multiple creators

### Creator Networks are Coordinated (POST-Launch)
- 7 coordinated networks identified
- Creators share treasury addresses
- All members already blocked

### The Coordinated 5-Creator Network Details

**Members**:
1. 2NuAgVk3hcb7s4YvP4GjV5fD8eDvZQv5wuN6ZC8igRfV (MALICIOUS - 2+ rugs)
2. 8UwGyvVSLz9SV1qKFSu13xTvhqhdxDpiRjzrjByS8vFo (SUSPICIOUS - 1 rug)
3. 4cVkLoYBeVX6y38DY3XVC756CdfPm3XRd55dnHww6jo8 (SUSPICIOUS - 1 rug)
4. 8k7ixJ9Xou4mkT7zm3pFBFQFvqWkHrdbphiRXfd47T82 (SUSPICIOUS - 1 rug)
5. 4Er1AvGbfzsCtDa4z28aKcJ2oxnvT9kMocPGoR9vcWr4 (SUSPICIOUS - 1 rug)

**Shared Treasuries**:
- `hi5C6CNiKdZRSbPCMChu9LWE5Dq7oVRtjBA5T5RhFqh` (receives from 4 creators)
- `gdtAELiTGwHY8gmhyXBN5FR5PyxNxGTbDoN3wF1XJ7v` (receives from 3 creators)

**Network Statistics**:
- Total rugs: 4 out of 7 tokens (57%)
- Shared treasury pattern: Professional operation
- Pre-migration funding: NONE (self-funded)
- Post-migration consolidation: 0.07 SOL to shared addresses

## Why Pre-Launch Funding is Distributed

Several explanations:

1. **Legitimate projects**: Real developers fund their own projects
2. **Individual funders**: Angel investors/early believers fund specific projects
3. **No incentive to multi-fund**: External funders have no reason to supply multiple creators
4. **Risk distribution**: Multi-creator funding would create visible patterns

Contrast with **coordinated creators** who MUST consolidate to maintain secrecy:
- Multiple profit streams → single treasury = harder to track
- Shared wallet = proof of coordination
- Better for money laundering

## The Business Model

### Legitimate External Funding
- Founders/teams fundraise individually
- Each token has its own funding source(s)
- Different funders for different projects

### Coordinated Rug Operations
- Single person/team runs multiple creator accounts
- Creates many tokens to maximize rug profits
- Consolidates to shared treasuries to collect proceeds
- Makes money from: multiple rug pulls + token fees

## Implications

1. **External funder networks don't exist** (in this dataset)
   - No cartel of funders supplying many creators
   - No "funder families" like we see with creator networks

2. **Creator networks are the real threat**
   - Multiple accounts controlled by same person/team
   - Consolidation pattern is smoking gun
   - All 7 networks in dataset are already blocked

3. **Risk scoring should focus on creator consolidation**
   - Multiple creators → same address = HIGH RISK
   - Amount of post-launch consolidation = risk signal
   - Treasury address monitoring = detection strategy

## Database Tables Created

### creator_unified_network
Stores complete funding profile for each creator:
- Inbound funders count, total SOL
- Outbound destinations count, total SOL  
- Network membership (if any)
- Reputation (MALICIOUS, SUSPICIOUS, CLEAN)

### creator_network_group
Stores coordinated network information:
- Network size, risk level
- Treasury addresses (JSON)
- Member counts and reputations

## Statistics Summary

| Category | Count |
|----------|-------|
| **Total Creators** | 21 |
| **In Networks** | 9 |
| **Blocked/Suspicious** | 13 |
| **Safe** | 8 |
| | |
| **External Funders** | 46 |
| **Multi-Creator Funders** | 0 |
| **Inbound SOL** | 36.60 |
| | |
| **Coordinated Networks** | 7 |
| **Network Members** | 15 |
| **Outbound SOL** | 1.28 |

## Conclusion

Your instinct was correct - there ARE "multfunding" accounts! But they're **creators, not funders**:

- **Creators**: 5 creators share 2 treasuries (DETECTED, BLOCKED)
- **Funders**: 46 funders each supply 1 creator only (distributed, no coordination)

The system successfully identified both patterns:
1. ✅ Distributed pre-migration funding (from external sources)
2. ✅ Centralized post-migration consolidation (to shared treasuries)

Both are now integrated into the risk scoring system for enhanced rug detection.

