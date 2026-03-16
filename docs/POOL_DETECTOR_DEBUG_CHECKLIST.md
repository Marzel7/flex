# Pool Detector Debug Checklist

**Purpose:** Quickly diagnose why a token's pool is not being detected/registered.

**For token:** `8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump` (or any failing mint)

---

## Quick Start

### 1. Enable Debug Mode

```bash
# Stop current listener
pkill -f pumpfun_curve_listener

# Start with debug logging enabled
POOL_DETECTOR_DEBUG=true PYTHONPATH="." python -m src.core.pumpfun_curve_listener > /tmp/listener_debug.log 2>&1 &

# Verify it started
sleep 2
ps aux | grep pumpfun_curve_listener | grep -v grep
```

### 2. Trigger Token Launch Detection

Either:
- **Wait for a real token to launch** on PumpSwap
- **Manually inject a test token** if you have a recent migration signature

### 3. Tail Logs for Your Token

```bash
# Watch logs as they appear
tail -f /tmp/listener_debug.log | grep -E "(MIGRATION|8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump|POOL_DETECT)"

# Or search after the fact
grep "8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump" /tmp/listener_debug.log
```

---

## Log Lines to Look For

### Expected Detection Start

```
[EVENT] 🚀 MIGRATION DETECTED: 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[EVENT] Migration signature: 5uHXbZu...
```

### Transaction Shape (Required)

Look for this line—it tells you if tx is v0:

```
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 has_addressTableLookups=False total=25
```

**Interpretation:**
- `tx_version=None` → Not a v0 transaction (normal for most PumpSwap)
- `base_keys=25` → 25 accounts in regular accountKeys (good)
- `writable_loaded=0, readonly_loaded=0` → No loaded addresses (expected for non-v0)
- `has_addressTableLookups=False` → Confirms not v0

### Per-Account Scans (Debug Mode Only)

```
[POOL_DETECT_DEBUG] idx=0 addr=11111111111111... owner=11111111111111... exec=False data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=1 addr=TokenkegQf8fwkgw... owner=BPFLoaderUpgradeab... exec=True data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=2 addr=pAMMBay6oceH9fJK... owner=pAMMBay6oceH9fJK... exec=False data_len=500 amm_match=True
```

**Look for:**
- Lines with `amm_match=True` → Found a potential pool
- `data_len=500` or similar reasonable value → Pool state account
- `data_len=0` or very small → Helper PDA or token account

### Successful Detection

```
[POOL_DETECT] ✅ Found pumpswap pool at index 2: pAMMBay6oce... (data_len=500)
```

### Fallback Detection (If Primary Failed)

```
[POOL_DETECT] No AMM-owned pool found in transaction (25 base + 0 writable + 0 readonly). Trying fallback discovery...
[POOL_DETECT_FALLBACK] Starting vault-based discovery for 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[POOL_DETECT_FALLBACK] Checked vault abc123... owner=TokenkegQf8fwkgw...
[POOL_DETECT] ✅ Fallback vault discovery succeeded: pAMMBay6oce...
```

---

## Failure Mode Diagnosis

Use these logs to answer the 7 key questions:

### Q1: Is the transaction actually v0?

**Look at:** `[POOL_DETECT] tx_version=... has_addressTableLookups=...`

- If `tx_version=0` and `has_addressTableLookups=True` → YES, it's v0
- If `tx_version=None` and `has_addressTableLookups=False` → NO, it's regular tx
- Expected: Most PumpSwap mints are regular (non-v0), so empty loaded addresses is normal

### Q2: Are loaded addresses populated?

**Look at:** `[POOL_DETECT] ... writable_loaded=X readonly_loaded=Y ...`

- If both are 0 → No loaded addresses in this tx
- If either > 0 → Loaded addresses were present
- Expected: Usually 0 for non-v0 txs, but for v0 txs should be > 0

### Q3: What accounts are present?

**Look at:** `[POOL_DETECT] ... total=25` and `[POOL_DETECT_DEBUG] idx=0...idx=24...`

