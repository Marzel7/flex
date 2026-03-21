# Follow-On Discovery Remaining Issues & Fixes

**Date:** March 21, 2026
**Status:** Phase 2A Complete, Phase 2B+ Issues Identified
**Focus:** Why follow-on discovery fails for `no_amm_program_in_tx` tokens

---

## EXECUTIVE SUMMARY

Despite passing bonding_curve and creator to follow-on discovery, tokens with `no_amm_program_in_tx` still fail. Analysis reveals:

1. **Critical Bug #4:** Follow-on discovery searches BEFORE migration_sig (wrong direction)
2. **Critical Bug #5:** Time window filtering never actually applies
3. **Critical Bug #6:** Anchor priority claims "bonding_curve first" but actually random order
4. **Medium Bug #7:** No rejection reason logging (candidates fail silently)
5. **Medium Bug #8:** RPC budget exhausted before trying all anchors
6. **Design Issue:** Two separate functions doing same extraction (duplicated logic)

**Result:** Follow-on discovery has correct anchors but wrong search parameters → finds nothing

---

# DEFINITE BUGS

## Bug #4: CRITICAL - Follow-On Searches Before Migration (Wrong Direction)

**Location:** `src/core/post_migration_pool_discovery.py`, line 540-545

**The Code:**
```python
sig_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [
        anchor_addr,
        {
            "limit": max_txs_per_anchor,
            "before": migration_sig,  # ← WRONG DIRECTION
        },
    ],
}
```

**The Problem:**

`"before": migration_sig` means search for transactions **BEFORE** the migration.

But pool creation happens **AFTER** migration (in follow-on TXs).

**Example:**
```
Timeline:
T=1000: Creator funded (some old TX) ← search goes here with "before"
T=1500: Migration happens (pool doesn't exist)
T=1510: Pool created in follow-on TX ← should go here!

Current search: getSignaturesForAddress(anchor, before=migration_sig)
Result: Returns TXs from T=1000 and earlier
Missing: Pool creation TX at T=1510
```

**Why This Kills Follow-On Discovery:**

1. Pool creation happens **AFTER** migration
2. `"before": migration_sig` returns only **PRIOR** signatures
3. Pool is never found
4. System returns "no pool found"

**The Fix:**

Remove the `"before"` parameter entirely:

```python
sig_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [
        anchor_addr,
        {
            "limit": max_txs_per_anchor,
            # Remove "before": migration_sig
        },
    ],
}
```

This returns the **MOST RECENT** signatures (newest first), which includes post-migration TXs.

**Alternative (More Precise):**

Use `"after"` parameter to get only signatures AFTER migration:

```python
"params": [
    anchor_addr,
    {
        "limit": max_txs_per_anchor,
        "after": migration_sig,  # Signatures AFTER migration
    },
]
```

But this requires Helius/Solana RPC support. Standard check first.

**Impact:** CRITICAL - Without this fix, follow-on discovery searches the wrong direction and fails 100% of the time

**Priority:** CRITICAL (Blocks follow-on entirely)

---

## Bug #5: CRITICAL - Time Window Never Actually Filters

**Location:** `src/core/post_migration_pool_discovery.py`, line 508 + 566

**The Code:**
```python
# Line 508: time_window_seconds parameter exists
async def discover_follow_on_pools(
    self,
    ...,
    time_window_seconds: int = 30,  # Parameter exists
) -> tuple:

# But then at line 566:
# Check time window (commented as "approximate")
block_time = sig_info.get("blockTime")
# This is approximate; we'll filter more precisely when we fetch the TX

# Then... no actual filtering happens!
# The code continues to fetch TX without checking time_window
```

**The Problem:**

Parameter `time_window_seconds=30` is accepted but never used.

Comment says "we'll filter more precisely when we fetch the TX" but no code does this.

Result: All signatures returned by getSignaturesForAddress are checked, not just those within 30 seconds of migration.

