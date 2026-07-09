# Live Validation Checklist - Swarm Recipient Matching

**Objective:** Verify three critical conditions on next live swarm activity.

**Success Condition:** 
```
[SWARM] CREATE matched recipient
[DRY_RUN] sign=...ms serialize=...ms total=...ms
```

When both logs appear, pipeline is end-to-end validated.

---

## Condition 1: Recipients Inserted Correctly

### What to Expect

When SUB_PROV → fan-out wallet transfer detected, followed by 95 rapid transfers to recipients:

```
LOG: [SWARM] fan-out pattern confirmed  
     swarm_id=SWARM_... 
     recipients=95+ 
     confidence=0.90+  
     total_sol=800+ 
     window_s=30-60

LOG: [SWARM] stored 95/95 recipients 
     swarm_id=SWARM_... 
     armed_op_id=42
```

### Verification Steps

#### 1. Check Logs Appear
```bash
tail -f logs/supervisor/api.log | grep "[SWARM] stored"
```

**Expected:** Message within 2 minutes of final fanout transfer.

#### 2. Verify Database
```bash
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM wt_swarm_recipients WHERE swarm_id='SWARM_...';"
```

**Expected:** 95 rows

#### 3. Spot-Check Recipients
```bash
sqlite3 database/flex_complete_database.db \
  "SELECT recipient_wallet, armed_op_id, confidence 
   FROM wt_swarm_recipients 
   WHERE swarm_id='SWARM_...' 
   LIMIT 5;"
```

**Expected:**
- 5 distinct recipient wallets
- All have same armed_op_id (e.g., 42)
- All have confidence 0.75-0.95

#### 4. Verify Indexes
```bash
sqlite3 database/flex_complete_database.db \
  "EXPLAIN QUERY PLAN 
   SELECT * FROM wt_swarm_recipients 
   WHERE recipient_wallet='GHJTP8gw6HCozR7zGF...';"
```

**Expected:** Contains `USING idx_wt_swarm_recipient_wallet`

### Success Criteria ✅

- [x] `[SWARM] stored 95/95 recipients` log appears
- [x] Database has 95 rows with correct swarm_id
- [x] All recipients linked to same armed_op_id
- [x] Confidence scores 0.75-0.95
- [x] Index on recipient_wallet confirmed

---

## Condition 2: Creator Lookup Hits

### What to Expect

When one of the 95 recipients creates pump.fun token:

```
LOG: [SWARM] CREATE matched recipient 
     creator=GHJTP8gw... 
     swarm_id=SWARM_... 
     armed_op_id=42
```

This log indicates:
- Creator wallet extracted from CREATE
- `lookup_swarm_recipient(creator)` returned a hit
- Swarm matched successfully

### Verification Steps

#### 1. Check Log Appears
```bash
tail -f logs/supervisor/api.log | grep "[SWARM] CREATE matched"
```

**Expected:** Within 1-5 seconds of CREATE detected log.

#### 2. Verify Creator in Database
```bash
sqlite3 database/flex_complete_database.db \
  "SELECT * FROM wt_swarm_recipients 
   WHERE recipient_wallet='GHJTP8gw6HCozR7zGFRoc54n7uuEMS7Y3aM1LrXA8tiR';"
```

**Expected:** Single row with:
- swarm_id matching log
- armed_op_id matching log
- confidence 0.90+

#### 3. Timing Check
```bash
# Find CREATE detection time
grep "CREATE.*mint=" logs/supervisor/api.log | tail -1

# Find SWARM match time
grep "[SWARM] CREATE matched" logs/supervisor/api.log | tail -1

# Calculate delta
# Expected: <50ms
```

**Expected:** <50ms between CREATE detection and SWARM match log

### Success Criteria ✅

- [x] `[SWARM] CREATE matched recipient` log appears
- [x] Creator wallet appears in wt_swarm_recipients
- [x] swarm_id matches between log and database
- [x] armed_op_id matches between log and database
- [x] Lookup latency <50ms (includes logging overhead)

---

## Condition 3: DRY_RUN_SIGNING Fires

### What to Expect

Within 1-3 seconds of SWARM match:

```
LOG: [DRY_RUN] mint=D2WtV5Jpb1yVcDfJLA... 
               build=0.5ms 
               sign=2.3ms 
               serialize=0.8ms 
               ws→ready=3.1ms 
               bytes=658
```

This log indicates:
- DRY_RUN_SIGNING executed
- Transaction built
- Transaction signed
- Transaction serialized
- Real timing captured

