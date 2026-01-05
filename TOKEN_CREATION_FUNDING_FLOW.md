# Token Creation Funding Flow - System Architecture

## High-Level Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PUMPSWAP LISTENER (WebSocket)                         │
│                                                                               │
│  Real-time detection of pool creation events on PumpSwap                     │
│  Trigger: New token migration from Pump.fun → PumpSwap                       │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                              │ Pool created event detected
                              │ Extract: token_mint, creator_address
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CREATOR WALLET ANALYSIS SYSTEM                            │
│                                                                               │
│  Input: creator_address from pool creation event                             │
│  Action: Fetch complete transaction history from Helius API                  │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────────┐
        │  Extract SOL Transfer Analysis                  │
        │                                                  │
        │  1. Incoming Transfers (Funding Sources)        │
        │     └─ Which addresses fund this creator?       │
        │     └─ How many times? (treasury detection)     │
        │                                                  │
        │  2. Outgoing Transfers (Profit Extraction)      │
        │     └─ Where does creator send SOL?             │
        │     └─ How many times? (treasury detection)     │
        │                                                  │
        │  3. Timing Analysis                             │
        │     └─ When were transfers relative to token    │
        │        creation?                                │
        └─────────────────────┬──────────────────────────┘
                              │
                ┌─────────────┴──────────────┐
                │                            │
                ▼                            ▼
    ┌──────────────────────┐    ┌──────────────────────┐
    │ FUNDING ACCOUNTS     │    │ TREASURY ACCOUNTS    │
    │ (Incoming Treasury)  │    │ (Outgoing Treasury)  │
    │                      │    │                      │
    │ Addresses sending    │    │ Addresses receiving  │
    │ SOL to creator       │    │ SOL from creator     │
    │ >5 transfers = 🏦    │    │ >5 transfers = 🏦    │
    └──────────┬───────────┘    └──────────┬───────────┘
               │                           │
               │ Store in Database         │ Store in Database
               └───────────────┬───────────┘
                               │
                               ▼
                ┌──────────────────────────────────────────┐
                │  DATABASE: creator_sol_transfers         │
                │                                          │
                │  creator_address                         │
                │  transfer_type (incoming/outgoing)      │
                │  counterparty_address (real account)    │
                │  transfer_count (aggregated)            │
                │  is_treasury (>5 transfers = 1)         │
                │  total_amount (SOL moved)               │
                └──────────┬───────────────────────────────┘
                           │
                ┌──────────┴──────────────┐
                │                         │
                ▼                         ▼
        ┌───────────────────┐    ┌──────────────────────┐
        │ SINGLE TOKEN VIEW │    │ NETWORK ANALYSIS     │
        │                   │    │                      │
        │ For THIS token:   │    │ Query all creators:  │
        │ • Creator address │    │ • Find shared        │
        │ • Funding sources │    │   funding accounts   │
        │ • Treasuries used │    │ • Find shared profit │
        │ • Risk signals    │    │   destinations       │
        │                   │    │ • Detect coordination│
        └───────────────────┘    └──────────────────────┘
```

---

## Detailed Interaction Flow

### Phase 1: Token Creation Event Detection

```
PumpSwap WebSocket Event
│
├─ Event: Pool Created
├─ Data:
│  ├─ token_mint: EH3taj1h...
│  ├─ creator_address: 6FCpd6KM...
│  ├─ timestamp: 2026-01-05 10:00:00
│  └─ signature: 5JdoKAa...
│
└─ ACTION: Add to tokens table
   └─ status: 'waiting'
   └─ first_seen: now
```

### Phase 2: Creator Funding Analysis

```
New Token Created → Trigger Creator Analysis
│
├─ Creator Address: 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA
│
├─ QUERY: Helius API - Transaction History
│
├─ FILTER: Only TRANSFER type (native SOL)
│  └─ Exclude: Token transfers, SPL, Mints
│
├─ EXTRACT: nativeTransfers field
│  └─ Real wallet addresses (not parsed text)
│  └─ Exact amounts in lamports
│
└─ AGGREGATE: By source/destination
   ├─ Funding Sources (incoming):
   │  ├─ Address A: 6 transfers → 🏦 Treasury (funding source)
   │  ├─ Address B: 3 transfers → Normal
   │  └─ Address C: 2 transfers → Normal
   │
   └─ Profit Destinations (outgoing):
      ├─ Address X: 55 transfers → 🏦 Treasury (profit destination)
      ├─ Address Y: 8 transfers → 🏦 Treasury
      └─ Address Z: 2 transfers → Normal
```

### Phase 3: Data Storage & Enrichment

```
For Each Token Created:

┌─ pools table (existing)
│  ├─ token_mint
│  ├─ creator_address
│  ├─ first_seen
│  └─ status
│
├─ creator_wallets table (NEW context)
│  ├─ creator_address
│  ├─ account_age_days
│  ├─ total_sol_in (total funding received)
│  ├─ total_sol_out (total extraction)
│  ├─ net_sol_position (in - out)
│  └─ last_analyzed
│
└─ creator_sol_transfers table (NEW details)
   ├─ creator_address
   ├─ transfer_type ('incoming' | 'outgoing')
   ├─ counterparty_address (funding source or profit destination)
   ├─ transfer_count (how many times)
   ├─ total_amount (total SOL)
   ├─ is_treasury (1 if >5 transfers)
   └─ [timestamps]