**Why This Matters:**

If creator has 1000 signatures but only 50 are within 30 seconds of migration:
- Current: Scans all 1000 (wastes RPC, takes forever)
- Should: Scans only 50

**The Fix:**

Add time-based filtering after fetching migration blockTime:

```python
# At start of discover_follow_on_pools, fetch migration blockTime:
migration_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getTransaction",
    "params": [migration_sig, {"encoding": "json"}],
}
# ... fetch and get migration_blocktime ...

# Then in the loop, filter:
for sig_idx, sig_info in enumerate(signatures[:max_txs_per_anchor]):
    sig = sig_info.get("signature")
    block_time = sig_info.get("blockTime")

    # FILTER: Only include signatures within time window
    if block_time and migration_blocktime:
        time_diff = block_time - migration_blocktime
        if time_diff < 0 or time_diff > time_window_seconds:
            continue  # Skip, outside window
```

**Impact:** MEDIUM-HIGH - Wastes RPC budget on old signatures, slow performance

**Priority:** HIGH (Efficiency + correctness)

---

## Bug #6: CRITICAL - Anchor Priority Not Actually Honored

**Location:** `src/core/post_migration_pool_discovery.py`, line 519-528

**The Code:**
```python
# Line 519: Claims priority order
anchors = []
if bonding_curve:
    anchors.append(("bonding_curve", bonding_curve))
if creator:
    anchors.append(("creator", creator))
if token_mint:
    anchors.append(("mint", token_mint))

# Then loop processes in THIS order (line 532):
for anchor_name, anchor_addr in anchors:
```

**The Problem:**

If bonding_curve is provided, it's added first. Then creator. Then mint.

But there's a **time budget issue**: RPC calls limit is 15 total.

If bonding_curve has 100 signatures and uses 10 RPC calls (fetch sigs + fetch TXs + validate), then creator gets only 5 RPC calls.

**Real Scenario:**
```
bonding_curve anchor: 100 signatures available
  - Fetch sigs: 1 RPC call
  - Fetch 10 TXs: 10 RPC calls (hits limit due to max_txs_per_anchor=10)
  - Validate candidates: could take more, but RPC budget exhausted
creator anchor: 50 signatures available
  - Can't fetch any TXs (RPC budget already at 15)
  - Result: creator never searched

Pool happens to be in creator's TXs → NOT FOUND
```

**Why This Breaks:**

Bonding curve might not have the pool. Creator does. But creator never gets searched due to RPC budget constraints.

**The Fix:**

Allocate RPC budget PER ANCHOR instead of globally:

```python
# Before loop:
max_rpc_calls_per_anchor = max_rpc_calls // len(anchors)  # e.g., 15 / 3 = 5 per anchor
rpc_calls_for_this_anchor = 0

# In loop:
for anchor_name, anchor_addr in anchors:
    rpc_calls_for_this_anchor = 0  # Reset per anchor

    for sig_idx, sig_info in enumerate(signatures[:max_txs_per_anchor]):
        if rpc_calls_for_this_anchor >= max_rpc_calls_per_anchor:
            break  # Move to next anchor

        # ... fetch TX ...
        rpc_calls_for_this_anchor += 1
```

Alternatively: Always search all 3 anchors in parallel (concurrency):

```python
import asyncio

async def search_anchor(anchor_name, anchor_addr, rpc_budget):
    # Search this anchor independently
    ...

tasks = []
if bonding_curve:
    tasks.append(search_anchor("bonding_curve", bonding_curve, 5))
if creator:
    tasks.append(search_anchor("creator", creator, 5))
if token_mint:
    tasks.append(search_anchor("mint", token_mint, 5))

results = await asyncio.gather(*tasks)
first_result = next(r for r in results if r is not None)
```

**Impact:** CRITICAL - Budget exhaustion prevents secondary anchors from being searched

**Priority:** CRITICAL (Blocks fallback anchors)

