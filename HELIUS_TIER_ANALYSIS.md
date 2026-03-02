# Helius Tier Analysis & Credit Tracking

**Date**: 2026-03-02
**Status**: Verified - You're on a tier WITHOUT usage API access

---

## What We Found

### ✅ What Works
- RPC endpoint: `https://mainnet.helius-rpc.com/?api-key={KEY}` ✅
- All RPC methods available
- Enhanced API endpoints available
- Rate limiting enforced (no headers, but 429s on limit)

### ❌ What Doesn't Work
- Usage API: `/v0/projects/{ID}/usage` - **404 Not Found**
- Helius CLI with your keypair: **No projects found**
- Programmatic credit tracking: **Not available**

### Your Tier Characteristics
```
Plan: Developer or Free (CANNOT access usage metrics API)
RPC Requests: 50/sec (apparent limit based on rate limiting)
Enhanced APIs: 10/sec (apparent limit based on rate limiting)
Usage API: ❌ NOT AVAILABLE
CLI Support: ❌ NOT AVAILABLE
```

---

## The Real Problem

**You cannot programmatically access your Helius usage data.**

This explains the 33,530 call discrepancy:
- We can **record every RPC call** your code makes (instrumentation)
- We **cannot verify** what Helius actually charged
- We have **no way to get real credit data** except manually from dashboard

---

## The Solution: Manual + Instrumented Tracking

### Method 1: Dashboard Snapshots (Manual)
**Frequency**: Daily or weekly
**Process**:
1. Visit: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
2. Note: Credits Remaining, Credits Used
3. Record: `python helius_usage_cli.py update X Y Z`

**Accuracy**: High (direct from Helius)
**Effort**: 2 minutes per check

### Method 2: RPC Instrumentation (Automated)
**Frequency**: Real-time as calls happen
**Process**:
1. Every RPC call recorded with `record_request()`
2. Credits calculated based on CREDIT_SCHEDULE
3. Access via: `python analyze_rpc_accuracy.py`

**Accuracy**: Depends on CREDIT_SCHEDULE accuracy
**Effort**: Zero (automatic)

### Method 3: Reconciliation
**Frequency**: Weekly
**Process**:
1. Take dashboard snapshot (Method 1)
2. Check instrumented total (Method 2)
3. Calculate discrepancy
4. Investigate root cause

---

## What This Means for the 33,530 Discrepancy

### We Can NOW Measure It Manually

**Hypothesis Testing**:

1. **Collect baseline** (today):
   - Dashboard: Credits remaining = X
   - Stop all Flex processes
   - Note: Credits remaining stays at X

2. **Run creator_outgoing_extractor** for 1 hour:
   - Dashboard: Credits remaining = X - Y
   - RPC instrumentation: recorded Z calls × 10 = Z × 10 credits
   - Compare: Y (actual cost) vs Z × 10 (expected cost)

3. **Find the ratio**:
   - If Y = Z × 10 → Cost schedule is correct ✅
   - If Y = Z × 0.1 → Calls cost 10x less than expected ❌
   - If Y = Z × 1 → Calls cost 1/10th of expected ❌
   - If Y ≈ 0 → Calls aren't executing (test mode) ❌

---

## Setup for Manual Testing

### 1. Clean Baseline
```bash
# Stop all Flex processes
pkill -f "pumpfun_curve_listener\|creator_outgoing\|funder_incoming"

# Wait 5 minutes
sleep 300

# Check dashboard - note the "Credits Remaining" number
python helius_usage_cli.py dashboard

# Save it
python helius_usage_cli.py update [REMAINING] [USED] [MONTH]
```

### 2. Run One Component in Isolation
```bash
# Run only creator_outgoing_extractor for exactly 1 hour
timeout 3600 python creator_outgoing_extractor.py > /tmp/creator_out.log 2>&1

# Check logs for statistics
tail -100 /tmp/creator_out.log
```

