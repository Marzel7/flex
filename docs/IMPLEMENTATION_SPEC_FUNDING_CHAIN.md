# Implementation Specification: Funding Chain with In/Out SOL Tracking

## Complete Data Flow

When a new token launches, we trace the COMPLETE SOL flow through treasury accounts:

```
New Token Created by Creator X
        ↓
Step 1: INCOMING to Creator X (Who funds them?)
        ↓
    Funding Account A ──(6 SOL, 8 transfers)──> Creator X
    Funding Account B ──(3 SOL, 12 transfers)──> Creator X
    Funding Account C ──(1.5 SOL, 4 transfers)──> Creator X
        ↓
Step 2: What do THOSE funding accounts do? (Complete audit trail)
        ↓
    Funding Account A:
    ├─ INCOMING: Receives from Treasury Hub X (20 SOL, 55 transfers)
    ├─ OUTGOING: Sends to Creator X (6 SOL, 8 transfers)
    │           Sends to Creator Y (4 SOL, 6 transfers)
    │           Sends to Creator Z (5 SOL, 7 transfers)
    └─ NET: +5 SOL (receives more than it distributes)

    Funding Account B:
    ├─ INCOMING: Receives from Treasury Hub X (8 SOL, 12 transfers)
    ├─ OUTGOING: Sends to Creator X (3 SOL, 12 transfers)
    │           Sends to Creator W (2 SOL, 5 transfers)
    └─ NET: +3 SOL

    Funding Account C:
    ├─ INCOMING: Receives from Coinbase DPqsoby... (10 SOL, 4 transfers)
    ├─ OUTGOING: Sends to Creator X (1.5 SOL, 4 transfers)
    │           Sends to Creator V (2 SOL, 2 transfers)
    └─ NET: +6.5 SOL
        ↓
Step 3: Trace back THEIR funding sources (Who funds the funders?)
        ↓
    Treasury Hub X:
    ├─ INCOMING: Receives from Coinbase DPqsoby... (100 SOL, 55 transfers)
    │           Receives from Kraken Hot Wallet... (50 SOL, 30 transfers)
    ├─ OUTGOING: Sends to Funding Account A (20 SOL, 55 transfers)
    │           Sends to Funding Account B (8 SOL, 12 transfers)
    │           Sends to Other Funders (80 SOL, various)
    └─ NET: -38 SOL (distribution hub)
        ↓
Step 4: Identify CEX pattern
        ↓
    Coinbase DPqsoby...:
    ├─ INCOMING: Unknown (probably exchange deposits)
    ├─ OUTGOING: Sends to Treasury Hub X (100 SOL)
    │           Sends to Treasury Hub Y (80 SOL)
    │           Sends to Treasury Hub Z (120 SOL)
    └─ NET: Large negative (distribution point)

RESULT: Complete Money Trail
Coinbase → Treasury Hubs → Funding Accounts → Creators → Pump Tokens
                                           ↓
                                    (Profit extraction points)
```

---

## Database Queries for Each Step

### Query Set 1: Creator's INCOMING (Who funds this creator?)

```sql
SELECT
  creator_address,
  counterparty_address as funding_source,
  transfer_type,
  transfer_count,
  total_amount,
  is_treasury,
  first_transfer_timestamp,
  last_transfer_timestamp
FROM creator_sol_transfers
WHERE creator_address = '<new_creator>'
AND transfer_type = 'incoming'
ORDER BY transfer_count DESC;
```

**Output columns:**
- Funding source address
- How many times it funded
- Total SOL sent
- Is it a treasury (>5 transfers)?

### Query Set 2: Funding Account's INCOMING (Where do they get their SOL?)

```sql
SELECT
  creator_address as funding_source_being_analyzed,
  counterparty_address as upstream_source,
  transfer_type,
  transfer_count,
  total_amount,
  is_treasury
FROM creator_sol_transfers
WHERE creator_address = '<funding_account>'
AND transfer_type = 'incoming'
ORDER BY transfer_count DESC;
```

**Output columns:**
- Where does this funding account receive FROM
- How much and how often
- Is that source a treasury?

### Query Set 3: Funding Account's OUTGOING (What do they do with the money?)

