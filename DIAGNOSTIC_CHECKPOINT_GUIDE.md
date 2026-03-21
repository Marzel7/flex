# Diagnostic Checkpoint Guide - Follow-On Discovery Validation

## Status: READY FOR REAL TOKEN TEST

System is fully instrumented with decision point logging. When a REAL token migration arrives, these logs MUST appear in sequence.

## Critical Checkpoints (In Order)

### Checkpoint 1: Creator Extraction
```
🔴 [CREATOR_EXTRACTION] creator=... provenance=...
```
**What:** Token creator extracted from earliest TX  
**Must see:** creator != 'NONE'  
**If missing:** Creator extraction failed → retries won't have context  

### Checkpoint 2: Discovery Source Decision
```
🔴 [DISCOVERY_CHECKPOINT] pool_discovery_source='none' (or other value)
```
**What:** Initial pool discovery result  
**Must see:** pool_discovery_source='none' (NOT a success source like 'tx_parsing')  
**If says:** 'tx_parsing' or 'rpc_discovery' → pool found initially, NO retries needed  

### Checkpoint 3: Retry Decision
```
🔴 [DECISION] pool_discovery_source=none → WILL SCHEDULE RETRIES
```
**What:** Decision to schedule retry loop  
**Must see:** "WILL SCHEDULE RETRIES"  
**If sees:** "NO retries needed" → stop here, pool already found  

### Checkpoint 4: Retry Task Creation
```
🔴 [RETRY_START] CRITICAL: Retry loop started for ...
  sig=... delays=12 curve=... creator=... tx_data=...
```
**What:** Retry loop actually running  
**Must see:** This appears after ~0.5 second delay  
**If missing:** Task not executing (asyncio issue)  

### Checkpoint 5: Retry Attempt
```
🔴 [DISCOVERY] corr=... tx_source=... window=...
```
**What:** Individual retry attempt starting  
**Appears:** Once per retry (multiple times)  

### Checkpoint 6: TX Parsing
```
[CACHED_TX_PARSE] cached_tx_present=yes cached_tx_parsed=... cached_candidate_count=...
```
**What:** Parsing migration TX for candidates  
**Must see:** cached_candidate_count >= 0  

### Checkpoint 7: Follow-On Trigger Check
```
[FOLLOW_ON_CHECK] mint=... follow_on_max_txs=... tx_data=... cached_count=...
```
**What:** Evaluating if follow-on should run  
**Expected values:**
- follow_on_max_txs > 0 ✅
- tx_data = True ✅
- cached_count = 0 ✅

**If any False:** Follow-on won't trigger

### Checkpoint 8: Follow-On Search
```
[FOLLOW_ON_DISCOVERY] Starting search for ... migration_sig=... curve=... creator=... window=30s
```
**What:** Follow-on discovery starting  
**Must see:** Once per retry attempt where trigger conditions met  

### Checkpoint 9: Anchor Scanning
```
[FOLLOW_ON_DISCOVERY] Scanning anchor=bonding_curve
[FOLLOW_ON_DISCOVERY] Scanning anchor=creator  
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] Found 20 signatures for creator
```
**What:** Searching for signatures from anchors  
**Must see:** At least bonding_curve getting signatures  

### Checkpoint 10: SUCCESS or FAILURE
```
[FOLLOW_ON_DISCOVERY] ✅ Found valid pool ...
  OR
[FOLLOW_ON_DISCOVERY] No pool found after scanning ... TXs
```
**What:** Follow-on completion  
**Success:** Pool found, registered, resolution time shown  
**Failure:** Reason logged, falls back to RPC  

## Quick Diagnostic Checklist

When a new token appears, run:

```bash
# Watch for all checkpoints in real-time
tail -f listener.log | grep -E "DISCOVERY_CHECKPOINT|DECISION|FOLLOW_ON_CHECK|FOLLOW_ON_DISCOVERY|Found valid pool"
```

### Expected Log Sequence for Successful Follow-On

```
🔴 [CREATOR_EXTRACTION] creator=ABC... provenance=success
🔴 [DISCOVERY_CHECKPOINT] pool_discovery_source='none'
🔴 [DECISION] pool_discovery_source=none → WILL SCHEDULE RETRIES
(await 0.5 seconds)
🔴 [RETRY_START] CRITICAL: Retry loop started for XYZ...
🔴 [DISCOVERY] corr=... tx_source=... window=ACTIVE
[CACHED_TX_PARSE] cached_tx_present=yes cached_candidate_count=0
[FOLLOW_ON_CHECK] follow_on_max_txs=10 tx_data=True cached_count=0
[FOLLOW_ON_DISCOVERY] Starting search for XYZ...
[FOLLOW_ON_DISCOVERY] Scanning anchor=bonding_curve (ABC...)
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] Found candidate 123ABC... from anchor=bonding_curve at offset=1
[FOLLOW_ON_DISCOVERY] ✅ Found valid pool 123ABC... via anchor=bonding_curve at offset=1
[FOLLOW_ON_SUCCESS] Found pool 123ABC... via anchor=bonding_curve at offset=1
Resolution: 2.3 seconds
```

## What Each Log Absence Means

| Missing Log | Root Cause |
|---|---|
| [CREATOR_EXTRACTION] | Creator not extracted from earliest TX |
| [DECISION] → WILL SCHEDULE | Discovery source != 'none' (pool found initially) |
| [RETRY_START] | Retry task not running (asyncio/scheduling issue) |
| [FOLLOW_ON_CHECK] | Retry loop reached but follow-on conditions false |
| [FOLLOW_ON_DISCOVERY] Starting | Trigger condition failed (tx_data=None or cached_count!=0) |
| ✅ Found valid pool | Extraction returning 0 candidates (owner mismatch or TX structure) |

## System is Ready

✅ All checkpoints instrumented  
✅ Listener running with diagnostics  
✅ Waiting for NEW token migration  

When it arrives, these logs will either:
- Show follow-on working → celebrate ✅
- Identify exactly which checkpoint fails → know what to fix ❌

This is the final validation phase.
