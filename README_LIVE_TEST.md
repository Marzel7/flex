# Live Validation - What to Look For

**Status:** System deployed and ready  
**Waiting for:** Next SUB_PROV fan-out activity  
**Success indicator:** Two specific log lines appearing in sequence  

---

## The Only Two Logs You Need to See

### Log 1: Creator Match
```
[SWARM] CREATE matched recipient creator=GHJTP8gw... swarm_id=SWARM_... armed_op_id=42
```

### Log 2: DRY_RUN Execution
```
[DRY_RUN] mint=D2WtV5... build=0.5ms sign=2.3ms serialize=0.8ms ws→ready=3.1ms bytes=658
```

**When both appear in sequence = Pipeline validated ✅**

---

## Watch for These Logs First (Pattern Detection)

Before the success logs, you'll see:
```
[SWARM] fan-out pattern confirmed  swarm_id=SWARM_... recipients=95 confidence=0.95
[SWARM] stored 95/95 recipients  swarm_id=SWARM_... armed_op_id=42
```

This happens when SUB_PROV distributes to 95 bot wallets in rapid succession.

---

## Quick Monitor Command

```bash
tail -f logs/supervisor/api.log | grep -E "\[SWARM\]|\[DRY_RUN\]"
```

This will show:
1. Fan-out pattern confirmed
2. Recipients stored (95)
3. CREATE matched recipient ← **Condition 1 & 2**
4. DRY_RUN execution ← **Condition 3**

---

## What Happens Under the Hood

```
Step 1: Pattern Detection
  SUB_PROV → fanout → 95 recipients (189 txs in 30s)
  → [SWARM] fan-out pattern confirmed
  → [SWARM] stored 95/95 recipients

Step 2: Recipient CREATE
  One of 95 wallets creates pump.fun token
  → lookup_swarm_recipient() queries wt_swarm_recipients
  → Finds match in <1ms
  → [SWARM] CREATE matched recipient

Step 3: DRY_RUN Execution  
  DRY_RUN_SIGNING fires automatically
  → Builds real transaction
  → Signs with keypair
  → Serializes to wire format
  → Measures timing at each step
  → [DRY_RUN] sign=2.3ms serialize=0.8ms total=3.1ms
```

---

## Database Verification (Optional)

If you want to manually verify the database:

```bash
# Check recipients were stored
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM wt_swarm_recipients WHERE swarm_id='SWARM_...';"
# Should return: 95

# Check creator was matched
sqlite3 database/flex_complete_database.db \
  "SELECT swarm_id, armed_op_id FROM wt_swarm_recipients \
   WHERE recipient_wallet='GHJTP8gw6HCozR7zGFRoc54n7uuEMS7Y3aM1LrXA8tiR';"
# Should return: SWARM_..., 42

# Check timing was captured
sqlite3 database/flex_complete_database.db \
  "SELECT sign_ms, serialize_ms, total_build_sign_ms FROM wt_detected_creates \
   WHERE sign_ms IS NOT NULL ORDER BY detected_at DESC LIMIT 1;"
# Should return: 2.3, 0.8, 3.1 (approximately)
```

---

## Timeline

| Time | Event | Expected |
|------|-------|----------|
| T+0m | SUB_PROV → fanout | Watch for logs |
| T+1-2m | Pattern confirmed | `[SWARM] stored 95/95` |
| T+5-30m | Recipient creates | Watch for CREATE |
| T+5-30m+1s | **Match** | `[SWARM] CREATE matched` ✅ |
| T+5-30m+3s | **Execute** | `[DRY_RUN] sign=...ms` ✅ |

**Total wait:** 5-30 minutes from pattern to full validation

---

## Expected Values (When You See Log 2)

```
[DRY_RUN] mint=D2WtV5...
          build=0.1-1.0ms        (instruction building)
          sign=2-5ms             (transaction signing)
          serialize=0.1-1.0ms    (wire format encoding)
          ws→ready=2.5-7.0ms     (total end-to-end)
          bytes=600-700          (transaction size)
```

---

## What If You Don't See Log 2?

If Log 1 appears but Log 2 doesn't within 5 seconds:

1. **Check mode:**
   ```bash
   curl -s http://localhost:5002/api/watchtower/interceptor/status | jq .mode
   ```
   Should be: `"PASSIVE"` or `"DRY_RUN_SIGNING"`

2. **Check for errors:**
   ```bash
   tail -50 logs/supervisor/api.log | grep -i "error\|fail"
   ```

3. **Check database state:**
   ```bash
   sqlite3 database/flex_complete_database.db \
     "SELECT COUNT(*) FROM wt_armed_operations WHERE state='ARMED';"
   ```
   Should be > 0

---

## Success Message

When both logs appear:

```
✅ VALIDATION SUCCESSFUL

Recipients inserted:    95 rows ✓
Creator lookup:        <1ms hit ✓
DRY_RUN_SIGNING:       Fired ✓

Timing captured:
  sign:       2.3ms
  serialize:  0.8ms
  total:      3.1ms

Next step: Collect 5-10 more samples for position estimate validation
```

---

## Key Files

- **This file:** `README_LIVE_TEST.md`
- **Validation plan:** `VALIDATION_PLAN.md`
- **Live monitoring:** `SWARM_LIVE_MONITORING.md`
- **Full checklist:** `docs/LIVE_VALIDATION_CHECKLIST.md`

---

## TL;DR

**Watch logs:**
```bash
tail -f logs/supervisor/api.log | grep -E "\[SWARM\]|\[DRY_RUN\]"
```

**Look for:**
```
[SWARM] CREATE matched recipient ...
[DRY_RUN] ... sign=Xms serialize=Yms ...
```

**When you see both:** Pipeline is validated ✅

---

**System Status: READY**

Waiting for next SUB_PROV fan-out activity. Next coordinated token launch from any of the 95 recipients will trigger automatic DRY_RUN_SIGNING with correct timing capture.
