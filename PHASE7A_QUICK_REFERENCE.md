# Phase 7A Quick Reference

## What Was Implemented

Two new features added to network_scores table:
1. **smoothed_score** - Exponential smoothing with alpha=0.3
2. **stability_coeff** - Volatility-based stability metric (0-1 scale)

Both computed at build-time in Phase I (after alerts, before cleanup).

---

## Schema Addition

```sql
ALTER TABLE network_scores ADD COLUMN smoothed_score INTEGER;
ALTER TABLE network_scores ADD COLUMN stability_coeff REAL;
ALTER TABLE network_scores ADD COLUMN smoothing_alpha REAL DEFAULT 0.3;
ALTER TABLE network_scores ADD COLUMN smoothing_version INTEGER DEFAULT 1;
ALTER TABLE network_scores ADD COLUMN smoothed_updated_at TIMESTAMP;
```

---

## Formulas

### Exponential Smoothing
```
smooth_t = alpha * raw_t + (1 - alpha) * smooth_{t-1}
alpha = 0.3 (default)
if no previous smooth: smooth = raw
result: rounded to integer
```

### Stability Coefficient
```
vol5 = average(|score_delta|) over last 5 transitions
stability = 1 / (1 + (vol5 / 10))
clamped to [0.1, 1.0]
if <2 history: stability = 1.0
```

---

## Interpreting Values

### Smoothed Score
- Same 0-100 scale as raw score
- Reduces noise from volatile networks
- Lags raw score by ~1-2 builds (alpha=0.3)
- Useful for: trend analysis, risk rankings

### Stability Coefficient
| Value | Interpretation | Example |
|-------|---|---|
| 1.0 | Very stable | Constant scores (vol5=0) |
| 0.8 | Stable | Small fluctuations (vol5=2) |
| 0.5 | Moderate volatility | vol5=10 |
| 0.33 | Volatile | vol5=20 |
| 0.1 | Extremely volatile | vol5>100 (clamped) |

---

## Testing

Run all tests:
```bash
pytest tests/test_phase7a_smoothing.py -v                                    # 13 Phase 7A tests
pytest tests/test_scoring_v2.py tests/test_alerts_phases.py tests/test_idempotency.py tests/test_build_integration.py tests/test_phase7a_smoothing.py -v  # All 65 tests
```

Expected: **65 PASS** in ~0.6 seconds

---

## Build Output Example

```
🔄 Phase I: Apply score smoothing & stability coefficient...
   ✅ Smoothing applied: 52 networks
      Stability coefficient: avg=0.75 (min=0.1, max=1.0)
```

---

## Idempotency

Run build twice with same data → same results:
- smoothed_score identical
- stability_coeff identical
- Database state unchanged

This is guaranteed by deterministic formulas + no random state.

---

## UI Display (Phase 7B)

Currently: build-time computation only, values stored but not displayed

Future: monitoring dashboard can show:
```
Network: TestNet
├─ Raw Score: 45
├─ Smoothed Score: 46         ← New (Phase 7B)
├─ Stability: 0.80 (Stable)   ← New (Phase 7B)
└─ Network Type: cex_connected
```

---

## Backward Compatibility

✅ New columns added with ALTER TABLE (safe)
✅ Existing networks get smoothed values on next build
✅ First build: smoothed = raw (no prior data)
✅ Monitoring queries unchanged
✅ No UI code changes required

---

## Files Modified

- `build_networks_release.py` - Added Phase I
- `PHASE7A_SCHEMA_MIGRATION.sql` - Migration script
- `tests/test_phase7a_smoothing.py` - 13 new tests

---

## Next Steps

Phase 7B (UI Integration):
- Display smoothed_score in monitoring dashboard
- Display stability_coeff with status indicator
- Use stability for network rankings or alerts

---

**Status**: ✅ Complete, Ready for Phase 7B
