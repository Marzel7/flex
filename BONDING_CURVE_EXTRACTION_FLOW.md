# Bonding Curve PDA Extraction Flow

## Overview

The `bonding_curve_pda` is extracted from the token's CREATE transaction through a multi-step process.

---

## Call Stack

```
analyze_post_migration()
    ↓
get_creator_from_earliest_tx()  (line 1372)
    ├─ Step 1: Extract bonding curve from creation tx (line 1432)
    │   └─ extract_bonding_curve_from_creation_tx()  (line 1020)
    │       ├─ FAST PATH: Use Helius if have CREATE sig (line 1033-1039)
    │       │   └─ extract_bonding_curve_via_helius_parse()  (line 952)
    │       └─ SLOW PATH: Paginate through mint signatures (line 1043-1193)
    │           └─ _extract_bonding_curve_from_tx(tx)  (line 1195)
    │
    └─ Step 2: Query bonding curve for earliest signature (line 1445)
        └─ get_true_earliest_signature(bonding_curve_pda)
```

---

## Detailed Process

### 1️⃣ Extract Bonding Curve from Creation Transaction

**File**: `pump_fun_post_migration_analyzer.py`
**Method**: `extract_bonding_curve_from_creation_tx()` (lines 1020-1193)

#### FAST PATH: Use Helius Direct Parse (If CREATE sig already known)

**Lines 1031-1039**:
```python
if self._create_tx_signature and HELIUS_API_KEY:
    print(f"[CREATOR] 🚀 Fast path: Using Helius to parse CREATE tx directly")
    bonding_curve = await self.extract_bonding_curve_via_helius_parse(self._create_tx_signature)
    if bonding_curve:
        return bonding_curve
```

**Advantage**: 1 API call to Helius instead of 5000 pagination requests

**Process**:
1. Use Helius `/v0/transactions` API to parse the CREATE transaction
2. Validate it's actually a CREATE (use `_validate_pumpfun_create_tx`)
3. Extract bonding curve from validated transaction
4. Return immediately

---

#### SLOW PATH: Paginate Through Mint Signatures

**Lines 1043-1193**: For tokens without a known CREATE signature yet

**Process**:

```
Step 1: Paginate mint signatures (oldest-first)
├─ Call getSignaturesForAddress(token_mint) with pagination
├─ Reverse sort to go oldest-to-newest
└─ For each signature:
    ├─ Fetch the transaction
    ├─ Validate it's a Pump.Fun CREATE (line 1107)
    │   └─ _validate_pumpfun_create_tx(tx)
    │       ├─ Check: mint_in_accounts
    │       ├─ Check: pumpfun_program_found
    │       └─ Check: [THIRD CONDITION NEEDED - account creation]
    └─ If valid CREATE found:
        ├─ Store signature in self._create_tx_signature
        ├─ Extract bonding curve (line 1185-1186)
        └─ Return bonding curve

Step 2: Extract bonding curve from CREATE transaction
└─ _extract_bonding_curve_from_tx(tx)
```

**Pagination Config**:
- **Max pages**: 5000 (line 1057)
- **Signatures per page**: 1000 (line 1070)
- **Total possible**: ~5M signatures
- **Rate limiting**: 0.1s delay between pages (line 1064)

---

### 2️⃣ Extract Bonding Curve from Transaction

**Method**: `_extract_bonding_curve_from_tx()` (lines 1195-1336)

#### Step 1: Find Pump.Fun Instruction

**Lines 1219-1230**:
```python
# Look through all instructions for Pump.Fun program ID
for ix_idx, ix in enumerate(all_ix):
    program_id = ix.get("programId")

    # Handle programIdIndex format (most common)
    if not program_id and "programIdIndex" in ix:
        program_id_idx = ix.get("programIdIndex")
        if isinstance(program_id_idx, int) and 0 <= program_id_idx < len(account_keys):
            acct = account_keys[program_id_idx]
            program_id = acct if isinstance(acct, str) else acct.get("pubkey")

    # Check if this is Pump.Fun program
    if program_id not in PUMPFUN_PROGRAM_IDS:
        continue

    # Found Pump.Fun instruction!
```

**Pump.Fun Program IDs** (from `infra_mapping.py`):
```python
PUMPFUN_PROGRAM_IDS = {
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # Main Pump.Fun program
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Migration processor
}
```

#### Step 2: Extract Accounts from Instruction

**Lines 1234-1242**:
```python
# Get accounts from instruction (handle both formats)
accounts = ix.get("accounts")

if accounts is None and "parsed" in ix:
    # jsonParsed format stores in parsed.info
    parsed_info = ix.get("parsed", {}).get("info", {})
    accounts = self._extract_accounts_from_parsed_info(parsed_info)
```

#### Step 3: Resolve Account Indexes to Pubkeys

**Lines 1250-1270**:
```python
instruction_accounts = []
for acc in accounts:
    if isinstance(acc, int):
        # Account index - resolve to pubkey
        if 0 <= acc < len(account_keys):
            acct = account_keys[acc]
            pubkey = acct if isinstance(acct, str) else acct.get("pubkey")
            if pubkey:
                instruction_accounts.append({
                    "pubkey": pubkey,
                    "index": acc
                })
    elif isinstance(acc, str):
        # Direct pubkey string
        instruction_accounts.append({"pubkey": acc, "index": None})
```

