# Passive Validation Framework
*Tiered observation system for CREATE Interceptor validation*

---

## Problem Statement

WATCHTOWER produces 2–3 launches per day. Waiting for 50 launches to validate the interceptor would take weeks. We need real-world metrics **now**, without deploying capital yet.

**Solution:** Passive observation framework that separates infrastructure latency validation (testable on thousands of pump.fun CREATEs) from strategy validation (observable on WATCH launches as they occur).

---

## Three-Tier Architecture

### Tier 1: Confirmed WATCH Launches
**Purpose:** Validate actual WATCH interception strategy

**Detection:** Launches matched to armed operations with high confidence

**Sample rate:** 2–3 per day  
**Target sample:** 20–40 launches (2–3 weeks observation period)

**Metrics:**
- Actual buyer position (if our tx landed when simulated)
- Rate of top-5, top-10, top-25 entry positions
- Average entry market cap
- Bonding curve state at CREATE

---

### Tier 2: WATCH-like Operators
**Purpose:** Validate interceptor works on operator-driven launches beyond WATCH

**Detection:** Any launch discovered through:
- SUB_PROV relay monitoring (3-hop relay detected)
- Treasury-like outflows
- Signaller correlation

**Sample rate:** 5–15 per day (depends on ecosystem activity)  
**Target sample:** 100+ launches for statistical confidence

**Same metrics as Tier 1**

---

### Tier 3: All pump.fun CREATEs
**Purpose:** Pure infrastructure validation (latency, parsing, slot timing)

**Detection:** Every CREATE detected, regardless of WATCHTOWER signal

**Sample rate:** Thousands per day

**Metrics:**
- WebSocket detect latency (CREATE block → our logsSubscribe notification)
- Tx build time (parse CREATE → construct buy instruction)
- Slot delta (CREATE slot vs theoretical submit slot)
- Percentile latencies (p50, p95, p99)

**NO ATTRIBUTION REQUIRED** — validates infrastructure independent of strategy

---

## Operating Modes

### PASSIVE Mode (Default)
```bash
INTERCEPTOR_MODE=PASSIVE python src/core/main.py
```