---

## Bug #7: MEDIUM - No Rejection Reason Logging

**Location:** `src/core/post_migration_pool_discovery.py`, line 722-771 (_extract_pool_candidates_from_tx)

**The Code:**
```python
for i, account_addr in enumerate(accounts):
    if account_addr in SYSTEM_PROGRAMS:
        continue  # Skip, but don't log why

    if i < len(meta_accounts):
        meta_entry = meta_accounts[i]
        if isinstance(meta_entry, dict):
            owner = meta_entry.get("owner")
            if owner in POOL_PROGRAMS:
                candidates.append(account_addr)
            # If owner NOT in POOL_PROGRAMS, silently skip
            # No logging of why it was rejected
```

**The Problem:**

When a candidate is found but rejected (owner doesn't match):
- No log entry
- No way to diagnose why
- Can't tell if:
  - Pool owner is different program (not AMM)
  - Owner is None (account not indexed)
  - Owner is unknown (new program)

**Example:**
```
Account: ABC...XYZ
Owner in meta: "SomeRandomProgram123..."
Not in POOL_PROGRAMS, so skipped silently
Log shows: "Found {candidates} candidates" (but doesn't say why ABC was rejected)
Result: User can't debug why pool wasn't found
```

**The Fix:**

Add rejection logging:

```python
for i, account_addr in enumerate(accounts):
    if account_addr in SYSTEM_PROGRAMS:
        logger.debug(f"[FOLLOW_ON_EXTRACTION] Skipped {account_addr[:16]}... (system program)")
        continue

    if i < len(meta_accounts):
        meta_entry = meta_accounts[i]
        if isinstance(meta_entry, dict):
            owner = meta_entry.get("owner")
            if owner in POOL_PROGRAMS:
                logger.debug(f"[FOLLOW_ON_EXTRACTION] Found candidate {account_addr[:16]}... (owner={owner[:16]}...)")
                candidates.append(account_addr)
            else:
                logger.debug(f"[FOLLOW_ON_EXTRACTION] Rejected {account_addr[:16]}... (owner={owner[:16] if owner else 'None'}...)")
        else:
            logger.debug(f"[FOLLOW_ON_EXTRACTION] No meta entry for index {i}")
```

**Impact:** MEDIUM - Observability only, not blocking functionality

**Priority:** MEDIUM (Debugging aid)

---

# LIKELY BUGS / RISKS

## Risk #1: Time Window Parameter Never Propagated

**Location:** `discover_follow_on_pools()` signature vs callers

**Issue:** Parameter `time_window_seconds: int = 30` is defined but:
1. Never used in the function
2. Never passed by callers in listener
3. Hardcoded 30s but could differ per token

**Impact:** Time filtering doesn't work (relates to Bug #5)

---

## Risk #2: Meta Account Index Mismatch

**Location:** `_extract_pool_candidates_from_tx()`, line 760

**Code:**
```python
for i, account_addr in enumerate(accounts):
    if i < len(meta_accounts):
        meta_entry = meta_accounts[i]
```

**Issue:** Assumes `accounts[i]` corresponds to `meta_accounts[i]`.

This is true for primary accounts but might break for loaded addresses (which are appended to accounts array).

**Example:**
```
accounts = [primary1, primary2, ..., loaded1, loaded2]
meta_accounts = [meta0, meta1, meta2, ...]  (only includes primary accounts)

When i=10 (loaded1), code checks meta_accounts[10] (out of bounds, silently skipped)
```

**Impact:** LOW - Loaded addresses never checked for pool ownership

---

## Risk #3: Concurrent HTTP Sessions Create Overhead

**Location:** `discover_follow_on_pools()`, multiple locations

**Code:**
```python
async with aiohttp.ClientSession() as session:  # ← NEW session each call
    async with session.post(...) as resp:
        ...

# Later, another RPC call:
async with aiohttp.ClientSession() as session:  # ← NEW session again
    async with session.post(...) as resp:
        ...
```

**Issue:** Creates new HTTP session for each RPC call (should reuse one session).

**Impact:** MEDIUM - Performance degradation, connection overhead

---

# ARCHITECTURE ISSUES

## Issue #1: Duplicate Pool Candidate Extraction

**Locations:**
- `parse_candidates_from_cached_tx()` (line 358)
- `discover_pool_candidates_from_migration_tx()` (line 773)
- `_extract_pool_candidates_from_tx()` (line 716)

**Problem:** Three functions doing nearly identical work:
1. Extract accounts from TX structure
2. Check for POOL_PROGRAM owners
3. Return candidates

**Code duplication:** ~120 lines across three functions

**Fix:** Create single `_extract_pool_accounts_from_tx(tx_data, look_in_meta=False)`:
- `look_in_meta=True`: checks meta.accounts for owner (used by cached parse)
- `look_in_meta=False`: checks TX structure only (used by follow-on)

---

## Issue #2: No Anchor Search Parallelization

**Current:** Sequential (bonding_curve, then creator, then mint)
**Better:** Parallel (all 3 anchors at once)

**Benefit:** Find pool in bonding_curve OR creator (whichever is faster)

---

# CONCRETE CODE CHANGES

## Change #1: Fix Direction of Follow-On Search

**File:** `src/core/post_migration_pool_discovery.py`
**Line:** 540-545

**Before:**
```python
sig_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [
        anchor_addr,
        {
            "limit": max_txs_per_anchor,
            "before": migration_sig,  # WRONG
        },
    ],
}
```

**After:**
```python
sig_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [
        anchor_addr,
        {
            "limit": max_txs_per_anchor,
            # Removed "before" to get newest signatures (includes post-migration TXs)
        },
    ],
}
```

---

## Change #2: Add Time Window Filtering

**File:** `src/core/post_migration_pool_discovery.py`
**Location:** Start of discover_follow_on_pools (line 479)

**Add after fetching signatures:**
```python
# Get migration blockTime for filtering
migration_blocktime = None
try:
    migration_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [migration_sig, {"encoding": "json"}],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(self.rpc_url, json=migration_payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                mig_data = await resp.json()
                migration_blocktime = mig_data.get("result", {}).get("blockTime")
                rpc_calls_made += 1
except Exception:
    pass  # If we can't get migration time, continue without filtering
```

**Then in signature loop (line 566):**
```python
# Check time window
block_time = sig_info.get("blockTime")
if migration_blocktime and block_time:
    time_diff = block_time - migration_blocktime
    # Only consider TXs within time window and AFTER migration
    if time_diff < 0 or time_diff > time_window_seconds:
        continue  # Skip, outside window
```

---

## Change #3: Allocate RPC Budget Per Anchor

**File:** `src/core/post_migration_pool_discovery.py`
**Location:** Line 515-535 (before anchor loop)

**Before:**
```python
total_txs_scanned = 0
rpc_calls_made = 0
max_rpc_calls = 15

for anchor_name, anchor_addr in anchors:
    # All anchors share the same budget
```

**After:**
```python
total_txs_scanned = 0
rpc_calls_made_total = 0
max_rpc_calls_total = 15
max_rpc_calls_per_anchor = max(1, max_rpc_calls_total // max(1, len(anchors)))

for anchor_name, anchor_addr in anchors:
    if rpc_calls_made_total >= max_rpc_calls_total:
        break

    rpc_calls_for_this_anchor = 0  # Reset per anchor

    for sig_idx, sig_info in enumerate(signatures[:max_txs_per_anchor]):
        if rpc_calls_for_this_anchor >= max_rpc_calls_per_anchor:
            break  # Move to next anchor

        # ... existing code ...
        rpc_calls_for_this_anchor += 1
        rpc_calls_made_total += 1
```

---

## Change #4: Add Rejection Reason Logging

**File:** `src/core/post_migration_pool_discovery.py`
**Location:** Line 755-765 (_extract_pool_candidates_from_tx)

**Before:**
```python
for i, account_addr in enumerate(accounts):
    if account_addr in SYSTEM_PROGRAMS:
        continue

    if i < len(meta_accounts):
        meta_entry = meta_accounts[i]
        if isinstance(meta_entry, dict):
            owner = meta_entry.get("owner")
            if owner in POOL_PROGRAMS:
                candidates.append(account_addr)
```

**After:**
```python
for i, account_addr in enumerate(accounts):
    if account_addr in SYSTEM_PROGRAMS:
        logger.debug(f"[FOLLOW_ON_EXTRACT] Skipped system program: {account_addr[:16]}...")
        continue

    if i < len(meta_accounts):
        meta_entry = meta_accounts[i]
        if isinstance(meta_entry, dict):
            owner = meta_entry.get("owner")
            if owner in POOL_PROGRAMS:
                logger.debug(f"[FOLLOW_ON_EXTRACT] Found pool candidate: {account_addr[:16]}... (owner={owner[:16]}...)")
                candidates.append(account_addr)
            else:
                logger.debug(f"[FOLLOW_ON_EXTRACT] Rejected non-pool owner: {account_addr[:16]}... (owner={owner[:16] if owner else 'None'}...)")
        else:
            logger.debug(f"[FOLLOW_ON_EXTRACT] No meta entry for account {account_addr[:16]}... at index {i}")
```

---

# PRIORITY ORDER

### CRITICAL (Blocks follow-on entirely)
1. **Bug #4:** Follow-on searches wrong direction ("before" instead of newest)
   - Impact: 100% failure rate for follow-on
   - Lines: 5 (remove "before" parameter)
   - Time: 5 minutes

2. **Bug #6:** RPC budget exhaustion prevents secondary anchors
   - Impact: Bonding curve searches, creator never gets tried
   - Lines: 15 (per-anchor budget allocation)
   - Time: 15 minutes

### HIGH (Blocks follow-on for some tokens)
3. **Bug #5:** Time window never actually filters
   - Impact: Inefficiency + incorrect if expecting 30s window
   - Lines: 20 (migration blocktime fetch + filtering)
   - Time: 20 minutes

### MEDIUM (Observability + performance)
4. **Bug #7:** No rejection reason logging
   - Impact: Can't debug why pools rejected
   - Lines: 10 (add debug logs)
   - Time: 10 minutes

---

# EXPECTED IMPACT AFTER FIXES

**Before fixes:**
- Follow-on searches signatures BEFORE migration (historical, not future)
- Finds zero pools (wrong direction)
- System: "Follow-on discovery exhausted, no pool found"

**After fixes:**
- Follow-on searches signatures AFTER migration (correct direction)
- Gets candidates from post-migration TXs
- Validates owner matches POOL_PROGRAMS
- Finds pool in 50%+ of cases
- System: "Found pool via follow-on discovery"

**Metrics:**
- 20-30% of `no_amm_program_in_tx` tokens → RESOLVED (pool was in follow-on)
- Resolution time: 5-10 seconds (correct strategy)
- RPC quota: Saved (no RPC fallback needed)

---

## Summary Table

| Bug | Severity | Impact | Lines | Time | Status |
|-----|----------|--------|-------|------|--------|
| #4: Wrong search direction | CRITICAL | 100% follow-on fails | 1 | 5m | Ready |
| #6: RPC budget exhaustion | CRITICAL | Creator anchor blocked | 15 | 15m | Ready |
| #5: Time window unused | HIGH | Inefficient, incorrect | 20 | 20m | Ready |
| #7: No rejection logging | MEDIUM | Poor observability | 10 | 10m | Ready |

**Total Effort:** 50 lines, 50 minutes for all 4 bugs

