# Funding Account Flag System - Design Specification

## Overview

When a new token is launched on PumpSwap, we check if its creator has **funding accounts already in our database**. If yes, we flag it to highlight potential coordination.

---

## Feature Flow

```
New Token Created Event
        ↓
Extract creator_address
        ↓
Query Database:
"Does this creator exist in creator_sol_transfers?"
        ↓
        ├─ YES → Extract known funding accounts
        │        └─ Known accounts that fund this creator
        │
        └─ NO → Creator is new
                └─ No flag (first time seeing them)
        ↓
Query Database (CROSS-CREATOR):
"Do ANY of this creator's funding accounts
 fund other creators?"
        ↓
        ├─ YES → SHARED FUNDING DETECTED 🚩
        │        └─ Multiple creators share same funding source
        │
        └─ NO → Dedicated funding account
                └─ Only funds this creator
        ↓
Output Table:
Token | Creator | Funding Account | Count | Shared? | 🚩
```

---

## Database Queries to Execute

### Query 1: Get Known Funding Accounts for Creator

```sql
-- When creator X launches a new token:
SELECT
  counterparty_address,      -- Funding source address
  transfer_count,            -- How many times funded this creator
  is_treasury                -- Is it a treasury (>5 transfers)?
FROM creator_sol_transfers
WHERE creator_address = '<new_token_creator>'
AND transfer_type = 'incoming'
AND transfer_count >= 2      -- Only significant funding (2+ transfers)
ORDER BY transfer_count DESC;
```

**Result:** List of accounts we KNOW fund this creator

### Query 2: Check if Funding Account Funds Other Creators

```sql
-- For each funding account found above:
SELECT
  COUNT(DISTINCT creator_address) as creator_count,
  GROUP_CONCAT(creator_address) as other_creators
FROM creator_sol_transfers
WHERE counterparty_address = '<funding_account>'
AND transfer_type = 'incoming'
AND creator_address != '<new_token_creator>';
```

**Result:**
- `creator_count > 0` = Shared funding account (RED FLAG)
- `creator_count = 0` = Dedicated account (normal)

### Query 3: Get All Shared Funding Accounts (Network View)

```sql
-- Find aggregation hubs (addresses funding multiple creators)
SELECT
  counterparty_address,      -- Funding address
  COUNT(DISTINCT creator_address) as creator_count,
  GROUP_CONCAT(creator_address) as creators,
  SUM(transfer_count) as total_transfers
FROM creator_sol_transfers
WHERE transfer_type = 'incoming'
AND is_treasury = 1
GROUP BY counterparty_address
HAVING creator_count > 1
ORDER BY creator_count DESC;
```

**Result:** All funding accounts used by multiple creators

---

## Table Output Format

### Current Output (analyze_creator_wallet.py)

```
Incoming SOL transfers: 71

Source Address                                | SOL Amount   | Transfers  | Type
dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc  | 0.6000       | 6          | 🏦 Treasury
9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g  | 0.6000       | 6          | 🏦 Treasury
```

### Enhanced Output (WITH FUNDING FLAGS)

```
Incoming SOL transfers: 71

Source Address                                | SOL Amount   | Transfers  | Type           | Status
dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc  | 0.6000       | 6          | 🏦 Treasury    | 🚩 SHARED (3 creators)
9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g  | 0.6000       | 6          | 🏦 Treasury    | ✓ Dedicated
4tsuj32yitzpk3gvw9erhugdqfminsmxy6s59u3nnwdn  | 0.0000       | 6          | 🏦 Treasury    | 🚩 SHARED (5 creators)
```

### What the Flags Mean

| Flag | Meaning | Risk | Action |
|------|---------|------|--------|
| 🚩 SHARED (N creators) | This funding account funds N different creators | HIGH | Investigate coordination |
| ✓ Dedicated | This account only funds this creator | LOW | Normal |
| ❓ NEW | Creator not in database yet | UNKNOWN | Analyze when found |
| 🔗 CROSS-FUNDING | This creator is also a funding source for others | MEDIUM | Check if multi-tier |

---

## Test Output Example

### test_pumpswap_listener.py Output

```
Running: test_listener_detects_funding_account_reuse

New Token Created: SHITCOIN2
Creator: 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA

Checking creator funding accounts...

✓ Found 3 funding sources for this creator:
  1. dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc
     - Funds THIS creator: 6 times
     - 🚩 ALSO funds: SHITCOIN (creator A), BADTOKEN (creator B)
     - Risk: HIGH (shared with 2 other creators)

  2. 9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g
     - Funds THIS creator: 6 times
     - ✓ ONLY funds this creator
     - Risk: LOW

  3. 4tsuj32yitzpk3gvw9erhugdqfminsmxy6s59u3nnwdn
     - Funds THIS creator: 6 times
     - 🚩 ALSO funds: PUMP (creator C), MOON (creator D), LAMBO (creator E)
     - Risk: HIGH (shared with 4 other creators)

OVERALL RISK: HIGH 🚩
Recommendation: This token likely part of coordinated pump group
```

---

## Implementation Strategy

### Step 1: Enhance Creator Analysis (analyze_creator_wallet.py)

Add new function:
```python
def check_funding_account_reuse(creator_address):
    """
    Check if creator's funding accounts are also used by other creators

    Returns:
    {
        'creator_address': creator_address,
        'funding_sources': [
            {
                'address': '0xFUND1...',
                'funds_this_creator': 6,
                'is_treasury': True,
                'funds_other_creators': ['0xCREATOR1', '0xCREATOR2'],
                'creator_count': 2,
                'risk_level': 'HIGH',
                'flag': '🚩 SHARED (2 creators)'
            },
            {
                'address': '0xFUND2...',
                'funds_this_creator': 4,
                'is_treasury': False,
                'funds_other_creators': [],
                'creator_count': 0,
                'risk_level': 'LOW',
                'flag': '✓ Dedicated'
            }
        ],
        'overall_risk': 'HIGH'
    }
    """
    # Query creator's funding accounts
    # For each account, check if it funds other creators
    # Return enriched data with flags
```

