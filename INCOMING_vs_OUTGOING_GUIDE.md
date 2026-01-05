# SOL Transfer Direction Guide - Incoming vs Outgoing

## Quick Reference

### INCOMING Transfers
**Direction:** Other wallets → Creator wallet
**Meaning:** Creator is RECEIVING SOL
**Treasury Flag:** Address sends >5 times = 🏦 Funding source

```
Sender Address ---[SOL]---> Creator Address
```

Example:
```
dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc  (sends 0.6 SOL)
          ↓
6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA  (receives)
```

### OUTGOING Transfers
**Direction:** Creator wallet → Other wallets
**Meaning:** Creator is SENDING SOL / Extracting profits
**Treasury Flag:** Creator sends to same address >5 times = 🏦 Treasury destination

```
Creator Address ---[SOL]---> Recipient Address
```

Example:
```
6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA  (sends)
          ↓
6fcpd6kmkkrplzgtqect3yczepwykqt2a6pkp8rpfgaa  (receives 81.9733 SOL)
```

---

## Understanding the Data

### In the Database

```sql
creator_sol_transfers (
  creator_address,        -- The creator wallet being analyzed
  transfer_type,          -- 'incoming' or 'outgoing'
  counterparty_address,   -- The OTHER address involved
  total_amount,           -- Total SOL transferred
  transfer_count,         -- Number of transfers to/from this counterparty
  is_treasury             -- TRUE if >5 transfers (significant relationship)
)
```

### In Query Results

When you run `query_creator_wallets.py <creator_address>`:

**INCOMING TRANSFERS Section:**
- Shows addresses that **SEND SOL TO** the creator
- Represents funding sources
- **Treasury flag (🏦)** = This address regularly funds the creator (>5 transfers)

**OUTGOING TRANSFERS Section:**
- Shows addresses that **RECEIVE SOL FROM** the creator
- Represents where profits/funds are moved
- **Treasury flag (🏦)** = Creator regularly sends to this address (profit extraction point)

---

## Real Examples

### Example: Creator with Incoming Treasury
```
INCOMING TRANSFERS:
  Address A: 10.5 SOL (6 transfers)  🏦 Treasury
  Address B: 2.3 SOL (2 transfers)
```

**Interpretation:**
- Address A is a major funding source
- Creator receives regular deposits from Address A
- Likely a partner wallet or fund manager

### Example: Creator with Outgoing Treasury
```
OUTGOING TRANSFERS:
  Address C: 50.0 SOL (8 transfers)  🏦 Treasury
  Address D: 5.5 SOL (2 transfers)
```

**Interpretation:**
- Address C receives most of creator's profits
- Creator regularly extracts/moves funds to Address C
- Likely a main wallet or profit collection address
- Creator moved ~50 SOL out across 8 transactions

---

## Risk Assessment with Treasury Accounts

### Suspicious Patterns

**Red Flag #1: Multiple Incoming Treasuries**
```
INCOMING:
  Wallet A: 100 SOL (10 transfers) 🏦
  Wallet B: 80 SOL (8 transfers)   🏦
  Wallet C: 60 SOL (6 transfers)   🏦
```
→ Creator receiving funds from multiple sources
→ Potential coordination or multi-wallet operation

**Red Flag #2: Rapid Extraction Pattern**
```
INCOMING: 1000 SOL (various sources)
OUTGOING: 950 SOL to single address (8+ transfers)
```
→ Creator immediately extracts/moves funds elsewhere
→ Suggests pump-and-dump coordination or washing

**Red Flag #3: Treasury Aggregation Hub**
```
OUTGOING Treasury: Address X receives from 3+ creators
```
→ Multiple creators sending to same address
→ Potential money laundering or profit consolidation

---

## How to Interpret Real Output

### Your Data Shows:

```
SOL TRANSFER ANALYSIS
Total SOL received: 4.5004 SOL
Total SOL sent out: 0.0000 SOL
Net SOL position: +4.5004 SOL
```

**Meaning:**
- Creator received 4.5 SOL total from incoming transfers
- Creator sent out 0 SOL (no outgoing transfers recorded)
- Creator is a net receiver (accumulated funds, not extracting)

### Treasury Flag Explanation:

When you see:
```
6fcpd6kmkkrplzgtqect3yczepwykqt2a6pkp8rpfgaa | 81.9733 | 15 | 🏦 Treasury
```

This means:
- This is an OUTGOING transfer destination
- Creator sent 81.9733 SOL total to this address
- Across 15 separate transactions
- >5 transfers = flagged as treasury destination
- Creator uses this address to extract profits/move funds

---

## Visual Representation

### Fund Flow Diagram

```
External Wallets (Funding)
    ↓  ↓  ↓
    └→ Creator Wallet ←┘
       ↓
    Treasury Addresses (Profit Extraction)
```

### Key Metrics

| Metric | What It Shows | Red Flag |
|--------|---------------|----------|
| Many incoming treasuries | Multiple funding sources | Yes (coordination) |
| Large incoming, no outgoing | Accumulating funds | No (normal) |
| Small incoming, large outgoing | Immediate extraction | Yes (wash) |
| Single outgoing treasury | Profit consolidation | Maybe (depends on size) |
| Multiple outgoing treasuries | Fund dispersal/mixing | Yes (possibly laundering) |

---

## How Treasuries Are Identified

### Algorithm

```python
if transfer_count > 5:
    is_treasury = True  # 🏦
else:
    is_treasury = False
```

### Why 5?

- **Less than 5:** Random one-time transfers
- **5+ transfers:** Indicates intentional, repeated relationship
- Suggests wallet is "important" to the creator
- Worth investigating for patterns

---

## Database Queries to Analyze Treasuries

### Find All Treasury Accounts
```sql
SELECT
    creator_address,
    transfer_type,
    counterparty_address,
    total_amount,
    transfer_count
FROM creator_sol_transfers
WHERE is_treasury = 1
ORDER BY transfer_count DESC;
```

### Find Creators with Multiple Outgoing Treasuries
```sql
SELECT creator_address, COUNT(*) as treasury_count
FROM creator_sol_transfers
WHERE transfer_type = 'outgoing' AND is_treasury = 1
GROUP BY creator_address
HAVING COUNT(*) > 1;
```

### Find Aggregation Hubs (Addresses receiving from many creators)
```sql
SELECT
    counterparty_address,
    COUNT(DISTINCT creator_address) as creator_count,
    SUM(total_amount) as total_sol
FROM creator_sol_transfers
WHERE transfer_type = 'outgoing' AND is_treasury = 1
GROUP BY counterparty_address
HAVING creator_count > 1
ORDER BY creator_count DESC;
```

---

## Summary

| Aspect | Incoming | Outgoing |
|--------|----------|----------|
| Direction | → Creator | Creator → |
| Meaning | Funding | Extraction |
| Treasury (🏦) | Funding source | Profit destination |
| Red flag if | Multiple | Rapid/large |
| Investigated via | Where funds come from | Where profits go |

**Key Takeaway:** Track both directions to understand creator behavior and detect coordination with other wallets.