```sql
SELECT
  creator_address as funding_source_being_analyzed,
  counterparty_address as downstream_recipient,
  transfer_type,
  transfer_count,
  total_amount,
  is_treasury
FROM creator_sol_transfers
WHERE creator_address = '<funding_account>'
AND transfer_type = 'outgoing'
ORDER BY transfer_count DESC;
```

**Output columns:**
- Where does this funding account send TO
- How much and how often
- Does it go to multiple creators (coordination)?

### Query Set 4: NET SOL Position for Funding Account

```sql
SELECT
  '<funding_account>' as account,
  COALESCE(SUM(CASE WHEN transfer_type = 'incoming' THEN total_amount ELSE 0 END), 0) as total_in,
  COALESCE(SUM(CASE WHEN transfer_type = 'outgoing' THEN total_amount ELSE 0 END), 0) as total_out,
  COALESCE(SUM(CASE WHEN transfer_type = 'incoming' THEN total_amount ELSE -total_amount END), 0) as net_position,
  COUNT(*) as total_transfers
FROM creator_sol_transfers
WHERE creator_address = '<funding_account>'
OR counterparty_address = '<funding_account>';
```

**Output:**
- Total SOL in
- Total SOL out
- Net position (positive = accumulator, negative = distributor)
- Total transfers

### Query Set 5: Detect Aggregation/Treasury Hubs

```sql
SELECT
  counterparty_address as hub_account,
  COUNT(DISTINCT creator_address) as sources_count,
  SUM(CASE WHEN transfer_type = 'incoming' THEN total_amount ELSE 0 END) as total_in,
  SUM(CASE WHEN transfer_type = 'outgoing' THEN total_amount ELSE 0 END) as total_out,
  SUM(CASE WHEN transfer_type = 'incoming' THEN total_amount ELSE -total_amount END) as net_position
FROM creator_sol_transfers
WHERE is_treasury = 1
GROUP BY counterparty_address
HAVING sources_count > 2
ORDER BY sources_count DESC, net_position DESC;
```

**Identifies:**
- Accounts that receive from multiple creators
- Net position (treasury hub or distribution hub)
- Total SOL flow

---

## Table Output Structure

### Level 1: Creator's Funding Sources

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ INCOMING SOL TRANSFERS TO CREATOR (Funding Sources)                                │
├─────────────────────────────────────────────────────────────────────────────────────┤
│ Funding Source Address        │ SOL In │ Count │ Treasury │ Funds Other? │ Suspicious
├──────────────────────────────────────────────────────────────────────────────────────
│ dnd5bzqmcnfd6ycnequgumpba...  │ 0.6    │ 6     │ 🏦       │ 🚩 3x        │ MEDIUM
│ 9zz1mp5bnayyunuwwmbhpeeck...  │ 0.6    │ 6     │ 🏦       │ ✓ 1x         │ LOW
│ 4tsuj32yitzpk3gvw9erhugdq...  │ 0.0    │ 6     │ 🏦       │ 🚩 5x        │ HIGH
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### Level 2: Drill Into Funding Source

When clicked/expanded, show:

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ FUNDING SOURCE: dnd5bzqmcnfd6ycnequgumpba...                                        │
├─────────────────────────────────────────────────────────────────────────────────────┤

INCOMING TO THIS ACCOUNT (Where does it get its SOL?)
├─────────────────────────────────────────────────────────────────────────────────────┤
│ Source                        │ SOL    │ Count │ Treasury │ Type
├───────────────────────────────────────────────────────────────────
│ DPqsobysNf5iA9w7zrQM8HLz...   │ 15.0   │ 12    │ 🏦       │ 🏛️ COINBASE
│ Other_Treasury_Hub...         │ 8.5    │ 8     │ 🏦       │ 🏛️ LIKELY CEX
│ Private_Wallet_XYZ...         │ 2.0    │ 4     │          │ ✓ Private

OUTGOING FROM THIS ACCOUNT (What does it do with the SOL?)
├─────────────────────────────────────────────────────────────────┤
│ Destination                   │ SOL    │ Count │ Type
├───────────────────────────────────────────────────────────────────
│ Creator: 6FCpd6KMKKrPLzgt...  │ 0.6    │ 6     │ 📊 Token Creator
│ Creator: AnotherCreator...    │ 0.5    │ 5     │ 📊 Token Creator
│ Creator: ThirdCreator...      │ 0.4    │ 4     │ 📊 Token Creator