### Step 2: Update Table Display (analyze_creator_wallet.py)

Modify incoming transfers display:
```python
# Current code shows:
# Source Address | SOL Amount | Transfers | Type

# Enhanced code shows:
# Source Address | SOL Amount | Transfers | Type | Status
#                                                   ↑ NEW
#                                          Shows flag + shared count
```

### Step 3: Update Listener (test_pumpswap_listener.py)

When token is created:
```python
def on_pool_created(event):
    creator = extract_creator(event)

    # NEW: Check if creator's funding accounts are shared
    funding_analysis = check_funding_account_reuse(creator)

    # Log with flags
    for fund_account in funding_analysis['funding_sources']:
        if fund_account['creator_count'] > 1:
            logger.warning(
                f"🚩 SHARED FUNDING DETECTED: "
                f"{fund_account['address']} funds {fund_account['creator_count']} creators"
            )

    # Store token with risk assessment
    store_token(event, risk_level=funding_analysis['overall_risk'])
```

### Step 4: Write Tests

Test cases in test_pumpswap_listener.py:
```python
def test_listener_detects_shared_funding_account():
    """New token from creator using known shared funding account"""
    pass

def test_listener_flags_multiple_shared_accounts():
    """Creator with multiple shared funding sources"""
    pass

def test_listener_handles_new_creator():
    """Creator not in database (no known funding accounts)"""
    pass

def test_listener_shows_funding_flags_in_output():
    """Table output includes funding account flags"""
    pass
```

---

## Database Changes Required

### No schema changes needed!

The data already exists in `creator_sol_transfers`:
- `creator_address` - Who is being funded
- `counterparty_address` - Who is funding them
- `transfer_type = 'incoming'` - Funding transfers
- `is_treasury` - Important funding (>5 transfers)

We just need to:
1. Query existing data
2. Cross-reference creators
3. Flag shared accounts
4. Display in output

---

## Risk Assessment Algorithm

```python
def calculate_funding_risk(funding_analysis):
    """
    Calculate overall risk based on funding pattern

    Factors:
    - Number of funding accounts: 1 = LOW, 2-3 = MEDIUM, 4+ = HIGH
    - Shared funding accounts: Each shared = +25 risk
    - Treasury status: Treasury account = +10 risk
    - Total funding amount: Very large = +15 risk

    Result: 0-100 risk score
    """
    risk_score = 0

    # Factor 1: Multiple funding sources
    if len(funding_analysis) >= 4:
        risk_score += 30
    elif len(funding_analysis) >= 2:
        risk_score += 15

    # Factor 2: Shared funding accounts
    for fund_account in funding_analysis:
        if fund_account['creator_count'] > 1:
            risk_score += (fund_account['creator_count'] * 15)

    # Factor 3: Treasury accounts
    treasury_count = sum(1 for f in funding_analysis if f['is_treasury'])
    risk_score += (treasury_count * 10)

    return min(risk_score, 100)  # Cap at 100
```

---

## Display Priority

When showing results, prioritize:

1. **CRITICAL** 🚩🚩 - Shared with 5+ creators
2. **HIGH** 🚩 - Shared with 2-4 creators
3. **MEDIUM** ⚠️ - Shared with 1 creator
4. **LOW** ✓ - Only funds this creator

```
Incoming SOL transfers (sorted by risk):

Source Address | SOL | Transfers | Type | Status
───────────────────────────────────────────────────────
dnd5bzq...     | 0.6 | 6         | 🏦  | 🚩🚩 SHARED (7 creators)
9zz1mp5...     | 0.6 | 6         | 🏦  | 🚩 SHARED (3 creators)
4tsuj32...     | 0.0 | 6         | 🏦  | ✓ Dedicated
```

---

## Summary

**What we're doing:**
- When new token launches, extract creator
- Check if we know their funding accounts (from previous analysis)
- Check if those funding accounts also fund OTHER creators
- Flag shared accounts with count
- Display in table with risk level

**Why it matters:**
- Detects coordinated pump groups early
- Shows which accounts are "central hubs" funding multiple creators
- Helps identify money laundering patterns
- Spots duplicate operations using same funding wallets

**Output:**
- Table with new "Status" column
- Shows 🚩 SHARED (N creators) for coordinated funding
- Shows ✓ Dedicated for isolated funding
- Overall risk assessment at top

---

## Example Scenario

**Before system:**
```
Token SHITCOIN launched by Creator A
Token BADTOKEN launched by Creator B
Token PUMP launched by Creator C
(No connection visible)
```

**After system:**
```
Token SHITCOIN launched by Creator A
  └─ Funded by: 0xFUND_HUB (6 times)
     └─ 🚩 0xFUND_HUB also funds Creator B and Creator C!

Token BADTOKEN launched by Creator B
  └─ Funded by: 0xFUND_HUB (5 times)
     └─ 🚩 0xFUND_HUB also funds Creator A and Creator C!

Token PUMP launched by Creator C
  └─ Funded by: 0xFUND_HUB (7 times)
     └─ 🚩 0xFUND_HUB also funds Creator A and Creator B!

FINDING: All 3 creators funded by same account
CONCLUSION: Coordinated pump group detected 🚩
```
