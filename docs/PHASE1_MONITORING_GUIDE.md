# Phase 1 Monitoring Guide

## Overview

Phase 1 (Address Cursor-based Incremental Extraction) is now **LIVE**. This guide explains how to monitor its progress over the 1-week validation period.

**Status**: ✅ Deployed March 10, 2026
**Branch**: rpc (commit: ba680df)
**Expected Timeline**: 7-day validation period → full deployment

---

## Quick Start

### Monitor Dashboard

Real-time monitoring dashboard shows cursor coverage and trending:

```bash
# Show dashboard once
python3 phase1_monitoring_dashboard.py --once

# Continuous monitoring (updates every 60 seconds)
python3 phase1_monitoring_dashboard.py --interval 60
```

### Test Phase 1 Behavior

Run a sample extraction with cursor monitoring:

```bash
# Single extraction with cursor tracking
python3 test_phase1_with_env.py

# Two extractions to show incremental behavior
python3 test_incremental_extraction.py
```

---

## What Phase 1 Does

### Before Phase 1
```
Creator A extraction:
  → Helius API: getSignatures(creator_a)
  → Returns: 100 signatures (0-100)
  → Process all signatures

Creator A extraction (next time):
  → Helius API: getSignatures(creator_a)
  → Returns: 100 signatures (SAME 0-100) ← DUPLICATE!
  → Process all signatures again

Result: 2× RPC cost for same data
```

### After Phase 1
```
Creator A extraction:
  → Get cursor for creator_a (none exists)
  → Helius API: getSignatures(creator_a, limit=100)
  → Returns: 100 signatures (0-100)
  → Save cursor: "100th_signature"
  → Process signatures

Creator A extraction (next time):
  → Get cursor for creator_a → "100th_signature"
  → Helius API: getSignatures(creator_a, before="100th_signature")
  → Returns: ~5 NEW signatures (101-105)
  → Save cursor: "105th_signature"
  → Process only new signatures

Result: 95% fewer RPC calls on second+ runs
```

---

## Key Metrics to Monitor

### 1. Cursor Coverage

```bash
sqlite3 flex_complete_database.db "
  SELECT
    COUNT(*) as total_cursors,
    COUNT(CASE WHEN last_signature IS NOT NULL THEN 1 END) as with_signatures,
    COUNT(CASE WHEN last_signature IS NOT NULL THEN 1 END) * 100.0 / COUNT(*) as coverage_percent
  FROM address_scan_state;
"
```

**Expected progression**:
- Hour 1: 0-5% (just started)
- Day 1: 10-20% (first extractions happening)
- Day 3: 30-50% (more creators being scanned)
- Day 7: 60-80% (good coverage)

### 2. RPC Call Trending

If RPC metrics are enabled, check daily call counts:

```bash
sqlite3 flex_complete_database.db "
  SELECT
    DATE(timestamp) as date,
    COUNT(*) as rpc_calls,
    COUNT(DISTINCT source_file) as unique_sources
  FROM rpc_request_log
  WHERE source_file = 'realtime_creator_funding_extractor'
  GROUP BY DATE(timestamp)
  ORDER BY date DESC
  LIMIT 7;
"
```

**Expected trend**:
- Day 0: 1000 calls (baseline, full scans)
- Day 1-2: ~800 calls (some cursors warming up)
- Day 3-4: ~600 calls (more cursors active)
- Day 7: ~400 calls (60% reduction achieved)

### 3. Cursor Activity

Check how many cursors are due for next scan:

```bash
sqlite3 flex_complete_database.db "
  SELECT
    status,
    COUNT(*) as count,
    COUNT(CASE WHEN next_scan_at <= CURRENT_TIMESTAMP THEN 1 END) as due_now
  FROM address_scan_state
  GROUP BY status;
"
```

### 4. Signature Distribution

Check activity-based scheduling (low activity = longer delays):

```bash
sqlite3 flex_complete_database.db "
  SELECT
    CASE
      WHEN next_scan_at > CURRENT_TIMESTAMP THEN 'Active (will scan soon)'
      WHEN next_scan_at <= CURRENT_TIMESTAMP THEN 'Due now'
      ELSE 'Unknown'
    END as status,
    COUNT(*) as count
  FROM address_scan_state
  GROUP BY status;
"
```

---

## Monitoring Checklist

### Daily (First 3 Days)

- [ ] Run dashboard: `python3 phase1_monitoring_dashboard.py --once`
- [ ] Verify CursorManager is initializing without errors
- [ ] Check cursor coverage is increasing
- [ ] Monitor for any extraction errors in logs

### Every 2-3 Days

- [ ] Review cursor coverage percentage (should be rising)
- [ ] Check RPC call trending (should be declining)
- [ ] Verify signature counts match between old and new extractions
- [ ] Look for any unusual patterns in extraction logs

### At Day 7 (End of Validation)

- [ ] Confirm cursor coverage is 60%+
- [ ] Verify RPC calls have dropped by 60%
- [ ] Check that all creators are returning consistent results
- [ ] Document final metrics for Phase 1 completion

---

## Log Messages to Watch For

### Phase 1 Cursor Operations

