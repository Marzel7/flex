# Pool Detector Hardening & Debugging Design

**Date:** 2026-03-14
**Status:** Design Document (Ready for Implementation)
**Scope:** Enhance `PoolDetector` observability and robustness

---

## Executive Summary

The current `PoolDetector.detect_pool_from_tx()` correctly merges all account sources (base keys + loaded addresses) but lacks observability into why a pool is not being found. For a failing token (`8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump`), the detector reported:

```
[POOL_DETECT] No AMM-owned pool found in 25 accounts (searched 25 + 0 + 0)
```

This log line alone does NOT prove the extraction logic is broken—it only shows that `loadedAddresses` was empty in that particular transaction. The actual problem could be:

1. Account key normalization mismatch
2. AMM-owned account exists but is being skipped or misread
3. Pool PDA not present in the tx account set at all
4. Provider payload shape differs from assumption
5. Transaction is not v0, so empty loaded addresses is expected

**Goal:** Add comprehensive instrumentation and hardening so the exact failure mode is visible, then deploy a secondary discovery path (fallback) for resilience.

---

## Assumptions

1. **LoadedAddresses being empty is not inherently broken**
   - Some transactions are not v0 and naturally have empty loaded addresses
   - Some RPC providers may not populate them
   - The pool PDA may simply not be in the transaction account list

2. **Account key shapes vary**
   - Helius and other RPC providers may return `accountKeys` as:
     - Plain strings: `"Address123..."`
     - Objects: `{"pubkey": "...", "signer": false, "writable": true}`
   - Code must normalize before scanning

3. **Data length validation is essential**
   - An AMM-owned account might be a helper PDA, not the pool state
   - Pool state accounts have minimum data lengths (varies by AMM)
   - Validation prevents false positives

4. **Secondary discovery is not fallback—it's strategy diversity**
   - Primary: transaction-based detection (fast, deterministic)
   - Secondary: vault discovery (slower, more reliable for edge cases)
   - Tertiary: external pricing (when discovery fails)

5. **Performance vs observability tradeoff**
   - Debug logging should be optional (behind a flag) if concerned about volume
   - Production should log transaction shape (minimal overhead)
   - Per-account debug logs only when explicitly enabled

---

## Failure Analysis

### Current Behavior

When `detect_pool_from_tx()` processes a transaction:

1. Extract `message.accountKeys` (no normalization)
2. Extract `meta.loadedAddresses.{writable, readonly}` (assumes dict structure)
3. Merge all into `all_accounts` list
4. For each account, fetch `getAccountInfo` and check owner
5. If no AMM-owned account found, log warning and return `None`

### What's Missing

| Aspect | Current | Missing |
|--------|---------|---------|
| Account key normalization | None | Normalize strings + objects |
| Transaction shape visibility | Implicit | Log version, account counts, lookups |
| Per-account scanning | Silent | Log index, owner, data length for each |
| AMM candidate validation | Ownership only | Add data-length validation by AMM type |
| Fallback path | None | Vault discovery path |
| Debug mode | No | Optional verbose logging |

### For the Failing Token

Given:
- 25 total accounts scanned (all from base keys)
- 0 writable loaded addresses
- 0 readonly loaded addresses

Unknowns:
- Was the transaction actually v0 (has `addressTableLookups`)?
- What was `transaction.version`?
- Were any accounts owned by known AMM programs?
- What was the data length of AMM-owned candidates?
- Is the pool PDA completely absent from the tx?

**Solution:** Log all these before and during the scan so the evidence is preserved.

---

## Implementation Plan

### Phase 1: Account Key Normalization

**File:** `src/core/pool_detector.py`

Add helper function to normalize account keys:

```python
def _normalize_account_key(acc):
    """
    Normalize account key from various RPC provider formats.

    Handles:
    - Plain string: "Address123..."
    - Dict with pubkey: {"pubkey": "Address123...", ...}
    - Dict with address: {"address": "Address123...", ...}

    Returns:
        Normalized pubkey string or None
    """
    if isinstance(acc, str):
        return acc
    if isinstance(acc, dict):
        return acc.get("pubkey") or acc.get("address")
    return None
```

Use before merging accounts:

```python
account_keys_raw = message.get("accountKeys", []) or []
account_keys = [_normalize_account_key(a) for a in account_keys_raw]
account_keys = [a for a in account_keys if a]  # Filter nones

writable_accounts_raw = loaded_addresses.get("writable", []) or []
writable_accounts = [_normalize_account_key(a) for a in writable_accounts_raw]
writable_accounts = [a for a in writable_accounts if a]

readonly_accounts_raw = loaded_addresses.get("readonly", []) or []
readonly_accounts = [_normalize_account_key(a) for a in readonly_accounts_raw]
readonly_accounts = [a for a in readonly_accounts if a]
```

---

### Phase 2: Transaction Shape Logging

Log transaction metadata before scanning:

```python
# Extract transaction version and lookup info
tx_version = tx_data.get("transaction", {}).get("version")
has_lookups = bool(message.get("addressTableLookups"))

logger.info(
    f"[POOL_DETECT] tx_version={tx_version} "
    f"base_keys={len(account_keys)} "
    f"writable_loaded={len(writable_accounts)} "
    f"readonly_loaded={len(readonly_accounts)} "
    f"has_addressTableLookups={has_lookups} "
    f"total={len(all_accounts)}"
)
```

This answers: Is the tx actually v0? Are there address table lookups?

---

### Phase 3: Per-Account Debug Logging

Add a `debug` flag to the detector:

```python
def __init__(self, rpc_url: str, debug: bool = False):
    self.rpc_url = rpc_url
    self.rpc_cache = {}
    self.debug = debug
```

For each scanned account, log detailed info:

```python
for i, account_addr in enumerate(all_accounts):
    try:
        account_info = await self._get_account_info_cached(account_addr)

        if not account_info:
            if self.debug:
                logger.debug(f"[POOL_DETECT_DEBUG] idx={i} addr={account_addr[:16]}... result=NOT_FOUND")
            continue

        owner = account_info.get("owner", "???")
        executable = account_info.get("executable", False)
        data_len = account_info.get("data_len", 0) if isinstance(account_info.get("data"), str) else len(account_info.get("data", []))
        amm_match = owner in AMMPrograms.ALL

        if self.debug or amm_match:
            logger.info(
                f"[POOL_DETECT_DEBUG] idx={i} addr={account_addr[:16]}... "
                f"owner={owner[:16] if owner else 'None'}... "
                f"exec={executable} data_len={data_len} amm_match={amm_match}"
            )

        if owner in AMMPrograms.ALL:
            # ... rest of logic
```

---

### Phase 4: AMM Candidate Validation by Data Length

Define minimum and expected data lengths for each AMM:

```python
class AMMDataLengths:
    """Minimum data lengths for pool state accounts."""
    RAYDIUM_AMM_MIN = 296  # Raydium AMM v4 pool state
    ORCA_WHIRLPOOL_MIN = 232  # Orca Whirlpool pool state
    METEORA_MIN = 232  # Meteora DLMM pool state
    PUMPSWAP_MIN = 296  # Uses Raydium layout

    EXPECTED = {
        AMMPrograms.RAYDIUM_AMM: RAYDIUM_AMM_MIN,
        AMMPrograms.PUMPSWAP: PUMPSWAP_MIN,
        AMMPrograms.ORCA_WHIRLPOOL: ORCA_WHIRLPOOL_MIN,
        AMMPrograms.METEORA_DLMM: METEORA_MIN,
    }
```

Validate before returning:

```python
if owner in AMMPrograms.ALL:
    program_name = AMMPrograms.identify_program(owner)
    min_len = AMMDataLengths.EXPECTED.get(owner, 200)

    # Reject if data length is implausibly small
    if data_len < min_len:
        logger.warning(
            f"[POOL_DETECT] AMM-owned account {account_addr[:16]}... "
            f"(owner={program_name}) has invalid data_len={data_len} "
            f"(expected >= {min_len})"
        )
        continue  # Skip this account, keep scanning

    logger.info(
        f"[POOL_DETECT] ✅ Found {program_name} pool at index {i}: "
        f"{account_addr[:16]}... (data_len={data_len})"
    )
    return account_addr
```

---

### Phase 5: Secondary Discovery Path

If transaction scanning finds no pool, run fallback:

