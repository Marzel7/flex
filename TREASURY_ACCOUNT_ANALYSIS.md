# Treasury Account Analysis Guide

## What is a Treasury Account?

A **treasury account** is a wallet address that has a significant, repeated relationship with a creator wallet:

- **For Incoming Transfers:** An address that regularly SENDS SOL to the creator (>5 transfers)
- **For Outgoing Transfers:** An address that regularly RECEIVES SOL from the creator (>5 transfers)

Treasury accounts are flagged with 🏦 in analysis output.

---

## Why Treasury Accounts Matter

### Incoming Treasury (Funding Sources)
Reveals **where the creator gets their money:**
- **Normal:** Single address sending consistent funding
- **Suspicious:** Multiple addresses sending funds (coordination signal)

Example:
```
Wallet A sends to Creator 6 times → Treasury funding source 🏦
```

### Outgoing Treasury (Profit Extraction)
Reveals **where the creator moves profits:**
- **Normal:** All profits go to one main wallet
- **Suspicious:** Profits dispersed to multiple wallets (mixing/washing)

Example:
```
Creator sends to Wallet B 8 times → Treasury profit destination 🏦
```

---

## Detection Algorithm

```
If transfer_count > 5:
    is_treasury = TRUE  🏦
```

Treasury is marked when:
- Same counterparty receives >5 transfers, OR
- Same counterparty sends >5 transfers

---

## Interpreting Your Data

### Current Creator Analysis

```
Creator: 6FCpd6KM...FGaA

INCOMING (Funding):
  Total addresses: 35
  Total SOL: 2.5 SOL
  Treasury accounts: 0

  → 35 different addresses send 1 transfer each
  → No repeated funding sources
  → Normal/expected pattern for new account

OUTGOING (Extraction):
  Total addresses: 0
  Total SOL: 0
  Treasury accounts: 0

  → Creator has not sent SOL anywhere
  → Accumulating received funds
```

**What This Means:**
- Creator is receiving small amounts from many different sources
- No single address is "managing" the creator (no incoming treasury)
- Creator is not extracting profits (no outgoing treasury)
- Very early stage or still receiving initial funding

---

## Analyzing Multiple Creators for Coordination

When you analyze multiple creators, treasuries reveal **connections:**

### Example: Detecting Coordination

```
Creator A:
  OUTGOING Treasury: Wallet X receives 10 transfers

Creator B:
  OUTGOING Treasury: Wallet X receives 8 transfers

Creator C:
  OUTGOING Treasury: Wallet X receives 6 transfers
```

**Finding:** All three creators send profits to the same wallet (X)
**Interpretation:** Potential coordination - they share a common treasury

### Using Network Analysis Tools

```bash
# Find all creators sending to Wallet X
python3 sol_network_analysis.py <wallet_x_address>

# Detect aggregation hubs (addresses receiving from multiple creators)
python3 sol_network_analysis.py --aggregation

# Find creator connections through shared treasuries
python3 find_creator_connections.py <creator_a_address>
```

---

## Treasury Account Patterns

### Pattern 1: Legitimate Creator
```
INCOMING:
  Wallet A: 0.5 SOL (6 transfers) 🏦

OUTGOING:
  Wallet B: 1.0 SOL (8 transfers) 🏦
```
**Interpretation:**
- Regular funding from one source
- Regular profit extraction to one destination
- Normal single-person operation

### Pattern 2: Coordinated Group
```
Creator 1 → Treasury Hub X
Creator 2 → Treasury Hub X
Creator 3 → Treasury Hub X
```
**Interpretation:**
- Multiple creators using same profit destination
- Likely coordinated operation
- Money consolidation point

### Pattern 3: Fund Washing
```
INCOMING: 10.0 SOL (various sources)
OUTGOING: 9.5 SOL to Address Y (12 transfers)
```
**Interpretation:**
- Immediate extraction (no retention)
- Money passes through quickly
- Potential wash trading

### Pattern 4: Multi-Level Coordination
```
Creator A → Treasury B → Treasury C → Main Wallet
Creator B → Treasury B → Treasury C → Main Wallet
```
**Interpretation:**
- Hierarchical fund movement
- Multiple layers of obfuscation
- Professional operation

---

## How to Query Treasury Accounts

### Find Treasury Accounts in Database

