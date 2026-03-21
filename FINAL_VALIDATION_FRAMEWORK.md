# Follow-On Discovery - Final Validation Framework

## Current State: CODE READY, TRIGGER PATH UNKNOWN

### What We've Done
✅ Fixed Bug #4: Search direction (backwards → forwards)  
✅ Fixed Bug #6: RPC budget per-anchor allocation  
✅ Fixed Bug #5: Time-window filtering  
✅ Verified extraction logic works with real blockchain data  
✅ Instrumented 10 diagnostic checkpoints  
✅ Listener running with full logging  

### What We DON'T Know
❌ Is retry loop actually triggering in production?  
❌ Is tx_data reaching _retry_pool_discovery()?  
❌ Are trigger conditions being met ([FOLLOW_ON_CHECK])?  

## The Framework (from FOLLOW_ON_FINAL_VALIDATION.md)

When REAL token arrives, check these checkpoints IN ORDER:

### Critical Checkpoint: [RETRY_START]
```
🔴 [RETRY_START] CRITICAL: Retry loop started for ...
  curve=ABC... 
  creator=XYZ... 
  tx_data=YES
```

**MUST SEE ALL THREE:**
- curve != 'None' → bonding_curve passed ✅
- creator != 'None' → creator passed ✅
- tx_data=YES → migration TX data passed ✅

**If tx_data=NO** → THIS IS THE BUG (tx_data lost somewhere)

### NEW Critical Checkpoint: [TX_DATA_VALIDATION]
```
🔴 [TX_DATA_VALIDATION] has_meta=True has_blockTime=True has_transaction=True has_meta_accounts=True
```

**MUST SEE ALL TRUE:**
- has_meta=True → TX metadata present ✅
- has_blockTime=True → TX blockTime present ✅
- has_transaction=True → TX transaction structure present ✅
- has_meta_accounts=True → metadata accounts present ✅

**CRITICAL:** This catches the subtle bug where:
- tx_data != None (passes surface check)
- BUT tx_data['meta'] is None (fails downstream)
- Follow-on appears not to run, but actually runs on corrupted data
- Returns 0 candidates, looks like extraction failed

**If any False** → `[TX_DATA_INCOMPLETE]` warning logged, follow-on likely to fail silently

### Critical Checkpoint: [FOLLOW_ON_CHECK]
```
[FOLLOW_ON_CHECK] follow_on_max_txs=10 tx_data=True cached_count=0
```

**ALL must be true:**
- follow_on_max_txs > 0 ✅
- tx_data=True ✅
- cached_count=0 ✅

**If any False** → follow-on skipped

### Critical Checkpoint: [FOLLOW_ON_DISCOVERY]
```
[FOLLOW_ON_DISCOVERY] Starting search for ...
```

**Must appear** → conditions passed, follow-on executing

## Diagnosis Matrix

| Log Present? | Conclusion |
|---|---|
| [RETRY_START] with tx_data=YES | ✅ Code path reached |
| [TX_DATA_VALIDATION] all True | ✅ TX data has integrity |
| [TX_DATA_VALIDATION] has False | ⚠️ tx_data incomplete (meta/blockTime missing) |
| [FOLLOW_ON_CHECK] all true | ✅ Trigger conditions met |
| [FOLLOW_ON_DISCOVERY] Starting | ✅ Follow-on executing |
| ✅ Found valid pool | ✅ SUCCESS |
| [RETRY_START] missing | ❌ Retry task not running |
| tx_data=NO in [RETRY_START] | ❌ tx_data lost in propagation |
| [TX_DATA_VALIDATION] missing | ❌ tx_data never checked (won't run if bad) |
| [TX_DATA_INCOMPLETE] warning | ⚠️ tx_data corrupted - follow-on will fail silently |
| [FOLLOW_ON_CHECK] missing | ❌ Retry loop not reaching that point |
| [FOLLOW_ON_DISCOVERY] missing | ❌ Trigger condition failing |

## How to Verify

```bash
# When token arrives, watch real-time:
tail -f listener.log | grep -E "\[RETRY_START\]|\[FOLLOW_ON_CHECK\]|\[FOLLOW_ON_DISCOVERY\]"

# Check for tx_data specifically:
tail -f listener.log | grep "RETRY_START.*tx_data"
```

## Expected Logs for Success

```
🔴 [CREATOR_EXTRACTION] creator=ABC...
🔴 [DISCOVERY_CHECKPOINT] pool_discovery_source='none'
🔴 [DECISION] → WILL SCHEDULE RETRIES
(0.5 second delay)
🔴 [RETRY_START] curve=ABC... creator=XYZ... tx_data=YES
🔴 [DISCOVERY] corr=... tx_source=cached window=ACTIVE
[CACHED_TX_PARSE] cached_candidate_count=0
[FOLLOW_ON_CHECK] follow_on_max_txs=10 tx_data=True cached_count=0
[FOLLOW_ON_DISCOVERY] Starting search for ...
[FOLLOW_ON_DISCOVERY] Scanning anchor=bonding_curve
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] Found candidate ABC... from anchor=bonding_curve at offset=1
[FOLLOW_ON_DISCOVERY] ✅ Found valid pool ABC...
[FOLLOW_ON_SUCCESS] Found pool ABC...
Resolution: 2.3 seconds
```

## Next Token Validation Checklist

When a NEW token migration detected:

- [ ] See [RETRY_START] log?
  - [ ] Yes → continue below
  - [ ] No → retry task not running (asyncio issue)

- [ ] Check tx_data value in [RETRY_START]
  - [ ] YES → continue below
  - [ ] NO → **THIS IS THE BUG** (tx_data lost)

- [ ] See [TX_DATA_VALIDATION] log?
  - [ ] Yes → continue below
  - [ ] No → tx_data validation skipped (something wrong)

- [ ] Check all fields in [TX_DATA_VALIDATION]
  - [ ] has_meta=True ✅
  - [ ] has_blockTime=True ✅
  - [ ] has_transaction=True ✅
  - [ ] has_meta_accounts=True ✅
  - [ ] Any False? → **ROOT CAUSE FOUND** (corrupted tx_data)

- [ ] See [FOLLOW_ON_CHECK] log?
  - [ ] Yes → continue below
  - [ ] No → retry loop didn't reach this point

- [ ] Check all conditions in [FOLLOW_ON_CHECK]
  - [ ] follow_on_max_txs > 0? ✅
  - [ ] tx_data=True? ✅
  - [ ] cached_count=0? ✅
  - [ ] Any False? → follow-on skipped

- [ ] See [FOLLOW_ON_DISCOVERY] Starting?
  - [ ] Yes → continue below
  - [ ] No → trigger condition failed

- [ ] See anchor signatures found?
  - [ ] Yes → continue below
  - [ ] No → RPC issue or anchor addresses wrong

- [ ] See candidates extracted and validated?
  - [ ] Yes → continue below
  - [ ] No → extraction issue (owner mismatch or structure)

- [ ] See ✅ Found valid pool?
  - [ ] Yes → **SUCCESS** 🎉
  - [ ] No → fell back to RPC

## System Status

✅ **Production-Ready**  
- All fixes deployed
- All checkpoints logged
- Listener running

⏳ **Waiting for:**
- Real token migration for validation

🎯 **Next Step:**
- Monitor logs when token arrives
- Use diagnostic matrix above to identify exact issue if follow-on fails
- Fix will be pinpoint-accurate based on which checkpoint is missing
