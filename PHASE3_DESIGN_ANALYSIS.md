# Phase 3 Pool Discovery Coverage Expansion — Technical Design

**Date:** March 20, 2026
**Status:** Design Phase
**Target:** Resolve tokens where cached migration TX yields zero candidates

---

## 1. Root Cause Analysis

### The New Failure Class (Proven by Phase 2 logs)

Logs now show tokens that fail with a specific pattern:

```
[CACHED_TX_PARSE] cached_tx_present=yes cached_tx_parsed=True cached_candidate_count=0
[DISCOVERY_TX] attempt=1 candidates=0 (tx_not_indexed)
[DISCOVERY_TX] attempt=2 candidates=0 (tx_not_indexed)
[DISCOVERY_RPC] attempt=4 strategy=light_rpc rejected=vaults_not_ready
[DISCOVERY_RPC] attempt=6 strategy=full_rpc rejected=vaults_not_ready
[DISCOVERY_FAILED] All 12 attempts exhausted
```

This is NOT a retry timing issue or cached-TX plumbing bug.

### Why Zero Candidates From Cached Migration TX?

The cached migration TX parsing function (`parse_candidates_from_cached_tx`) currently:

1. Extracts account keys from the transaction message
2. Loads versioned transaction addresses (writable + readonly)
3. Filters by known pool program owners from meta
4. Returns only accounts whose owner is in `meta.accounts[i].owner`

**Failure modes for zero candidates:**