```bash
# Show all treasuries for a creator
sqlite3 pumpswap_tokens.db "
SELECT
    transfer_type,
    counterparty_address,
    total_amount,
    transfer_count
FROM creator_sol_transfers
WHERE creator_address = '6FCpd6KM...'
  AND is_treasury = 1
ORDER BY total_amount DESC;
"
```

### Find Creators with Multiple Treasuries (Coordination Signal)

```bash
sqlite3 pumpswap_tokens.db "
SELECT
    creator_address,
    transfer_type,
    COUNT(*) as treasury_count
FROM creator_sol_transfers
WHERE is_treasury = 1
GROUP BY creator_address, transfer_type
HAVING COUNT(*) > 1;
"
```

### Find Aggregation Hubs (Multi-Creator Destinations)

```bash
sqlite3 pumpswap_tokens.db "
SELECT
    counterparty_address,
    COUNT(DISTINCT creator_address) as creator_count,
    SUM(total_amount) as total_sol,
    COUNT(*) as total_transfers
FROM creator_sol_transfers
WHERE transfer_type = 'outgoing'
  AND is_treasury = 1
GROUP BY counterparty_address
HAVING creator_count > 1
ORDER BY creator_count DESC;
"
```

---

## Treasury Detection Examples

### Example 1: No Treasuries
```
Creator has 10 incoming addresses (1 transfer each)
Creator has 2 outgoing addresses (3 transfers each)

Result: 0 treasuries (nothing >5 transfers)
Meaning: Random, uncoordinated activity
```

### Example 2: Single Incoming Treasury
```
Address A sends 6 times (0.6 SOL) 🏦
9 other addresses send 1 time each

Result: 1 incoming treasury
Meaning: Reliable funding source (single manager)
```

### Example 3: Multiple Outgoing Treasuries
```
Address X receives 10 times (1.0 SOL) 🏦
Address Y receives 7 times (0.7 SOL)  🏦
5 other addresses receive 1 time each

Result: 2 outgoing treasuries
Meaning: Profits split between 2 destinations (suspicious)
```

---

## Visual Treasury Flow

### Healthy Single Creator
```
        Fund Sources
            ↓↓↓
        Creator Wallet
            ↓
        Treasury Account
```

### Suspicious: Multi-Hub Coordination
```
Creator 1 ─┐
Creator 2 ─┼→ Treasury Hub ← Creator 4
Creator 3 ─┘
            ↓
        Main Wallet
```

### Red Flag: Fund Dispersal
```
        Creator Wallet
       ↙    ↓    ↘
    Wallet A  B  C  D  E
    (multiple destinations = washing)
```

---

## Using Treasury Info for Risk Assessment

### Low Risk (Normal Activity)
```
✓ Single incoming treasury
✓ Single outgoing treasury
✓ Stable transaction patterns
✓ Holder of own tokens
```

### Medium Risk (Investigation Needed)
```
⚠ Multiple incoming treasuries
⚠ Multiple outgoing treasuries
⚠ Rapid extraction (high outgoing/incoming ratio)
⚠ Addresses change frequently
```

### High Risk (Suspicious)
```
✗ Many creators sharing same treasury
✗ Treasury addresses sending elsewhere
✗ Rapid fund dispersal
✗ Coordinated multi-treasury pattern
```

---

## Next Steps

### To Analyze Treasury Networks

1. **Collect Data**
   ```bash
   python3 analyze_creator_wallet.py <creator_1>
   python3 analyze_creator_wallet.py <creator_2>
   python3 analyze_creator_wallet.py <creator_3>
   ```

2. **Find Shared Treasuries**
   ```bash
   python3 find_creator_connections.py --network
   ```

3. **Identify Hubs**
   ```bash
   python3 sol_network_analysis.py --aggregation
   ```

4. **Investigate Suspicious Addresses**
   ```bash
   python3 sol_network_analysis.py <treasury_address>
   ```

---

## Summary

| Treasury Type | Detected By | Meaning | Risk |
|---------------|------------|---------|------|
| Incoming 🏦 | >5 deposits to creator | Funding source | Multi=⚠️ |
| Outgoing 🏦 | >5 withdrawals from creator | Profit destination | Multi=⚠️ |
| No treasuries | All transfers are 1-time | Random activity | Low |
| Shared treasury | Multiple creators→same address | Coordination | High |

---

## References

- See `INCOMING_vs_OUTGOING_GUIDE.md` for direction explanation
- See `QUICK_REFERENCE.md` for tool usage
- See `sol_network_analysis.py` for detecting hubs
- See `find_creator_connections.py` for finding creator pairs
