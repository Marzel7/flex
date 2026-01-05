# Multi-Token Funding Account Tracking System

## Overview

Display when a funding account is reused across multiple tokens, showing:
- Which tokens are funded by the same account
- Pattern of token launches
- Risk assessment for coordinated pumps

```
Token 1: SHITCOIN
  Funding: Account A (6 transfers, 0.6 SOL)
  └─ 🚩 Account A also funds: Token 2, Token 3, Token 4 (4 tokens total)

Token 2: BADTOKEN
  Funding: Account A (5 transfers, 0.5 SOL)
  └─ 🚩 Account A also funds: Token 1, Token 3, Token 4 (4 tokens total)

Token 3: PUMP
  Funding: Account A (7 transfers, 0.7 SOL)
  └─ 🚩 Account A also funds: Token 1, Token 2, Token 4 (4 tokens total)

Token 4: MOON
  Funding: Account A (6 transfers, 0.6 SOL)
  └─ 🚩 Account A also funds: Token 1, Token 2, Token 3 (4 tokens total)

FINDING: Account A funds ALL 4 tokens
RISK: CRITICAL - Coordinated pump group using same funding account
```

---

## Database Schema

### Existing Tables Used:
- `pools` - Token information
- `creator_sol_transfers` - SOL transfer relationships

### New View/Query: Token-Funding-Account Mapping

```sql
-- Bridge table concept (created via query, not stored):
SELECT
  p.base_mint as token_mint,
  p.pumpfun_creator as creator_address,
  cst.counterparty_address as funding_account,
  cst.transfer_count,
  cst.total_amount,
  cst.is_treasury,
  p.first_seen as token_launch_date
FROM pools p
JOIN creator_sol_transfers cst ON p.pumpfun_creator = cst.creator_address
WHERE cst.transfer_type = 'incoming'
ORDER BY cst.counterparty_address, p.first_seen;
```

### Cache Table (Optional): token_funding_accounts

```sql
CREATE TABLE token_funding_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    creator_address TEXT NOT NULL,
    funding_account TEXT NOT NULL,
    transfer_count INTEGER,
    total_amount REAL,
    is_treasury BOOLEAN,
    token_launch_date TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_mint) REFERENCES pools(base_mint)
);

CREATE INDEX idx_funding_account ON token_funding_accounts(funding_account);
CREATE INDEX idx_token_mint ON token_funding_accounts(token_mint);
```

---

## Query: Find Funding Account Reuse

### Query 1: Get All Tokens Funded by Specific Account

```sql
-- Show all tokens funded by Account X
SELECT
  token_mint,
  creator_address,
  funding_account,
  transfer_count,
  total_amount,
  token_launch_date,
  is_treasury
FROM (
  SELECT
    p.base_mint as token_mint,
    p.pumpfun_creator as creator_address,
    cst.counterparty_address as funding_account,
    cst.transfer_count,
    cst.total_amount,
    p.first_seen as token_launch_date,
    cst.is_treasury
  FROM pools p
  JOIN creator_sol_transfers cst ON p.pumpfun_creator = cst.creator_address
  WHERE cst.transfer_type = 'incoming'
) t
WHERE funding_account = '<funding_account>'
ORDER BY token_launch_date DESC;
```

**Result:** All tokens funded by that account

### Query 2: Get Token-to-Funding-Account Multiplicity

```sql
-- Show how many tokens each funding account funds
SELECT
  funding_account,
  COUNT(DISTINCT token_mint) as token_count,
  COUNT(DISTINCT creator_address) as creator_count,
  SUM(total_amount) as total_sol_distributed,
  MIN(token_launch_date) as first_token_date,
  MAX(token_launch_date) as latest_token_date,
  GROUP_CONCAT(DISTINCT token_mint) as tokens,
  GROUP_CONCAT(DISTINCT creator_address) as creators
FROM (
  SELECT
    p.base_mint as token_mint,
    p.pumpfun_creator as creator_address,
    cst.counterparty_address as funding_account,
    cst.total_amount,
    p.first_seen as token_launch_date,
    cst.is_treasury
  FROM pools p
  JOIN creator_sol_transfers cst ON p.pumpfun_creator = cst.creator_address
  WHERE cst.transfer_type = 'incoming'
  AND cst.is_treasury = 1  -- Only treasury-level funding
) t
GROUP BY funding_account
HAVING token_count > 1  -- Only accounts funding multiple tokens
ORDER BY token_count DESC;
```

