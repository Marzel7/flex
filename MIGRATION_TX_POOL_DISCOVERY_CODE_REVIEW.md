# Migration TX → Pool Discovery Code Review

**Date:** March 20, 2026
**Focus:** Code-level bugs and architectural issues in migration TX → pool/vault discovery path

---

## DEFINITE BUGS

### Bug #1: Bonding Curve Extracted But Never Passed to Follow-On Discovery

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2612 + 2803

**The Issue:**

Line 2612 extracts bonding curve from migration context:
```python
bonding_curve_pda = provenance.get('bonding_curve_pda') if provenance else None
```

This is in `_process_migration_with_mint()` and is passed to `_update_token_entry_with_creator()`.

Later, at line 2803 in `_retry_pool_discovery()`, follow-on discovery is called with:
```python
bonding_curve = None
creator = None
```

**Why this is a bug:**

- bonding_curve_pda is known at migration detection time (line 2612)
- It is NOT passed into `_retry_pool_discovery()`
- Follow-on discovery then searches with `bonding_curve=None` (fallback to weaker anchors)
- This defeats the entire purpose of extracting bonding_curve early

**The Fix:**

Pass bonding_curve_pda as parameter to `_retry_pool_discovery()`:

```python
async def _retry_pool_discovery(
    self,
    mint,
    original_migration_sig,
    delays,
    tx_source,
    tx_data,
    bonding_curve=None,  # NEW
    creator=None,        # NEW
    migration_timestamp=None,  # NEW
):
```

Then call follow-on with real values:
```python
follow_on_pool, follow_on_anchor, follow_on_offset, follow_on_txs_scanned = await discovery.discover_follow_on_pools(
    mint=mint,
    migration_sig=original_migration_sig,
    bonding_curve=bonding_curve,  # PASS REAL VALUE
    creator=creator,  # PASS REAL VALUE
    token_mint=mint,
    max_txs_per_anchor=follow_on_max_txs,
)
```

**Impact:** HIGH - This is the single most important fix. Bonding curve is a strong anchor and should be used.

---

### Bug #2: Creator Extracted But Never Passed to Follow-On Discovery

**Location:** Same as Bug #1, lines 2612 + 2803

**The Issue:**

Line 2612 also extracts creator (earliest_creator):
```python
if earliest_creator:
    ...
    await self._update_token_entry_with_creator(mint, earliest_creator, ...)
```

But `earliest_creator` is a local variable in `_process_migration_with_mint()` and is NOT passed to `_retry_pool_discovery()`.

Later follow-on discovery is called with:
```python
creator = None  # Should be earliest_creator
```

**The Fix:**

Same as Bug #1 - pass `creator` parameter through:

```python
async def _retry_pool_discovery(
    self,
    ...
    creator=None,  # NEW PARAMETER
):
```

And pass it to follow-on:
```python
await discovery.discover_follow_on_pools(
    ...
    creator=creator,  # USE REAL VALUE
    ...
)
```

**Impact:** HIGH - Creator is the secondary anchor and should be passed.

---

### Bug #3: Duplicated Migration TX Discovery Logic

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2326-2470 vs 2753-2847

**The Issue:**

There are TWO completely separate migration TX discovery flows:

**Flow 1 (lines 2326-2470):** In `_process_migration_with_mint()`
```python
if tx_data:
    discovery = PostMigrationPoolDiscovery(RPC_HTTP)
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)
```
- Runs early in migration detection
- Uses `discover_pool_candidates_from_migration_tx()`
- Limited retry logic

**Flow 2 (lines 2753-2847):** In `_retry_pool_discovery()`
```python
if run_tx:
    discovery = PostMigrationPoolDiscovery(RPC_HTTP)
    candidates_from_cached, ..., cached_diagnostics = await discovery.parse_candidates_from_cached_tx(tx_data)
```
- Runs during retry loop
- Uses `parse_candidates_from_cached_tx()`
- Has full Phase 3 follow-on integration

**The Problem:**

- Flow 1 never runs follow-on discovery
- Flow 1 uses older `discover_pool_candidates_from_migration_tx()` without diagnostics
- Flow 2 is the newer, better path but only runs after 1st attempt fails
- If Flow 1 succeeds early, Flow 2's improvements are never used
- If Flow 1 fails, it's not logged why (no diagnostics)

**The Fix:**

Consolidate to single flow. Flow 2 should be THE canonical path:

