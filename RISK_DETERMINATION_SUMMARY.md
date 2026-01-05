# Quick Reference: Risk Determination (Level 1 + Level 2)

## The Question You Asked

**"How do we determine risk (LOW, MEDIUM, HIGH, CRITICAL) when a new creator is linked to existing level 1 or level 2?"**

## The Answer

Risk is determined by analyzing **TWO independent levels** of funding connections:

### Level 1: Direct Reuse (Primary Signal)
**"How many OTHER creators does this treasury fund?"**

| Count | Base Score | Risk |
|-------|-----------|------|
| 0 | 10 | 🟢 LOW |
| 1 | 35 | 🟡 MEDIUM |
| 2-4 | 60 | 🟠 HIGH |
| 5+ | 80 | 🔴 CRITICAL |

### Level 2: Funding Chain (Secondary Signal)
**"Who funds THIS treasury?"**

| Factor | Points |
|--------|--------|
| Per funding source | +10 (max 30) |
| Per treasury-treasury connection | +20 |
| Per high-activity account (>10 transfers) | +40 |

Result: 0-100 score

### Combined Score
```
Combined = Base + (Level2 × 0.3)
```

**Why 70% Level 1, 30% Level 2?**
- Level 1 (reuse) is direct proof of coordination
- Level 2 (funding chain) is suggestive but less direct
- Level 2 acts as amplifier, not primary driver

### Final Risk Level for Individual Treasury
```
≥70  → CRITICAL
≥50  → HIGH
≥30  → MEDIUM
<30  → LOW
```

## Overall Creator Risk

Considers **ALL treasuries combined**:

| Condition | Overall Risk | Pattern |
|-----------|-------------|---------|
| Any treasury funds 5+ | **CRITICAL** | HIGHLY_COORDINATED_GROUP |
| 2+ treasuries with HIGH Level 2 | **CRITICAL** | HIGHLY_COORDINATED_GROUP |
| 2+ treasuries reuse + Level 2 | **HIGH** | MULTI_LEVEL_COORDINATED_GROUP |
| 2+ treasuries reuse | **HIGH** | COORDINATED_GROUP |
| 1 treasury reuses + Level 2 | **MEDIUM** | NESTED_COORDINATION |
| 1 treasury reuses | **MEDIUM** | SOME_COORDINATION |
| NO Level 1 but Level 2 connects | **MEDIUM** | HIDDEN_COORDINATION |
| Neither Level 1 nor Level 2 | **LOW** | INDEPENDENT_CREATOR |

## Real Examples

### Example 1: New Creator → Shared Treasury
```
New Creator linked to Treasury X

Level 1 Analysis:
  Treasury X funds 3 other creators
  → reuse_count = 3
  → Base Risk = 60 (HIGH)

Level 2 Analysis:
  No significant Level 2 connections
  → Level 2 Score = 0

Combined: 60 + (0 × 0.3) = 60
→ Individual Risk: HIGH

Creator Overall: MEDIUM (SOME_COORDINATION)
```

### Example 2: New Creator → Clean Treasury with Hub
```
New Creator linked to Treasury Y

Level 1 Analysis:
  Treasury Y funds ONLY this creator
  → reuse_count = 0
  → Base Risk = 10 (LOW)

Level 2 Analysis:
  Treasury Y funded by central hub
  → High transfer count
  → Level 2 Score = 70

Combined: 10 + (70 × 0.3) = 31
→ Individual Risk: MEDIUM

Creator Overall: MEDIUM (HIDDEN_COORDINATION)
```

### Example 3: New Creator → Professional Group
```
New Creator linked to Treasury Z

Level 1 Analysis:
  Treasury Z funds 8 other creators
  → reuse_count = 8
  → Base Risk = 80 (CRITICAL - funds 5+)

Level 2 Analysis:
  Doesn't matter (already ≥70)

Combined: ≥70
→ Individual Risk: CRITICAL

Creator Overall: CRITICAL (HIGHLY_COORDINATED_GROUP)
```

## Key Patterns Explained

### 🔴 CRITICAL - HIGHLY_COORDINATED_GROUP
- Any treasury funds 5+ creators, OR
- Multiple treasuries with strong Level 2 connections
- **Interpretation**: Professional pump group
- **Action**: Avoid immediately

### 🟠 HIGH - COORDINATED_GROUP / MULTI_LEVEL_COORDINATED_GROUP
- 2+ treasuries with reuse, OR
- Both Level 1 and Level 2 showing activity
- **Interpretation**: Planned coordination
- **Action**: High rug probability, avoid

### 🟡 MEDIUM - SOME_COORDINATION / NESTED_COORDINATION / HIDDEN_COORDINATION
- 1 treasury with reuse, OR
- Treasury has Level 2 funding hub, OR
- Mixed signals
- **Interpretation**: Coordination detected at some level
- **Action**: Verify other factors before trading

### 🟢 LOW - INDEPENDENT_CREATOR
- All treasuries dedicated (no reuse)
- No Level 2 connections
- **Interpretation**: Independent operator
- **Action**: Normal monitoring

## Why NEW Pattern: HIDDEN_COORDINATION?

**Old System:**
- Treasury has no reuse → LOW risk
- Missed: Treasury funded by centralized hub

**New System with Level 2:**
- Treasury has no reuse (Level 1 clean) → could be LOW
- BUT treasury funded by central hub (Level 2 connected) → upgrade to MEDIUM
- **Pattern**: HIDDEN_COORDINATION

**Example**:
- Your creator's treasury appears "independent"
- But it's funded by address that also funds 10 other treasuries
- Coordination is hidden one level deep
- New System catches this ✅

## Detection Speed

```
New Token Created
  ↓ (< 1 second)
Listener detects via WebSocket
  ↓ (< 1 second)
Fetch creator's transactions
  ↓ (< 1 second)
Analyze funding sources
  ↓ (< 1 second)
Calculate Level 1 + Level 2 risk
  ↓ (< 1 second)
Display alert if HIGH/CRITICAL
  ↓ (2-3 seconds total)
User sees risk assessment
```

## Implementation Files

- **analyze_creator_wallet.py**: Risk calculation logic
  - `calculate_level2_risk_score()` - Level 2 analysis
  - `analyze_creator_with_funding_reuse()` - Combined analysis

- **RISK_DETERMINATION_GUIDE.md**: Full documentation with formulas

- **test_pumpswap_listener.py**: Display of risk alerts

## Testing

```bash
# Test with a creator
python3 << 'EOF'
from analyze_creator_wallet import analyze_creator_with_funding_reuse
analysis = analyze_creator_with_funding_reuse("creator_address")
print(f"Risk: {analysis['overall_risk']}")
print(f"Pattern: {analysis['coordination_pattern']}")
EOF
```

## Summary

**When new creator is linked to existing funding:**

1. **Query Level 1**: How many other creators does their treasury fund?
   - ✅ If 5+: CRITICAL
   - ✅ If 2-4: HIGH (but per treasury)
   - ⚠️ If 1: MEDIUM
   - 🟢 If 0: Check Level 2

2. **Query Level 2**: Who funds their treasury?
   - ✅ If hub with >10 transfers: Can upgrade risk
   - ✅ If treasury-to-treasury: Strong signal
   - ⚠️ If multiple sources: Aggregate

3. **Combine Scores**: Base + (Level2 × 0.3)
   - This gives individual treasury risk

4. **Aggregate All Treasuries**: Determine overall creator risk
   - Multiple treasuries → Higher overall risk
   - Both levels active → Amplified risk

**Result**: Comprehensive risk assessment catching both obvious AND hidden coordination.