```

---

## Use Case Examples

### Use Case 1: Single Token Investigation

```
New Token: "SHITCOIN" migrated to PumpSwap
│
├─ Get creator: 0xABC123...
│
├─ Analyze creator:
│  ├─ Found 5 funding sources
│  │  ├─ Source A: 15 transfers → 🏦 Treasury (suspicious!)
│  │  └─ Source B: 2 transfers
│  │
│  └─ Found 3 profit destinations
│     ├─ Destination X: 20 transfers → 🏦 Treasury
│     └─ Destination Y: 5 transfers → 🏦 Treasury
│
└─ RED FLAG: Creator has multiple funding sources (potential pump)
   └─ Risk: Coordinated funding from Account A
```

### Use Case 2: Detect Funded Group

```
Token 1 Created by Creator A
├─ Funding Source: Account FUND_1 (8 transfers) 🏦
└─ Profit Destination: Account PROFIT_1 (12 transfers) 🏦

Token 2 Created by Creator B
├─ Funding Source: Account FUND_1 (6 transfers) 🏦  ← SAME!
└─ Profit Destination: Account PROFIT_2 (10 transfers) 🏦

Token 3 Created by Creator C
├─ Funding Source: Account FUND_1 (7 transfers) 🏦  ← SAME!
└─ Profit Destination: Account PROFIT_3 (8 transfers) 🏦

FINDING: Account FUND_1 funds ALL 3 creators
CONCLUSION: Coordinated pump operation with shared funding wallet
```

### Use Case 3: Detect Extraction Hub

```
Token A Created by Creator A
├─ Profit: Extract to Account HUB_X (15 transfers) 🏦

Token B Created by Creator B
├─ Profit: Extract to Account HUB_X (12 transfers) 🏦

Token C Created by Creator C
├─ Profit: Extract to Account HUB_X (18 transfers) 🏦

FINDING: Account HUB_X receives from 3 different creators
CONCLUSION: Centralized profit collection point (potential laundering)
```

---

## System State Transitions

```
┌─────────────────┐
│  Token Created  │
└────────┬────────┘
         │
         ▼
┌──────────────────────────┐
│  Status: 'waiting'       │
│  • Token added to pools  │
│  • Creator extracted     │
│  • Ready for analysis    │
└────────┬─────────────────┘
         │
         │ [Call analyze_creator_wallet]
         │
         ▼
┌──────────────────────────┐
│  Creator Analyzed        │
│  • Transactions fetched  │
│  • Transfers extracted   │
│  • Treasury detected     │
│  • Data stored to DB     │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│  Status: 'analyzed'      │
│  • Funding accounts known│
│  • Risk profile visible  │
│  • Network ready for    │
│    cross-creator compare │
└──────────────────────────┘
```

---

## Data Flow for Testing

### test_pumpswap_listener.py

```python
# Mock a token creation event
@pytest.fixture
def token_creation_event():
    return {
        'type': 'pool_created',
        'token_mint': 'EH3taj1h...',
        'creator': '6FCpd6KM...',
        'timestamp': 1234567890
    }

# Test 1: Listener detects event
def test_listener_detects_pool_creation(token_creation_event):
    listener.process_event(token_creation_event)
    assert get_token_from_db(event.token_mint)

# Test 2: Automatically trigger analysis
def test_listener_triggers_creator_analysis(token_creation_event):
    listener.process_event(token_creation_event)
    # EXPECTED: analyze_creator_wallet called
    assert creator_wallet_analyzed(event.creator)

# Test 3: Funding pattern detected
def test_listener_identifies_funding_sources(token_creation_event):
    listener.process_event(token_creation_event)
    creator = get_creator_data(event.creator)
    assert creator.incoming_treasury_count > 0
    assert creator.risk_score > LOW_RISK
```

### analyze_creator_wallet.py

```python
# Enhancement: Return structured funding data
def analyze_creator_wallet(creator_address):
    # Existing functionality...
    wallet_analysis = {
        'creator_address': creator_address,
        'funding_sources': [
            {
                'address': '0xFUND1...',
                'total_sol': 50.0,
                'transfer_count': 8,
                'is_treasury': True,
                'risk': 'HIGH'
            },
            # ...
        ],
        'profit_destinations': [
            {
                'address': '0xPROFIT1...',
                'total_sol': 45.0,
                'transfer_count': 15,
                'is_treasury': True,
                'risk': 'MEDIUM'
            },
            # ...
        ],
        'risk_profile': {
            'multiple_funding_sources': 3,
            'multiple_profit_destinations': 2,
            'rapid_extraction': False,
            'score': 65  # 0-100
        }
    }
    return wallet_analysis
```

---

## Key Questions This Answers

### For Each Token Created:
1. **Who funded the creator to build this token?**
   - Query: incoming transfers with >5 count
   - Result: List of funding sources

2. **Are multiple creators sharing the same funding source?**
   - Query: Same funding address across creators
   - Result: Coordination signals

3. **Where does the creator extract profits?**
   - Query: outgoing transfers with >5 count
   - Result: Profit destination addresses

4. **Are profits concentrated or dispersed?**
   - Query: Outgoing treasury count
   - Result: Risk assessment (single wallet = safe, many = suspicious)

5. **Is there a hub pattern?**
   - Query: Which addresses receive from multiple creators
   - Result: Potential laundering operation

---

## Summary

**The system flow:**

```
Token Created
    ↓
Listener Detects
    ↓
Extract Creator
    ↓
Analyze Creator Wallet
    ├─ Get transaction history
    ├─ Extract funding sources
    └─ Extract profit destinations
    ↓
Store to Database
    ├─ creator_wallets
    ├─ creator_sol_transfers
    └─ pools (enriched)
    ↓
Risk Analysis
    ├─ Single creator view
    └─ Multi-creator network view
    ↓
DETECT COORDINATION
    ├─ Shared funding sources
    ├─ Shared profit destinations
    └─ Aggregation hubs
```

This gives you a complete picture of **which accounts are funding token creation operations** and **where the profits are extracted to**.
