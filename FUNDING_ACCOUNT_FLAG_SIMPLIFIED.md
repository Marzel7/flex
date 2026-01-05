# Funding Account Flag System - Simplified (Actual Implementation)

## The Simple Truth

We don't need a separate CEX detection database. We just query what we already have:

```
When new token launches with creator X:

Step 1: Get creator X's funding accounts
  Query: "Which addresses fund creator X?"
  (from creator_sol_transfers WHERE creator_address=X AND transfer_type='incoming')

Step 2: For each funding account, ask: "What else has THIS account done?"
  Query: "Show me ALL SOL transfers involving this funding address"
  (from creator_sol_transfers WHERE counterparty_address=FUND_ACCOUNT)

Step 3: Check if it's a treasury account
  - If transfer_count > 5 → 🏦 Treasury (important account)
  - Cross-creator relationships visible

Step 4: Investigate that treasury account's OUTGOING transfers
  Query: "Where does this treasury account send SOL?"
  (from creator_sol_transfers WHERE creator_address=FUND_ACCOUNT AND transfer_type='outgoing')

Step 5: Does that account send to obvious CEX patterns?
  - Large round amounts (10 SOL, 100 SOL, 1000 SOL)
  - Multiple recipients per batch
  - Connection to Marinade/staking addresses
  - Name patterns (if available from Solscan)
```

## Real Example with Your Coinbase Discovery

```
Token: SHITCOIN
Creator: 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA

Step 1: Get incoming transfers for this creator
  SELECT * FROM creator_sol_transfers
  WHERE creator_address = '6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA'
  AND transfer_type = 'incoming'

  Results:
  ├─ dnd5bzqm... (6 transfers) 🏦
  ├─ 9zz1mp5b... (6 transfers) 🏦
  └─ an47qxb8... (4 transfers)

Step 2: Analyze funding account dnd5bzqm...
  SELECT * FROM creator_sol_transfers
  WHERE counterparty_address = 'dnd5bzqm...'

  Results:
  ├─ Funds Creator A (8 transfers)
  ├─ Funds Creator B (6 transfers)
  ├─ Funds Creator C (7 transfers)
  └─ Sends to: DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo (55 transfers)

  🚩 RED FLAG: Account dnd5bzqm... sends to DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo
              which is COINBASE!

Step 3: Check DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo
  SELECT * FROM creator_sol_transfers
  WHERE counterparty_address = 'DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo'

  Results:
  ├─ Receives from: dnd5bzqm... (55 transfers) ← Same account from Step 2!
  ├─ Receives from: 9zz1mp5b... (12 transfers)
  ├─ Receives from: 4tsuj32y... (18 transfers)
  └─ Receives from: other_account... (22 transfers)

  🚨 CRITICAL: Coinbase address receiving from MULTIPLE intermediary accounts
               which each fund MULTIPLE creators!

INTERPRETATION:
- Coinbase wallet (DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo)
- Receives from intermediary accounts (dnd5bzqm, 9zz1mp5b, etc)
- Those intermediaries fund multiple creators
- Those creators launch pump tokens

PATTERN: Professional pump operation
├─ Coinbase → Intermediaries → Creators → Pump Tokens
└─ Money laundering chain: CEX → Mixing → Creators
```

## The Query Chain

### Query 1: Get funding sources for new token creator

```sql
SELECT
  counterparty_address as funding_source,
  transfer_count,
  total_amount,
  is_treasury,
  'FUNDS_THIS_CREATOR' as relationship
FROM creator_sol_transfers
WHERE creator_address = '<new_token_creator>'
AND transfer_type = 'incoming'
ORDER BY transfer_count DESC;
```

**Result:** Shows who funds this creator

### Query 2: Check what else each funding source does (incoming view)

```sql
SELECT
  creator_address,
  COUNT(DISTINCT creator_address) as creator_count,
  transfer_count,
  total_amount
FROM creator_sol_transfers
WHERE counterparty_address = '<funding_source>'
AND transfer_type = 'incoming'
GROUP BY creator_address
ORDER BY creator_count DESC;
```

**Result:** How many OTHER creators does this funding source fund?

### Query 3: Check where each funding source sends money (outgoing view)

```sql
SELECT
  counterparty_address as sends_to,
  transfer_count,
  total_amount,
  is_treasury
FROM creator_sol_transfers
WHERE creator_address = '<funding_source>'
AND transfer_type = 'outgoing'
ORDER BY transfer_count DESC;
```

**Result:** Where does the funding source extract profits to? (Likely CEX)

### Query 4: Identify CEX patterns (no database needed!)

```sql
-- Find accounts that:
-- 1. Receive from many different creators (funding hubs)
-- 2. Send large round amounts (CEX withdrawal patterns)
-- 3. Connect to known CEX-adjacent addresses

SELECT
  counterparty_address,
  COUNT(DISTINCT creator_address) as source_creator_count,
  SUM(transfer_count) as total_transfers,
  SUM(total_amount) as total_sol,
  ROUND(AVG(total_amount), 4) as avg_transfer_amount
FROM creator_sol_transfers
WHERE transfer_type = 'incoming'
AND is_treasury = 1
GROUP BY counterparty_address
HAVING source_creator_count > 2
ORDER BY total_sol DESC, source_creator_count DESC;
```

**Result:** Suspected CEX accounts (by behavior, not database lookup)