- Should see 25 debug lines for a 25-account transaction
- Each line shows: index, address, owner, data length
- If you don't see ~25 lines, account list was truncated (bug)

### Q4: Are any accounts AMM-owned?

**Look for:** `[POOL_DETECT_DEBUG] ... amm_match=True`

- If you see at least one `amm_match=True` → YES, pool is in the tx
- If no lines with `amm_match=True` → Pool is NOT in the transaction at all
- Expected: At least one (usually the pool PDA)

### Q5: Do AMM candidates pass validation?

**Look for:** `[POOL_DETECT] AMM-owned account ... has invalid data_len=X (expected >= Y)`

- If you see rejection warnings → Candidate existed but was too small
- If no rejection warnings → All AMM-owned candidates were valid

**Important:** Even if pool is present, invalid data length could cause rejection.

### Q6: Is the pool PDA completely absent?

**If:** No `amm_match=True` anywhere AND no fallback succeeded

Then: Pool account is not in the transaction at all

Possible causes:
- Pool hadn't been created yet when tx was sent
- Pool is in a different transaction (external swap contract)
- Non-standard pool structure (unlikely)

### Q7: Does the fallback vault-resolution path succeed?

**Look for:** `[POOL_DETECT_FALLBACK] ... succeeded` or `failed`

- If fallback succeeded → Secondary path found the pool
- If fallback attempted but failed → Need vault analysis
- If fallback not attempted → Primary path found pool (good)

---

## Common Scenarios

### Scenario A: Pool Found in Transaction

```
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 ...
[POOL_DETECT] ✅ Found pumpswap pool at index 3: pAMMBay... (data_len=500)
[POOL] 🚀 Auto-registered pool for WebSocket pricing
```

**Status:** ✅ Success
**Action:** None, pool is being registered

---

### Scenario B: Pool in Loaded Addresses (V0 Transaction)

```
[POOL_DETECT] tx_version=0 base_keys=4 writable_loaded=12 readonly_loaded=8 has_addressTableLookups=True total=24
[POOL_DETECT_DEBUG] idx=18 addr=pAMMBay6oceH9fJK... owner=pAMMBay6oceH9fJK... exec=False data_len=500 amm_match=True
[POOL_DETECT] ✅ Found pumpswap pool at index 18: pAMMBay... (data_len=500)
```

**Status:** ✅ Success (with loaded addresses)
**Action:** None, pool found in writable/readonly loaded addresses

---

### Scenario C: No Pool in Transaction, Fallback Succeeds

```
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 ...
[POOL_DETECT] No AMM-owned pool found in transaction (25 base + 0 writable + 0 readonly). Trying fallback...
[POOL_DETECT_FALLBACK] Starting vault-based discovery for TOKEN
[POOL_DETECT_FALLBACK] Checked vault abc... owner=TokenkegQf...
[POOL_DETECT] ✅ Fallback vault discovery succeeded: pAMMBay...
```

**Status:** ⚠️ Success but via fallback (slower)
**Action:** Investigate why pool wasn't in primary tx. May be normal for certain pool types.

---

### Scenario D: AMM-Owned Account Rejected (Data Length)

```
[POOL_DETECT] tx_version=None base_keys=25 ...
[POOL_DETECT_DEBUG] idx=5 addr=pAMMBay6oceH9fJK... owner=pAMMBay6oceH9fJK... exec=False data_len=100 amm_match=True
[POOL_DETECT] AMM-owned account pAMMBay6oce... (owner=pumpswap) has invalid data_len=100 (expected >= 296)
[POOL_DETECT] No AMM-owned pool found in transaction...
[POOL_DETECT_FALLBACK] Starting vault-based discovery...
```

**Status:** ❌ Failure (but diagnosable)
**Action:** Investigate account. 100 bytes is too small for Raydium pool state. Likely:
- Account is a helper PDA, not pool state
- Data length threshold needs adjustment (less likely)
- Account data is malformed

**To investigate:**
```bash
# Fetch the actual account
curl -s 'https://mainnet.helius-rpc.com/?api-key=YOUR_KEY' \
  -X POST \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getAccountInfo",
    "params": ["pAMMBay6oceH9fJK...", {"encoding": "jsonParsed"}]
  }' | jq '.result.value | {owner, executable, data}'
```

