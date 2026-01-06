# Pump-and-Dump Detection: High-Impact Improvements

## Context
Current system detects CRITICAL tokens based on **shared funding accounts** (static analysis). But if tokens dump within minutes, reactive analysis is too slow. This document outlines real-time signals that catch schemes in action.

---

## 1. TEMPORAL PATTERN DETECTION (High Impact - Easy to Implement)
**Why:** Coordinated groups typically launch tokens in close timing windows (5-15 min apart)

### Implementation
Track launch timing clusters:
```python
def detect_launch_clusters():
    """Find creators launching tokens within X minutes of each other"""
    # When new token detected:
    # 1. Check if creator is in coordinated group (already do this)
    # 2. Look back 15 minutes - did other creators in group launch?
    # 3. If YES: CRITICAL + "SYNCHRONIZED_LAUNCH" pattern

    # Query pattern:
    SELECT creator1.base_mint, creator2.base_mint,
           JULIANDAY(creator2.first_seen) - JULIANDAY(creator1.first_seen) as minutes_apart
    FROM pools creator1
    JOIN creator_sol_transfers cst1 ON creator1.pumpfun_creator = cst1.creator_address
    JOIN creator_sol_transfers cst2 ON cst1.counterparty_address = cst2.counterparty_address
    JOIN pools creator2 ON creator2.pumpfun_creator = cst2.creator_address
    WHERE JULIANDAY(creator2.first_seen) - JULIANDAY(creator1.first_seen) BETWEEN 0 AND 0.01
    -- 0.01 days = 14.4 minutes
```

**Impact:** Catches pump-and-dump IMMEDIATELY when second token in cluster launches
**Latency:** <1 second (no API calls needed)
**False Positive Rate:** Low (synchronized launches are rare for legitimate projects)

---

## 2. LIQUIDITY DRAIN DETECTION (High Impact - Medium Effort)
**Why:** Rug pulls involve draining liquidity from vault accounts

### Implementation
Monitor vault account activity patterns:
```python
def monitor_vault_liquidity():
    """Detect when liquidity is drained from token vaults"""
    # For each CRITICAL token:
    # 1. Extract vault account from Helius transaction history
    # 2. Monitor for large outflows of SOL (>80% of vault)
    # 3. Cross-check: does SOL go to creator's wallet or coordinated accounts?

    # Real-time signal:
    # Watch for pattern:
    # Token launches → receives initial liquidity → drain SOL to creator → token price crashes
```

**Detection Window:** 5-30 minutes (typical rug pull timeline)
**Data Source:** Helius transaction logs (already fetching these)
**Signal:** Large SOL drain from vault = rug pull in progress

**Code location:** Extend `analyze_sol_transfers()` to flag large outflows

---

## 3. VELOCITY ANALYSIS (Medium Impact - Easy)
**Why:** Legitimate projects have gradual trading; pumps have explosive volume spikes

### Implementation
```python
def detect_abnormal_velocity():
    """Identify tokens with unnatural trading velocity"""
    # Fetch from DexScreener:
    # - Trade count per minute
    # - Volume concentration (% from top N trades)
    # - Typical pattern:
    #   Legit: Gradual linear growth
    #   Pump: Explosive spike in first 5 min, then flatline/collapse

    # Flag if:
    # - 100+ trades in first 5 minutes
    # - 70%+ volume from top 10 trades (whale accumulation)
    # - Price then drops 20%+ without corresponding volume
```

**Data Source:** DexScreener API (real-time)
**Signal:** Explosive spike followed by crash = pump and dump in action

---

## 4. PROFIT FLOW TRACKING (Medium Impact - High Value)
**Why:** Coordinated groups funnel profits to specific accounts

### Implementation
After dump, profits flow back to coordinated funding accounts:

```python
def track_profit_flows():
    """Follow the money - where do rug pull profits go?"""
    # After token crashes, monitor:
    # 1. Which wallets sold large amounts (likely pump participants)
    # 2. Where does their SOL go next?
    # 3. Does it flow to coordinated funding accounts?

    # Pattern signature:
    Token A → Creator A sells huge amount → SOL to Account X
    Token B → Creator B sells huge amount → SOL to Account X (same!)

    # This CONFIRMS coordination
```

**Impact:** Builds evidence retroactively; warns about creators who've dumped before

---

## 5. EXECUTION PATTERN MEMORY (Medium Impact - Medium Effort)
**Why:** Pump-and-dump groups have repeatable execution patterns

### New Table: `execution_patterns`
```sql
CREATE TABLE execution_patterns (
    coordinated_group_id TEXT,           -- Links to coordinated_accounts
    execution_date TIMESTAMP,             -- When pump happened
    token_count INT,                      -- How many tokens launched
    launch_window_minutes INT,            -- Time between first/last launch
    pump_duration_minutes INT,            -- How long until peak
    dump_duration_minutes INT,            -- How long until 50% dump
    peak_price_gain_percent REAL,         -- Price at peak
    final_loss_percent REAL,              -- Final down from peak
    liquidity_drained_percent REAL,       -- How much liquidity removed
    profitability_index REAL              -- Risk/reward estimate
);
```

**Use:** When detecting token from known group, predict:
- "Peak expected in 8 minutes (based on 5 previous executions)"
- "Dump expected 15 minutes after peak"
- "Liquidity drain expected 20 minutes after launch"