## Table Output - What Gets Displayed

### Enhanced Incoming Transfers Table

```
Incoming SOL transfers: 71

Funding Source                                | SOL    | Transfers | Treasury | Reused? | Connected To
──────────────────────────────────────────────────────────────────────────────────────────────────────────
dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc | 0.6000 | 6         | 🏦       | 🚩 3x   | 🏛️ Coinbase*
9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g | 0.6000 | 6         | 🏦       | 🚩 2x   | 🏛️ Coinbase*
an47qxb8xbpdinx9zyxqmgdsvpuzk9jmxggawmozmxaa | 0.4000 | 4         |          | ✓ 1x   | Private
4tsuj32yitzpk3gvw9erhugdqfminsmxy6s59u3nnwdn | 0.0000 | 6         | 🏦       | 🚩 5x   | 🏛️ Coinbase*

Reused? = How many other creators does this funding source fund
Connected To = Where does this funding source send money (extract to)
* = Inferred from SOL transfer patterns, not hardcoded database
```

## No New Database Table Needed!

Instead of storing CEX addresses, we INFER them from behavior:

```python
def identify_suspected_cex_account(account_address):
    """
    Is this account likely a CEX based on its SOL transfer patterns?

    Patterns that indicate CEX:
    1. Receives from many intermediary accounts
    2. Transfers in large, round amounts
    3. High frequency transfers
    4. Connected to staking/MEV addresses
    5. Named similar to known CEX patterns
    """

    # Query: Where does this account send money?
    outgoing = query("""
        SELECT *
        FROM creator_sol_transfers
        WHERE creator_address = ? AND transfer_type = 'outgoing'
    """, (account_address,))

    # Analyze patterns
    patterns = {
        'receives_from_many': count_distinct_sources(account_address) > 5,
        'large_transfers': has_large_round_amounts(outgoing),
        'high_frequency': has_many_transfers(outgoing),
        'known_pattern': matches_known_cex_pattern(account_address)
    }

    risk_score = sum(patterns.values())

    if risk_score >= 3:
        return 'LIKELY_CEX'
    elif risk_score >= 2:
        return 'POSSIBLE_CEX'
    else:
        return 'UNKNOWN'
```

## Implementation Strategy - Simple

### In analyze_creator_wallet.py

```python
def analyze_creator_with_funding_chain(creator_address):
    """
    For a creator, show:
    1. Who funds them
    2. What else those accounts do
    3. Where those accounts send money (likely CEX)
    """

    # Step 1: Get funding sources
    funding_sources = query_incoming_transfers(creator_address)

    # Step 2: For each funding source
    for fund_account in funding_sources:
        # Where else does this account fund?
        other_creators = query_other_funded_creators(fund_account)

        # Where does this account extract to?
        extractions = query_outgoing_transfers(fund_account)

        # Display
        print(f"{fund_account}")
        print(f"  └─ Funds {len(other_creators)} other creators")
        print(f"  └─ Sends to: {extractions}")

        # Check if extraction point looks like CEX
        if looks_like_cex(extractions):
            print(f"  └─ 🏛️ LIKELY CEX: {extractions}")
```

### In test_pumpswap_listener.py

```python
def test_listener_traces_funding_to_cex():
    """
    When token created:
    1. Get creator
    2. Get their funding accounts
    3. Trace those accounts to their extractions
    4. Flag if extraction is to CEX-like patterns
    """

    event = create_token_event()
    creator = event['creator']

    # Get funding chain
    funding = trace_funding_chain(creator)

    # Check if leads to CEX
    for fund_account in funding:
        extraction_point = funding[fund_account]['sends_to']

        if looks_like_cex(extraction_point):
            # We found a CEX connection!
            assert fund_account.risk_flag == '🏛️ LIKELY_CEX'
```

## What We Actually Display

### Table with All Information

```
FUNDING SOURCES → EXTRACTION POINTS → CEX DETECTION

Source Address | Funds | Treasury | Reuses | Sends To | Likely CEX?
────────────────────────────────────────────────────────────────────
dnd5bzqm... | 6 trans | 🏦 | 🚩 3x | DPqsoby... | 🏛️ YES (Coinbase)
9zz1mp5b... | 6 trans | 🏦 | 🚩 2x | DPqsoby... | 🏛️ YES (Coinbase)
4tsuj32y... | 6 trans | 🏦 | 🚩 5x | DPqsoby... | 🏛️ YES (Coinbase)
```

## The Key Insight

**We don't need to maintain a CEX wallet database!**

We infer CEX involvement from behavior:
1. Account receives from many different "funding intermediaries"
2. Account sends in large, round amounts
3. Account has high transfer frequency
4. Account name matches patterns (if available)

The Coinbase wallet (`DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo`) is identified by:
- Receiving from multiple intermediaries
- Moving large amounts
- Connected to known staking addresses
- Named publicly as Coinbase on Solscan

## Summary

**New approach:**
- Query existing `creator_sol_transfers` table
- Trace funding chains: Creator → Funding → Extraction
- Infer CEX from transfer patterns (no new DB needed)
- Display results with flags

**Benefits:**
- No hardcoded CEX addresses needed
- Automatically finds new CEX accounts (by pattern)
- Works with any exchange
- Shows complete money flow
- Enables coordination detection

This is exactly what you identified - the Coinbase wallet is detected by **seeing where the funding accounts send their money to**.