---

### Scenario E: Pool Not in Transaction at All

```
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 ...
[POOL_DETECT_DEBUG] idx=0 addr=11111111111111... owner=11111111111111... exec=False data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=1 addr=TokenkegQf8fwkgw... owner=BPFLoaderUpgradeab... exec=True data_len=0 amm_match=False
... (no amm_match=True lines) ...
[POOL_DETECT] No AMM-owned pool found in transaction (25 base + 0 writable + 0 readonly). Trying fallback...
[POOL_DETECT_FALLBACK] Starting vault-based discovery...
[POOL_DETECT_FALLBACK] Checked vault abc... owner=TokenkegQf...
[POOL_DETECT] ⚠️ Fallback vault discovery failed for TOKEN
```

**Status:** ❌ Failure
**Action:** This is the worst case. Pool PDA is not in the transaction AND not discoverable from vaults.

**Possible causes:**
- Pool was created in a separate transaction (token launched, pool created later)
- Pool on a different AMM than expected
- Pool creation failed silently
- Token is a wrapped/derivative, not direct pool

**To investigate:**
1. Check if pool exists at all:
   ```bash
   sqlite3 database/flex_complete_database.db \
     "SELECT COUNT(*) FROM token_pool_accounts WHERE mint='TOKEN';"
   ```

2. Manually search for pools:
   ```bash
   # Use Raydium pool API or similar
   curl 'https://api.raydium.io/v2/pools' | jq '.[] | select(.baseMint=="TOKEN")'
   ```

3. Check if pool is on a different AMM (Orca, Meteora, etc)

---

## Quick Command Reference

### Start Listener with Debug

```bash
pkill -f pumpfun_curve_listener
POOL_DETECTOR_DEBUG=true PYTHONPATH="." python -m src.core.pumpfun_curve_listener > /tmp/listener_debug.log 2>&1 &
```

### Watch for Failures

```bash
tail -f /tmp/listener_debug.log | grep "No AMM-owned pool found"
```

### Search for Specific Token

```bash
grep "YOUR_MINT" /tmp/listener_debug.log | head -20
```

### Extract Full Detection Report

```bash
MINT="YOUR_MINT"
echo "=== Transaction Shape ==="
grep "\[POOL_DETECT\] tx_version" /tmp/listener_debug.log | grep "$MINT"
echo "=== Account Scans ==="
grep "\[POOL_DETECT_DEBUG\]" /tmp/listener_debug.log | grep -A 30 "$MINT" | head -30
echo "=== Fallback Attempts ==="
grep "\[POOL_DETECT_FALLBACK\]" /tmp/listener_debug.log | grep "$MINT"
echo "=== Final Result ==="
grep -E "(Found.*pool|No AMM-owned pool found)" /tmp/listener_debug.log | grep "$MINT"
```

### Check Pool in Database

```bash
sqlite3 database/flex_complete_database.db \
  "SELECT mint, base_account, quote_account FROM token_pool_accounts WHERE mint='YOUR_MINT';"
```

### Verify WebSocket Pricing Activated

```bash
curl -s http://localhost:5002/api/price/YOUR_MINT | jq '.price_usd, .source'
```

---

## Rollback Debug Mode

```bash
# Stop listener
pkill -f pumpfun_curve_listener

# Start without debug (normal mode)
PYTHONPATH="." python -m src.core.pumpfun_curve_listener > /tmp/listener.log 2>&1 &
```

---

## Next Steps After Diagnosis

Once you've answered the 7 questions, you'll know:

1. **If primary tx path works** → Pool found in transaction (expected path)
2. **If primary tx path fails but fallback works** → Edge case, investigate why pool isn't in tx
3. **If both fail** → Pool discovery impossible via current methods
   - Check if pool exists at all
   - Check if pool is on different AMM
   - Manual registration may be needed

Document your findings and we can iterate on:
- Data length thresholds
- Fallback strategy improvements
- Pool detection for specific AMM types

