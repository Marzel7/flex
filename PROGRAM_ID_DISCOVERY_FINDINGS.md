# Program ID Discovery - Findings from Real Token Analysis

## Status: ✅ DISCOVERY IN PROGRESS

**Date**: 2026-01-27
**Token Tested**: `62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump`

---

## Key Findings

### Oldest Transaction #1 Programs
```
[CREATOR] Oldest tx #1: 2haZVG8x1CbtYM15... | Programs:
  - ComputeBudget111111111111111111111111111111 (System)
  - ComputeBudget111111111111111111111111111111 (System)
  - FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9 (Unknown - FLASHX?)
  - FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9 (Unknown - FLASHX?)
  - 11111111111111111111111111111111 (System Program)
  - 11111111111111111111111111111111 (System Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA (Unknown - Pool?)
  - pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ (Unknown - Fee program?)
  - TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb (Token Extensions)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA (Unknown - Pool?)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - 11111111111111111111111111111111 (System Program)
```

### Oldest Transaction #2 Programs
```
[CREATOR] Oldest tx #2: 2aWmhmucmGxcfvUw... | Programs:
  - ComputeBudget111111111111111111111111111111 (System)
  - ComputeBudget111111111111111111111111111111 (System)
  - ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL (ATA Program)
  - ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL (ATA Program)
  - CxvksNjwhdHDLr3qbCXNKVdeYACW8cs93vFqLqtgyFE5 (Unknown - Exchange?)
  - BBRouter1cVunVXvkcqeKkZQcBK7ruan37PPm3xzWaXD (Unknown - Router?)
  - CxvksNjwhdHDLr3qbCXNKVdeYACW8cs93vFqLqtgyFE5 (Unknown - Exchange?)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - 11111111111111111111111111111111 (System Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - 11111111111111111111111111111111 (System Program)
  - pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA (Unknown - Pool?)
  - pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ (Unknown - Fee program?)
  - TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb (Token Extensions)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
  - pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA (Unknown - Pool?)
  - TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA (Token Program)
```

---

## Unknown Program IDs That Could Be Pump.fun

Looking at the programs that appear in the oldest transactions but are NOT in `PUMPFUN_PROGRAM_IDS`:

1. **FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9**
   - Appears in oldest tx #1
   - Unknown purpose
   - Could be Pump.fun related

2. **pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA**
   - Appears in both oldest tx #1 and #2
   - Pool program? Swap program?
   - Could be Pump.fun AMM

3. **pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ**
   - Appears in both oldest tx #1 and #2
   - Fee program?
   - Could be Pump.fun fee processor

4. **CxvksNjwhdHDLr3qbCXNKVdeYACW8cs93vFqLqtgyFE5**
   - Appears in oldest tx #2
   - Exchange program?

5. **BBRouter1cVunVXvkcqeKkZQcBK7ruan37PPm3xzWaXD**
   - Appears in oldest tx #2
   - Router program?

---

## Current Status

✗ **NOT Found**: `39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg` (current PUMPFUN_PROGRAM_IDS)

This program ID does **NOT** appear in the oldest transactions for this token. This confirms the user's concern that:
1. Either PUMPFUN_PROGRAM_IDS is incomplete
2. Or the actual Pump.fun program ID is different

---

## Next Steps

To identify the correct Pump.fun program ID:

### Option 1: Query Pump.fun API/Documentation
Get the official Pump.fun program IDs from:
- Pump.fun documentation
- Pump.fun official repository
- Solana blockchain explorer

### Option 2: Identify by Characteristics
The actual Pump.fun CREATE instruction likely:
- Includes the bonding curve account (writable, non-signer)
- Has the mint in its accounts
- Invokes one of the unknown program IDs
- Most likely: `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (appears in both transactions)

### Option 3: Manual Inspection
For token `62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump`:
1. Get signature `2haZVG8x1CbtYM15...`
2. Inspect on Solana Explorer
3. Identify which program is the "CREATE" operation
4. That's the Pump.fun program ID

---

## Recommendation

The most likely candidate for Pump.fun program ID is:
```
pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
```

**Rationale**:
- Appears in oldest transactions
- AMM/pool program pattern fits Pump.fun's bonding curve approach
- Common to both major old transactions

---

## Data Collected

✅ Program IDs from 2 oldest transactions logged
✅ Unknown programs identified
✅ Current assumption (39az...) confirmed NOT in oldest txs
⏳ Awaiting manual identification or official documentation

---

## Files Modified

- `pump_fun_post_migration_analyzer.py`
  - Fixed: programIdIndex handling
  - Improved: Show all program IDs (not just first 3)
  - Improved: Use HISTORY_RPC_URLS instead of hardcoded public RPC

---

**Last Updated**: 2026-01-27
**Status**: Awaiting program ID identification
**Recommendation**: Test hypothesis with `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` as Pump.fun program ID