```python
async def _discover_pool_via_vaults(
    self,
    token_mint: str
) -> Optional[str]:
    """
    Fallback pool discovery via vault discovery.

    Strategy:
    1. Call getTokenLargestAccounts(mint)
    2. Identify candidate vaults
    3. Resolve vault owner to pool state
    4. Validate pool state data length

    Returns:
        Pool address or None
    """
    try:
        logger.info(f"[POOL_DETECT_FALLBACK] Starting vault-based discovery for {token_mint}")

        # Fetch largest token accounts (likely to be pool vaults)
        import aiohttp
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_mint]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None

                result = await resp.json()
                if "result" not in result or not result["result"]["value"]:
                    return None

                accounts = result["result"]["value"]

        # For each large account, check if it's a pool vault
        for account in accounts[:5]:  # Check top 5 largest
            vault_addr = account["address"]
            vault_info = await self._get_account_info_cached(vault_addr)

            if not vault_info:
                continue

            vault_owner = vault_info.get("owner")
            # Vault owner is typically TokenkegQf8fwkgw1212...
            # This is a token vault, not directly the pool
            # In practice, vaults can be in different positions

            logger.debug(f"[POOL_DETECT_FALLBACK] Checked vault {vault_addr[:16]}... owner={vault_owner}")

        logger.warning(f"[POOL_DETECT_FALLBACK] Failed to resolve pool via vaults for {token_mint}")
        return None

    except Exception as e:
        logger.debug(f"[POOL_DETECT_FALLBACK] Error in vault discovery: {e}")
        return None
```

Integrate into `detect_pool_from_tx()`:

```python
# In detect_pool_from_tx(), after main scan completes with no result:

logger.warning(
    f"[POOL_DETECT] No AMM-owned pool found in transaction "
    f"({len(account_keys)} base + {len(writable_accounts)} writable + "
    f"{len(readonly_accounts)} readonly). Trying fallback discovery..."
)

# Try fallback path
fallback_pool = await self._discover_pool_via_vaults(token_mint)
if fallback_pool:
    logger.info(f"[POOL_DETECT] ✅ Fallback vault discovery succeeded: {fallback_pool[:16]}...")
    return fallback_pool

logger.warning(f"[POOL_DETECT] All pool discovery methods failed for {token_mint}")
return None
```

---

### Phase 6: Update Listener to Enable Debug Mode

In `src/core/pumpfun_curve_listener.py`, create detector with debug flag:

```python
# Check environment for debug mode
import os
debug_mode = os.getenv("POOL_DETECTOR_DEBUG", "false").lower() == "true"

self.pool_detector = PoolDetector(
    rpc_url=self.http_rpc_url,
    debug=debug_mode
)
```

Default is OFF (no extra logging). Enable for investigation:

```bash
POOL_DETECTOR_DEBUG=true python -m src.core.pumpfun_curve_listener
```

---

### Phase 7: Health Endpoint Enhancement

In `src/apis/price_api.py`, add detection stats to health:

```python
@app.route("/api/price/health", methods=["GET"])
def price_health():
    health = {
        "status": "healthy",
        "pool_stats": {
            "ws": {
                "connected": is_ws_connected,
                "subscribed_pools": len(active_pools),
                "multi_pool_enabled": True,
            },
            "detection": {
                "primary_success": get_detector_stats()["primary_successes"],
                "fallback_used": get_detector_stats()["fallback_uses"],
                "total_attempted": get_detector_stats()["total_attempts"],
            }
        }
    }
    return jsonify(health)
```

---

## Logging Strategy

### Production (Default)

Always log:
- Transaction shape summary (version, account counts, lookups)
- When AMM-owned account is found
- When no pool found after primary scan
- Fallback discovery attempt start/end

Example:

```
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 has_addressTableLookups=False total=25
[POOL_DETECT] ✅ Found pumpswap pool at index 3: pAMMBay6oce... (data_len=500)

[POOL_DETECT] tx_version=0 base_keys=4 writable_loaded=12 readonly_loaded=8 has_addressTableLookups=True total=24
[POOL_DETECT] No AMM-owned pool found in transaction (4 base + 12 writable + 8 readonly). Trying fallback discovery...
[POOL_DETECT] ✅ Fallback vault discovery succeeded: pAMMBay6oce...
```

### Debug Mode (POOL_DETECTOR_DEBUG=true)

