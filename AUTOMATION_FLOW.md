# Coordination Detection Automation Flow

**Status**: ✅ **FULLY AUTOMATED**

---

## 🎯 The Complete Automation Process

When a new token is detected on PumpSwap, the entire coordination detection flow runs automatically:

```
NEW TOKEN DETECTED (WebSocket)
         ↓
    STEP 1: Extract metadata
    ├─ Token mint
    ├─ Creator address
    └─ Token symbol
         ↓
    STEP 2: Fetch creator's SOL transfers (Helius API)
    ├─ Get all incoming SOL (funding sources)
    ├─ Get all outgoing SOL (extraction accounts)
    └─ Store to creator_sol_transfers table
         ↓
    STEP 3: Run funding reuse analysis
    ├─ Query: Does this treasury fund OTHER creators?
    ├─ Calculate Level 1 risk (direct reuse)
    └─ Calculate Level 2 risk (funding chain)
         ↓
    STEP 4: Search database for duplicates
    ├─ Find all creators funded by SAME accounts
    ├─ Identify coordination networks
    └─ Detect HIDDEN_COORDINATION patterns
         ↓
    STEP 5: Calculate final risk score
    ├─ Formula: Base + (Level2 × 0.3)
    ├─ Determine risk level (LOW/MEDIUM/HIGH/CRITICAL)
    └─ Classify coordination pattern
         ↓
    STEP 6: Store to database
    ├─ Update pools table with risk level
    ├─ Update pools table with pattern
    └─ Store timestamp of analysis
         ↓
    STEP 7: Alert if necessary
    ├─ If HIGH/CRITICAL: Display detailed alert
    ├─ Show all reused accounts
    └─ Show other creators using same accounts
         ↓
    DONE ✓
```

---

## 📝 Code Implementation

