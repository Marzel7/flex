# UNKNOWN_CLUSTER_ALPHA: Preliminary Investigation

**Date:** 2026-06-02  
**Status:** Evidence-based investigation (not confirmation)  
**Confidence Levels:** As stated per finding  

---

## Summary of Findings by Confidence

### STRONG EVIDENCE (95% confidence)

**Finding:** `9MBEPB4Q...` and `gangJEP5...` are operationally linked

**Evidence:**
- Bidirectional SOL flows: 720 SOL (9MBEPB4Q → gangJEP5), 944 SOL (return)
- 4 outbound transfers from 9MBEPB4Q to gangJEP5
- 5 inbound transfers from gangJEP5 to 9MBEPB4Q
- Time window: 2026-03-05 to 2026-03-08
- **Exclusive relationship:** 9MBEPB4Q sends to ONLY gangJEP5 (no other recipients)

**What this proves:**
- ✅ These wallets coordinate with each other
- ✅ They manage significant SOL flows
- ✅ They maintain bidirectional contact

**What this does NOT prove:**
- ❌ Identity of either wallet
- ❌ Relationship to WATCHTOWER
- ❌ Purpose of the flows (could be legitimate)

---

### MODERATE EVIDENCE (80% confidence)

**Finding:** `gangJEP5...` acts as both token creator AND funds relay

**Evidence:**
- Creator status: 1 token in `wt_interceptor_validation` (WATCH classification)
- Relay activity: 216.37 SOL distributed to 56 distinct recipients
- Dual role: Listed among 29 creators who also perform relay functions
- Token created: 2026-06-02 10:00:37 (recent)
- Token classification: GENERAL_PUMPFUN with watch_confidence = 0.0

**Pattern Analysis:**
- `5FqUo9aBjsp7QeeyN6Vi2ZmF2fjS4H5EU7wnAQwPy17z`: 362.2 SOL to 74 recipients (creator + heaviest relay)
- `gangJEP5geDHjPVRhDS5dTF5e6GtRvtNogMEEVs91RV`: 216.4 SOL to 56 recipients (creator + relay)
- 27 others with similar patterns

**What this proves:**
- ✅ gangJEP5 creates tokens while acting as a funds distributor
- ✅ This pattern is not unique (29 creators do this)
- ✅ It differs from classical WATCH model (usually disposable creators)

**What this does NOT prove:**
- ❌ Whether this is malicious or legitimate
- ❌ What the funds are for (could be trading, community distribution, etc.)
- ❌ Connection to any known coordinated operation

---

### WEAK EVIDENCE (40% confidence)

**Finding:** Connection to WATCHTOWER infrastructure

**Evidence:**
- ❌ No appearance in `wt_armed_operations`
- ❌ No appearance in `wt_swarm_recipients`
- ❌ No creator relationship to known WATCHTOWER wallets
- ❌ No fanout pattern (9MBEPB4Q only sends to gangJEP5)
- ❌ No standard WATCH transfer amounts (0.10203928, 0.03928)
- ❌ No match with known signal token transfers

**What's missing:**
- Proof of `44orWS68` or `N3TKf3wM` funding 9MBEPB4Q
- Signal token transfer to gangJEP5
- Known WATCH fanout behavior to recipients
- Membership in any known coordination network

**Conclusion on WATCHTOWER link:** 40% confidence (speculative, not evidence-based)

---

## UNKNOWN_CLUSTER_ALPHA Profile

### Primary Coordinator: `9MBEPB4QFfCSKwaR3azaFp4BTv43yqsT8MBoKtd3EXJw`

**Status:** Unknown purpose, suspicious patterns

**In-flow (funding sources):**
- Source: `gangJEP5...` ONLY
- Amount: 944.02 SOL in 5 transfers
- Period: 2026-03-05 to 2026-03-08
- **Reverse funding: unusual** (why would relay send back to source?)

**Out-flow (destinations):**
- Destination: `gangJEP5...` ONLY  
- Amount: 720.0 SOL in 4 transfers
- **Exclusive relationship: not a treasury** (treasury would fund multiple channels)

**Characteristics:**
- Single dedicated relay (not a distribution hub)
- Bidirectional money movement (suggests mutual funding or accounting)
- Recent activity (March 2026)
- Zero WATCH fingerprints

---

### Relay/Creator: `gangJEP5geDHjPVRhDS5dTF5e6GtRvtNogMEEVs91RV`

**Dual roles:**
1. **Creator:** 1 WATCH token (fresh classification)
2. **Relay:** 216.37 SOL → 56 recipients