### 3. Measure the Cost
```bash
# Check dashboard again - note new "Credits Remaining"
python helius_usage_cli.py dashboard

# Update snapshot
python helius_usage_cli.py update [NEW_REMAINING] [NEW_USED] [NEW_MONTH]

# Calculate actual cost
COST = OLD_REMAINING - NEW_REMAINING

# Check instrumented calls
grep "creator_outgoing_scan" /tmp/creator_out.log | wc -l
# Or check logs for "record_request" calls

# Compare
echo "Actual cost: $COST credits"
echo "Instrumented calls: X × 10 credits"
echo "Ratio: $COST / (X × 10)"
```

### 4. Analyze the Ratio
```
If ratio ≈ 1.0 → Cost schedule is CORRECT
If ratio ≈ 10   → Calls cost 10x LESS than expected (schedule too high)
If ratio ≈ 0.1  → Calls cost 10x MORE than expected (schedule too low)
If ratio ≈ 0    → Calls aren't executing (test/mock mode)
```

---

## Recommended Approach: Hybrid Model

### Daily Operations
1. **Instrumentation** (automatic):
   - Every RPC call logged
   - Credits calculated from CREDIT_SCHEDULE
   - View with: `python analyze_rpc_accuracy.py`

2. **Weekly Dashboard Check** (manual, 2 minutes):
   - Visit dashboard on Monday morning
   - Compare with instrumented total
   - Update snapshot: `python helius_usage_cli.py update X Y Z`

3. **Monthly Reconciliation**:
   - Review weekly discrepancies
   - If consistent, adjust CREDIT_SCHEDULE
   - If erratic, investigate root cause

### Cost Formula

```
Daily Cost = Sum of all RPC calls × credits per call

Actual Cost = Dashboard (Credits Used)
Estimated Cost = Instrumentation (Credits Calculated)
Discrepancy = Actual - Estimated

If Discrepancy > 20%:
  - Re-run isolation test for 1 hour
  - Calculate actual cost per call
  - Update CREDIT_SCHEDULE
```

---

## Tools for Manual Tracking

### View History
```bash
python helius_usage_cli.py history --limit 30
```

Output:
```
{
  "timestamp": "2026-03-02T12:00:00",
  "creditsRemaining": 975318,
  "creditsUsed": 24682,
  "creditsUsedMonth": 24682
}
```

### Track Changes
```bash
# Snapshot 1: Monday morning
python helius_usage_cli.py update 975318 24682 24682

# Snapshot 2: Friday evening
python helius_usage_cli.py update 950000 50000 50000

# Weekly cost
WEEKLY_COST = 950000 - 975318 = 25318 credits
```

---

## Files & Tools

| Tool | Purpose | Command |
|------|---------|---------|
| helius_usage_cli.py | Dashboard snapshots | `update X Y Z` or `history` |
| analyze_rpc_accuracy.py | Compare instrumented vs actual | `python analyze_rpc_accuracy.py` |
| rpc_metrics_recorder.py | Credit calculation | CREDIT_SCHEDULE (updated with official rates) |
| creator_outgoing_extractor.py | Measure cost of 1 component | `timeout 3600 python ...` |

---

## Next Steps

1. **Establish baseline**:
   ```bash
   python helius_usage_cli.py update [CURRENT_REMAINING] [CURRENT_USED] [CURRENT_MONTH]
   ```

2. **Run isolation test** (1 hour creator_outgoing_extractor):
   - Measure actual cost
   - Compare with (call count × 10)
   - Calculate ratio

3. **Update CREDIT_SCHEDULE** if needed:
   - Edit `rpc_metrics_recorder.py`
   - Change `"getSignaturesForAddress": 10` to actual cost
   - Verify other methods similarly

4. **Set up weekly cadence**:
   - Every Monday: `python helius_usage_cli.py dashboard`
   - Record snapshot
   - Calculate weekly cost
   - Compare with instrumentation

---

## Summary

✅ **We know your tier doesn't have usage API**
✅ **We can measure via manual dashboard checks**
✅ **We can track via RPC instrumentation**
✅ **We can isolate components for accuracy testing**
❌ **We cannot programmatically access usage (API limitation)**

**Path forward**: Hybrid approach - instrumentation + weekly manual checks

**To fix 33,530 discrepancy**: Run 1-hour isolation test, measure actual cost per call, update CREDIT_SCHEDULE

---

**Generated**: 2026-03-02
**Status**: Ready for manual testing phase
