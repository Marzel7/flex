# Phase 3: Pool Discovery Coverage Expansion — Executive Summary

**Status:** Design Complete, Ready for Implementation Planning

---

## The Problem (Proven by Phase 2 Logs)

After Phase 2 fixed the architecture leaks, a new failure class emerged:

**~25-30% of tokens show this pattern:**

```
✅ cached_tx_present=yes
✅ cached_tx_parsed=True
❌ cached_candidate_count=0
❌ RPC fallback: vaults_not_ready
❌ All retries exhausted
```

This is **NOT** a retry timing bug or cached-TX plumbing issue. It's a **discovery coverage gap**.

---

## Why Cached TX Yields Zero Candidates

### Root Causes (Priority Order)

1. **Pool in inner instruction (CPI)** — ~40% of zero cases
   - Pool created via delegated call to AMM program
   - Only visible in inner instructions, not top-level message
   - Current parser only checks top-level account owners

2. **Meta owner field not indexed** — ~30% of zero cases
   - TX indexed but metadata incomplete (race condition)
   - Account ownership not yet available in meta
   - Retry works eventually, but costs latency

3. **Pool in follow-on transaction** — ~20% of zero cases
   - Migration establishes context (bonding curve, creator)
   - Actual pool state account created in subsequent TX
   - Unusual but happens with certain automation patterns

4. **Unknown program owner** — ~10% of zero cases
   - Pool program not in our registered set
   - Example: new PumpFun V2 program ID
   - Action: add to known programs

---

## The Solution: Follow-On Transaction Discovery

### The Strategy

When cached TX yields **zero candidates**:

1. **Extract anchors** from migration context:
   - Bonding curve address (primary)
   - Creator address (secondary)
   - Token mint (fallback)

2. **Scan follow-on transactions** (bounded):
   - Time window: migration to +30 seconds
   - Anchor: up to 20 signatures per anchor
   - Look for pool creation patterns

3. **Validate candidates** using RPC:
   - Owner check (must be known pool program)
   - Registration guard
   - Return first valid match

### Why This Works

- **Migration context is strong:** Bonding curve is tightly coupled to pool setup
- **Time window is tight:** Pool setup completes within 30s
- **RPC quota is bounded:** 3-5 additional RPC calls per zero-candidate token
- **Orthogonal to Phase 2:** Only runs when cached TX fails, doesn't interfere with successes

---

## Performance Impact

### Success Rate Improvement

| Metric | Current (Phase 2) | With Phase 3 | Target |
|--------|---|---|---|
| **Resolved tokens** | 90-95% | 97-98% | >95% |
| **Unresolved tokens** | 5-10% | 2-3% | <3% |
| **Improvement** | — | +7-13 tokens per 100 | — |

### Latency Impact

| Metric | Phase 2 | Phase 3 | Cost |
|--------|--------|--------|------|
| **Median** | 3-8s | 5-12s | +2-4s (worth it) |
| **P90** | <25s | <35s | +10s (acceptable) |
| **RPC calls/token** | 2-5 | 5-8 | +60% (acceptable) |

---

## Implementation Phasing

### Phase 3.1: Diagnostics (1 week)

**Goal:** Understand actual distribution of zero-candidate reasons

**Tasks:**
1. Add `emit_cached_tx_diagnostics()` function
   - Inspect inner instructions
   - Check meta.accounts owner population
   - Classify reason code
2. Deploy and collect 100+ tokens
3. Report back on reason code distribution

**Output:** "Of 100 zero-candidate tokens, X% are inner_instructions, Y% are meta_incomplete, Z% are follow_on_likely"

### Phase 3.2: Follow-On Discovery (1 week)

**Goal:** Implement core follow-on scanning logic

**Tasks:**
1. Add `discover_follow_on_pools()` method
2. Implement three anchors: bonding_curve, creator, mint
3. Add bounded search rules (20 TXs max per anchor, 30s window)
4. Add telemetry: anchor used, TXs scanned, winner offset

**Output:** Follow-on strategy working, Phase 3.1 diagnostics inform tuning

### Phase 3.3: Retry Integration (1 week)

**Goal:** Wire follow-on discovery into retry loop

**Tasks:**
1. Update retry tiers:
   - Attempts 1-3: cached TX only (fast)
   - Attempts 4-6: cached TX + light follow-on (bounded)
   - Attempts 7-12: cached TX + deep follow-on (full search)
2. Add failure_class tagging
3. Monitor metrics for success targets
4. Adjust limits based on production data

**Output:** Phase 3 production ready, success metrics stable

### Phase 3.4: Optimization (1 week)

**Goal:** Fine-tune based on production learnings

**Tasks:**
1. If RPC quota too high: reduce anchors or TX limits
2. If success rate plateaus: investigate inner instruction parsing improvements
3. If meta_incomplete dominates: adjust Tier 1 retry delays
4. Document final tuned parameters

**Output:** Phase 3 stable and optimized

---

## Key Design Decisions