**Result:** Accounts funding multiple tokens (suspicious patterns)

### Query 3: For New Token, Show Reuse of Its Funding Accounts

```sql
-- When token X is created by Creator Y:
-- Show all OTHER tokens funded by the same funding accounts

SELECT
  '<new_token>' as this_token,
  funding_account,
  transfer_count as transfers_to_this_token,
  total_amount as sol_to_this_token,
  COUNT(DISTINCT CASE WHEN token_mint != '<new_token>' THEN token_mint END) as other_tokens_count,
  GROUP_CONCAT(DISTINCT CASE WHEN token_mint != '<new_token>' THEN token_mint END) as other_tokens
FROM (
  SELECT
    p.base_mint as token_mint,
    p.pumpfun_creator as creator_address,
    cst.counterparty_address as funding_account,
    cst.transfer_count,
    cst.total_amount,
    p.first_seen as token_launch_date
  FROM pools p
  JOIN creator_sol_transfers cst ON p.pumpfun_creator = cst.creator_address
  WHERE cst.transfer_type = 'incoming'
) t
WHERE funding_account IN (
  -- Get all funding accounts of the new creator
  SELECT cst.counterparty_address
  FROM creator_sol_transfers cst
  WHERE cst.creator_address = '<new_creator>'
  AND cst.transfer_type = 'incoming'
)
GROUP BY funding_account
ORDER BY other_tokens_count DESC;
```

**Result:** For each funding account, shows how many OTHER tokens it also funded

---

## Data Structure: Token-Funding Summary

```python
{
  'token_mint': 'EH3taj1h...',
  'creator_address': '6FCpd6KM...',
  'funding_accounts': [
    {
      'address': 'dnd5bzqm...',
      'transfers_to_this_token': 6,
      'sol_to_this_token': 0.6,
      'is_treasury': True,

      # REUSE INFO - NEW
      'reused_across_tokens': [
        {
          'token_mint': 'SHITCOIN',
          'creator': 'CreatorA',
          'transfers': 5,
          'sol': 0.5,
          'launch_date': '2026-01-04 10:00:00'
        },
        {
          'token_mint': 'BADTOKEN',
          'creator': 'CreatorB',
          'transfers': 7,
          'sol': 0.7,
          'launch_date': '2026-01-05 08:30:00'
        },
        {
          'token_mint': 'PUMP',
          'creator': 'CreatorC',
          'transfers': 4,
          'sol': 0.4,
          'launch_date': '2026-01-05 12:15:00'
        }
      ],
      'total_reuse': 3,  # Funds this token + 3 others = 4 tokens
      'risk_level': 'CRITICAL'
    },
    # ... other funding accounts
  ],
  'overall_risk': 'CRITICAL',
  'funding_pattern': 'COORDINATED_GROUP'
}
```

---

## Implementation in analyze_creator_wallet.py

### Function: Get Token Funding History

```python
def get_funding_account_token_history(funding_account):
    """
    Get all tokens funded by this account

    Returns:
    [
      {
        'token_mint': 'ABC123...',
        'creator': '0xCREATOR1...',
        'transfers': 6,
        'sol': 0.6,
        'treasury': True,
        'launch_date': timestamp
      },
      ...
    ]
    """
    query = """
    SELECT
      p.base_mint,
      p.pumpfun_creator,
      cst.transfer_count,
      cst.total_amount,
      cst.is_treasury,
      p.first_seen
    FROM pools p
    JOIN creator_sol_transfers cst ON p.pumpfun_creator = cst.creator_address
    WHERE cst.counterparty_address = ?
    AND cst.transfer_type = 'incoming'
    ORDER BY p.first_seen DESC
    """

    results = db.query(query, (funding_account,))

    return [
        {
            'token_mint': r['base_mint'],
            'creator': r['pumpfun_creator'],
            'transfers': r['transfer_count'],
            'sol': r['total_amount'],
            'treasury': r['is_treasury'],
            'launch_date': r['first_seen']
        }
        for r in results
    ]


def analyze_creator_with_funding_reuse(creator_address):
    """
    Analyze creator and show funding account reuse
    """

    # Get creator's funding accounts
    funding_accounts = query_incoming_transfers(creator_address)

    enriched_funders = []

    for funder in funding_accounts:
        funder_data = {
            'address': funder['address'],
            'transfers': funder['transfer_count'],
            'sol': funder['total_amount'],
            'treasury': funder['is_treasury']
        }

        # NEW: Get what else this funding account has done
        history = get_funding_account_token_history(funder['address'])

        # Filter to OTHER tokens (not this creator's token)
        other_tokens = [
            h for h in history
            if h['creator'] != creator_address
        ]

        funder_data['reused_across_tokens'] = other_tokens
        funder_data['total_reuse_count'] = len(other_tokens)
        funder_data['reuse_risk'] = calculate_reuse_risk(len(other_tokens))

        enriched_funders.append(funder_data)

    return {
        'creator': creator_address,
        'funding_accounts': enriched_funders,
        'coordination_score': calculate_coordination_score(enriched_funders)
    }
```