**Behavior:**
- Observe all CREATEs
- Build transaction (measure time)
- Calculate buyer position (fetch first 100 buyers, estimate where we'd land)
- Persist metrics
- **Do NOT sign, do NOT submit**

**Output:** `wt_interceptor_validation` table with:
- build_ms (Tier 3 metric)
- estimated_slot_delta (Tier 3 metric)
- actual_position (Tier 1–2 metric)
- est_position_top5/10/25/50 (Tier 1–2 metric)
- watch_confidence (how confident this is a WATCH launch)

**Risk:** None — passive observation only

---

### ARMED Mode
```bash
INTERCEPTOR_MODE=ARMED python src/core/main.py
```

Default mode. Arms on SIGNAL/TREASURY_OUT/relay detection, fires on CREATE. Still no actual sends (see LIVE mode).

---

### LIVE Mode
```bash
INTERCEPTOR_MODE=LIVE INTERCEPTOR_BUY_SOL=5.0 python src/core/main.py
```

Actually signs and sends transactions. Not recommended until Tier 1–2 metrics are solid.

---

## Metrics Collected

### Infrastructure (Tier 3 — All CREATEs)

Per CREATE:
```json
{
  "create_detected_at": timestamp,
  "build_ms": 42.5,
  "submit_ms": 5.0,
  "network_latency_ms": 200.0,
  "estimated_slot_delta": -1,
  "estimated_land_ts": timestamp
}
```

After 7 days (thousands of samples):
```
Build time:        mean=42ms, p50=40ms, p95=55ms, max=120ms
Slot delta:        mean=-0.5, p50=0, p95=1, min=-2, max=+4
Network latency:   assumed constant 200ms (tunable)
Total latency:     ~250ms (build + network)
```

**Decision threshold:** If p95 build time > 100ms, infrastructure needs optimization.

---

### Strategy (Tier 1–2 — WATCH & WATCH-like)

Per launch:
```json
{
  "mint": "...",
  "launch_type": "WATCH",
  "watch_confidence": 0.85,
  "actual_position": 12,
  "est_position_top5": false,
  "est_position_top10": false,
  "est_position_top25": true,
  "est_position_top50": true,
  "first_25_buyers": [
    {"slot": 12345, "ts": 1234567890, "buyer": "...", "amount": 1.5},
    ...
  ]
}
```

After 14 days (20–40 WATCH launches + 100+ WATCH-like):
```
WATCH launches observed:        28
  Top 5 entry rate:            18% (5 launches)
  Top 10 entry rate:           39% (11 launches)
  Top 25 entry rate:           71% (20 launches)
  Average buyer position:      19

WATCH-like launches observed:   142
  Top 5 entry rate:            12%
  Top 10 entry rate:           28%
  Top 25 entry rate:           58%
  Average buyer position:      24
```

---

## Report Generation

### Command
```bash
python -c "from src.core.watchtower.passive_validator import generate_report; \
print(generate_report(days=7, output_path='validation_report.json'))"
```

### Output
```json
{
  "period_days": 7,
  "generated_at": "2026-06-08T12:00:00",

  "infrastructure_metrics": {
    "total_creates_observed": 8432,
    "build_time_ms": {
      "mean": 42.1,
      "p50": 40.2,
      "p95": 54.7,
      "max": 142.3
    },
    "slot_delta": {
      "mean": -0.8,
      "p50": 0,
      "p95": 1,
      "min": -2,
      "max": 4
    }
  },

  "watch_metrics": {
    "launches_observed": 18,
    "avg_position": 18.4,
    "top_5_count": 3,
    "top_10_count": 7,
    "top_25_count": 13,
    "top_5_rate": 16.7,
    "top_10_rate": 38.9,
    "top_25_rate": 72.2
  },

  "sample_records": [...]
}
```

---

## Decision Framework

### After 7 Days (Tier 3 — Infrastructure)

| p95 Build Time | Verdict | Action |
|---|---|---|
| < 60ms | ✅ Excellent | Proceed to 14-day observation |
| 60–100ms | ⚠️ Acceptable | Monitor closely, optimize if possible |
| > 100ms | ❌ Poor | Investigate bottleneck, may need standalone process |

### After 14 Days (Tier 1–2 — Strategy)

| Top-25 Entry Rate | Verdict | Action |
|---|---|---|
| > 60% | ✅ Strong | Pilot live with 0.5 SOL buys |
| 40–60% | ⚠️ Marginal | Further optimize latency or increase sample |
| < 40% | ❌ Weak | Reassess strategy (maybe we're just slow) |

### Final Decision

| All Metrics | Result | Next Step |
|---|---|---|
| Infrastructure p95 < 70ms + Top-25 rate > 60% | 🟢 GO | Launch live trading with capital |
| Infrastructure good but Top-25 < 40% | 🟡 PARTIAL | Either we're hitting the slot limit, or WATCH margins are thinner than expected |
| Infrastructure p95 > 100ms | 🔴 REDESIGN | Move interceptor to standalone process, use WebSocket directly for everything |

---

## Implementation Details

### Database Table: `wt_interceptor_validation`

```sql
CREATE TABLE wt_interceptor_validation (
    mint                TEXT UNIQUE,
    creator             TEXT,
    create_slot         INTEGER,
    create_ts           REAL,
    create_detected_at  REAL,
    build_ms            REAL,
    submit_ms           REAL,
    estimated_slot_delta INTEGER,
    armed_source        TEXT,
    launch_type         TEXT,        -- "WATCH", "watch_like", "general"
    watch_confidence    REAL,
    first_100_buyers    TEXT,        -- JSON array
    actual_position     INTEGER,
    est_position_top5   BOOLEAN,
    est_position_top10  BOOLEAN,
    est_position_top25  BOOLEAN,
    est_position_top50  BOOLEAN,
    mode                TEXT,        -- "PASSIVE", "ARMED", "LIVE"
    created_at          INTEGER
);
```

### Buyer Position Calculation

```
1. Fetch first 100 buyers of token (by slot, ascending)
2. Estimate our submission slot:
   submit_slot = CREATE_slot + (total_latency_ms / 1000) * slot_per_second
3. Count how many actual buyers came before submit_slot
4. That count is our estimated position
5. If position <= 5, flag as top-5, etc.
```

---

## Expected Results After 14 Days

**Realistic scenario:**
```
Infrastructure (p95 build): 50–70ms ✅
Tier 3 sample (all CREATEs): 10,000+
Top-25 entry rate: 55–75%
Average position: 15–25
```

This tells us:
- Infrastructure is solid (400ms slot, 250ms latency = comfortable margin)
- We land in the early buyer positions most of the time
- WATCHTOWER has real edge (top-25 > 50% is strong)
- Ready for live trading with controlled position sizes

---

## Operational Checklist

- [ ] Deploy with `INTERCEPTOR_MODE=PASSIVE`
- [ ] Monitor `wt_interceptor_validation` table for data accumulation
- [ ] Generate report daily for first 7 days (infrastructure tuning window)
- [ ] Generate final report at day 14 (strategy validation)
- [ ] Review metrics against decision framework
- [ ] If all pass: enable `INTERCEPTOR_MODE=LIVE` with small `INTERCEPTOR_BUY_SOL` (0.1–0.5)
- [ ] If any fail: iterate on optimization or pivot to Tier 3 bottleneck fixes

---

## Why This Works

1. **Decoupled validation** — infrastructure metrics don't depend on WATCH, strategy metrics aren't clouded by latency noise
2. **Real-world data** — thousands of CREATEs daily, not simulated
3. **Low risk** — passive observation only, no capital at risk
4. **Fast feedback** — infrastructure answer in 7 days, strategy answer in 14 days
5. **Clear decision gate** — metrics-driven, not gut-feel

---

## Next Phase After Validation

Once metrics pass:
1. **Pilot phase** — enable LIVE with `INTERCEPTOR_BUY_SOL=0.1`, run 3–5 WATCH launches
2. **Real PnL** — measure actual slippage, bonding curve impact, MEV
3. **Scaling** — gradually increase buy amount as confidence grows
4. **Portfolio sizing** — size positions per launch based on observed variance
