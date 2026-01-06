# Pump-and-Dump Risk Assessment System

## Overview

The system automatically identifies pump-and-dump token schemes by analyzing creator funding patterns. It detects when multiple token creators share the same funding sources (a strong indicator of coordination/manipulation).

## Architecture

### 1. Token Detection
```
WebSocket → PumpSwap program detects migration
├─ Extract token mint & creator
├─ Add to database as LOW risk (default)
└─ Begin analysis pipeline
```

### 2. Helius Analysis (SOL Transfer History)
```
Creator detected → Helius API
├─ Fetch last 100 transactions
├─ Analyze SOL transfers (IN and OUT)
├─ Store funding sources/destinations
└─ creator_sol_transfers table populated
```

**Files involved:**
- `analyze_creator_wallet.py` - `fetch_helius_transactions()` & `analyze_sol_transfers()`
- `test_pumpswap_listener.py` - Lines 2470-2496 (Helius fetch & storage)

**Key insight:** Discovers which accounts funded/funded by each creator

### 3. Coordination Detection
```
SOL transfer data → analyze_creator_with_funding_reuse()
├─ Find creators with shared funding accounts
├─ Detect Level 1: Funding accounts with 5+ creators
├─ Detect Level 2: Funding sources of funding accounts
└─ Calculate CRITICAL/HIGH/MEDIUM/LOW risk
```

**Files involved:**
- `analyze_creator_wallet.py` - `analyze_creator_with_funding_reuse()` (Lines 1348+)
- `test_pumpswap_listener.py` - Lines 2652-2710 (Runs in background thread)

**Key insight:** Identifies coordinated groups of creators

### 4. Registry Management
```
High/Critical creators → Coordinated Funding Registry
├─ Register funding accounts that show coordination
├─ Link creators funded by same accounts
├─ Persist to coordinated_accounts.json
└─ Check against registry for future tokens
```

**Files involved:**
- `coordinated_funding_registry.py` - JSON-based registry
- `test_pumpswap_listener.py` - Lines 2667 (Registry registration)

## The Complete Automated Pipeline

When a new token is detected:

```
1. DETECTION (seconds 0-5)
   ├─ WebSocket catches migration
   ├─ Extract token mint & creator from transaction
   ├─ Store in database as LOW risk
   └─ Begin Helius analysis

2. HELIUS ANALYSIS (seconds 5-15)
   ├─ Fetch creator's transaction history
   ├─ Analyze SOL transfers (identify funding sources)
   ├─ Store 5-100+ treasury/funding relationship records
   └─ [FUNDING] messages show progress

3. COORDINATION DETECTION (seconds 15-30, async)
   ├─ Check if creator's funding accounts are shared
   ├─ If HIGH/CRITICAL: register coordinated accounts
   ├─ Update risk level in database
   └─ [COORDINATION] messages show escalation

4. TRADING DECISION
   ├─ If LOW risk: trading bot can buy
   ├─ If HIGH risk: restricted (manual review)
   ├─ If CRITICAL: blocked (pump-and-dump scheme)
   └─ Updated risk level visible in UI
```

**Total time:** ~30 seconds from detection to full risk assessment

## Data Flow Example

### Scenario: Creator funded by shared account

```
Creator A: Receives SOL from Account X (shared)
Creator B: Receives SOL from Account X (shared)
Creator C: Receives SOL from Account X (shared)

Detection:
1. Creator A detected first
2. Helius analysis finds "Account X" as funding source
3. Coordination check discovers Account X funds 3+ creators
4. Creator A marked CRITICAL
5. Creator A token marked CRITICAL

Later, when Creator B detected:
1. Creator B analysis finds same Account X
2. Coordination check escalates to CRITICAL immediately
3. All tokens from Creator A, B, C marked CRITICAL
```

## Risk Levels

| Level | Definition | Threshold |
|-------|-----------|-----------|
| **CRITICAL** | 🚨 Pump-and-dump scheme detected | Funding account funds 5+ creators |
| **HIGH** | ⚠️ Suspicious coordination detected | Funding account funds 2-4 creators |
| **MEDIUM** | ⚠️ Borderline activity | Creator has multiple funding sources with overlap |
| **LOW** | ✓ Independent creator | Unique funding sources, no shared accounts |

## Key Statistics

Current system status:
- **Total analyzed creators:** 109+
- **CRITICAL risk creators:** 21 (pump-and-dump schemes)
- **HIGH risk creators:** 3 (suspicious coordination)
- **MEDIUM risk creators:** 12 (borderline patterns)
- **LOW risk creators:** 93 (independent)

### Coordinated Groups Identified
- Account `5tzFkiK...` funds **11 creators** (CRITICAL)
- Account `AxiomRXZ...` funds **8 creators** (CRITICAL)
- Account `ASTyfSi...` funds **4 creators** (HIGH)
- Several accounts fund 2-3 creators each

## Running the System