### Function: Calculate Reuse Risk

```python
def calculate_reuse_risk(reuse_count):
    """
    Determine risk level based on how many tokens this account funds

    0-1 tokens: LOW (only this creator)
    2-3 tokens: MEDIUM (coordinated group)
    4-6 tokens: HIGH (organized operation)
    7+ tokens: CRITICAL (professional pump network)
    """
    if reuse_count == 0:
        return 'LOW'
    elif reuse_count <= 2:
        return 'MEDIUM'
    elif reuse_count <= 5:
        return 'HIGH'
    else:
        return 'CRITICAL'
```

---

## Display Format: Multi-Level Tables

### Level 1: Token Funding Summary (in test_pumpswap_listener.py output)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ NEW TOKEN: SHITCOIN  |  Creator: 6FCpd6KM...  |  Time: 2026-01-05 14:00:00  │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ FUNDING ANALYSIS                                                             │
│                                                                              │
│ Funding Account          │ Transfers │ SOL  │ Treasury │ Reused By │ Risk   │
│────────────────────────────────────────────────────────────────────────────────
│ dnd5bzqm...              │ 6         │ 0.6  │ 🏦       │ 🚩 3x    │ HIGH   │
│ 9zz1mp5b...              │ 6         │ 0.6  │ 🏦       │ ✓ 1x    │ MEDIUM │
│ 4tsuj32y...              │ 6         │ 0.0  │ 🏦       │ 🚩 5x    │ CRITICAL│
│                                                                              │
│ Overall Risk: 🚨 CRITICAL                                                   │
│ Pattern: Coordinated pump group                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Level 2: Expand Funding Account to See Reuse

When analyzing `dnd5bzqm...`:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ FUNDING ACCOUNT: dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc              │
│ Funds 4 tokens across 3 creators                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Tokens Funded by This Account (across all creators):                        │
│                                                                              │
│ Token          │ Creator  │ Transfers │ SOL  │ Launch Time          │ Status │
│─────────────────────────────────────────────────────────────────────────────
│ SHITCOIN (NEW) │ 6FCpd6KM │ 6         │ 0.6  │ 2026-01-05 14:00:00 │ 🆕    │
│ BADTOKEN       │ CreatorA │ 5         │ 0.5  │ 2026-01-04 10:30:00 │ ✓     │
│ PUMP           │ CreatorB │ 7         │ 0.7  │ 2026-01-03 22:15:00 │ ✓     │
│ MOON           │ CreatorC │ 4         │ 0.4  │ 2026-01-02 18:45:00 │ ✓     │
│                                                                              │
│ Summary:                                                                     │
│ • Total tokens funded: 4                                                    │
│ • Total creators: 3                                                         │
│ • Total SOL distributed: 2.2                                                │
│ • Time span: 3 days (2026-01-02 to 2026-01-05)                              │
│ • Launch frequency: Every 6-12 hours                                        │
│                                                                              │
│ FINDING: 🚨 CRITICAL                                                        │
│ This account funds 4 different token launches in rapid succession            │
│ Pattern matches professional pump & dump operation                          │
│                                                                              │
│ Next: Check where THIS account gets its SOL...                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Level 3: Trace Upstream

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ UPSTREAM FUNDING SOURCE: dnd5bzqm...                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ This funding account receives from:                                          │
│                                                                              │
│ Source                   │ SOL    │ Transfers │ Type     │ Connected To    │
│─────────────────────────────────────────────────────────────────────────────
│ DPqsobysNf5iA9w...       │ 15.0   │ 12        │ 🏦 Treas │ 🏛️ Coinbase    │
│ Other_Treasury_Hub...    │ 8.5    │ 8         │ 🏦 Treas │ 🏛️ LIKELY CEX │
│                                                                              │
│ Upstream Analysis:                                                           │
│ ├─ Gets 23.5 SOL from CEX sources                                           │
│ ├─ Distributes 2.2 SOL to 4 token creators                                  │
│ ├─ Holds 21.3 SOL (accumulator)                                             │
│ └─ Role: DISTRIBUTION HUB                                                   │
│                                                                              │
│ NETWORK CHAIN:                                                              │
│ Coinbase → dnd5bzqm... → [SHITCOIN, BADTOKEN, PUMP, MOON]                  │
│                                                                              │
│ RISK: 🚨 PROFESSIONAL PUMP OPERATION                                        │
│ Coinbase-funded, orchestrated token launches every 6-12 hours               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Test Cases: test_pumpswap_listener.py