```python
async def _get_pool_from_migration_context(
    self,
    mint: str,
    migration_sig: str,
    tx_data: Optional[Dict],
    bonding_curve: str = None,
    creator: str = None,
    block_time: int = None,
) -> Tuple[Optional[str], str, Dict]:
    """
    Single canonical path for migration TX → pool discovery.

    Handles:
    1. Cached TX parse (fast)
    2. Diagnostics (why zero candidates)
    3. Follow-on discovery (if zero candidates)
    4. RPC fallback (if follow-on fails)

    Returns: (pool_address, discovery_source, diagnostics)
    """
    # Implement full Phase 3 pipeline here
    # Use real bonding_curve, creator, block_time
```

Then call this ONCE from `_process_migration_with_mint()` for early detection.

**Impact:** MEDIUM-HIGH - Architectural issue, not immediate bug, but causes inconsistent behavior.

---

### Bug #4: Cached TX Diagnostics Not Persisted Across Retries

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2774-2779

**The Issue:**

When cached TX yields zero candidates with reason code `no_amm_program_in_tx`:

```python
if cached_candidate_count == 0 and cached_diagnostics:
    diag = cached_diagnostics
    log_print(f"[CACHED_TX_DIAGNOSTICS] {diag.get('diagnostic_detail', 'unknown reason')}")
```

This logs the reason but doesn't store it. Then on attempt 2, 3, 4, etc., the system re-parses the SAME static cached TX and gets the SAME zero-candidate result.

**Why this is a bug:**

- The cached TX is immutable (it came from chain)
- If it has `reason=no_amm_program_in_tx` on attempt 1, that fact never changes
- Retrying the same parse is wasted effort
- The diagnostic info should be stored and used to skip re-parsing

**The Fix:**

Store cached-TX diagnostic result after first parse:

```python
# After first parse in attempt 1
if cached_candidate_count == 0 and cached_diagnostics:
    self.cached_tx_diagnostics[mint] = cached_diagnostics

# On subsequent attempts, check first
if mint in self.cached_tx_diagnostics:
    cached_diagnostics = self.cached_tx_diagnostics[mint]
    # Skip re-parsing, use stored result
    candidates_from_cached = []
else:
    # First attempt, parse now
    candidates_from_cached, ..., cached_diagnostics = await discovery.parse_candidates_from_cached_tx(tx_data)
    self.cached_tx_diagnostics[mint] = cached_diagnostics
```

**Impact:** MEDIUM - Not a correctness bug, but inefficiency. Avoids re-parsing static TX.

---

## LIKELY BUGS / RISKY CODE

### Risk #1: Pool Candidates Could Reference Uninitialized Variable

**Location:** `src/core/pumpfun_curve_listener.py`, line 2841

**The Code:**
```python
if not pool_candidates:  # Line 2841
    using_cached_payload = tx_data is not None
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)
```

**The Issue:**

If `attempt < 4`, the `follow_on_max_txs == 0` block at line 2791 is skipped entirely. This means:
- The nested `if follow_on_max_txs > 0 ...` block (line 2799) is never entered
- `pool_candidates` is only set inside that block (lines 2824, 2832, 2837)
- **YOU FIXED THIS** by initializing `pool_candidates = []` at line 2788

Verify this fix is in place:

```python
else:  # Line 2787
    pool_candidates = []  # NEW - MUST BE HERE
    follow_on_max_txs = 0
```

**Status:** FIXED (by your earlier commit)

---

### Risk #2: Creator Variable Scope Issue

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2610 + 2803

**The Issue:**

In `_process_migration_with_mint()`, `earliest_creator` is extracted at line 2610 (inside a try block).

But it's used at line 2640 (outside the try block):
```python
except Exception as creator_err:
    log_print(f"[CREATOR] ⚠ Could not extract creator: {creator_err}", flush=True)

# ... then later ...

log_print(f"[MIGRATION] ✅ CRITICAL PATH COMPLETE - Token {mint[:8]}... with creator {earliest_creator[:8]...}")
# ↑ earliest_creator may not be defined if exception occurred!
```

**The Fix:**

Initialize `earliest_creator = None` before the try block:

```python
earliest_creator = None  # Initialize before try

try:
    # ... extraction logic ...
    earliest_creator = provenance.get('creator')
except Exception as creator_err:
    log_print(f"[CREATOR] ⚠ Could not extract creator: {creator_err}", flush=True)

# Safe to use now
log_print(f"[MIGRATION] ... creator {earliest_creator[:8] if earliest_creator else 'unknown'}...")
```

**Status:** NEEDS FIX

---

### Risk #3: RPC Fallback Called Twice in Same Attempt