NET POSITION
├───────────────────────────────────────────────────────────────────┤
│ Total In:  23.5 SOL
│ Total Out: 1.5 SOL
│ NET:       +22.0 SOL (ACCUMULATOR - Collects from CEX, distributes to creators)

RISK ASSESSMENT: CRITICAL 🚩
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Level 3: Trace to CEX

When tracing DPqsobysNf5iA9w7zrQM8HLz...:

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ CEX FUNDING TRACE: DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo                   │
│ 🏛️ COINBASE CUSTODY/STAKING WALLET                                                 │
├─────────────────────────────────────────────────────────────────────────────────────┤

NETWORK ANALYSIS
├─────────────────────────────────────────────────────────────────┤
│ This Coinbase wallet sends to:
│
│ Treasury Hub A: 100 SOL (55 transfers)
│ ├─ Funds Creator 1, Creator 2, Creator 3, ... (10 creators)
│ ├─ Each creator launching pump tokens
│ └─ 🚨 COORDINATED PUMP OPERATION
│
│ Treasury Hub B: 80 SOL (40 transfers)
│ ├─ Funds Creator X, Creator Y, Creator Z, ... (8 creators)
│ ├─ Parallel pump group
│ └─ 🚨 COORDINATED PUMP OPERATION
│
│ Treasury Hub C: 120 SOL (60 transfers)
│ ├─ Feeds into wider network
│ └─ 🚨 COORDINATED PUMP OPERATION

TOTAL COINBASE OUTFLOW: 300+ SOL across multiple pump networks

CONCLUSION: PROFESSIONAL PUMP OPERATION
- Coinbase funding → Treasury hubs → Creator funders → Pump tokens
- Organized, well-capitalized group
- HIGH RISK OF RUG PULL
└─────────────────────────────────────────────────────────────────┘
```

---

## Code Implementation

### Function: Trace Funding Chain

```python
def trace_funding_chain(creator_address):
    """
    Complete funding chain trace with in/out SOL tracking

    Returns hierarchical structure:
    {
        'creator': creator_address,
        'direct_funders': [
            {
                'address': funding_account,
                'incoming_to_creator': {'count': 6, 'total_sol': 0.6},
                'is_treasury': True,
                'coordinates_with': 3,  # funds 3 other creators
                'funding_source': {  # Where THIS account gets its SOL
                    'address': upstream_source,
                    'incoming': {'count': 12, 'total_sol': 15.0},
                    'is_treasury': True,
                    'likely_cex': 'Coinbase'
                },
                'net_position': {
                    'total_in': 23.5,
                    'total_out': 1.5,
                    'net': 22.0,  # Accumulator
                    'role': 'DISTRIBUTOR'
                }
            }
        ],
        'upstream_sources': [
            {
                'address': cex_or_hub,
                'type': 'Coinbase',
                'outgoing': [
                    {'to': 'Treasury Hub 1', 'sol': 100, 'creators_funded': 10},
                    {'to': 'Treasury Hub 2', 'sol': 80, 'creators_funded': 8},
                    {'to': 'Treasury Hub 3', 'sol': 120, 'creators_funded': 12}
                ]
            }
        ],
        'risk_assessment': 'CRITICAL',
        'reason': 'Coinbase-funded coordinated pump operation'
    }
```

### Function: Get In/Out SOL for Account

```python
def get_account_flow(account_address):
    """
    Get complete in/out SOL flow for any account

    Returns:
    {
        'address': account_address,
        'incoming': [
            {'from': creator_or_account, 'sol': 0.6, 'count': 6, 'is_treasury': True},
            ...
        ],
        'outgoing': [
            {'to': creator_or_account, 'sol': 0.5, 'count': 5, 'is_treasury': False},
            ...
        ],
        'totals': {
            'total_in': 25.0,
            'total_out': 2.0,
            'net': 23.0
        },
        'role': 'ACCUMULATOR' | 'DISTRIBUTOR' | 'RELAY'
    }
```

### Tests for test_pumpswap_listener.py

```python
def test_listener_traces_funding_to_coinbase():
    """
    When new token created:
    1. Extract creator
    2. Get their funding accounts
    3. Trace those accounts' incoming/outgoing
    4. Identify Coinbase as upstream source
    """
    event = create_pool_event(creator='NewCreator')

    result = trace_funding_chain(event.creator)

    # Assertions
    assert result['direct_funders'] > 0
    for funder in result['direct_funders']:
        assert 'incoming_to_creator' in funder
        assert 'funding_source' in funder
        assert 'net_position' in funder

    # Check if traces to Coinbase
    upstream = result['upstream_sources']
    assert any(s['type'] == 'Coinbase' for s in upstream)

    # Risk assessment
    assert result['risk_assessment'] in ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