### Test 1: Detect Funding Account Reuse

```python
def test_listener_detects_funding_account_reuse_on_new_token():
    """
    When token 4 launches:
    - Its creator uses funding_account_A
    - That account also funded tokens 1, 2, 3
    - Should flag reuse
    """

    # Setup: Pre-populate database with tokens 1-3 funded by account_A
    setup_test_tokens_with_shared_funding()

    # New token 4 launches with same funding account
    event = create_pool_event(
        token='TOKEN4',
        creator='Creator4',
        funding_account='account_A'  # SHARED
    )

    # Analyze
    result = analyze_listener_event(event)

    # Assertions
    assert result['funding_accounts'][0]['total_reuse_count'] == 3
    assert result['funding_accounts'][0]['reuse_risk'] == 'CRITICAL'
    assert 'TOKEN1' in result['funding_accounts'][0]['reused_across_tokens']
    assert 'TOKEN2' in result['funding_accounts'][0]['reused_across_tokens']
    assert 'TOKEN3' in result['funding_accounts'][0]['reused_across_tokens']

def test_listener_shows_token_launch_timeline():
    """Verify we display when each token was launched"""
    result = analyze_creator_with_funding_reuse(creator)

    for funder in result['funding_accounts']:
        for reused_token in funder['reused_across_tokens']:
            assert 'launch_date' in reused_token
            assert isinstance(reused_token['launch_date'], datetime)

def test_listener_calculates_launch_frequency():
    """
    For funding account funding multiple tokens:
    - Calculate time between launches
    - Flag if rapid launches (suspicious)
    """

    # Set up 4 tokens launched within 2 days
    setup_rapid_launches()

    result = analyze_funding_account_pattern()

    assert result['launch_frequency'] == 'RAPID'  # Every 6-12 hours
    assert result['risk_level'] == 'CRITICAL'

def test_listener_identifies_coordination_network():
    """
    Show the network of creators sharing same funding
    """

    result = analyze_funding_account_reuse()

    # Should show:
    # - Token 1 (Creator A)
    # - Token 2 (Creator B)  <- Different creator
    # - Token 3 (Creator C)  <- Different creator
    # All funded by same account

    creators = set()
    for token in result['reused_tokens']:
        creators.add(token['creator'])

    assert len(creators) > 1  # Multiple different creators

def test_listener_table_output_format():
    """Verify table includes reuse information"""

    output = get_listener_table_output()

    # Should contain:
    # - Token name
    # - Creator address
    # - Funding accounts
    # - Transfer counts
    # - SOL amounts
    # - 🚩 Reuse indicators
    # - Risk levels

    assert 'Reused' in output or '🚩' in output
    assert 'CRITICAL' in output or 'HIGH' in output
```

---

## Console Output Example