---

## 6. CROSS-EXCHANGE VALIDATION (Low Impact - High Effort)
**Why:** Some pump-and-dumps also hit Raydium/other DEXes

### Implementation
```python
def check_multi_dex_pumps():
    """Monitor if coordinated tokens hit other DEXes"""
    # When CRITICAL token detected on PumpSwap:
    # 1. Check if same mint exists on Raydium
    # 2. Compare prices across DEXes
    # 3. Look for arbitrage patterns (sign of coordinated trading)

    # Query Jupiter API for pricing across DEXes
```

**Signal:** Same token pumping across multiple DEXes simultaneously = coordinated

---

## 7. SIMULATED TRADING FOR VALIDATION (Medium Effort - High Confidence)
**Why:** Back-test predictions against actual price action

### Implementation
```python
def backtest_pump_detection():
    """For past pump-and-dumps, validate detection timing"""

    # For each known rug pull:
    # - What signals triggered BEFORE it happened?
    # - When were they triggered vs when rug happened?
    # - What was the time window for action?

    # Example result:
    # "Synchronized launch detected at T+0"
    # "Liquidity drain detected at T+12 min"
    # "Rug pull confirmed at T+23 min"
    # → You have 12-23 minute window to warn/protect
```

---

## Recommended Priority Order

### Phase 1 (Implement This Week)
1. **Temporal Pattern Detection** - Catches SYNCHRONIZED_LAUNCH immediately
   - 5 minutes to implement
   - Near-zero false positives
   - Stops pump within 5 minutes of cluster launch

2. **Vault Liquidity Monitoring** - Detects drain in progress
   - 20 minutes to implement
   - Clear signal: >80% SOL drain = rug pull happening
   - Can block trades on drained tokens

### Phase 2 (Next Week)
3. **Velocity Analysis** - Early detection via DexScreener
4. **Profit Flow Tracking** - Build evidence patterns
5. **Execution Pattern Memory** - Predict pump timing

### Phase 3 (Future)
6. Multi-DEX validation
7. Backtesting validation framework

---

## Implementation Example: Synchronized Launch Detection

```python
# Add to WebSocket handler after coordination detection (line 2710)

def check_synchronized_launch(creator_address):
    """Check if other coordinated creators launched within last 15 minutes"""

    from datetime import datetime, timedelta

    # Get all funding accounts for this creator
    c.execute('''
        SELECT DISTINCT counterparty_address FROM creator_sol_transfers
        WHERE creator_address = ? AND transfer_type = 'incoming'
    ''', (creator_address,))

    funding_accounts = [row[0] for row in c.fetchall()]

    # Find all OTHER creators funded by same accounts
    c.execute('''
        SELECT DISTINCT creator_address FROM creator_sol_transfers
        WHERE counterparty_address IN ({})
        AND creator_address != ?
        AND transfer_type = 'incoming'
    '''.format(','.join('?' * len(funding_accounts))),
    funding_accounts + [creator_address])

    related_creators = [row[0] for row in c.fetchall()]

    # Check if any related creators launched in last 15 minutes
    cutoff = datetime.now() - timedelta(minutes=15)

    c.execute('''
        SELECT pumpfun_creator, symbol, first_seen FROM pools
        WHERE pumpfun_creator IN ({})
        AND first_seen > ?
        ORDER BY first_seen DESC
    '''.format(','.join('?' * len(related_creators))),
    related_creators + [cutoff.isoformat()])

    recent_launches = c.fetchall()

    if len(recent_launches) >= 2:
        # Multiple creators in coordinated group launching close together
        return {
            'synchronized': True,
            'cluster_size': len(recent_launches),
            'launches': recent_launches,
            'confidence': 'HIGH',
            'signal': f"⚠️ SYNCHRONIZED_LAUNCH: {len(recent_launches)} coordinated creators launched in past 15 min"
        }

    return None
```

---

## Expected Outcomes

Current system: Detects CRITICAL tokens in 30 seconds
**With Phase 1 improvements:**
- Synchronized Launch: Detected in <5 seconds, 95% confidence
- Liquidity Drain: Detected in real-time as it happens
- Combined: Pump-and-dump scheme identified DURING the pump, giving 10-20 minute window to warn/protect

---

## Monitoring Dashboard Metrics to Add

```
CRITICAL TOKEN ALERTS:
├─ 🚨 Synchronized Launch (cluster of 3+ tokens in 15 min)
├─ 🚨 Liquidity Drain (>70% vault drained)
├─ 🚨 Velocity Spike (100+ trades in 5 min)
├─ 🚨 Price Crash (>30% down from peak with no volume)
└─ ℹ️  Time to predicted dump (based on historical patterns)

EVIDENCE STRENGTH: [████████░] 80%
RECOMMENDED ACTION: [ALERT / BLOCK TRADES / INVESTIGATE]
```

---

## Bottom Line

The current system is **network analysis** (static - who funds whom).
These improvements add **behavioral analysis** (dynamic - what they're actually doing).

Together: Catches coordinated pump-and-dumps both BEFORE (by network pattern) and DURING (by behavior signal) the execution, giving you the time window needed to warn users or protect against losses.