**Location:** `src/core/pumpfun_curve_listener.py`, lines 2357 + 2843

**The Issue:**

In `_process_migration_with_mint()` at line 2357, RPC is called to validate pool candidates:
```python
acct = await self._post_rpc_with_fallback(account_info_payload, timeout=5)
```

Then in `_retry_pool_discovery()` at line 2843, RPC is called again:
```python
pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...tx_data...)
```

This function internally calls RPC again (in `discover_pool_candidates_from_migration_tx` it calls `get_account_info` for each candidate).

**Why this is risky:**

- If Flow 1 (`_process_migration_with_mint`) fails to register the pool, it falls through to `_retry_pool_discovery`
- Flow 2 re-fetches the same TX and re-validates the same candidates
- Redundant RPC work during critical window when quota is limited (8 concurrent slots)

**The Fix:**

If a pool is found in Flow 1 but registration fails, save it in state:

```python
self.failed_pool_candidates[mint] = pool_candidates  # Save for retry

# Then in _retry_pool_discovery, check first:
if mint in self.failed_pool_candidates:
    pool_candidates = self.failed_pool_candidates[mint]
else:
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)
```

**Status:** NEEDS FIX if Flow 1 is kept (but recommend consolidating to single flow instead)

---

## ARCHITECTURAL CLEANUP RECOMMENDATIONS

### Recommendation #1: Unify Migration TX Discovery

**Current State:**
- Flow 1: `_process_migration_with_mint()` (early attempt, no follow-on)
- Flow 2: `_retry_pool_discovery()` (retry loop, with follow-on + diagnostics)

**Recommended State:**

Single function `_get_pool_from_migration_context()` that:
1. Parses cached TX
2. Emits diagnostics
3. Runs follow-on discovery (if zero candidates)
4. Falls back to RPC
5. Returns (pool_address, discovery_source, metrics)

Called once from `_process_migration_with_mint()` for early path, and again from `_retry_pool_discovery()` if needed.

**Benefit:** Single source of truth for discovery logic, consistent behavior.

---

### Recommendation #2: Make bonding_curve and creator Required Parameters

**Current State:**
```python
async def _retry_pool_discovery(
    self,
    mint,
    original_migration_sig,
    delays,
    tx_source,
    tx_data,
    # bonding_curve NOT a parameter
    # creator NOT a parameter
):
```

**Recommended State:**
```python
async def _retry_pool_discovery(
    self,
    mint: str,
    original_migration_sig: str,
    delays: list,
    tx_source: str,
    tx_data: Optional[Dict],
    bonding_curve: Optional[str] = None,  # REQUIRED
    creator: Optional[str] = None,         # REQUIRED
    migration_timestamp: Optional[int] = None,  # OPTIONAL
):
```

**Benefit:** Follow-on discovery gets real anchors by design, not accident.

---

### Recommendation #3: Classify Cached TX Failures Explicitly

**Current State:**
```python
if cached_candidate_count == 0 and cached_diagnostics:
    diag = cached_diagnostics
    log_print(f"[CACHED_TX_DIAGNOSTICS] {diag.get('diagnostic_detail', 'unknown reason')}")
    # Logged but not stored or used for routing
```

**Recommended State:**

Store and use diagnostics to guide follow-on strategy:

```python
if cached_candidate_count == 0 and cached_diagnostics:
    reason_code = cached_diagnostics.get('reason_code')

    # Store for telemetry
    self.cached_tx_zero_reasons[mint] = reason_code

    # Route based on reason
    if reason_code == 'no_amm_program_in_tx':
        # This token NEEDS follow-on discovery
        # Use all 3 anchors (bonding_curve, creator, mint)
        follow_on_max_txs = 20 if attempt >= 4 else 0
    elif reason_code == 'meta_incomplete':
        # Metadata not indexed yet, more waiting helps
        # Try again in next retry
        follow_on_max_txs = 0
    elif reason_code == 'inner_instructions_only':
        # Pool in CPI, follow-on will help
        follow_on_max_txs = 20
```

**Benefit:** Follow-on discovery is prioritized only when useful.

---

## PRIORITY ORDER FOR FIXES

### Priority 1: IMMEDIATE (Blocks follow-on from working)

1. **Pass bonding_curve to `_retry_pool_discovery()`**
   - Lines: 2612 extraction, 2268 function signature, 2803 call site
   - Effort: 3 lines changed
   - Impact: HIGH - Makes follow-on discovery actually work

2. **Pass creator to `_retry_pool_discovery()`**
   - Lines: 2610 extraction, 2268 function signature, 2803 call site
   - Effort: 3 lines changed
   - Impact: HIGH - Same as above