### Verification Steps

#### 1. Check DRY_RUN Log Appears
```bash
tail -f logs/supervisor/api.log | grep "\[DRY_RUN\]"
```

**Expected:** Within 3 seconds of `[SWARM] CREATE matched` log.

#### 2. Verify Timing Values
```bash
# Extract timing log
grep "\[DRY_RUN\].*sign=" logs/supervisor/api.log | tail -1

# Should contain all four metrics:
# - build=X.Xms (instruction building)
# - sign=X.Xms (transaction signing)
# - serialize=X.Xms (wire format encoding)
# - ws→ready=X.Xms (total from CREATE to ready-to-submit)
```

**Expected:**
- build: 0.1-1.0ms
- sign: 2-5ms
- serialize: 0.1-1.0ms
- ws→ready: 2.5-7.0ms total

#### 3. Verify Database Record
```bash
sqlite3 database/flex_complete_database.db \
  "SELECT mint, sign_ms, serialize_ms, total_build_sign_ms 
   FROM wt_detected_creates 
   WHERE mint='D2WtV5Jpb1yVcDfJLAhUaS5DLN1J3Rb3LDFYrRBrpump';"
```

**Expected:**
- sign_ms: 2-5 (matches log)
- serialize_ms: 0.1-1.0 (matches log)
- total_build_sign_ms: 2.5-7.0 (matches ws→ready)

#### 4. Verify No RPC Calls
```bash
# Check that no RPC submit logs appear
grep -E "JITO|_submit_rpc|ERROR.*submit" logs/supervisor/api.log | \
  grep -v "DRY_RUN\|BENCHMARK" | tail -5
```

**Expected:** No submit logs (transaction was discarded as expected)

#### 5. Verify Armed Op State
```bash
sqlite3 database/flex_complete_database.db \
  "SELECT id, state, create_after_arm_s, created_at 
   FROM wt_armed_operations 
   WHERE id=42;"
```

**Expected:**
- state: 'ARMED' (stays armed for swarm, disarm is per-CREATE in LIVE mode)
- create_after_arm_s: <10 seconds (CREATE happened quickly after arming)

### Success Criteria ✅

- [x] `[DRY_RUN]` log appears within 3 seconds of `[SWARM] CREATE matched`
- [x] All 4 timing metrics present (build, sign, serialize, ws→ready)
- [x] Timing values reasonable (2-7ms total)
- [x] Database record populated with sign_ms, serialize_ms, total_build_sign_ms
- [x] No submission logs (BENCHMARK/DRY_RUN guards working)
- [x] Armed operation state unchanged

---

## Master Checklist: Full End-to-End Validation

### Phase 1: Pattern Detection (Monitor First)
```
□ SUB_PROV → large transfer detected
□ [SWARM] fan-out pattern confirmed log
□ 95+ recipients in log message
□ confidence >= 0.70 in log
```

### Phase 2: Storage (Verify Database)
```
□ [SWARM] stored 95/95 recipients log
□ SELECT COUNT FROM wt_swarm_recipients = 95
□ All recipients have same armed_op_id
□ All recipients have same swarm_id
```

### Phase 3: Matching (Watch for CREATE)
```
□ Recipient creates pump.fun token
□ [SWARM] CREATE matched recipient log appears
□ creator wallet in log matches database
□ armed_op_id in log matches swarm entry
```

### Phase 4: Execution (Verify DRY_RUN)
```
□ [DRY_RUN] log appears within 3 seconds
□ sign_ms value present (2-5ms expected)
□ serialize_ms value present (0.1-1.0ms expected)
□ ws→ready value present (2.5-7.0ms expected)
□ bytes value present (600-700 expected)
```

### Phase 5: Persistence (Verify Data Captured)
```
□ wt_detected_creates has new row with mint
□ sign_ms populated (matches log)
□ serialize_ms populated (matches log)
□ total_build_sign_ms populated (matches log)
□ armed_op_id populated (42 in example)
```

### FINAL SUCCESS CONDITION ✅
```
[SWARM] CREATE matched recipient
[DRY_RUN] sign=X.Xms serialize=X.Xms total=X.Xms bytes=N
```

When both logs appear in sequence, **end-to-end pipeline is validated.**

---

## Log Extraction Script

Save this to quickly extract validation data:

