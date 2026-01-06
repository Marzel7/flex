# Python Files Organization Guide

**Date**: January 6, 2026
**Status**: ✅ **ORGANIZED**

---

## 📁 File Organization

### Core Application (Root Directory)

These files are essential to the application and must stay in the root:

#### 1. **main.py** (3945 lines) - PRIMARY APPLICATION
- Main Flask web server
- WebSocket listener for token detection
- Price update daemon
- Database operations
- UI endpoints
- **Status**: Essential - do NOT move
- **Usage**: `python main.py`

#### 2. **analyze_creator_wallet.py** (1535 lines) - RISK ANALYSIS ENGINE
- Two-level funding analysis
- Creator funding pattern detection
- Treasury/funding account identification
- Coordination detection
- **Status**: Essential - used by backfill_risk_assessment.py
- **Usage**: Imported by backfill script

#### 3. **backfill_risk_assessment.py** (105 lines) - RISK BACKFILL UTILITY
- Analyzes tokens with UNKNOWN risk status
- Imports analyze_creator_wallet.py
- Bulk risk assessment for historical tokens
- **Status**: Important operational utility
- **Usage**: `python backfill_risk_assessment.py`

#### 4. **hide_poor_performers.py** (159 lines) - TOKEN FILTERING UTILITY
- Identifies tokens with ≤-75% price decline
- Marks tokens as hidden from display
- Updates database with hidden_from_table flag
- **Status**: Important operational utility
- **Usage**: `python hide_poor_performers.py`

#### 5. **trading_executor.py** (1113 lines) - TRADING FEATURES
- Buy/sell execution
- Trade tracking
- Profit/loss calculation
- **Status**: Complete feature (not active in current UI)
- **Usage**: Imported by tests

---

### Utility Scripts (scripts/ Directory)

These are standalone analysis and diagnostic scripts. They can be run independently but are not part of the core application:

#### 1. **analyze_creator_patterns.py** (312 lines)
- Pattern analysis of creator behaviors
- One-time analysis utility
- **Usage**: `python scripts/analyze_creator_patterns.py`

#### 2. **analyze_duplicate_creators.py** (234 lines)
- Find creators with duplicate funding patterns
- Investigative utility
- **Usage**: `python scripts/analyze_duplicate_creators.py`

#### 3. **analyze_sol_destinations.py** (245 lines)
- SOL transfer destination analysis
- Investigate where SOL goes
- **Usage**: `python scripts/analyze_sol_destinations.py`

#### 4. **find_creator_connections.py** (277 lines)
- Find connections between creators
- Network analysis utility
- **Usage**: `python scripts/find_creator_connections.py`

#### 5. **get_token_creators.py** (124 lines)
- Extract creator information for tokens
- Lookup utility
- **Usage**: `python scripts/get_token_creators.py`

#### 6. **query_creator_wallets.py** (293 lines)
- Query creator wallet information
- Research utility
- **Usage**: `python scripts/query_creator_wallets.py`

#### 7. **sol_network_analysis.py** (259 lines)
- Analyze SOL transfer networks
- Network visualization/analysis
- **Usage**: `python scripts/sol_network_analysis.py`

---

## 🎯 Quick Reference

### To Run the Main Application
```bash
python main.py
```

### To Run Operational Utilities
```bash
# Update risk assessment for unknown tokens
python backfill_risk_assessment.py

# Hide poor performers (≤-75% decline)
python hide_poor_performers.py
```

### To Run Analysis Scripts
```bash
# Pattern analysis
python scripts/analyze_creator_patterns.py

# Duplicate creator detection
python scripts/analyze_duplicate_creators.py

# And so on for other scripts in scripts/
```

---

## 📊 Summary

### Root Directory (5 files)

| File | Lines | Type | Purpose |
|------|-------|------|---------|
| **main.py** | 3945 | App | Core application |
| **analyze_creator_wallet.py** | 1535 | Engine | Risk analysis |
| **backfill_risk_assessment.py** | 105 | Util | Bulk risk update |
| **hide_poor_performers.py** | 159 | Util | Token filtering |
| **trading_executor.py** | 1113 | Feature | Trading (archived) |

### scripts/ Directory (7 files)

| File | Lines | Type |
|------|-------|------|
| analyze_creator_patterns.py | 312 | Analysis |
| analyze_duplicate_creators.py | 234 | Analysis |
| analyze_sol_destinations.py | 245 | Analysis |
| find_creator_connections.py | 277 | Analysis |
| get_token_creators.py | 124 | Utility |
| query_creator_wallets.py | 293 | Utility |
| sol_network_analysis.py | 259 | Analysis |

---

## ✅ What's Essential

### For Running the Application
1. **main.py** - MUST have
2. **analyze_creator_wallet.py** - MUST have (used by main and backfill)

### For Complete Functionality
3. **backfill_risk_assessment.py** - Important (risk assessment)
4. **hide_poor_performers.py** - Important (API optimization)
5. **trading_executor.py** - Optional (completed feature)

### For Analysis/Investigation
6-12. **scripts/*.py** - Optional (utility analysis)

---

## 🚀 Dependencies

### analyze_creator_wallet.py (used by)
- main.py (risk assessment in listener)
- backfill_risk_assessment.py (bulk updates)

### backfill_risk_assessment.py (imports)
- analyze_creator_wallet.py

### hide_poor_performers.py (uses)
- SQLite database

### trading_executor.py (standalone)
- Can be used independently

### scripts/* (all standalone)
- No dependencies on each other
- Each can run independently

---

## 📝 File Organization Benefits

### Before
- 12 Python files in root (messy)
- Mix of core app and utilities
- Hard to distinguish what's essential

### After
- 5 core files in root (clean)
- 7 utility scripts in scripts/ (organized)
- Clear separation of concerns
- Easy to identify essential files

---

## 🔄 Maintenance Notes

### When Adding New Scripts
1. If it's essential to core app → Keep in root
2. If it's utility/analysis → Move to scripts/
3. Update this guide if you add new files

### When Removing Scripts
1. Check if it's imported elsewhere
2. Update this guide
3. Git commit with clear message

### Current Status
✅ Core application files: 5 (root)
✅ Utility scripts: 7 (scripts/)
✅ Total size: ~7.8 MB Python code
✅ All essential files identified

---

**Last Updated**: January 6, 2026
**Organization Status**: Clean and organized
**Essential Files**: 2 (main.py, analyze_creator_wallet.py)
**Important Utilities**: 2 (backfill_risk_assessment.py, hide_poor_performers.py)
**Optional/Analysis**: 8 (trading_executor.py + 7 scripts)