These messages indicate Phase 1 is working:

```
[REALTIME_FUNDING]    ℹ No cursor found for creator... (first-time scan)
```
→ First extraction for this creator (expected)

```
[REALTIME_FUNDING]    ✅ Loaded cursor: will fetch signatures after ...
```
→ ✅ **GOOD**: Incremental extraction detected (RPC savings!)

```
[REALTIME_FUNDING] ✅ Updated cursor for creator... (fetched 93 txs)
```
→ Cursor saved for next time

### Potential Issues

```
[REALTIME_FUNDING] ⚠ Error loading cursor: ...
```
→ CursorManager had an issue - may fall back to full scan (non-critical)

```
[REALTIME_FUNDING] ⚠ Error updating cursor: ...
```
→ Could not save cursor state (worth investigating, non-critical)

---

## Expected Results

### Week 1: Warm-Up Phase

- **Cursor coverage**: 0% → 60%
- **RPC calls**: ~1000/day (baseline)
- **Cost**: $50/day
- **Status**: Building up cursor history

### Week 2: Steady State

- **Cursor coverage**: 60-80%+
- **RPC calls**: ~400/day (60% reduction!)
- **Cost**: $20/day
- **Status**: Phase 1 validated ✅

### Annual Impact

- **Cost savings**: $30/day × 365 = **$10,950/year**
- **RPC budget**: From $18,250 → $7,300/year
- **Efficiency**: Only fetch what's changed, not everything

---

## Next Steps After Validation

Once Phase 1 is validated (Day 7-10):

1. **Phase 2: RPC Caching** (Weeks 3-4)
   - Additional 35% reduction
   - Cache frequently-accessed signatures
   - Target: $13/day

2. **Phase 3: Due-Time Scheduling** (Weeks 5-6)
   - 40-60% database reduction
   - Smart scheduling based on activity patterns
   - Target: $10/day

3. **Phase 4-7: Full FLEX V2** (Weeks 7-12)
   - Complete architecture optimization
   - Final target: $5/day (~73% total reduction)

---

## Troubleshooting

### Issue: Cursor coverage not increasing

**Check**:
```bash
# Are extractions happening at all?
grep "REALTIME_FUNDING" .logs/app.log | tail -20

# Are creators being processed?
sqlite3 flex_complete_database.db "
  SELECT COUNT(*) FROM creator_funders
  WHERE first_detected_at > datetime('now', '-1 day');
"
```

**Fix**: Verify pumpfun_curve_listener is running and detecting new tokens

### Issue: RPC calls not decreasing

**Check**:
```bash
# Is CursorManager initializing?
grep "CursorManager" .logs/app.log

# Are cursors being loaded?
grep "Loaded cursor" .logs/app.log | wc -l
```

**Fix**: Ensure extraction is loading cursors (check for "ℹ No cursor found" pattern - should be only on first run per creator)

### Issue: Cursor not being saved

**Check**:
```bash
# Are update calls happening?
grep "Updated cursor" .logs/app.log

# Does database have the cursor?
sqlite3 flex_complete_database.db "
  SELECT COUNT(*) FROM address_scan_state
  WHERE last_signature IS NOT NULL;
"
```

**Fix**: Check database permissions and SQLite WAL mode is enabled

---

## Dashboard Interpretation

### Status Legend

🟢 **ACTIVE** - Good cursor coverage (60%+)
- Phase 1 is working well
- Continue monitoring

🟠 **IN PROGRESS** - Medium coverage (25-50%)
- Phase 1 is building up
- Still warming up

🟡 **WARMING UP** - Low coverage (<25%)
- Early stage, expected
- Check back tomorrow

🟡 **WAITING** - No cursors yet
- No extractions have run
- Check if listener is active

---

## Command Reference

```bash
# View dashboard (once)
python3 phase1_monitoring_dashboard.py --once

# Continuous dashboard (every 60 seconds)
python3 phase1_monitoring_dashboard.py --interval 60

# Test Phase 1 with single extraction
python3 test_phase1_with_env.py

# Test incremental behavior (two extractions)
python3 test_incremental_extraction.py

# Check cursor coverage
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM address_scan_state WHERE last_signature IS NOT NULL;"

# View recent cursor updates
sqlite3 flex_complete_database.db \
  "SELECT address, last_scan_at FROM address_scan_state
   WHERE last_scan_at > datetime('now', '-1 hour')
   ORDER BY last_scan_at DESC LIMIT 10;"

# Check RPC calls trending
grep "REALTIME_FUNDING.*RPC CALL" .logs/app.log | tail -50
```

---

## Questions?

See the full implementation guide at:
- [PHASE1_IMPLEMENTATION_GUIDE.md](docs/PHASE1_IMPLEMENTATION_GUIDE.md)
- [PHASE1_DELIVERY_SUMMARY.md](docs/PHASE1_DELIVERY_SUMMARY.md)

For issues with cursor state or database:
- Check address_scan_state table schema
- Verify SQLite WAL mode is active
- Confirm database file is writable

---

**Last Updated**: March 10, 2026
**Status**: ✅ Deployed and Monitoring
