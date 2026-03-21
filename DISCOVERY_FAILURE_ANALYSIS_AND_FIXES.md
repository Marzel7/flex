# Migration TX → Pool Discovery: Failure Analysis & Fixes

**Date:** March 21, 2026
**Status:** Post-anchor implementation (bonding_curve, creator, migration_timestamp now passed)
**Scope:** Unresolved tokens where cached TX parses but yields zero candidates

---

## EXECUTIVE SUMMARY

The system now correctly passes discovery context (bonding_curve, creator) to follow-on discovery. However, **three critical bugs prevent correct routing and failure classification**:

1. **Bug #1:** Cached TX that returns `zero candidates` is mislabeled as `tx_not_indexed` instead of actual reason (e.g., `no_amm_program_in_tx`)
2. **Bug #2:** Duplicate discovery paths cause inconsistent behavior and telemetry
3. **Bug #3:** Cached-TX diagnostics are logged but never stored, preventing smart routing based on reason codes

---

# DEFINITE BUGS

## Bug #1: Incorrect Failure Reason Classification (CRITICAL)

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2870-2900

**The Problem:**

When no pool candidates are found, the system logs rejection reason:

```python
if not pool_candidates:
    using_cached_payload = tx_data is not None
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)
```

But the logged metrics classify this as:

```python
rejection_reasons.append("tx_not_indexed")
```

**Why this is wrong:**

The cached TX WAS successfully parsed (we have `cached_tx_parsed=True` at line 2803). The real reason is in `cached_diagnostics.reason_code`, which could be:

- `no_amm_program_in_tx` → Pool creation happened in follow-on TX (needs follow-on, NOT RPC retry)
- `inner_instructions_only` → Pool is in CPI/inner instruction (needs follow-on)
- `meta_incomplete` → Metadata not indexed yet (try RPC in 3 seconds)
- `meta_owner_not_indexed` → TX indexed but account metadata not (wait or follow-on)

**Current Impact:**

Tokens are routed to RPC retry when they should go directly to follow-on discovery. This:
- Wastes RPC quota during critical window
- Causes false "vaults_not_ready" failures
- Delays resolution by retrying wrong strategy

**The Fix:**

```python
# BEFORE (line ~2870):
if not pool_candidates:
    using_cached_payload = tx_data is not None
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)

# AFTER:
if not pool_candidates:
    using_cached_payload = tx_data is not None

    # Store the actual zero-candidate reason instead of assuming tx_not_indexed
    actual_reason = cached_diagnostics.get('reason_code', 'unknown') if cached_candidate_count == 0 and cached_diagnostics else 'tx_not_indexed'
    rejection_reasons.append(actual_reason)

    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)
```

**Priority:** CRITICAL (Blocks correct routing)

---

## Bug #2: Duplicate Discovery Paths Cause Inconsistent Behavior (HIGH)

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2326-2470 (Flow A) vs 2753-2920 (Flow B)

**The Problem:**

Two completely separate migration TX discovery implementations exist:

**Flow A (Initial)** - in `_process_migration_with_mint()`:
```python
pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
    mint=mint,
    migration_sig=signature,
    tx_data=tx_data
)
```
- Runs immediately on migration detection
- Uses older `discover_pool_candidates_from_migration_tx()` (no diagnostics)
- No follow-on discovery
- No Phase 3 integration

**Flow B (Retry)** - in `_retry_pool_discovery()`:
```python
candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
    await discovery.parse_candidates_from_cached_tx(tx_data)
```
- Runs on retry (attempt 1+)
- Uses newer `parse_candidates_from_cached_tx()` (WITH diagnostics)
- Includes Phase 3 follow-on discovery
- Proper diagnostic classification

**Why this is a bug:**

- If Flow A succeeds early, Flow B improvements (diagnostics, follow-on) are never used
- If Flow A fails, no diagnostics are logged to explain why
- RPC quota is wasted: Flow A calls `discover_pool_candidates_from_migration_tx()` which internally does RPC validation, then if it fails, Flow B repeats the same candidates parsing
- Different code paths = inconsistent telemetry

**Example scenario:**
```
Migration: token XYZ
Flow A runs: returns zero candidates (no reason logged)
Flow A fails: transitions to retry
Flow B runs: parse cached TX, gets reason_code='no_amm_program_in_tx', tries follow-on
Result: delayed resolution due to wrong path being taken first
```

**The Fix:**

Create single canonical path `_get_pool_from_migration_context()`:

```python
async def _get_pool_from_migration_context(
    self,
    mint: str,
    migration_sig: str,
    tx_data: Optional[Dict],
    bonding_curve: Optional[str] = None,
    creator: Optional[str] = None,
    migration_timestamp: Optional[int] = None,
) -> Tuple[Optional[str], str, Dict]:
    """
    Single source of truth for migration TX → pool discovery.

    Combines:
    1. Cached TX parsing (fast)
    2. Diagnostic classification (why zero candidates)
    3. Follow-on discovery (if zero candidates)
    4. RPC fallback (if follow-on fails)

    Returns:
        (pool_address, discovery_source, diagnostics)
        - pool_address: Found pool or None
        - discovery_source: 'tx_parsing' | 'follow_on' | 'rpc_discovery' | 'none'
        - diagnostics: Reason codes and metrics for telemetry
    """
    discovery = PostMigrationPoolDiscovery(RPC_HTTP)

    # Step 1: Parse cached TX
    candidates_from_cached, parsed_ok, candidate_count, diagnostics = \
        await discovery.parse_candidates_from_cached_tx(tx_data)

    if candidates_from_cached:
        return (candidates_from_cached[0], 'tx_parsing', diagnostics)

    # Step 2: Log why zero candidates
    reason_code = diagnostics.get('reason_code', 'unknown')
    log_print(f"[DISCOVERY_REASON] {reason_code}: {diagnostics.get('diagnostic_detail')}")

    # Step 3: Route based on reason
    if reason_code in ['no_amm_program_in_tx', 'inner_instructions_only']:
        # Pool likely in follow-on TX
        follow_on_pool = await discovery.discover_follow_on_pools(
            mint=mint,
            migration_sig=migration_sig,
            bonding_curve=bonding_curve,
            creator=creator,
            max_txs_per_anchor=20
        )
        if follow_on_pool:
            return (follow_on_pool, 'follow_on', diagnostics)

    # Step 4: RPC fallback
    # ... (vault discovery logic) ...

    return (None, 'none', diagnostics)
```

Then call it ONCE from `_process_migration_with_mint()`:

```python
pool_address, discovery_source, discovery_diags = await self._get_pool_from_migration_context(
    mint=mint,
    migration_sig=signature,
    tx_data=tx_data,
    bonding_curve=bonding_curve_pda,
    creator=earliest_creator,
    migration_timestamp=block_time
)

if pool_address:
    # Register pool
else:
    # Schedule retries
```

And use the same function in `_retry_pool_discovery()` for consistency.

**Priority:** HIGH (Architectural correctness)

---

## Bug #3: Cached-TX Diagnostics Not Persisted (HIGH)

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2809-2814

**The Problem:**

```python
if cached_candidate_count == 0 and cached_diagnostics:
    diag = cached_diagnostics
    log_print(
        f"{Colors.DISCOVER}[CACHED_TX_DIAGNOSTICS] {diag.get('diagnostic_detail', 'unknown reason')}{Colors.RESET}",
        flush=True
    )
    # Logged, but then discarded
```

On retry attempt 2, 3, 4... the system re-parses the SAME static cached TX and gets the SAME zero-candidate result with the SAME reason code.

**Why this is a bug:**

- Cached TX is immutable (from chain)
- If attempt 1 gets `reason_code='no_amm_program_in_tx'`, that never changes
- Re-parsing wastes CPU and log bandwidth
- The diagnostic result should be cached and reused
- Smart routing strategies can't be implemented without persisted diagnostics

**Example:**
```
Attempt 1: parse cached TX → no_amm_program_in_tx, 0 candidates
Attempt 2: parse cached TX again → no_amm_program_in_tx, 0 candidates (WASTED WORK)
Attempt 3: parse cached TX again → no_amm_program_in_tx, 0 candidates (WASTED WORK)
...
Attempt 12: parse cached TX again → no_amm_program_in_tx, 0 candidates (WASTED WORK)
```

**The Fix:**

Add instance dictionary to cache diagnostics:

```python
def __init__(self, ...):
    # ... existing init ...
    self.cached_tx_diagnostics_cache: Dict[str, Dict] = {}  # mint → diagnostics
```

Then in `_retry_pool_discovery()`:

```python
# Check if we already have diagnostics for this cached TX
if mint in self.cached_tx_diagnostics_cache:
    cached_diagnostics = self.cached_tx_diagnostics_cache[mint]
    cached_tx_parsed = True
    cached_candidate_count = 0
    candidates_from_cached = []
    log_print(f"[CACHED_TX_DIAGNOSTICS] Using cached result: {cached_diagnostics.get('reason_code')}")
else:
    # First time parsing this TX
    candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
        await discovery.parse_candidates_from_cached_tx(tx_data)

    # Store diagnostics for future retries
    if cached_candidate_count == 0 and cached_diagnostics:
        self.cached_tx_diagnostics_cache[mint] = cached_diagnostics
```

**Priority:** HIGH (Efficiency + enables smart routing)

---

# LIKELY BUGS / RISKS

## Risk #1: Follow-On Discovery Insufficient Logging (MEDIUM)

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2851-2868

**Current Log:**
```
[FOLLOW_ON_SUCCESS] Found pool xxx via anchor=bonding_curve at offset=+3tx
```

**Missing Info:**
- How many TXs were scanned total?
- How many candidates were evaluated?
- What was the search depth?
- Why was this one chosen over others?

**Impact:**
- Can't diagnose why follow-on fails silently
- Can't measure anchor effectiveness (bonding_curve vs creator vs mint)
- Telemetry is incomplete for success rate tracking

**Recommended Fix:**

```python
if follow_on_pool:
    log_print(
        f"{Colors.DISCOVER}[FOLLOW_ON_SUCCESS] "
        f"mint={mint[:8]}... "
        f"anchor={follow_on_anchor} "
        f"txs_scanned={follow_on_txs_scanned} "
        f"pool_offset={follow_on_offset} "
        f"attempt={attempt} "
        f"tier={tier}{Colors.RESET}",
        flush=True
    )
else:
    log_print(
        f"{Colors.DISCOVER}[FOLLOW_ON_EXHAUSTED] "
        f"mint={mint[:8]}... "
        f"anchors_tried={follow_on_anchor if follow_on_anchor else 'all'} "
        f"txs_scanned={follow_on_txs_scanned} "
        f"max_per_anchor={follow_on_max_txs} "
        f"attempt={attempt}{Colors.RESET}",
        flush=True
    )
```

**Priority:** MEDIUM (Observability, not correctness)

---

## Risk #2: Follow-On Discovery Anchor Priority Not Explicit (MEDIUM)

**Location:** `src/core/pumpfun_pool_discovery.py` line ~2842 (the call)

**Issue:**

The function `discover_follow_on_pools()` is called with all three anchors:

```python
follow_on_pool, follow_on_anchor, follow_on_offset, follow_on_txs_scanned = await discovery.discover_follow_on_pools(
    mint=mint,
    migration_sig=original_migration_sig,
    bonding_curve=bonding_curve_for_follow_on,  # May be None
    creator=creator_for_follow_on,             # May be None
    token_mint=mint,
    max_txs_per_anchor=follow_on_max_txs,
)
```

But there's no documentation of what happens if:
- `bonding_curve=None` but `creator` is available?
- Both are None (fallback to mint)?
- Which anchor gets priority in search order?

**Impact:**

- Inconsistent behavior if bonding_curve extraction fails
- No way to know why follow-on chose a particular anchor
- Can't optimize search order based on anchor reliability

**Recommended Fix:**

Ensure anchor priority is explicit in the implementation:

```python
# Priority order: bonding_curve > creator > mint
if bonding_curve:
    anchor_used = await discover_follow_on_pools(..., bonding_curve=bonding_curve, creator=None, ...)
if not pool_found and creator:
    anchor_used = await discover_follow_on_pools(..., bonding_curve=None, creator=creator, ...)
if not pool_found:
    anchor_used = await discover_follow_on_pools(..., bonding_curve=None, creator=None, ...)  # Falls back to mint
```

**Priority:** MEDIUM (Consistency, not critical)

---

## Risk #3: RPC Fallback Strategy Doesn't Account for Reason Code (MEDIUM)

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2900-3000 (RPC fallback block)

**Issue:**

When cached TX yields zero candidates, RPC fallback runs unconditionally:

```python
if run_rpc:
    rpc_success = await discover_and_register_all_pools(...)
```

But if the reason was `no_amm_program_in_tx`, RPC won't find anything (pool wasn't created yet). The system should:
- Skip RPC if reason code suggests follow-on is the only option
- Adjust timeout/retry strategy based on reason code

**Example:**
```
reason_code='no_amm_program_in_tx' → Skip RPC, just do follow-on
reason_code='meta_incomplete'       → RPC might help in 5s, don't retry yet
reason_code='meta_owner_not_indexed'→ RPC retry worth trying
```

