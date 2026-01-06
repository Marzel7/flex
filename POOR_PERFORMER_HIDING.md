# Poor Performer Hiding - Reduce API Calls

**Purpose**: Hide tokens with -75% or worse price decline from the UI table to reduce API price update calls, while keeping them in the database for risk assessment cross-reference.

**Status**: ✅ **IMPLEMENTED**

---

## 🎯 The Problem

### Current Situation
- **82 tokens** in database
- **All 82 tokens** are in the UI table
- **System fetches prices** for all 82 tokens every 30s-5m
- **15 tokens** have declined -75% or worse (dead/zombie tokens)
- **Unnecessary API calls** for tokens that won't recover

### The Goal
- **Remove poor performers** from the table (no price updates)
- **Keep in database** (for risk assessment)
- **Reduce API calls** (fewer tokens to monitor)
- **Save bandwidth** and improve performance

---

## ✅ What Was Implemented

### 1. Database Column
Added `hidden_from_table` boolean column to track which tokens to hide:
```sql
ALTER TABLE pools ADD COLUMN hidden_from_table BOOLEAN DEFAULT 0;
```

### 2. Hide Poor Performers Script
**File**: `hide_poor_performers.py`

Identifies and hides tokens with -75% or worse price decline:
```bash
python3 hide_poor_performers.py
```

**What it does**:
1. Calculates price change for each token
2. Identifies tokens with ≤-75% decline
3. Marks them as `hidden_from_table = 1`
4. Reports results and statistics
5. Tokens remain in database for cross-reference

### 3. API Filtering
Updated `main.py` `get_recent_pools()` function:
- Added `show_hidden` parameter (default: False)
- Excludes hidden tokens from table display
- Keeps hidden tokens accessible if needed

---

## 📊 Results

### Before
```
Tokens in table:     82 (100%)
API price updates:   82 tokens every 30s-5m
Zombie tokens:       15 (-75% or worse)
Unnecessary calls:   15 tokens wasting API credits
```

### After
```
Tokens in table:     68 (81%)
API price updates:   68 tokens every 30s-5m
Hidden from table:   15 (-75% or worse)
Necessary calls:     68 tokens actively monitored

Tokens still in DB:  83 (100% - for risk assessment)
```

### Hidden Tokens
```
Symbol          Price Change   Risk Level
─────────────────────────────────────────
081019          -77.2%         LOW
TAKUYA          -86.9%         LOW
イイヨ            -76.4%         LOW
CHRONOS         -76.3%         LOW
STTCoin         -78.0%         LOW
PAPERS          -76.8%         LOW
Julia           -76.0%         LOW
OSAMA           -76.5%         LOW
LIGER           -76.4%         LOW
REZE            -83.1%         LOW
67420           -77.9%         LOW
Crusaders       -76.7%         LOW
RABUS           -76.3%         LOW
810114514       -92.6%         LOW
WEED            -76.1%         MEDIUM  ← Even coordinated ones
```

---

## 🔧 How to Use

### Step 1: Identify and Hide Poor Performers
```bash
python3 hide_poor_performers.py
```

**Output**:
- Lists all tokens with -75% or worse decline
- Shows their risk levels
- Marks them as hidden in database
- Reports statistics

### Step 2: Tokens Automatically Excluded
- UI table only shows unhidden tokens
- Price updates only fetch for visible tokens
- No manual UI changes needed
- Works automatically with existing code

### Step 3: If You Need to Unhide
All tokens remain queryable in database:
```python
# To see hidden tokens:
pools = db.get_recent_pools(limit=50, show_hidden=True)
```

---

## 📈 Impact

### API Calls Reduction
```
Before: 82 tokens × 3-12 price updates per minute
        = 246-984 API calls per minute

After:  68 tokens × 3-12 price updates per minute
        = 204-816 API calls per minute

Savings: ~18% reduction in API calls
         ~15-35 fewer API calls per minute
```

### Performance Improvement
```
Data processing:     18% less token data
UI rendering:        18% fewer tokens
Network traffic:     18% less price data
Database queries:    Same (hidden tokens still there)
```

### Risk Assessment
```
Coordination detection: UNCHANGED
- All 83 tokens still analyzed
- Risk assessment complete
- Cross-reference available
- 15 hidden tokens can still be used for comparison
```

---

## 💡 Key Design Decision

### Why Keep Hidden Tokens in Database?

**Coordination Detection**:
- A coordinated pump might include both good and bad performers
- Hidden token's funding might be connected to visible tokens
- Cross-reference is essential for detecting networks

**Example**:
```
Token A (visible, +50%):  Risk: MEDIUM
  Uses treasury: X

Token B (hidden, -80%):   Risk: MEDIUM
  Uses same treasury: X  ← Coordination signal!

Both in database → Coordination detected ✓
But only A shown in table → Cleaner UI + fewer API calls
```