Additionally log:
- Every account checked with full owner/data info
- Candidate rejection reasons (data length too small)
- Vault discovery path details

Example:

```
[POOL_DETECT_DEBUG] idx=0 addr=11111111111111... owner=11111111111111... exec=False data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=1 addr=TokenkegQf8fwkgw... owner=BPFLoaderUpgradeab... exec=True data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=2 addr=pAMMBay6oceH9fJK... owner=pAMMBay6oceH9fJK... exec=False data_len=500 amm_match=True
[POOL_DETECT_FALLBACK] Starting vault-based discovery for 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[POOL_DETECT_FALLBACK] Checked vault abc... owner=TokenkegQf8fwkgw...
```

---

## Fallback Discovery Strategy

The secondary discovery path targets edge cases where:
- Pool PDA is not in the transaction account list
- External routing or batched operations hide the pool
- Need to reconstruct pool address from vault accounts

### How It Works

1. **Fetch largest token accounts** (`getTokenLargestAccounts`)
   - These are most likely to be pool vaults
   - Raydium/PumpSwap typically have 2+ large vaults per pool

2. **Analyze vault ownership** (optional, future enhancement)
   - Some pools have explicit pool-state references
   - Can inspect token account authority fields

3. **Attempt program-specific resolution**
   - Raydium: Fetch both vaults, query program for matching pool PDA
   - Orca: May have explicit pool state reference in vault metadata

### Current Limitation

This fallback is **sketch** (incomplete). Full implementation would require:
- Raydium program-specific pool discovery
- Cached pool index by vault pair
- More RPC calls (cost consideration)

For now, fallback is a **placeholder** that logs the attempt. Actual implementation depends on RPC costs and pool index availability.

---

## Debug Workflow for Failing Token

### Problem

Token `8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump` has no pool registered.

### Investigation Steps

1. **Enable debug mode:**
   ```bash
   killall -f pumpfun_curve_listener
   POOL_DETECTOR_DEBUG=true PYTHONPATH="." python -m src.core.pumpfun_curve_listener > /tmp/listener_debug.log 2>&1 &
   ```

2. **Wait for the token to be re-detected** (or manually trigger a token launch detection)

3. **Check transaction shape:**
   ```bash
   tail -f /tmp/listener_debug.log | grep "8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump"
   # Look for lines like:
   # [POOL_DETECT] tx_version=... base_keys=... writable_loaded=... readonly_loaded=... has_addressTableLookups=...
   ```

4. **Check per-account scans:**
   ```bash
   tail -f /tmp/listener_debug.log | grep "\[POOL_DETECT_DEBUG\]"
   # Look for the full owner/data breakdown
   ```

5. **Check fallback attempt:**
   ```bash
   tail -f /tmp/listener_debug.log | grep "\[POOL_DETECT_FALLBACK\]"
   # See if vault-based discovery was attempted and what result
   ```

6. **Answer these questions:**
   - **Is tx actually v0?** Check `has_addressTableLookups=True` or `addressTableLookups` in raw tx
   - **Are loaded addresses populated?** Check `writable_loaded > 0` or `readonly_loaded > 0`
   - **Are any accounts AMM-owned?** Check `amm_match=True` in per-account logs
   - **Were candidates rejected?** Check "invalid data_len" warnings
   - **Is pool PDA missing?** If no `amm_match=True` anywhere, pool is not in tx
   - **Did fallback succeed?** Check `[POOL_DETECT_FALLBACK] ... succeeded` vs no result

7. **Inspect raw transaction** (if needed):
   ```bash
   curl -s 'https://mainnet.helius-rpc.com/?api-key=YOUR_KEY' \
     -X POST \
     -H 'Content-Type: application/json' \
     -d '{
       "jsonrpc": "2.0",
       "id": 1,
       "method": "getTransaction",
       "params": ["MIGRATION_SIGNATURE"]
     }' | jq '.result.transaction.message | {version, accountKeys, addressTableLookups}'
   ```

---

## Rollout Plan

### Step 1: Code Changes (Day 1)

1. Add `_normalize_account_key()` helper
2. Add `AMMDataLengths` class with minimums
3. Update `detect_pool_from_tx()` with:
   - Account normalization
   - Transaction shape logging
   - Data length validation
   - Per-account debug logging (optional)