**Funding sources (7 wallets):**
| Source | SOL | Type |
|--------|-----|------|
| 9MBEPB4Q... | 720.0 | Primary coordinator |
| DY8SUSYr... | 154.0 | Unknown |
| 2Exjk12V... | 126.1 | Unknown |
| DMNFqcQL... | 116.5 | Unknown |
| GfEdywo... | 105.0 | Unknown |
| KGYWF8z... | 49.7 | Unknown |
| Others | 22.2 | Unknown |
| **Total inbound** | **1,293.6 SOL** | |

**Distribution pattern:**
- 56 unique recipients
- Distributed 216.4 SOL (kept majority, ~1,077 SOL unaccounted for)
- **Question:** Where did the remaining 1,077 SOL go?

---

## Critical Gaps (Things We Don't Know)

1. **Source of 9MBEPB4Q's initial funding**
   - The 944 SOL it receives comes from gangJEP5 (reverse flow)
   - No upstream source identified
   - Possible: self-funded, CEX withdrawal, or unknown pathway

2. **Other creator+relay wallets' connections**
   - 29 total wallets with this pattern
   - Different funding sources
   - Unclear if they're part of same cluster or separate operations
   - **Priority:** Do they cluster into coherent groups?

3. **Final destinations of aggregated funds**
   - gangJEP5 receives 1,293.6 SOL
   - Distributes 216.4 SOL (only 16.7%)
   - Remaining 1,077 SOL location unknown
   - **Critical question:** Where did it go?

4. **Purpose of the operation**
   - Could be: legitimate token trading, project funding, ecosystem support
   - Could be: money laundering, scam proceeds, stolen funds
   - **No evidence yet** for malicious intent
   - **No evidence yet** for legitimacy

---

## What Would Move This to "Confirmed WATCHTOWER"?

**Any ONE of these would dramatically increase confidence:**

1. **Direct funding chain:**
   ```
   44orWS68 (TREASURY)
   └─→ 9MBEPB4Q
   └─→ gangJEP5
   └─→ Recipients
   ```

2. **Signal token link:**
   ```
   gangJEP5 receives 0.0000151 of signal token
   (matching pattern from HLRKtAqU5... discovery)
   ```

3. **Known WATCH fanout match:**
   ```
   gangJEP5 distributes to recipients
   matching 95+ wallet patterns in 60s window
   (classical WATCH SUB_PROV signature)
   ```

4. **Orchestration Trio connection:**
   ```
   `Gp7RKGWpRugY45fbbZ56fbg7RChAzpze7jfWUPeDxJdr`
   └─→ any recipient of 9MBEPB4Q or gangJEP5
   ```

---

## Recommended Next Steps

### Priority 1: Map creator+relay cluster structure
```sql
-- Do the 29 creator+relay wallets cluster into groups?
-- Are there funding relationships between them?
-- Can we identify 5-10 coherent sub-clusters?
```

**Why:** If they're isolated, each is separate. If they cluster, we have discovered hidden infrastructure.

### Priority 2: Trace remaining SOL from gangJEP5
```sql
-- gangJEP5 receives 1,293.6 SOL
-- Distributes only 216.4 SOL via creator_outgoing_transfers
-- Where is the remaining 1,077 SOL?
-- Check: token swaps, DEX activity, CEX deposits?
```

**Why:** This is the highest-value finding if trackable.

### Priority 3: Identify 9MBEPB4Q origin
```sql
-- Find any wallet that funded 9MBEPB4Q before gangJEP5
-- Check: token transfers, not just SOL transfers
-- Reconstruct complete history (not just last transfers)
```

**Why:** The upstream source is the real coordinator.

### Priority 4: Check signal token on all creator+relay wallets
```sql
-- Do 5FqUo9aBj..., H6zpaY14W..., etc. have signal token transfers?
-- Do they show timing correlation with token creation?
```

**Why:** If signal token is present, confirms coordination layer.

---

## Confidence Matrix (Updated)

| Claim | Evidence | Confidence | Status |
|-------|----------|-----------|--------|
| 9MBEPB4Q ↔ gangJEP5 linked | Bidirectional flows | **95%** | ✅ Confirmed |
| gangJEP5 = creator + relay | Dual appearance + 29-wallet pattern | **80%** | ✅ Confirmed |
| Connection to WATCHTOWER | Zero matching fingerprints + no linking chains | **40%** | ⚠️ Speculative |
| 9MBEPB4Q = TREASURY equivalent | Supports only one relay | **20%** | ❌ Unlikely |
| UNKNOWN_CLUSTER_ALPHA exists | Coherent pattern + multiple evidence | **75%** | ✅ Probable |

---

## File Status

- [x] Evidence categorized by confidence
- [x] Unknown gaps documented
- [x] False conclusions removed
- [x] Hypotheses labeled as such
- [x] Next steps operationalized

**This is a lead, not a confirmation.**

Further investigation required before any claims about identity or coordination can be made.