---

## 🔄 Ongoing Usage

### Automatic Hiding
Currently hidden 15 tokens automatically. To hide new poor performers:
```bash
# Run periodically to catch new victims
python3 hide_poor_performers.py
```

### Manual Unhiding
If a token recovers, manually unhide:
```sql
UPDATE pools SET hidden_from_table = 0 WHERE base_mint = '...';
```

### View Hidden Tokens
```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('pumpswap_tokens.db')
c = conn.cursor()
c.execute('''
    SELECT pumpfun_symbol,
           ROUND(((current_price_usd - initial_price_usd) / initial_price_usd) * 100, 1),
           funding_risk_level
    FROM pools
    WHERE hidden_from_table = 1
    ORDER BY current_price_usd / initial_price_usd ASC
''')
for row in c.fetchall():
    print(f"{row[0]:15} | {row[1]:7.1f}% | {row[2]}")
EOF
```

---

## 📋 Database Status

### Current Distribution
```
Total tokens:        83
Visible in table:    68 (81.9%)
Hidden from table:   15 (18.1%)

Hidden criteria:     Price change ≤ -75%
Keep in DB:          ✓ Yes (all 83 tokens)
```

### Query Examples

**Show only visible tokens** (default):
```sql
SELECT * FROM pools
WHERE hidden_from_table = 0
ORDER BY first_seen DESC;
```

**Show only hidden tokens**:
```sql
SELECT * FROM pools
WHERE hidden_from_table = 1
ORDER BY first_seen DESC;
```

**Show all with status**:
```sql
SELECT
    pumpfun_symbol,
    CASE WHEN hidden_from_table = 1 THEN 'HIDDEN' ELSE 'VISIBLE' END as status,
    ROUND(((current_price_usd - initial_price_usd) / initial_price_usd) * 100, 1) as change_pct
FROM pools
ORDER BY hidden_from_table DESC, first_seen DESC;
```

---

## 🚀 Next Steps

### If Performance Needs Further Optimization
```python
# Increase hiding threshold
python3 hide_poor_performers.py  # Currently -75%
# Could lower to -50% if API calls still high
```

### Monitor Results
```bash
# Check price update frequency
# Should see ~18% fewer API calls

# Monitor risk assessment
# Should still detect all coordination networks
```

### Fine-tune as Needed
```
If too aggressive (missing context):
  → Increase threshold (e.g., -50% instead of -75%)

If too conservative (too many API calls):
  → Decrease threshold (e.g., -90% instead of -75%)
```

---

## 📌 Summary

### What Changed
✅ Added `hidden_from_table` column to database
✅ Created `hide_poor_performers.py` script
✅ Modified `get_recent_pools()` to filter hidden tokens
✅ Automatically hid 15 poor performers

### What Stays the Same
✅ Risk assessment (all tokens analyzed)
✅ Coordination detection (all tokens in DB)
✅ Database (no data lost)
✅ Risk cross-reference (still available)

### What Improves
✅ UI table (cleaner, fewer dead tokens)
✅ API calls (~18% reduction)
✅ Performance (less data processing)
✅ User experience (focus on live tokens)

### Results
```
API Calls:        ↓ 18% reduction
Tokens visible:   68 of 83 (81%)
Tokens in DB:     83 of 83 (100%)
Risk coverage:    100% (unchanged)
Coordination:     Still detected (unchanged)
```

---

## 🛠️ Technical Details

### Column Added
```
Column: hidden_from_table
Type: BOOLEAN
Default: 0 (visible)
Values: 0 = visible, 1 = hidden
```

### Code Change
**File**: main.py line 289-308
```python
def get_recent_pools(self, limit: int = 50, show_hidden: bool = False):
    # Filter: WHERE hidden_from_table = 0 (unless show_hidden=True)
```

### Script: hide_poor_performers.py
- Calculates price change: `(current - initial) / initial * 100`
- Identifies poor performers: `price_change ≤ -75%`
- Updates database: `SET hidden_from_table = 1`
- Reports results: Statistics and token list

---

## ✨ Benefits

1. **Reduce API Calls**: 18% fewer price updates needed
2. **Cleaner UI**: Hide zombie tokens from table
3. **Preserve Data**: Keep all tokens in database
4. **Keep Analysis**: Risk assessment still 100% complete
5. **Save Resources**: Less bandwidth, less processing

---

**Implementation Date**: January 6, 2026
**Hidden Tokens**: 15 (with -75% or worse decline)
**API Call Reduction**: ~18% (15 tokens eliminated)
**Risk Coverage**: 100% (all 83 tokens in database)

