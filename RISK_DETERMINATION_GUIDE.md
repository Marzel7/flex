# Risk Determination Logic - Level 1 + Level 2 Analysis

## Overview

Risk assessment now combines TWO independent levels of analysis to detect coordinated pump operations:

- **Level 1**: How many OTHER creators does this treasury/funding account fund?
- **Level 2**: Who funds THIS treasury? (funding chain analysis)

## Risk Calculation Components

### Individual Treasury Risk (Per Funding Account)

Each treasury account gets a **combined risk score** from 0-100:

```
Combined Score = Base Risk + (Level 2 Score × 0.3)
                 ↓           ↓
            Level 1      Level 2
           Reuse Risk    Chain Risk
```

#### Level 1: Reuse Risk (Base Score)

Based on how many OTHER creators this treasury funds:

| Scenario | Base Score | Risk Level | Interpretation |
|----------|-----------|-----------|-----------------|
| Funds 0 other creators | 10 | LOW | ✓ Dedicated - only funds this creator |
| Funds 1 other creator | 35 | MEDIUM | ⚠️ REUSED - shared with 1 other |
| Funds 2-4 other creators | 60 | HIGH | 🚩 SHARED - shared with multiple creators |
| Funds 5+ creators | 80 | CRITICAL | 🚩🚩 SHARED - professional operation |

**How it's calculated:**
```python
# Query: How many OTHER creators does this treasury fund?
other_tokens = [t for t in token_history if t['creator'] != this_creator]
reuse_count = len(other_tokens)

if reuse_count >= 5:
    base_risk = 80  # CRITICAL
elif reuse_count >= 2:
    base_risk = 60  # HIGH
elif reuse_count == 1:
    base_risk = 35  # MEDIUM
else:
    base_risk = 10  # LOW
```

#### Level 2: Funding Chain Risk (Dynamic Score)

Analyzes who FUNDS this treasury. Returns 0-100 score based on:

| Factor | Points | Condition |
|--------|--------|-----------|
| Number of Level 2 sources | +10 each (max 30) | Each account funding this treasury (+10) |
| Treasury-to-treasury connections | +20 each | If Level 2 source is ALSO a treasury |
| Professional operation signals | +40 each | If Level 2 source has >10 transfers |

**Example calculations:**
- Simple case (1 Level 2 source): Level 2 Score = 10 (1 source × 10) = 10/100
- Professional operation (1 source with 12 transfers): Level 2 Score = 10 + 40 = 50/100
- Multi-layer (2 sources, both treasuries): Level 2 Score = 20 + 40 + 80 = 140 → capped at 100

#### Combined Risk Score

```
Combined = Base Risk + (Level 2 Score × 0.3)

Examples:
1. Level 1 dedicated + No Level 2:
   Combined = 10 + (0 × 0.3) = 10 → LOW

2. Level 1 high reuse + Moderate Level 2:
   Combined = 60 + (40 × 0.3) = 60 + 12 = 72 → CRITICAL

3. Level 1 low but strong Level 2:
   Combined = 10 + (50 × 0.3) = 10 + 15 = 25 → LOW
```

#### Risk Level Recalculation

After combining, individual treasury risk is recalculated:

```
if combined_score >= 70:
    risk_level = 'CRITICAL'

elif combined_score >= 50:
    risk_level = 'HIGH'

elif combined_score >= 30:
    risk_level = 'MEDIUM'

else:
    risk_level = 'LOW'
```

### Overall Creator Risk (Final Risk Assessment)

The creator's overall risk is determined by analyzing ALL their funding sources:

#### Overall Risk Determination

| Condition | Overall Risk | Pattern |
|-----------|-------------|---------|
| ANY treasury funds 5+ creators | CRITICAL | HIGHLY_COORDINATED_GROUP |
| 2+ treasuries have HIGH Level 2 | CRITICAL | HIGHLY_COORDINATED_GROUP |
| 2+ treasuries reuse AND Level 2 connections | HIGH | MULTI_LEVEL_COORDINATED_GROUP |
| 2+ treasuries reuse (no Level 2) | HIGH | COORDINATED_GROUP |
| 1 treasury reuses AND has Level 2 | MEDIUM | NESTED_COORDINATION |
| 1 treasury reuses (no Level 2) | MEDIUM | SOME_COORDINATION |
| NO reuse but Level 2 shows connections | MEDIUM | HIDDEN_COORDINATION |
| NO risk at either level | LOW | INDEPENDENT_CREATOR |