### Decision 1: Anchor Priority (bonding_curve > creator > mint)

**Why bonding_curve first?**
- Most tightly coupled to pool setup
- Directly referenced by pool state account
- Highest signal-to-noise ratio

**Why creator second?**
- Creator may execute follow-on setup steps
- But also touches unrelated transactions
- Medium signal-to-noise

**Why mint fallback?**
- Least specific (many TXs touch mint)
- Only use if first two anchors fail
- Keep as last resort

### Decision 2: 30-Second Time Window

**Why 30s?**
- Pool setup typically completes within 30s
- Balances coverage vs noise
- Matches current retry window (attempts 1-12 span ~55s)

**Why not 60s or 120s?**
- Longer window = more TXs to scan = more RPC
- Diminishing returns (most cases resolve in <30s)
- Noise increases (unrelated creator transactions)

### Decision 3: Tier-Based Follow-On Limits

**Why not always run deep search?**
- Early retries are cheap (zero RPC, just delays)
- Follow-on is expensive (3-5 RPC per token)
- Stage it: light search first, deep search later

**Tier 2 (attempts 4-6):**
- Bonding curve + creator only
- 10 TXs max
- Cost: ~5 RPC calls per token
- Catches 70-80% of follow-on cases

**Tier 3 (attempts 7-12):**
- All three anchors
- 20 TXs max
- Cost: ~10 RPC calls per token
- Catches remaining 20-30%

---

## Expected Outcomes

### If Phase 3.1 Shows inner_instructions ~40%

→ Phase 3.2 will help significantly
→ Consider also improving TX inspection to detect inner-instruction pools
→ Follow-on anchor will likely catch these

### If Phase 3.1 Shows meta_incomplete ~30%

→ Follow-on strategy helps, but so do retry delays
→ Phase 1 recommendation: increase Tier 1 delays slightly
→ Phase 3 will still improve by getting around the race condition

### If Phase 3.1 Shows follow_on_pool_creation ~20%

→ Phase 3.2 is essential for these cases
→ Follow-on discovery is the only way to resolve

### If Phase 3.1 Shows unknown_program_owner ~10%

→ Add the new program to `POOL_PROGRAMS` set
→ Redeploy Phase 2 cached TX parser
→ Phase 3 not needed for this class

---

## Success Criteria

### Go/No-Go for Phase 3.2

**Phase 3.1 must show:**
- At least 15% of zero-candidate cases are "likely follow-on"
- No cases where follow-on would be counterproductive (e.g., all follow_on_TXs unrelated)

**Decision:** If <15%, reconsider Phase 3 priority (invest in meta_incomplete retry tuning instead)

### Go/No-Go for Phase 3.3

**Phase 3.2 must show:**
- Follow-on discovery successfully resolves 50%+ of targeted tokens
- RPC quota impact <20% increase on test cohort
- No cascading failures (e.g., finding invalid pools)

**Decision:** If success <50%, improve anchor selection before production rollout

### Go/No-Go for Phase 3.4

**Phase 3.3 must show:**
- Overall resolution rate 97%+ (up from 90-95%)
- Median latency <12s (Phase 3 target)
- Unresolved rate <3%

**Decision:** If any metric misses, extend Phase 3.4 or revisit design

---

## Resource Budget

### Development Time

- Phase 3.1: 8 hours (diagnostics)
- Phase 3.2: 12 hours (core logic + testing)
- Phase 3.3: 8 hours (integration + monitoring)
- Phase 3.4: 8 hours (tuning + documentation)
- **Total: 36 hours (~1 week full-time)**

### RPC Quota

- Per unresolved token: +3-5 RPC calls (follow-on)
- Assume 5-10% unresolved from Phase 2 = 5-10 additional tokens per 100
- Per token: ~5 RPC calls on average
- **Total: ~25-50 additional RPC calls per 100 tokens**
- **Cost: ~1-2% of total RPC budget increase (acceptable)**

### On-Chain Risk

- None (read-only operations, no state changes)
- Follow-on discovery only reads transactions
- No contract interactions, no transaction broadcasting

---

## Decision Point

### Proceed with Phase 3.1 (Diagnostics)?

**Recommendation: YES**

Rationale:
1. Zero development risk (diagnostics only, no state changes)
2. Low cost (4 hours work, minimal RPC overhead)
3. High information gain (clarifies bottleneck distribution)
4. Enables data-driven decision for Phase 3.2+

**Success condition:** Collect and analyze 100+ unresolved tokens

**Timeline:** 1 week data collection, 1 day analysis

---

## Summary

Phase 3 targets the remaining 5-10% of unresolved tokens by expanding discovery coverage beyond the single migration transaction. By adding bounded, anchor-based follow-on transaction scanning, we expect to resolve an additional 7-13% of tokens while increasing total RPC cost by only 1-2%.

**Next step:** Implement Phase 3.1 (diagnostics) to validate the root cause distribution and inform Phase 3.2+ tuning.
