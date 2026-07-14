# Swarm Live Monitoring - Quick Reference

**Three Things to Verify:**
1. Recipients inserted correctly
2. Creator lookup hits
3. DRY_RUN_SIGNING fires

**Success Condition:**
```
[SWARM] CREATE matched recipient
[DRY_RUN] sign=...ms serialize=...ms total=...ms
```

---

## During Live Testing

### Terminal 1: Watch Logs
```bash
tail -f logs/supervisor/api.log | grep -E "\[SWARM\]|\[DRY_RUN\]"
```

Expected sequence:
```
[SWARM] fan-out pattern confirmed swarm_id=SWARM_... recipients=95 confidence=0.95
[SWARM] stored 95/95 recipients swarm_id=SWARM_... armed_op_id=42
[SWARM] CREATE matched recipient creator=GHJTP8gw... swarm_id=SWARM_... armed_op_id=42
[DRY_RUN] mint=D2WtV5... build=0.5ms sign=2.3ms serialize=0.8ms ws→ready=3.1ms bytes=658
```

### Terminal 2: Quick Database Checks
```bash
#!/bin/bash
# Refresh every 5 seconds
while true; do
  echo "=== Swarms in DB ==="
  sqlite3 database/flex_complete_database.db \
    "SELECT swarm_id, COUNT(*) FROM wt_swarm_recipients GROUP BY swarm_id;"
  
  echo ""
  echo "=== Latest Armed Op ==="
  sqlite3 database/flex_complete_database.db \
    "SELECT id, state FROM wt_armed_operations WHERE state='ARMED' ORDER BY created_at DESC LIMIT 1;"
  
  echo ""
  sleep 5
done
```

### Terminal 3: Monitor DRY_RUN Data
```bash
#!/bin/bash
# After DRY_RUN logs appear
while true; do
  echo "=== Recent DRY_RUN Captures ==="
  sqlite3 database/flex_complete_database.db \
    "SELECT mint, sign_ms, serialize_ms, total_build_sign_ms FROM wt_detected_creates WHERE sign_ms IS NOT NULL ORDER BY detected_at DESC LIMIT 5;"
  
  echo ""
  sleep 5
done
```

---

## Checklist

### ✅ Condition 1: Recipients Inserted
```bash
# Should see log
grep "[SWARM] stored 95/95" logs/supervisor/api.log

# Should see 95 rows
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM wt_swarm_recipients WHERE swarm_id='SWARM_...';"
```

**Success:** 95 rows + log message

### ✅ Condition 2: Creator Lookup Hit
```bash
# Should see log
grep "[SWARM] CREATE matched recipient" logs/supervisor/api.log

# Creator should be in database
sqlite3 database/flex_complete_database.db \
  "SELECT * FROM wt_swarm_recipients WHERE recipient_wallet='GHJTP8gw6HCozR7zGF...';"
```

**Success:** Log + recipient found in DB

### ✅ Condition 3: DRY_RUN Fires
```bash
# Should see log with timing
grep "\[DRY_RUN\].*sign=" logs/supervisor/api.log

# Data should be in database
sqlite3 database/flex_complete_database.db \
  "SELECT sign_ms, serialize_ms, total_build_sign_ms FROM wt_detected_creates WHERE sign_ms IS NOT NULL LIMIT 1;"
```

**Success:** Log + timing data in DB

---

## Key Metrics to Record

When validation succeeds, capture:

```
[SWARM] fan-out pattern confirmed
  swarm_id: ________________
  recipients: ___
  confidence: ____
  total_sol: ____
  window_s: ___

[SWARM] stored recipients
  armed_op_id: __

[SWARM] CREATE matched recipient
  creator: ________________________
  mint: ________________________

[DRY_RUN] timing
  build: ____ms
  sign: ____ms
  serialize: ____ms
  total: ____ms
  bytes: ___
```

---

## Expected Values

| Metric | Expected | Unit |
|--------|----------|------|
| Recipients | 95+ | count |
| Confidence | 0.85-0.99 | score |
| Total SOL | 500-1000+ | SOL |
| Window | 20-60 | seconds |
| Build time | 0.1-1.0 | ms |
| Sign time | 2-5 | ms |
| Serialize time | 0.1-1.0 | ms |
| Total latency | 2.5-7.0 | ms |
| Bytes | 600-700 | count |

---

## If Something's Wrong

### No pattern detected?
```bash
# Check transfers are happening
grep "SUB_PROV\|recipient_" logs/supervisor/api.log | tail -20
```

### Pattern detected but no storage?
```bash
# Check for errors
grep "[SWARM].*error\|[SWARM].*fail" logs/supervisor/api.log
```

### Creator not matching?
```bash
# Verify creator is in DB
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM wt_swarm_recipients WHERE recipient_wallet LIKE 'GHJTP8%';"
```

### DRY_RUN not firing?
```bash
# Check mode
curl -s http://localhost:5002/api/watchtower/interceptor/status | jq .interceptor_mode

# Check for build errors
grep "buy_built_at\|build.*error" logs/supervisor/api.log | tail -5
```

---

## When Success Condition Met

```
✅ VALIDATION COMPLETE

Log Line 1:
[SWARM] CREATE matched recipient creator=... swarm_id=... armed_op_id=...

Log Line 2:
[DRY_RUN] mint=... build=...ms sign=...ms serialize=...ms total=...ms bytes=...

→ Pipeline is end-to-end validated
→ Ready for next phase: Collect 5-10 samples
→ Then: Enable live execution
```

---

## Key Files to Know

| Purpose | Path |
|---------|------|
| Main logs | `logs/supervisor/api.log` |
| Database | `database/flex_complete_database.db` |
| Code | `src/core/watchtower/create_interceptor.py` |
| Config | `config/supervisor/supervisord.conf` |

---

## Quick SQL Commands

```bash
# Count swarms detected
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(DISTINCT swarm_id) FROM wt_swarm_recipients;"

# Total recipients stored
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM wt_swarm_recipients;"

# Average confidence
sqlite3 database/flex_complete_database.db \
  "SELECT AVG(confidence), MIN(confidence), MAX(confidence) FROM wt_swarm_recipients;"

# Latest timing data
sqlite3 database/flex_complete_database.db \
  "SELECT mint, sign_ms, serialize_ms, total_build_sign_ms FROM wt_detected_creates WHERE sign_ms IS NOT NULL ORDER BY detected_at DESC LIMIT 3;"

# Check armed ops state
sqlite3 database/flex_complete_database.db \
  "SELECT id, state, wallet, created_at FROM wt_armed_operations ORDER BY created_at DESC LIMIT 1;"
```

---

## Timeline During Live Test

```
T+0m:  SUB_PROV → fanout transfer
T+1-2m: [SWARM] pattern confirmed log
T+1-2m: [SWARM] stored recipients log
T+5-30m: Recipient creates pump.fun token
T+5-30m+1s: [SWARM] CREATE matched log
T+5-30m+3s: [DRY_RUN] execution log

✅ VALIDATION COMPLETE
```

---

## Stay Focused On

1️⃣ **[SWARM] CREATE matched recipient** — This log = creator lookup worked  
2️⃣ **[DRY_RUN] sign=...ms serialize=...ms** — This log = DRY_RUN_SIGNING fired  

When both appear: **Pipeline validated** ✅

---

**Ready. Waiting for next SUB_PROV fan-out activity.**