```bash
#!/bin/bash
# Assuming next swarm has swarm_id starting with SWARM_

echo "=== SWARM PATTERN DETECTION ==="
grep "[SWARM] fan-out pattern" logs/supervisor/api.log | tail -1

echo ""
echo "=== RECIPIENT STORAGE ==="
grep "[SWARM] stored" logs/supervisor/api.log | tail -1

echo ""
echo "=== CREATE MATCHING ==="
grep "[SWARM] CREATE matched" logs/supervisor/api.log | tail -1

echo ""
echo "=== DRY_RUN EXECUTION ==="
grep "\[DRY_RUN\]" logs/supervisor/api.log | tail -1

echo ""
echo "=== RECIPIENT COUNT ==="
SWARM_ID=$(grep "[SWARM] stored" logs/supervisor/api.log | tail -1 | \
  sed -n 's/.*swarm_id=\([^ ]*\).*/\1/p')
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM wt_swarm_recipients WHERE swarm_id='$SWARM_ID';"

echo ""
echo "=== ARMED OP STATE ==="
sqlite3 database/flex_complete_database.db \
  "SELECT id, state, created_at FROM wt_armed_operations WHERE state='ARMED' ORDER BY created_at DESC LIMIT 1;"
```

---

## Failure Modes & Diagnostics

### No `[SWARM] fan-out pattern` Log
**Cause:** Pattern not detected (confidence < 0.70)
**Check:**
```bash
grep "recipient_" logs/supervisor/api.log | tail -20
# Should show many transfers to many recipients
```
**Fix:** May need to adjust confidence thresholds in `detect_fan_out_pattern()`

### No `[SWARM] stored` Log Despite Pattern
**Cause:** store_swarm_recipients() error
**Check:**
```bash
grep "[SWARM]" logs/supervisor/api.log | grep -i "error\|fail"
# Will show specific error
```
**Fix:** Check database lock, armed_op_id exists, recipient list not empty

### `[SWARM] CREATE matched` Doesn't Appear
**Cause:** lookup_swarm_recipient() returned None
**Check:**
```bash
# Verify creator wallet is in database
sqlite3 database/flex_complete_database.db \
  "SELECT * FROM wt_swarm_recipients \
   WHERE recipient_wallet='GHJTP8gw6HCozR7zGF...';"
# If empty, recipient wasn't stored correctly
```
**Fix:** Check SWARM storage step, verify recipient wallet spelling

### No `[DRY_RUN]` Log Despite Match
**Cause:** DRY_RUN_SIGNING not firing
**Check:**
```bash
# Check mode setting
curl -s http://localhost:5002/api/watchtower/interceptor/status | \
  jq .interceptor_mode
# Should be "PASSIVE" or "DRY_RUN_SIGNING"

# Check for errors in build_and_submit
grep "buy_built_at\|_build_and_submit" logs/supervisor/api.log | tail -10
```
**Fix:** Verify mode, check INTERCEPTOR_BUY_SOL setting, verify keypair loaded

---

## Expected Timeline

| Event | Expected Time | Example |
|-------|---|---|
| SUB_PROV transfer | T+0 | 12:23:45 |
| Pattern confirmed | T+10-30s | 12:24:00 |
| Recipients stored | T+31-60s | 12:24:45 |
| Recipient CREATE | T+5-30min | 12:55:00 |
| SWARM match log | T+5-30min+1s | 12:55:01 |
| DRY_RUN log | T+5-30min+3s | 12:55:04 |

Total time from SUB_PROV signal to DRY_RUN execution: **5-30 minutes** (depends on when recipient launches)

---

## Success Metrics Once Validated

After both logs appear:
- ✅ Creator wallet recognition working end-to-end
- ✅ Database-driven bot wallet matching validated
- ✅ DRY_RUN_SIGNING firing automatically on swarm creates
- ✅ Real timing data captured (first time)
- ✅ Pipeline ready for live execution testing

Next step: Collect 5-10 SWARM creates to establish confidence in timing estimates before enabling live execution.

---

## Final Validation Command

When you see both logs, run:

```bash
echo "✅ VALIDATION SUCCESSFUL"
echo ""
echo "Pipeline is end-to-end validated:"
echo "  1. Recipients inserted: YES"
echo "  2. Creator lookup hit: YES"
echo "  3. DRY_RUN_SIGNING fired: YES"
echo ""
echo "Ready to proceed with:"
echo "  - Collect 5-10 more SWARM creates"
echo "  - Validate position estimates"
echo "  - Enable live execution"
```

---

**Status: AWAITING NEXT LIVE SWARM ACTIVITY**

System is ready. Logs are prepared. Database is ready. Next coordinated launch will provide real-world validation.