### **Trigger Point: WebSocket Detection**
[tests/test_pumpswap_listener.py:2344](tests/test_pumpswap_listener.py#L2344)

```python
# When new token is detected:
funding_analysis = self.check_funding_account_reuse(creator)
```

### **Step 1 & 2: Fetch Creator Data**
[tests/test_pumpswap_listener.py:2300-2340](tests/test_pumpswap_listener.py#L2300-L2340)

```python
# Get creator's on-chain transactions
transactions = fetch_creator_transactions(creator, self.helius_api_key)

# Extract and aggregate SOL transfers
sol_transfers = parse_sol_transfers(transactions)

# Store to database
store_creator_wallet_data(creator, wallet_stats, sol_transfers)
```

**Stored in**: `creator_sol_transfers` table
- Incoming SOL (funding sources)
- Outgoing SOL (extraction destinations)
- Transaction signatures
- Timestamps

### **Step 3: Funding Reuse Analysis**
[analyze_creator_wallet.py:analyze_creator_with_funding_reuse()](analyze_creator_wallet.py)

```python
def analyze_creator_with_funding_reuse(creator_address):
    """
    Analyze funding patterns:
    - Level 1: Does treasury fund other creators? (direct reuse)
    - Level 2: Who funds the treasury? (funding chain)
    - Combine scores with 70/30 weighting
    """

    # Query Level 1: Other creators funded by same treasuries
    level1_analysis = query_direct_reuse(creator_address)

    # Query Level 2: Who funds those treasuries
    level2_analysis = query_funding_chain(creator_address)

    # Calculate combined risk
    final_risk = level1_analysis['base_score'] + (level2_analysis['score'] * 0.3)

    return {
        'overall_risk': classify_risk(final_risk),
        'coordination_pattern': classify_pattern(level1_analysis, level2_analysis),
        'funding_sources': level1_analysis['sources'],
        'token_count': token_count
    }
```

### **Step 4: Duplicate Detection (Database Queries)**

**Query 1: Direct Reuse - Same account funds multiple creators**
```sql
SELECT counterparty_address, COUNT(DISTINCT creator_address) as creator_count
FROM creator_sol_transfers
WHERE transfer_type = 'incoming'
GROUP BY counterparty_address
HAVING COUNT(DISTINCT creator_address) > 1
```

**Query 2: Funding Chain - Same sources fund multiple treasuries**
```sql
SELECT creator_address1, creator_address2, shared_source
FROM creator_sol_transfers cst1
JOIN creator_sol_transfers cst2
  ON cst1.counterparty_address = cst2.counterparty_address
WHERE cst1.creator_address < cst2.creator_address
AND cst1.transfer_type = 'incoming'
```

### **Step 5: Risk Calculation**

**Risk Scoring Formula**:
```
Final Risk = Base Risk + (Level 2 Score × 0.3)

Base Risk (Level 1 - Direct Reuse):
  0 other creators    → 10 (LOW)
  1 other creator     → 35 (MEDIUM)
  2-4 other creators  → 60 (HIGH)
  5+ other creators   → 80 (CRITICAL)

Final Risk Levels:
  ≥70  → CRITICAL (🔴)
  ≥50  → HIGH (🟠)
  ≥30  → MEDIUM (🟡)
  <30  → LOW (🟢)
```

**Coordination Patterns** (8 types):
- `INDEPENDENT_CREATOR` - No coordination detected
- `SOME_COORDINATION` - 1 treasury shared with 1 other creator
- `COORDINATED_GROUP` - Multiple treasuries share funding
- `HIDDEN_COORDINATION` - Level 1 clean but Level 2 hub-connected
- `HIGHLY_COORDINATED_GROUP` - 5+ creators share funding
- `EXTRACTION_HUB` - Creator extracts to multiple destinations
- And 2 more pattern types...

### **Step 6: Database Storage**
[tests/test_pumpswap_listener.py:2362-2374](tests/test_pumpswap_listener.py#L2362-L2374)

```python
db_cursor.execute('''
    UPDATE pools
    SET funding_risk_level = ?,
        funding_risk_pattern = ?,
        funding_check_timestamp = ?
    WHERE base_mint = ?
''', (risk_level, pattern, datetime.now(), token_mint))
```

**Updated columns**:
- `funding_risk_level` - The risk assessment (LOW/MEDIUM/HIGH/CRITICAL)
- `funding_risk_pattern` - The coordination pattern type
- `funding_check_timestamp` - When the analysis was done

### **Step 7: Alert Display**
[tests/test_pumpswap_listener.py:2379-2384](tests/test_pumpswap_listener.py#L2379-L2384)

```python
# Only display alert if HIGH or CRITICAL
if funding_analysis and funding_analysis['overall_risk'] in ['HIGH', 'CRITICAL']:
    self.display_funding_reuse_alert(token_mint, creator, funding_analysis)
elif funding_analysis:
    print(f"[FUNDING] ✓ No significant coordination detected ({funding_analysis['overall_risk']})")
else:
    print(f"[FUNDING] ✓ Creator has no on-chain funding data yet (set to LOW risk)")
```

**Alert includes**:
- Risk level with emoji (🟢/🟡/🟠/🔴)
- Coordination pattern
- All funding sources
- Which OTHER creators use those sources
- Transaction signatures
- Solscan links

---

## 🚀 Running the Automation

### **Automatic (Recommended)**
```bash
# Start the real-time listener
python tests/test_pumpswap_listener.py

# System will:
# 1. Listen for new tokens
# 2. Fetch creator funding data
# 3. Analyze coordination patterns
# 4. Store results to database
# 5. Alert if HIGH/CRITICAL risk found
# Runs continuously until you press Ctrl+C
```

### **Manual (For Existing Tokens)**
```bash
# Analyze a specific creator
python analyze_creator_wallet.py <creator_address>

# System will analyze and print results immediately
# Database will be updated with risk assessment
```

### **Bulk Backfill (For Tokens Outside Listener)**
```bash
# Analyze all tokens with UNKNOWN risk
python backfill_risk_assessment.py

# System will:
# 1. Find all tokens with UNKNOWN risk
# 2. Analyze each creator
# 3. Update database with proper risk levels
# 4. Report results
```

---

## ✅ Verification

### Check if Automation is Working

**1. Run the tests**:
```bash
python tests/test_pumpswap_listener.py test
```

Expected output:
```
✓ Test 1: Funding account queries
✓ Test 2: Creator funding reuse analysis
✓ Test 3: Listener detection verification
✓ Test 4: Alert display format
✓ Test 5: Full integration test
Summary: System ready for production!
```

**2. Start the listener**:
```bash
python tests/test_pumpswap_listener.py
```

Expected behavior:
- Waits for new token detection (may take 1-5 minutes)
- When detected: Fetches creator data, analyzes, stores results
- If coordination found: Displays alert automatically
- Continues listening

**3. Check database for results**:
```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('pumpswap_tokens.db')
c = conn.cursor()
# Check recent tokens with risk assessment
c.execute("""
    SELECT pumpfun_symbol, funding_risk_level, funding_risk_pattern, funding_check_timestamp
    FROM pools
    WHERE funding_risk_level IS NOT NULL
    ORDER BY funding_check_timestamp DESC
    LIMIT 5
""")
for row in c.fetchall():
    print(f"{row[0]:15} | {row[1]:8} | {row[2]:25} | {row[3]}")
EOF
```

Expected output:
```
Symbol         | Risk    | Pattern                  | Timestamp
WEED           | MEDIUM  | SOME_COORDINATION        | 2026-01-06 12:34:56
Purrcy         | MEDIUM  | SOME_COORDINATION        | 2026-01-06 12:34:55
[Other tokens] | LOW     | INDEPENDENT_CREATOR      | 2026-01-06 12:34:54
```

---

## 📊 Real-World Example

### **Scenario**: New token "PUMP" detected

```
1. WebSocket detects: PUMP token created by CreatorA
2. System fetches CreatorA's SOL transfers from Helius
3. Found: CreatorA received 1 SOL from Account X
4. Query database: Does Account X fund other creators?
5. Result: YES! Account X also funds CreatorB (token MOON)
6. Risk Assessment: MEDIUM (1 other creator = reuse detected)
7. Pattern: SOME_COORDINATION
8. Store to database:
   - pools.funding_risk_level = 'MEDIUM'
   - pools.funding_risk_pattern = 'SOME_COORDINATION'
9. Display Alert:
   🟡 Overall Risk: MEDIUM
   Pattern: SOME_COORDINATION
   Funding Sources:
   • Account X (1.0 SOL)
     └─ REUSED (1 other creator)
     └─ Also funded:
        • MOON (CreatorB) - 2 days ago
10. User sees the alert immediately
```

---

## 🔄 Automation Flow Summary

| Step | Component | Input | Action | Output | Database |
|------|-----------|-------|--------|--------|----------|
| 1 | WebSocket | New token | Detect creation | Token mint + creator | pools table |
| 2 | Helius API | Creator address | Fetch SOL transfers | List of transfers | creator_sol_transfers |
| 3 | Analyzer | Transfer data | Calculate Level 1+2 risk | Risk score + pattern | (memory) |
| 4 | Database Query | Creator address | Find duplicate accounts | List of reused accounts | (query result) |
| 5 | Risk Calculator | Scores | Combine 70/30 weighting | Final risk level | (memory) |
| 6 | Database Writer | Risk level + pattern | Store assessment | Confirmation | pools table updated |
| 7 | Alert System | Risk level | Check if HIGH/CRITICAL | Alert message | Console output |

---

## ⚙️ Configuration & Environment

### **Required Environment Variables**
```bash
export HELIUS_API_KEY="your-api-key-here"
export HELIUS_WEBSOCKET_API_KEY="your-websocket-key-here"
```

### **Database File**
```
pumpswap_tokens.db (auto-created if missing)
```

### **Key Tables**
- `pools` - Token metadata with risk assessment columns
- `creator_sol_transfers` - SOL transfer history with signatures
- `creator_wallets` - Creator wallet statistics

---

## 🚦 Automation Status

### ✅ What's Automated

- [x] New token detection (WebSocket)
- [x] Creator data fetching (Helius API)
- [x] SOL transfer extraction (nativeTransfers parsing)
- [x] Level 1 reuse detection (database queries)
- [x] Level 2 funding chain analysis (recursive queries)
- [x] Risk score calculation (formula-based)
- [x] Pattern classification (8 types)
- [x] Database storage (auto-update)
- [x] Alert display (HIGH/CRITICAL only)
- [x] Transaction signature capture (Helius API)
- [x] Duplicate account detection (SQL queries)

### ⚠️ Semi-Automated

- [ ] Manual backfill for tokens added outside listener
  - **Solution**: Use `backfill_risk_assessment.py` script
  - **Frequency**: As needed (not continuous)

### ✅ Currently Handling

- Tokens detected via WebSocket → Fully automated
- Tokens from database outside listener → Backfill script
- Manual analysis of specific creators → `analyze_creator_wallet.py`
- Real-time listener → Continuous automation

---

## 🎯 Summary

**YES - The entire process is fully automated:**

```
New Token → Metadata → SOL Data → Reuse Analysis → Duplicate Search → Risk Calc → Store → Alert
(automatic) (automatic) (automatic) (automatic) (automatic) (automatic) (automatic) (automatic)
```

When you run `python tests/test_pumpswap_listener.py`, the system:
1. **Listens** for new tokens automatically
2. **Fetches** funding data automatically
3. **Analyzes** coordination patterns automatically
4. **Searches** database for duplicates automatically
5. **Stores** results automatically
6. **Alerts** when suspicious activity detected automatically

No manual intervention required - just start the listener and monitor the output!