A. **Meta owner field not populated:** The transaction meta includes account data but the `owner` field is missing or not set. This happens when:
   - Transaction indexing is incomplete (Helius/RPC hasn't populated meta yet)
   - Inner instructions create the pool but meta doesn't reflect it
   - Pool creation delegated to CPI, owner not visible at top-level

B. **Pool created in inner instruction:** The actual pool account is created via CPI to the pool program, but it only exists in the signed-for accounts array (`signed_for_accounts`), not in the top-level message. Currently we skip inner instructions.

C. **Pool created in follow-on transaction:** The migration TX establishes context (bonding curve, creation authority) but the actual pool state account is created/referenced in a subsequent TX immediately after. This is uncommon but happens with certain automation patterns.

D. **Unknown pool program:** The pool is owned by a program not in our `POOL_PROGRAMS` set. PumpFun V2 or custom AMMs not yet registered.

E. **Parser too conservative:** We only accept candidates with confirmed owner in meta. For faster coverage, we could also include "likely pool" accounts based on size/structure heuristics.

### Why RPC Fallback Fails

When the TX yields zero candidates, the system falls back to RPC vault discovery:

```python
pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
    mint=mint,
    migration_sig=original_migration_sig,
    tx_data=tx_data  # Still passes cached TX
)
```

This function then:

1. Fetches the TX again via RPC (if not in cache)
2. Uses full RPC inspection to find accounts
3. Makes additional RPC calls to find vaults via `getTokenLargestAccounts(mint)`
4. Returns vault-derived candidates

But it fails with `vaults_not_ready` because:
- The vault accounts haven't been indexed yet (RPC is behind TX indexing)
- `getTokenLargestAccounts` returns empty
- Vault resolution fails repeatedly, exhausts retries

**This is a race condition:** The vault accounts exist on-chain but RPC hasn't indexed them yet.

---

## 2. Phase 3 Architecture Changes

### New Discovery Stack

**Phase 2 stack:**
```
1. cached TX parse (zero RPC)
   ↓ (if zero candidates)
2. RPC vault fallback (multiple RPC calls)
   ↓ (if vaults_not_ready)
3. retry loop (delay and repeat)
```

**Phase 3 stack:**
```
1. cached TX parse (zero RPC)
   ↓ (if zero candidates)
2. [NEW] follow-on TX discovery (bounded RPC scan)
   ↓ (if no pool found)
3. RPC vault fallback (multiple RPC calls)
   ↓ (if vaults_not_ready)
4. retry loop (delay and repeat)
```

### Key Insight

The migration TX context gives us strong anchors to search follow-on transactions:
- **Bonding curve address** (extracted from migration TX)
- **Creator address** (signer on migration TX)
- **Token mint** (created during migration)

We can scan 5-20 subsequent transactions touching these anchors and look for pool creation.

---

## 3. Follow-On Transaction Strategy Design

### Trigger Condition

Only run follow-on discovery when:
```python
if cached_tx_present and cached_tx_parsed and cached_candidate_count == 0:
    # Run follow-on strategy
```

This avoids wasting RPC quota when Phase 2 already succeeds.

### Search Anchors (Priority Order)

#### A. Bonding Curve (Highest Priority)

**Why:** The migration TX creates the bonding curve address. Subsequent transactions modifying the bonding curve account are likely related to pool setup.

**How to find:**
- Extract bonding curve address from migration TX (already done in listener)
- Call `getSignaturesForAddress(bonding_curve, limit=20, before=migration_signature)`
- Filter signatures after migration time
- Fetch each TX and inspect for pool program operations

**Typical latency:** 1-5 RPC calls, 5-10 accounts/TX

#### B. Creator Address (Medium Priority)

**Why:** The creator (signer on migration) may execute follow-on setup transactions.

**How to find:**
- Extract creator from migration TX signers
- Call `getSignaturesForAddress(creator, limit=20, before=migration_signature, after=some_time_boundary)`
- Time window: migration_time to migration_time + 30 seconds
- Inspect each TX for pool program operations

**Typical latency:** 1-5 RPC calls

#### C. Token Mint (Lower Priority)

**Why:** Some systems create the pool state account as a downstream effect of token interactions.

**How to find:**
- Call `getSignaturesForAddress(token_mint, limit=15, before=migration_signature)`
- Time window: migration_time to migration_time + 30 seconds
- Look for pool creation patterns

**Typical latency:** 1-3 RPC calls

#### D. Pool Program References (Fallback)

**Why:** Direct inspection of "recent" operations by known pool programs.

**How to find:**
- This requires `getProgramAccounts` or similar, which is expensive
- Only use if previous anchors fail
- Limit to very small time window or skip

**Typical latency:** 1 expensive RPC call or skip

### Search Algorithm

```
for anchor in [bonding_curve, creator, token_mint]:
    txs = await fetch_signatures_for_address(anchor, limit=20)

    for tx in txs:
        if is_after_migration_and_within_window(tx):
            accounts = parse_tx_accounts(tx)
            candidates = filter_pool_like_accounts(accounts)

            for candidate in candidates:
                if validate_candidate(candidate):  # Owner check
                    return (candidate, anchor, offset)
```

---

## 4. Bounded Search Rules (Hard Limits)

### Rule 1: Maximum Transactions Scanned

```python
MAX_FOLLOW_ON_TXS_PER_ANCHOR = 20
MAX_FOLLOW_ON_TXS_TOTAL = 50  # Across all anchors
```

**Rationale:** Pool creation typically happens within 20 seconds and within 5-15 signatures. 50 total prevents runaway RPC.

### Rule 2: Time Window

```python
FOLLOW_ON_TIME_WINDOW_SECONDS = 30
# Search only: migration_time to migration_time + 30s
```

**Rationale:** Pool setup usually completes within 30 seconds. Searching further is noise.

### Rule 3: RPC Quota

```python
MAX_FOLLOW_ON_RPC_CALLS = 15  # Across getSignaturesForAddress + getTransaction calls
```

**Rationale:** At ~1-3 calls per anchor, this allows 3-5 anchors fully explored.

### Rule 4: Skip if Already Resolved

```python
if cached_candidate_count > 0:
    skip_follow_on_strategy()  # Phase 2 worked
```

### Rule 5: Per-Attempt Limits

```python
# Retries 1-3: no follow-on (cached TX only)
# Retries 4-6: bounded follow-on (2 anchors, 10 txs)
# Retries 7-12: deeper follow-on (3 anchors, 20 txs)
```

**Rationale:** Early retries are cheap (milliseconds), follow-on is expensive (RPC). Delay until later attempts.

---

## 5. Improved Diagnostics

### A. Cached TX Zero-Candidate Reasons

Add detailed diagnostic function:

```python
async def emit_cached_tx_diagnostics(
    cached_tx: Dict,
    mint: str,
    attempt: int
) -> Dict:
    """
    Emit structured reason why cached TX yielded zero candidates.

    Returns:
    {
        'reason_code': str,  # no_amm_program_in_tx | no_pool_like_accounts | ...
        'accounts_count': int,
        'amm_program_present': bool,
        'meta_has_owners': bool,
        'inner_instructions_count': int,
        'largest_accounts': List[Dict],  # Top 5 accounts by data size
    }
    """
```

**Reason codes:**

| Code | Meaning | Action |
|------|---------|--------|
| `no_amm_program_in_tx` | No account owned by pool program | Try follow-on |
| `no_pool_like_accounts` | Accounts exist but none match pool structure | Try follow-on |
| `meta_owner_missing` | Meta present but owner field not populated | Likely race; retry |
| `inner_instructions_only` | Pool created via CPI, not visible at top-level | Try follow-on |
| `unknown_program_owner` | Accounts have unknown owner program | Check if new pool program |
| `tx_too_sparse` | Very few accounts in TX | Try follow-on |
| `metadata_incomplete` | Meta incomplete (indexing in progress) | Retry, likely transient |

**Log output:**

```
[CACHED_TX_PARSE_DETAIL]
  reason=inner_instructions_only
  accounts=12
  amm_program_present=no
  inner_instructions=3
  meta_has_owners=yes
  action=follow_on_recommended
```

### B. Follow-On Discovery Telemetry

Add structured telemetry:

```python
[FOLLOW_ON_DISCOVERY]
mint=7ye6z3UH...
anchor=bonding_curve
txs_scanned=7
candidates_found=2
candidates_tested=2
result=success
winner_offset=+3tx
winner_address=abc...
winning_strategy=follow_on_tx
discovery_elapsed=2.3s
```

**Capture:**
- Which anchor worked (bonding_curve | creator | mint | program)
- How many TXs examined
- How many candidates found
- How many actually valid
- Final result (success | failed | vaults_still_not_ready)
- Offset in TX chain from migration

### C. Explicit Failure Classification

When all strategies fail:

```python
[DISCOVERY_FAILED]
mint=...
failure_class=no_pool_in_cached_or_follow_on_txs
cached_candidate_count=0
follow_on_txs_scanned=45
follow_on_candidates=0
rpc_fallback_status=vaults_not_ready
retries=12
total_elapsed=55s
```

**Failure classes:**

| Class | Meaning |
|-------|---------|
| `cached_tx_found_candidates` | Phase 2 worked |
| `follow_on_tx_found_candidate` | Phase 3 worked |
| `rpc_vault_discovery_worked` | RPC fallback worked |
| `no_cached_no_follow_on_candidates` | Neither cached nor follow-on found pool |
| `no_pool_candidates_ever_yielded` | Zero candidates across all strategies |
| `vaults_never_ready_rpc` | RPC fallback stuck in vaults_not_ready loop |
| `follow_on_exhausted_then_vaults_not_ready` | Follow-on found candidates but registration failed, vaults still not ready |

---

## 6. Retry Integration Plan

### New Tier Structure

Keep current delays but add strategy branching:

```python
DISCOVERY_DELAYS = [0.5, 1, 1.5, 2, 3, 5, 8, 12, 18, 25, 35, 50]
```

**Tier 1 (Attempts 1-3, delays 0.5-1.5s):**
- Strategy: cached TX parse only
- RPC quota: discovery_rpc only
- Purpose: Fast path, no RPC latency
- Follow-on: NO (too early, RPC likely not indexed anyway)

**Tier 2 (Attempts 4-6, delays 2-5s):**
- Strategy: cached TX parse + light follow-on + light RPC
- RPC quota: discovery_rpc + 2-3 follow-on anchors
- Follow-on: YES, but bounded (bonding_curve + creator only)
- Purpose: Give RPC time to index, search near-migration transactions
- Follow-on limits: 10 TXs, 10 RPC calls

**Tier 3 (Attempts 7-12, delays 8-50s):**
- Strategy: cached TX parse + deep follow-on + full RPC
- RPC quota: discovery_rpc + full follow-on scan
- Follow-on: YES, all anchors (bonding_curve + creator + mint)
- Purpose: Extended search, vaults should be ready by now
- Follow-on limits: 20+ TXs, 15 RPC calls

### Code Structure

```python
async def _retry_pool_discovery(
    self, mint, original_migration_sig, tx_data, delays
):
    for attempt, delay in enumerate(delays, 1):
        await asyncio.sleep(delay)

        # === CACHED TX PARSE ===
        cached_candidates = await discover.parse_candidates_from_cached_tx(tx_data)

        if cached_candidates:
            # SUCCESS: Use cached TX result
            result = register_candidate(cached_candidates[0])
            return result

        # === FOLLOW-ON DISCOVERY (Tier 2+) ===
        if attempt >= 4:  # Tier 2 starts at attempt 4
            follow_on_result = await discover.discover_follow_on_pools(
                mint=mint,
                migration_sig=original_migration_sig,
                bonding_curve=...,  # From cache or parsing
                creator=...,        # From cache
                max_txs=10 if attempt < 7 else 20,  # Tier 2 vs 3
            )
            if follow_on_result:
                result = register_candidate(follow_on_result)
                return result

        # === RPC FALLBACK ===
        rpc_candidates = await discover.discover_pool_candidates_from_migration_tx(
            mint=mint,
            migration_sig=original_migration_sig,
            tx_data=tx_data,
        )
        if rpc_candidates:
            result = register_candidate(rpc_candidates[0])
            return result
```

---

## 7. Success Metrics and Targets

### New Metrics to Track

```sql
-- Add to token_resolution_telemetry or new table
ALTER TABLE token_resolution_telemetry ADD COLUMN (
    follow_on_txs_scanned INT DEFAULT 0,
    follow_on_anchor TEXT,  -- bonding_curve | creator | mint
    follow_on_candidates_found INT DEFAULT 0,
    follow_on_winner_offset INT,  -- Offset from migration_sig
    failure_class TEXT,
);
```

### Success Percentages (Current vs Phase 3 Target)

| Metric | Phase 2 | Phase 3 Target | Why |
|--------|---------|---|---|
| **Cached TX resolves** | 70-75% | 70-75% | Unchanged; Phase 3 doesn't affect |
| **Cached TX zero-candidate rate** | 25-30% | 25-30% | Unchanged; baseline |
| **Follow-on resolves from zero** | N/A | 60-70% | New strategy; target 60-70% of zero-candidate cases |
| **Overall resolution rate** | ~85% | ~92-95% | Combined effect |
| **Median latency** | 3-8s | 5-12s | Follow-on adds RPC; worth tradeoff |
| **P90 latency** | <25s | <35s | Deeper search takes longer |
| **Vaults-not-ready rate** | 15-20% | <10% | Follow-on finds pool earlier |
| **Final exhaustion rate** | 5-10% | <3% | Most zero-candidate cases resolved |

### Diagnostic Metrics

```
% of zero-candidate cached TXs where:
  - reason=inner_instructions_only → likely follow-on resolvable
  - reason=meta_incomplete → likely transient, retry helps
  - reason=unknown_program_owner → skip (new program not yet supported)
```

---

## 8. Implementation Roadmap

### Phase 3.1: Diagnostics (Week 1)

1. Add `emit_cached_tx_diagnostics()` function to `PostMigrationPoolDiscovery`
2. Add reason codes and structured logging
3. Add `failure_class` tracking to telemetry
4. Deploy and collect 100+ tokens to understand distribution

**Output:** Which zero-candidate reasons dominate?

### Phase 3.2: Follow-On Discovery (Week 2)

1. Add `discover_follow_on_pools()` method
   - Takes bonding_curve, creator, mint
   - Returns (pool_address, anchor, offset)
2. Integrate into retry loop (attempt >= 4)
3. Add bounded search rules
4. Add telemetry logging

### Phase 3.3: Retry Integration (Week 3)

1. Update retry tiers to trigger follow-on at correct attempts
2. Adjust limits based on Phase 3.2 learnings
3. Add failure_class tagging on exhaustion
4. Monitor metrics for success targets

### Phase 3.4: Optimization (Week 4)

1. If follow-on RPC budget is too high: reduce anchors or TX limits
2. If success rate plateaus: investigate inner instruction parsing
3. If meta_owner_missing is high: implement fallback heuristics

---

## 9. Expected Impact

### Before Phase 3

```
100 tokens
├─ 70-75 cached_tx finds pool (resolved)
├─ 0-5 follow-on (not implemented)
└─ 25-30 cached_tx zero candidates
   ├─ 20-25 RPC fallback finds pool (resolved)
   └─ 5-10 all retries exhausted (unresolved)

Total resolved: ~90-95 tokens
Total unresolved: ~5-10 tokens (5-10%)
```

### After Phase 3

```
100 tokens
├─ 70-75 cached_tx finds pool (resolved)
├─ 12-18 follow-on finds pool (resolved, new)
├─ 3-5 follow-on partial, RPC completes (resolved, new)
└─ 2-5 all strategies exhausted (unresolved)

Total resolved: ~97-98 tokens
Total unresolved: ~2-3 tokens (2-3%)

Improvement: +7-13 tokens resolved by follow-on strategy
```

### Resource Cost

- RPC calls per zero-candidate token: ~3-5 (vs 1-2 before)
- Time per attempt: +1-2 seconds (follow-on scan)
- Total RPC quota impact: ~15% increase
- Memory: negligible (no caching of full TXs)

---

## 10. Concrete Code Changes Summary

### New Functions to Add

**`PostMigrationPoolDiscovery.emit_cached_tx_diagnostics()`**
- Lines: ~40
- Purpose: Structured diagnostic of why cached TX yielded zero candidates

**`PostMigrationPoolDiscovery.discover_follow_on_pools()`**
- Lines: ~80
- Purpose: Search follow-on transactions using anchors

**`PumpFunCurveListener._integrate_follow_on_at_attempt()`**
- Lines: ~30
- Purpose: Conditional follow-on discovery in retry loop

### Modified Functions

**`PumpFunCurveListener._retry_pool_discovery()`**
- Add follow-on discovery call after cached TX, before RPC fallback
- Add failure_class tagging on exhaustion
- Add tier-based RPC budget limits

**`token_resolution_telemetry` table**
- Add follow_on_txs_scanned, follow_on_anchor, follow_on_candidates_found, follow_on_winner_offset, failure_class columns

### Total Lines Added

- Diagnostics: ~40 lines
- Follow-on discovery: ~80 lines
- Retry integration: ~30 lines
- Telemetry/logging: ~40 lines
- **Total: ~190 lines**

---

## 11. Risk Assessment

### Low Risk

- Follow-on discovery is gated (only runs when cached = zero)
- Bounded search rules prevent runaway RPC
- No existing code paths changed, only new conditional paths added
- Can disable with feature flag if needed

### Medium Risk

- RPC quota increase (~15%)
- Some tokens may not find pool even with follow-on (acceptable)
- Metric collection overhead (minimal)

### Mitigation

- Monitor RPC quota hourly during rollout
- Run Phase 3.1 (diagnostics only) for a week before Phase 3.2
- Feature flag: `enable_follow_on_discovery` (default: true)
- Fallback: disable on quota overload

---

## Conclusion

Phase 3 targets the **remaining discovery gap** for tokens where the migration transaction itself is incomplete or the pool is in a follow-on transaction. By adding bounded, anchor-based transaction scanning, we should resolve an additional 7-13% of unresolved tokens while keeping RPC quota overhead manageable.

**Next step:** Implement Phase 3.1 (diagnostics) to understand the actual distribution of zero-candidate reasons in production.