3. **Initialize earliest_creator = None before try block**
   - Lines: ~2610
   - Effort: 1 line added
   - Impact: MEDIUM - Prevents UnboundLocalError

### Priority 2: SHORT-TERM (Improve efficiency)

4. **Cache cached-TX diagnostics across retries**
   - Lines: 2774 store, 2765 check
   - Effort: 5 lines added
   - Impact: MEDIUM - Avoids re-parsing static TX

5. **Store zero-reason codes for telemetry**
   - Lines: 2774 capture, storage
   - Effort: 3 lines added
   - Impact: MEDIUM - Enables better routing

### Priority 3: CLEANUP (Architectural)

6. **Consolidate dual migration TX discovery flows**
   - Lines: 2326 vs 2753
   - Effort: Moderate refactor (extract common function)
   - Impact: MEDIUM - Single source of truth

7. **Add cached pool candidates to state to avoid re-validation**
   - Lines: ~2357 storage, ~2843 lookup
   - Effort: Small
   - Impact: LOW - Optimization, not correctness

---

## CODE-LEVEL CHANGES NEEDED

### Change #1: Update `_retry_pool_discovery()` Signature

**File:** `src/core/pumpfun_curve_listener.py`
**Line:** ~2685 (function definition)

**Before:**
```python
async def _retry_pool_discovery(
    self, mint, original_migration_sig, delays, tx_source, tx_data
):
```

**After:**
```python
async def _retry_pool_discovery(
    self,
    mint: str,
    original_migration_sig: str,
    delays: list,
    tx_source: str,
    tx_data: Optional[Dict],
    bonding_curve: Optional[str] = None,
    creator: Optional[str] = None,
    migration_timestamp: Optional[int] = None,
):
```

---

### Change #2: Pass Real Bonding Curve and Creator to `_retry_pool_discovery()`

**File:** `src/core/pumpfun_curve_listener.py`
**Line:** ~2465 (call site in `_process_migration_with_mint`)

**Before:**
```python
await self._retry_pool_discovery(
    mint=mint,
    original_migration_sig=signature,
    delays=DISCOVERY_DELAYS,
    tx_source=tx_source,
    tx_data=tx_data
)
```

**After:**
```python
await self._retry_pool_discovery(
    mint=mint,
    original_migration_sig=signature,
    delays=DISCOVERY_DELAYS,
    tx_source=tx_source,
    tx_data=tx_data,
    bonding_curve=bonding_curve_pda,
    creator=earliest_creator,
    migration_timestamp=block_time,
)
```

Where:
- `bonding_curve_pda` comes from line 2612
- `earliest_creator` is defined before try block
- `block_time` comes from `tx_data.get("blockTime")`

---

### Change #3: Use Real Anchors in Follow-On Discovery Call

**File:** `src/core/pumpfun_curve_listener.py`
**Line:** ~2810 (follow-on call)

**Before:**
```python
follow_on_pool, follow_on_anchor, follow_on_offset, follow_on_txs_scanned = await discovery.discover_follow_on_pools(
    mint=mint,
    migration_sig=original_migration_sig,
    bonding_curve=None,          # ← BUG
    creator=None,                 # ← BUG
    token_mint=mint,
    max_txs_per_anchor=follow_on_max_txs,
)
```

**After:**
```python
follow_on_pool, follow_on_anchor, follow_on_offset, follow_on_txs_scanned = await discovery.discover_follow_on_pools(
    mint=mint,
    migration_sig=original_migration_sig,
    bonding_curve=bonding_curve,  # ← PASS REAL VALUE
    creator=creator,               # ← PASS REAL VALUE
    token_mint=mint,
    max_txs_per_anchor=follow_on_max_txs,
)
```

---

## SUMMARY

| Issue | Type | Priority | Effort | Impact |
|-------|------|----------|--------|--------|
| Bonding curve not passed to follow-on | BUG | 1 | 3 lines | HIGH |
| Creator not passed to follow-on | BUG | 1 | 3 lines | HIGH |
| earliest_creator uninitialized | BUG | 1 | 1 line | MEDIUM |
| Cached TX re-parsed per attempt | BUG | 2 | 5 lines | MEDIUM |
| Dual discovery flows | ARCH | 3 | Refactor | MEDIUM |
| RPC called twice | RISK | 2 | 5 lines | LOW |

**Recommended next step:** Fix Priority 1 issues (3 changes, ~7 lines total) to make follow-on discovery actually receive real bonding_curve and creator anchors.