```
═══════════════════════════════════════════════════════════════════════════════
NEW TOKEN DETECTED: SHITCOIN
═══════════════════════════════════════════════════════════════════════════════

Creator: 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA
Timestamp: 2026-01-05 14:00:00

[STEP 1] Analyzing Creator's Funding Accounts...
✓ Found 3 funding sources

[STEP 2] Checking Funding Account Reuse...
┌─────────────────────────────────────────────────────────────────────────────┐
│ FUNDING ACCOUNT: dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc              │
├─────────────────────────────────────────────────────────────────────────────┤
│ Funds THIS token:     6 transfers, 0.6 SOL                                  │
│ 🚩 ALSO FUNDS:       3 other tokens                                         │
│                                                                              │
│ Timeline of this account's activity:                                        │
│ • 2026-01-02 18:45 → MOON (Creator C)      [4 transfers, 0.4 SOL]          │
│ • 2026-01-03 22:15 → PUMP (Creator B)      [7 transfers, 0.7 SOL]          │
│ • 2026-01-04 10:30 → BADTOKEN (Creator A)  [5 transfers, 0.5 SOL]          │
│ • 2026-01-05 14:00 → SHITCOIN (Creator X)  [6 transfers, 0.6 SOL] 🆕      │
│                                                                              │
│ Launch Frequency: Every 6-12 hours                                          │
│ Total tokens funded: 4                                                      │
│ Total creators: 4 (ALL DIFFERENT)                                           │
│ Total SOL distributed: 2.2                                                  │
│ Risk: 🚨 CRITICAL - Professional pump operation                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ FUNDING ACCOUNT: 9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g              │
├─────────────────────────────────────────────────────────────────────────────┤
│ Funds THIS token:     6 transfers, 0.6 SOL                                  │
│ ✓ ONLY funds this creator (not reused)                                     │
│ Risk: LOW - Appears to be dedicated account                                 │
└───────────────────────────────────────────────────────���─────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ FUNDING ACCOUNT: 4tsuj32yitzpk3gvw9erhugdqfminsmxy6s59u3nnwdn              │
├─────────────────────────────────────────────────────────────────────────────┤
│ Funds THIS token:     6 transfers, 0.0 SOL                                  │
│ 🚩 ALSO FUNDS:       5 other tokens                                         │
│ Risk: 🚨 CRITICAL - Major distribution hub                                  │
└─────────────────────────────────────────────────────────────────────────────┘

[STEP 3] Tracing Upstream Sources...
═══════════════════════════════════════════════════════════════════════════════
FUNDING NETWORK DIAGRAM:

                    ┌─────────────────────┐
                    │ COINBASE            │
                    │ DPqsoby...          │
                    │ (100+ SOL)          │
                    └──────────┬──────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
        ┌───────▼────────┐          ┌─────────▼────────┐
        │ Distribution   │          │ Treasury Hub 2   │
        │ Hub 1          │          │ (Other CEX)      │
        │ dnd5bzqm...    │          │ (50+ SOL)        │
        │ (23.5 SOL)     │          │                  │
        └───────┬────────┘          └──────────────────┘
                │
        ┌───────┴────────┬────────────┬────────────┐
        │                │            │            │
   ┌────▼────┐      ┌───▼───┐   ┌───▼───┐   ┌───▼───┐
   │ Creator │      │Creator│   │Creator│   │Creator│
   │   A     │      │  B    │   │  C    │   │  X    │
   └────┬────┘      └───┬───┘   └───┬───┘   └───┬───┘
        │                │           │           │
   ┌────▼────┐      ┌───▼───┐   ┌───▼───┐   ┌───▼───┐
   │BADTOKEN │      │ PUMP  │   │ MOON  │   │SHITCOIN
   │Launched │      │Launched   │Launched   │Launched
   │2026-0104│      │2026-01-03 │2026-01-02 │2026-01-05
   └─────────┘      └───────┘   └───────┘   └───────┘

═══════════════════════════════════════════════════════════════════════════════
RISK ASSESSMENT: 🚨 CRITICAL 🚨
═══════════════════════════════════════════════════════════════════════════════

Pattern: COORDINATED PUMP OPERATION
├─ Funding source: Coinbase (CEX)
├─ Distribution: Via intermediary hubs
├─ Recipients: 4 different creators (no overlap)
├─ Timeline: Rapid launches (every 6-12 hours)
├─ Total capital: 100+ SOL deployed
└─ Network scale: Professional operation

Indicators of Coordination:
✓ Same funding account across multiple token launches
✓ Different creators (obfuscation)
✓ Rapid launch schedule
✓ CEX-sourced capital
✓ Distribution through hubs (money laundering pattern)

Recommendation: AVOID / REPORT TO AUTHORITIES

Token Status: 🚨 HIGH RISK - DO NOT BUY
═══════════════════════════════════════════════════════════════════════════════
```

---

## Summary

This implementation tracks:

1. **Funding account reuse** - Which accounts fund multiple tokens
2. **Creator separation** - Even if different creators, same funding = coordination
3. **Timeline** - Launch frequency and patterns
4. **Upstream sources** - Where the funding accounts get their SOL (CEX)
5. **Network visualization** - Complete money flow diagram

The key insight: **Same funding account → Multiple different creators → Coordinated pump group**

This appears in both:
- **analyze_creator_wallet.py** - For detailed analysis of a specific creator
- **test_pumpswap_listener.py** - For real-time detection when tokens launch