**Recommended Fix:**

```python
# Route based on reason code
if cached_candidate_count == 0 and cached_diagnostics:
    reason_code = cached_diagnostics.get('reason_code')

    if reason_code == 'no_amm_program_in_tx':
        # Pool in follow-on TX, RPC won't help
        run_rpc = False
    elif reason_code == 'meta_incomplete':
        # Metadata still indexing, wait longer
        run_rpc = False  # Skip RPC, wait for next retry
    elif reason_code in ['meta_owner_not_indexed', 'meta_has_owners_but_no_pool_matches']:
        # RPC might help if account metadata catches up
        run_rpc = True
```

**Priority:** MEDIUM (Efficiency)

---

# ARCHITECTURAL RECOMMENDATIONS

## Recommendation #1: Implement Reason-Based Routing (HIGH PRIORITY)

Currently all zero-candidate failures are treated the same. Instead:

```python
# Initialize reason-based metrics
self.failure_reasons: Dict[str, int] = defaultdict(int)
self.anchor_success_rates: Dict[str, float] = {}

# On each zero-candidate result:
reason = cached_diagnostics.get('reason_code', 'unknown')
self.failure_reasons[reason] += 1

# Route differently based on reason
if reason == 'no_amm_program_in_tx':
    # Definitely needs follow-on discovery
    strategy = 'follow_on_only'
elif reason == 'inner_instructions_only':
    # Follow-on is likely to help
    strategy = 'follow_on_first'
elif reason == 'meta_owner_not_indexed':
    # Wait + RPC is appropriate
    strategy = 'rpc_wait'
elif reason == 'meta_incomplete':
    # Just wait for next retry
    strategy = 'wait_only'
else:
    # Unknown, try both
    strategy = 'both'
```

## Recommendation #2: Add Anchor Reliability Tracking (MEDIUM PRIORITY)

Track which anchor is most effective:

```python
self.anchor_success_stats = {
    'bonding_curve': {'attempts': 0, 'successes': 0},
    'creator': {'attempts': 0, 'successes': 0},
    'mint': {'attempts': 0, 'successes': 0},
}

# Update on follow-on result:
if follow_on_anchor:
    self.anchor_success_stats[follow_on_anchor]['successes'] += 1
self.anchor_success_stats[follow_on_anchor]['attempts'] += 1

# Use stats to adjust priority:
success_rate = stats['successes'] / stats['attempts'] if stats['attempts'] > 0 else 0
```

---

# PRIORITY ORDER FOR FIXES

### Phase 1: CRITICAL (Blocks correct functioning)

1. **Fix Bug #1: Correct failure reason classification** (~5 lines)
   - Line 2880: Store actual reason instead of "tx_not_indexed"
   - Impact: Enables correct routing

2. **Fix Bug #3: Cache diagnostics** (~10 lines)
   - Add `self.cached_tx_diagnostics_cache` dict
   - Check/store on each parse
   - Impact: Enables smart routing, saves compute

### Phase 2: HIGH (Architecture, correctness)

3. **Fix Bug #2: Unify discovery paths** (~100 lines)
   - Create `_get_pool_from_migration_context()`
   - Use from both initial and retry paths
   - Impact: Single source of truth, consistent behavior

### Phase 3: MEDIUM (Observability + efficiency)

4. **Implement reason-based routing** (~20 lines)
   - Use reason_code to skip unnecessary RPC/follow-on attempts
   - Impact: RPC quota savings, faster resolution

5. **Improve follow-on logging** (~10 lines)
   - Add anchor, txs_scanned, candidate_count to logs
   - Impact: Better observability

---

# CONCRETE CODE CHANGES

## Change #1: Fix Failure Reason Classification

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** Line ~2870 (inside the `if not pool_candidates:` block in cached TX parsing)

**Before:**
```python
if not pool_candidates:
    using_cached_payload = tx_data is not None
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
        mint=mint,
        migration_sig=original_migration_sig,
        tx_data=tx_data
    )
```

**After:**
```python
if not pool_candidates:
    using_cached_payload = tx_data is not None

    # Use actual diagnostic reason instead of assuming tx_not_indexed
    if cached_candidate_count == 0 and cached_diagnostics:
        actual_reason = cached_diagnostics.get('reason_code', 'unknown')
    else:
        actual_reason = 'tx_not_indexed'

    rejection_reasons.append(actual_reason)

    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
        mint=mint,
        migration_sig=original_migration_sig,
        tx_data=tx_data
    )
```