4. Add placeholder `_discover_pool_via_vaults()` fallback
5. Update `__init__` to accept `debug` flag

**Estimated impact:** ~150 lines added/modified
**Backwards compatibility:** 100% (only adds logging + optional fallback)

### Step 2: Listener Integration (Day 1)

Update `pumpfun_curve_listener.py`:
- Read `POOL_DETECTOR_DEBUG` env var
- Pass `debug` flag to PoolDetector

### Step 3: Testing (Day 1-2)

1. **Syntax check:**
   ```bash
   python3 -m py_compile src/core/pool_detector.py
   python3 -m py_compile src/core/pumpfun_curve_listener.py
   ```

2. **Manual test with failing token:**
   ```bash
   POOL_DETECTOR_DEBUG=true python -m src.core.pumpfun_curve_listener
   # Wait for next token launch or manually inject test tx
   ```

3. **Inspect logs for all 7 failure modes**

### Step 4: Deployment (Day 2)

- Merge to main
- Restart listener in production
- Monitor logs for format changes
- Adjust thresholds if needed

### Step 5: Validation (Day 2-3)

- Watch `[POOL_DETECT]` log lines as new tokens launch
- Verify transaction shape appears for each
- Verify AMM candidates are detected or fallback is attempted
- Validate pool registration succeeds with improved detection

---

## Backwards Compatibility

✅ **100% backwards compatible**

- Existing `detect_pool_from_tx()` signature unchanged
- All improvements are additive
- Debug flag defaults to `False`
- No database schema changes
- Fallback path only used if primary fails
- Can rollback in <1 minute by reverting pool_detector.py

---

## Success Criteria

### Phase 1 (Observability)
- [ ] Transaction shape logged for every detection attempt
- [ ] Per-account debug logs available when POOL_DETECTOR_DEBUG=true
- [ ] Log format enables clear diagnosis of 7 failure modes

### Phase 2 (Validation)
- [ ] AMM candidates validated by data length
- [ ] Invalid candidates skipped with warning
- [ ] False positives eliminated

### Phase 3 (Resilience)
- [ ] Fallback discovery path attempts when primary fails
- [ ] Fallback success rate measured and logged
- [ ] Documentation enables debugging of failing tokens

### Phase 4 (Production)
- [ ] Pool detection success rate improves from ~85% to >95%
- [ ] New tokens with failing detection are immediately diagnosable
- [ ] No performance regression from added logging

---

## Related Files

| File | Changes | Lines |
|------|---------|-------|
| `src/core/pool_detector.py` | Main implementation | ~150 |
| `src/core/pumpfun_curve_listener.py` | Debug flag + instantiation | ~5 |
| `src/apis/price_api.py` | Health endpoint stats (optional) | ~10 |
| `POOL_DETECTOR_DEBUG_CHECKLIST.md` | Troubleshooting guide (new) | ~50 |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Logging overhead | Low | Debug flag defaults to OFF |
| Fallback RPC calls | Low | Only attempts when primary fails |
| Data length threshold too strict | Medium | Can be adjusted per token; logged when rejected |
| Fallback path incomplete | Low | Primary path unchanged; fallback is bonus |
| Log volume in production | Low | Transaction shape only, per-account logs need debug flag |

---

## Next Steps

1. **Review this design** — Ensure assumptions and strategy align
2. **Implement Phase 1-5** — Core hardening and logging
3. **Test with failing token** — Verify all 7 failure modes are now visible
4. **Deploy to production** — Monitor and adjust thresholds
5. **Implement Phase 6 (fallback)** — If primary detection still has gaps after tuning

---

## Appendix: Failure Mode Checklist

For any failing pool detection, this design enables answering:

1. ✅ **Is transaction really v0?** → Log `has_addressTableLookups`
2. ✅ **Are loaded addresses populated?** → Log writable/readonly counts
3. ✅ **What accounts are present?** → Per-account debug logs
4. ✅ **Are any AMM-owned?** → Log `amm_match=True` per account
5. ✅ **Do they pass validation?** → Log data length validation
6. ✅ **Is pool PDA absent entirely?** → No `amm_match=True` anywhere
7. ✅ **Does fallback work?** → Log fallback start/result

**Every scenario is now observable and debuggable.**

