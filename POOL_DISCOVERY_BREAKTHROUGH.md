# Pool Discovery Breakthrough - Test Results

## Test Summary
Used known migration TX to validate focused candidate extraction + minimal pool detector.

**Migration TX:** `36TzLUy6QqPSwHZGQqGcJhM6GP7aQhXD6xNrgrLLmtfTR5Ft77Fw5VB8Bmmw3ZfqJP7YJnWf6VJ36rUVaUK8gV1D`

## Results

### ✅ Focused Candidate Extraction
```
Instruction-derived extraction: 17 candidates
(vs 25 from full account list)

First 5 candidates:
- 39azUYFWPz3V...
- 4D9RPC22fDDm...
- 5tkaAixycLTV...
- BeHh3siVFq9Q...
- 9njtsUnwFGo4...
```

### ✅ Minimal Pool Detector
```
Pool found: 5tkaAixycLTVKraA1QCxaXDQkE5TprpVzVNtQG73r2n8
Program: pumpswap (PumpSwap/Raydium AMM)
```

### ✅ Validation
```
Expected bonding curve (from logs): 5tkaAixycLTVKraA1QCxaXDQkE5TprpVzVNtQG73r2n8
Detected by minimal detector:        5tkaAixycLTVKraA1QCxaXDQkE5TprpVzVNtQG73r2n8

MATCH! ✓
```

## What This Proves

1. **Focused extraction works** — correctly identifies instruction-referenced accounts
2. **Minimal detector works** — successfully identifies AMM-owned pool accounts
3. **The implementation is sound** — both components are functioning correctly on real data

## Current Status

The listener has:
- ✅ TX enrichment (reconstructs meta.accounts)
- ✅ Focused candidate extraction (17 per TX vs 25)
- ✅ Minimal pool detector (finds AMM-owned accounts)
- ❌ **Integration gap** — candidates extracted but not properly validated in retry flow

## Next Step

The retry flow extracts candidates correctly but then validates them through the old owner-check RPC path instead of using the minimal detector. Need to ensure extracted candidates are validated efficiently and registered when valid.