#### Step 4: Find Bonding Curve Candidate

**Lines 1293-1323**: Filter and rank candidates

**Exclusion Rules**:
```python
# Skip the token mint itself (line 1299-1301)
if pubkey == self.token_mint:
    continue

# Skip known programs (line 1304-1306)
if pubkey in known_programs:
    continue

# Skip ATA program addresses (line 1311-1313)
if pubkey.startswith("ATA"):
    continue

# Accept middle-range accounts (line 1318-1319)
if 0 < i < len(instruction_accounts) - 1:
    bonding_curve_candidates.append(pubkey)
    # Best candidates: not first (fee payer), not last (system/token)
```

#### Step 5: Select Best Candidate

**Lines 1325-1329**:
```python
if bonding_curve_candidates:
    # Return the first candidate (usually the correct one)
    result = bonding_curve_candidates[0]
    print(f"[CREATOR] → Selected bonding curve: {result}")
    return result
```

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Token Migration Detected                                    │
│ (pumpfun_curve_listener.py detects REALTIME_FUNDING event)  │
└─────────────────────┬───────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────────────┐
│ analyze_post_migration()                                    │
│ (pump_fun_post_migration_analyzer.py)                       │
└─────────────────────┬───────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────────────┐
│ get_creator_from_earliest_tx()                              │
│ Extract the true creator from CREATE transaction            │
└─────────────────────┬───────────────────────────────────────┘
                      ↓
        ┌─────────────────────────┐
        │ extract_bonding_curve_  │
        │ from_creation_tx()      │
        └─────────────┬───────────┘
                      ↓
         ┌────────────┴────────────┐
         ↓                         ↓
    ┌─────────────┐        ┌──────────────┐
    │ FAST PATH   │        │ SLOW PATH    │
    │ (Helius)    │        │ (Pagination) │
    └─────────────┘        └──────────────┘
         ↓                         ↓
    [Parse 1 tx      [Paginate through
     if have sig]     mint signatures]
                              ↓
                      [For each sig:
                        Validate CREATE
                        (mint_in_accounts
                         + pump.fun_program
                         + NEEDED: account creation)]
                              ↓
                      [Extract bonding curve]
                              ↓
    ┌─────────────────────────────────┐
    │ _extract_bonding_curve_from_tx() │
    │ Find Pump.Fun instruction        │
    │ Extract account from instruction │
    │ Filter/rank bonding curve PDA    │
    │ Return best candidate            │
    └──────────────┬──────────────────┘
                   ↓
              BONDING_CURVE_PDA
                   ↓
    ┌─────────────────────────────────┐
    │ Use bonding curve to query for   │
    │ earliest transaction signature   │
    │ (the actual CREATE transaction)  │
    └─────────────────────────────────┘
```

---

## Key Points

### 1. Two Methods to Extract Bonding Curve

| Method | Speed | Reliability | When Used |
|--------|-------|-------------|-----------|
| **Helius** | 1 call | Very High | If CREATE sig already known |
| **Pagination** | 5000 calls max | High | If no CREATE sig yet |

### 2. Validation is Critical

The bonding curve extraction depends on **validating a real CREATE transaction**:

```python
validation = self._validate_pumpfun_create_tx(tx)
if validation['is_pumpfun_create']:
    # Extract from this transaction
```

**Currently validates**:
- ✅ `mint_in_accounts`
- ✅ `pumpfun_program_found`
- ❌ **MISSING**: Account creation instruction detection

**This is the bug!** Both CREATE and non-CREATE pass the first two conditions.

### 3. Bonding Curve Selection is Heuristic-Based

The code uses position and exclusion rules to find the bonding curve:
- **Not at position 0** (fee payer/signer)
- **Not at last positions** (system/token programs)
- **Not the mint itself**
- **Not known programs**
- **Choose middle-range account**

This works most of the time but isn't foolproof.

---

## Complete Code References

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| **extract_bonding_curve_from_creation_tx** | pump_fun_post_migration_analyzer.py | 1020-1193 | Main orchestration |
| **_extract_bonding_curve_from_tx** | pump_fun_post_migration_analyzer.py | 1195-1336 | Transaction parsing |
| **_validate_pumpfun_create_tx** | pump_fun_post_migration_analyzer.py | 663-750 | **VALIDATION (THE BUG)** |
| **get_true_earliest_signature** | pump_fun_post_migration_analyzer.py | 856-950 | Query bonding curve history |
| **PUMPFUN_PROGRAM_IDS** | infra_mapping.py | ~677-684 | Pump.Fun program identifiers |

---

## Related Issues

### Issue #1: CREATE Signature Bug

The bonding curve extraction depends on **correctly identifying CREATE transactions**.

**Current problem**: Both wrong and correct signatures pass validation.

**Fix needed**: Add account creation instruction detection as third validation condition.

---

## Next Steps

1. **Implement** account creation detection in `_validate_pumpfun_create_tx`
2. **Test** with diagnostic script to verify correct signatures are found
3. **Monitor** that bonding curves are extracted from real CREATE transactions
4. **Verify** downstream creator extraction uses correct bonding curves

---

**Status**: Process is sound, but validation is insufficient
**Confidence**: HIGH
**Critical dependency**: CREATE signature validation must be fixed first