## When New Creator Linked to Existing Treasury

### Scenario 1: Linked to Treasury with Level 1 Reuse

**Setup:** New creator funded by Treasury X that already funds 3 other creators

```
Analysis:
  Level 1: Treasury X funds 3 other creators
    → reuse_count = 3
    → base_risk = 60 (HIGH)

  Level 2: Treasury X funded by 2 accounts
    → level2_risk_score = 20

  Combined = 60 + (20 × 0.3) = 66
    → Individual Risk = HIGH

Creator Overall Risk:
  1 treasury with reuse
    → overall_risk = MEDIUM
    → pattern = SOME_COORDINATION

✅ Decision: NEW CREATOR = MEDIUM RISK
   Reason: Linked to shared treasury
```

### Scenario 2: Linked to "Clean" Treasury with Level 2 Connections

**Setup:** New creator funded by Treasury Y (dedicates to this creator), but Treasury Y funded by central hub

```
Analysis:
  Level 1: Treasury Y funds only this creator
    → reuse_count = 0
    → base_risk = 10 (LOW)

  Level 2: Treasury Y funded by central hub account
    → Central hub: 25+ transfers, is_treasury = true
    → level2_risk_score = 10 (1 source) + 20 (is treasury) + 40 (high transfers) = 70

  Combined = 10 + (70 × 0.3) = 10 + 21 = 31
    → Individual Risk = MEDIUM

Creator Overall Risk:
  NO Level 1 reuse
  BUT 1 treasury with Level 2 connections
    → overall_risk = MEDIUM
    → pattern = HIDDEN_COORDINATION

✅ Decision: NEW CREATOR = MEDIUM RISK
   Reason: Coordination hidden at funding chain level
```

### Scenario 3: Linked to Critical Treasury

**Setup:** New creator funded by Treasury Z that funds 8 other creators

```
Analysis:
  Level 1: Treasury Z funds 8 other creators
    → reuse_count = 8
    → base_risk = 80 (CRITICAL - funds 5+)

  Level 2: Doesn't matter (already critical)
    → level2_risk_score doesn't push beyond 70

  Combined >= 70
    → Individual Risk = CRITICAL

Creator Overall Risk:
  ANY treasury with reuse >= 5
    → overall_risk = CRITICAL
    → pattern = HIGHLY_COORDINATED_GROUP

✅ Decision: NEW CREATOR = CRITICAL RISK
   Reason: Direct connection to professional pump group
```

## Risk Categories

### 🟢 LOW RISK
- All funding accounts dedicated (no reuse)
- No Level 2 connections
- **Action**: Normal monitoring

### 🟡 MEDIUM RISK
- 1 treasury reuses OR treasury has Level 2 connections
- Mixed signals
- **Action**: Verify other factors before trading

### 🟠 HIGH RISK
- 2+ treasuries reuse with other creators
- Multiple funding sources shared
- **Action**: Flag as suspicious, high rug probability

### 🔴 CRITICAL RISK
- Any treasury funds 5+ creators
- Multiple treasuries with strong Level 2 connections
- **Action**: Immediate alert, professional pump group

## Key Insights

1. **Level 1 Dominates**: Reuse (70% weight) vs Funding Chain (30% weight)
   - Reuse is direct proof of coordination
   - Funding chains are suggestive

2. **Level 2 Can Override**: Even Level 1 clean can become MEDIUM risk
   - Pattern: HIDDEN_COORDINATION
   - Example: Treasury funded by central hub

3. **Multi-Level Amplifies Risk**: Both Level 1 AND Level 2 signals = HIGH/CRITICAL
   - Pattern: MULTI_LEVEL_COORDINATED_GROUP
   - Indicates professional operation

4. **Professional Signals**:
   - High transfer count (>10) at Level 2 = +40 points
   - Treasury-to-treasury connection = +20 points each
   - Suggests organized, sophisticated operation

## Summary

**New Creator Risk = Function of (Level 1 Reuse, Level 2 Funding Chain)**

- **Linked to dedicated treasury** → LOW
- **Linked to shared treasury** → MEDIUM/HIGH
- **Linked to treasury with Level 2 hub** → MEDIUM (HIDDEN_COORDINATION)
- **Linked to critical treasury** (5+ reuse) → CRITICAL
- **Linked to multi-level network** → CRITICAL

The system identifies coordination at TWO levels, catching even sophisticated operations where reuse is hidden at the funding chain level.