def test_listener_shows_in_out_sol():
    """Verify in/out SOL amounts are accurately tracked"""
    account = 'TestAccount123'

    flow = get_account_flow(account)

    # Verify structure
    assert 'incoming' in flow
    assert 'outgoing' in flow
    assert 'totals' in flow

    # Verify math
    assert flow['totals']['net'] == flow['totals']['total_in'] - flow['totals']['total_out']

    # Verify each transaction has amount
    for tx in flow['incoming'] + flow['outgoing']:
        assert 'sol' in tx
        assert 'count' in tx

def test_listener_identifies_cex_pattern():
    """
    Identify CEX-like accounts by behavior:
    - Receives from many sources
    - Sends in large amounts
    - Distributes to multiple hubs
    """
    # Coinbase-like account
    account = 'DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo'

    flow = get_account_flow(account)

    # Should show massive inflow/outflow
    assert flow['totals']['total_out'] > 100

    # Should distribute to multiple hubs
    outgoing_destinations = [tx['to'] for tx in flow['outgoing']]
    assert len(set(outgoing_destinations)) > 3

    # Role should be DISTRIBUTOR
    assert flow['role'] == 'DISTRIBUTOR'
```

---

## Display in Listener Output

### test_pumpswap_listener.py Console Output

```
[NEW TOKEN DETECTED]
Token: SHITCOIN
Creator: 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA
Status: analyzing...

[FUNDING ANALYSIS]
✓ Direct funders found: 3
  ├─ dnd5bzqm... → 0.6 SOL (6 transfers) 🏦 Treasury
  │  ├─ 🚩 REUSED: Funds 3 other creators
  │  ├─ Gets SOL from: DPqsoby... (Coinbase) [15.0 SOL]
  │  ├─ Net Position: +22.0 SOL (ACCUMULATOR)
  │  └─ Role: Distributor
  │
  ├─ 9zz1mp5b... → 0.6 SOL (6 transfers) 🏦 Treasury
  │  ├─ ✓ Only funds this creator
  │  ├─ Gets SOL from: DPqsoby... (Coinbase) [8.0 SOL]
  │  ├─ Net Position: +6.5 SOL
  │  └─ Role: Single-use funders
  │
  └─ 4tsuj32y... → 0.0 SOL (6 transfers) 🏦 Treasury
     ├─ 🚩 REUSED: Funds 5 other creators
     ├─ Gets SOL from: DPqsoby... (Coinbase) [12.0 SOL]
     ├─ Net Position: +11.5 SOL (ACCUMULATOR)
     └─ Role: Major distributor

[UPSTREAM SOURCES]
🏛️ DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo
   Exchange: COINBASE (Custody/Staking)
   Total outflow: 300+ SOL
   Funds: 15+ treasury hubs
   Which feed: 40+ creators
   Which launch: 40+ pump tokens

[NETWORK ANALYSIS]
Connected to: 40+ token launches
Shared funding accounts: 15+ hubs
CEX source: Coinbase
Group size: Professional operation

[RISK ASSESSMENT]
🚨 CRITICAL 🚨

Reason:
  • Coinbase-funded coordinated operation
  • Multiple shared intermediaries
  • Treasury accounts reused across creators
  • Professional money flow patterns
  • High probability coordinated pump & dump

Recommendation: AVOID / REPORT
```

---

## Summary

**What we're implementing:**

1. **Trace funding chain** - From creator → funders → upstream sources → CEX
2. **Track in/out SOL** - Complete flow accounting for each intermediary
3. **Net position** - Identify accumulators vs distributors
4. **CEX detection** - Inferred from behavior (no database needed)
5. **Display all info** - Show complete money trail in table/output

**Data structure:** Hierarchical tracing with complete SOL flow
**Tests:** Verify in/out amounts, chain integrity, CEX pattern detection
**Output:** Multi-level display showing full funding network

This enables complete visibility into pump-and-dump operations funded through CEX accounts.
