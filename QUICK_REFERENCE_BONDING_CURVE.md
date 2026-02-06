# Quick Reference: Bonding Curve PDA Determination

## The Answer in 30 Seconds

**Q: Which code determines bonding_curve_pda?**

**A**: [`_extract_bonding_curve_from_tx()`](pump_fun_post_migration_analyzer.py#L1195-L1336)
- Parses the CREATE transaction
- Finds Pump.Fun instruction
- Extracts bonding curve account
- Returns bonding_curve_pda address

---

## Call Chain

```
analyze_post_migration()
  ↓
get_creator_from_earliest_tx()  (line 1372)
  ├─ extract_bonding_curve_from_creation_tx()  (line 1020)
  │   └─ _extract_bonding_curve_from_tx(tx)  (line 1195)  ← THE ANSWER
  │       └─ Finds Pump.Fun instruction
  │           └─ Extracts bonding curve PDA
  │
  └─ get_true_earliest_signature(bonding_curve_pda)  (line 1445)
      └─ Uses bonding curve to find creator
```

---

## Step-by-Step Process

| Step | Code | What It Does |
|------|------|-------------|
| 1 | Line 1219-1230 | Find Pump.Fun instruction in transaction |
| 2 | Line 1234-1242 | Extract account references from instruction |
| 3 | Line 1250-1270 | Resolve account indexes to pubkeys |
| 4 | Line 1293-1323 | Filter: exclude mint, programs, first/last |
| 5 | Line 1325-1329 | Select best candidate → return bonding_curve_pda |

---

## Code Snippet

**File**: `pump_fun_post_migration_analyzer.py`
**Lines**: 1195-1336
**Method**: `_extract_bonding_curve_from_tx(tx: dict) -> Optional[str]`

Key lines:
- **1219**: Start iteration through instructions
- **1229**: Check if Pump.Fun program
- **1234**: Get accounts from instruction
- **1318**: Filter middle-range accounts (best candidates)
- **1327**: Select first candidate
- **1328**: Return bonding_curve_pda

---

## Important Dependencies

```
bonding_curve_pda extraction depends on:
  └─ Correct CREATE transaction validation
      └─ _validate_pumpfun_create_tx()  (line 663)
          └─ Currently checks: mint_in_accounts AND pumpfun_program_found
              └─ PROBLEM: Both CREATE and non-CREATE pass this!
```

**Fix needed**: Add account creation instruction detection

---

## Pump.Fun Program IDs (Hardcoded)

These are checked when finding the instruction:

```python
"pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # Main Pump.Fun
"39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"  # Migration processor
```

---

## What Gets Returned

```python
bonding_curve_pda: str = "HKCxoMfUYEkNKLrE1T8nRNVVDQc79Nbu8yLSZJx1pump"
# This is a Solana account address (base58 encoded pubkey)
# Used to query transaction history to find creator
```

---

## Related Files

| File | Purpose |
|------|---------|
| `pump_fun_post_migration_analyzer.py` | Contains the extraction method |
| `infra_mapping.py` | Contains PUMPFUN_PROGRAM_IDS |
| `BONDING_CURVE_EXTRACTION_FLOW.md` | Detailed documentation |
| `diagnostic_create_signature_issue.py` | Test showing validation bug |

---

## One-Liner Summary

**`_extract_bonding_curve_from_tx()` scans a CREATE transaction's Pump.Fun instruction, filters the accounts to find the bonding curve PDA, and returns its address.**

---

**Status**: ✅ Documented
**Confidence**: HIGH
**Dependency**: CREATE validation bug fix needed