---

## Change #2: Cache Diagnostics

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** `__init__` method (add instance variable)

**Add:**
```python
self.cached_tx_diagnostics_cache: Dict[str, Dict] = {}
```

**Location:** Line ~2798 (in retry loop, before parse_candidates_from_cached_tx call)

**Before:**
```python
if tx_data is not None:
    candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
        await discovery.parse_candidates_from_cached_tx(tx_data)
```

**After:**
```python
if tx_data is not None:
    # Check cache first
    if mint in self.cached_tx_diagnostics_cache:
        cached_diagnostics = self.cached_tx_diagnostics_cache[mint]
        cached_tx_parsed = True
        cached_candidate_count = 0
        candidates_from_cached = []
        log_print(
            f"{Colors.DISCOVER}[CACHED_TX_DIAGNOSTICS_CACHED] Using cached result: {cached_diagnostics.get('reason_code', 'unknown')}{Colors.RESET}",
            flush=True
        )
    else:
        # Parse for first time
        candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
            await discovery.parse_candidates_from_cached_tx(tx_data)

        # Store if zero candidates
        if cached_candidate_count == 0 and cached_diagnostics:
            self.cached_tx_diagnostics_cache[mint] = cached_diagnostics
```

---

## Change #3: Implement Reason-Based RPC Routing

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** Line ~2900 (before `if run_rpc:` block)

**Add:**
```python
# Route RPC based on cached TX diagnostic reason
if run_rpc and cached_candidate_count == 0 and cached_diagnostics:
    reason_code = cached_diagnostics.get('reason_code', 'unknown')

    # Skip RPC for reasons that definitely won't help
    if reason_code in ['no_amm_program_in_tx', 'inner_instructions_only']:
        log_print(
            f"{Colors.DISCOVER}[DISCOVERY_ROUTE] Skipping RPC for {reason_code} (needs follow-on only){Colors.RESET}",
            flush=True
        )
        run_rpc = False
    elif reason_code == 'meta_incomplete' and attempt < 7:
        # Metadata still indexing, wait longer
        log_print(
            f"{Colors.DISCOVER}[DISCOVERY_ROUTE] Skipping RPC for {reason_code} in tier {tier} (wait for metadata){Colors.RESET}",
            flush=True
        )
        run_rpc = False
```

---

# VALIDATION CHECKLIST

After implementing fixes:

```bash
# 1. Verify diagnostics are correct
sqlite3 database/flex_complete_database.db \
  "SELECT mint, failure_class FROM token_pool_accounts WHERE failure_class LIKE '%no_amm%' LIMIT 5"

# 2. Check cache is being used (log grep)
grep -c "CACHED_TX_DIAGNOSTICS_CACHED" listener.log

# 3. Verify follow-on is succeeding for no_amm_program_in_tx
grep "FOLLOW_ON_SUCCESS" listener.log | grep -E "no_amm|inner_instruction" | wc -l

# 4. Confirm RPC not wasted on zero-candidate cached TXs
grep "DISCOVERY_ROUTE.*Skipping RPC" listener.log | wc -l

# 5. Check resolution times improve
sqlite3 database/flex_complete_database.db \
  "SELECT AVG(resolve_seconds) FROM token_resolution_telemetry WHERE resolve_source='follow_on'"
```

---

# SUMMARY TABLE

| Bug | Location | Impact | Fix | Priority |
|-----|----------|--------|-----|----------|
| #1: Wrong failure reason | Line ~2870 | Incorrect routing | Store actual reason_code | CRITICAL |
| #2: Duplicate discovery paths | Lines 2326 vs 2753 | Inconsistent behavior | Unify into single function | HIGH |
| #3: Diagnostics not cached | Line ~2798 | Wasted computation | Add cache dict + check | HIGH |
| Risk #1: Insufficient logging | Lines 2851-2868 | Poor observability | Add anchor, txs_scanned | MEDIUM |
| Risk #3: RPC doesn't account for reason | Line ~2900 | Wasted RPC quota | Implement reason-based routing | MEDIUM |

---

## Key Insight

The system has graduated from "infrastructure debugging" to **"pool creation discovery"**:

- Migration TX doesn't always contain pool creation
- Pool might be in inner instruction (Flow discovery)
- Pool might be in follow-on TX (current, after migration)
- Pool might not be created yet (RPC wait strategy)

Each requires different strategy. The diagnostics already classify which applies. Now the routing logic needs to use that classification.