### Option 1: Real-time (Recommended)
```bash
# Start the listener - handles everything automatically
python3 tests/test_pumpswap_listener.py
```

When a token is detected, you'll see:
```
[WEBSOCKET] 🚨 Migration detected: xxx...
[WEBSOCKET] ✓ Successfully fetched transaction
[FUNDING] Checking funding account reuse...
[FUNDING] ✓ Extracted creator from transaction
[FUNDING] Analyzing creator wallet...
[HELIUS_DEBUG] fetch_helius_transactions called...
[FUNDING] ✓ Fetched 100 transactions
[FUNDING] ✓ Stored SOL transfer data
[COORDINATION] ✓ Creator123... escalated to CRITICAL
[COORDINATION] ✓ Registered FundingAccount... (funds 8 creators)
```

### Option 2: Batch Analysis

For existing/missed tokens:

```bash
# 1. Backfill missing Helius analysis
python3 backfill_missing_helius_analysis.py

# 2. Run coordination detection
python3 update_coordination_detection.py

# 3. (Optional) Test specific creator
python3 test_helius_analysis.py <creator_address>
```

## Files in the System

### Core Analysis
- `analyze_creator_wallet.py` - Helius API integration & coordination detection
- `coordinated_funding_registry.py` - Persistent registry of coordinated accounts
- `tests/test_pumpswap_listener.py` - WebSocket listener with full pipeline

### Backfill/Maintenance
- `backfill_missing_helius_analysis.py` - Analyze creators missing SOL transfer data
- `update_coordination_detection.py` - Re-analyze all creators for coordination
- `test_helius_analysis.py` - Test individual creator analysis
- `test_pumpswap_detection.py` - Unit tests for detection

### Configuration
- `.env` - Contains HELIUS_API_KEY
- `coordinated_accounts.json` - Registry of known coordinated accounts
- `pumpswap_tokens.db` - SQLite database with:
  - `pools` - Token metadata & risk assessment
  - `creator_sol_transfers` - Funding relationships
  - `creator_wallets` - Creator statistics

## Database Schema

### pools table
```sql
CREATE TABLE pools (
    base_mint TEXT PRIMARY KEY,
    pumpfun_creator TEXT,              -- Creator wallet address
    funding_risk_level TEXT,           -- LOW/MEDIUM/HIGH/CRITICAL
    funding_risk_pattern TEXT,         -- Pattern description
    funding_check_timestamp TIMESTAMP, -- Last assessment time
    -- ... other columns
)
```

### creator_sol_transfers table
```sql
CREATE TABLE creator_sol_transfers (
    creator_address TEXT,              -- Creator wallet
    transfer_type TEXT,                -- 'incoming' or 'outgoing'
    counterparty_address TEXT,         -- Funding source or destination
    total_amount REAL,                 -- SOL amount
    transfer_count INTEGER,
    first_transfer_timestamp REAL,
    last_transfer_timestamp REAL,
    is_treasury BOOLEAN,
    latest_tx_signature TEXT
)
```

## Troubleshooting

### No Helius data for recent tokens
```bash
# Check if API key is set
grep HELIUS_API_KEY .env

# Run test
python3 test_helius_analysis.py <creator_address>

# Check logs for [HELIUS_DEBUG] messages
```

### Risk levels not updating
```bash
# Run coordination detection
python3 update_coordination_detection.py --all

# Check database
sqlite3 pumpswap_tokens.db "SELECT COUNT(*) FROM creator_sol_transfers"
```

### Registry file issues
```bash
# View current registry
cat coordinated_accounts.json | python3 -m json.tool

# Rebuild registry
rm coordinated_accounts.json
python3 update_coordination_detection.py --all
```

## Performance Metrics

- **Token detection:** ~3-8 seconds (WebSocket)
- **Helius fetch:** ~2-5 seconds (100 transactions)
- **SOL analysis:** <1 second
- **Coordination check:** 1-2 seconds
- **Total:** ~30 seconds from detection to final risk assessment

## Security Considerations

1. **API Key Protection**
   - HELIUS_API_KEY stored in .env (never committed)
   - Check .gitignore includes .env

2. **Database Access**
   - SQLite with check_same_thread=False for async access
   - No direct SQL injection possible (parameterized queries)

3. **Registry Persistence**
   - coordinated_accounts.json stored locally
   - Not synced to remote (stays private)

## Future Enhancements

1. **Additional Data Sources**
   - Jupiter API for swap patterns
   - Magic Eden for NFT activity
   - On-chain program logs for complex transactions

2. **Advanced Coordination Detection**
   - Multi-level funding chains (A→B→C patterns)
   - Timing analysis (synchronized launches)
   - Market impact analysis (pump volume patterns)

3. **Machine Learning**
   - Classify pump-and-dump patterns
   - Predict which tokens will be coordinated
   - Estimate probability of rug pull

4. **Reporting**
   - Daily coordination reports
   - Creator risk scorecards
   - Funding network visualization
