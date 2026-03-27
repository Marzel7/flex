# Bug: `registration_failed` — Missing `pool_program` in `_extract_vaults_by_mint`

**Token**: `2izfNJ5bACaQDKyqGUYicSKhFE963TnkBo9tWxiqpump`
**Severity**: High — token never gets vault data, discovery exhausts all 12 attempts
**Status**: Root cause confirmed, fix identified

---

## Symptom

Token appears in `tracked_tokens` but has no record in `token_pool_accounts`.

Logs show 12 consecutive `registration_failed` rejections with no `[POOL_REJECTED]` reason code:

```
[DISCOVERY_TX] corr=2izfNJ5b|A1 ... tested=1 rejections=registration_failed
[DISCOVERY_TX] corr=2izfNJ5b|A2 ... tested=1 rejections=registration_failed
...
[DISCOVERY_FAILED] ❌ All 12 attempts exhausted for 2izfNJ5bACaQDKyq...
  failure_class=all_candidates_rejected_or_failed
```

---

## Root Cause

`_extract_vaults_by_mint()` in `src/core/pool_discovery.py` (lines 308–419) returns a vault dict
**without** the `pool_program` key:

```python
return {
    "base_account": base_vault["address"],
    "quote_account": quote_vault["address"],
    "base_token": base_vault["mint"],
    "quote_token": quote_vault["mint"],
    "base_decimals": base_vault["decimals"],
    "quote_decimals": quote_vault["decimals"],
    # ❌ "pool_program" is missing
}
```

`discover_and_register_pool()` then does:

```python
reserves["pool_address"] = pool_address
success = await self.register_pool_to_db(token_mint, reserves, discovery_method)
```

Inside `register_pool_to_db()`:

```python
pool_program = reserves.get("pool_program")  # → None

is_valid, error_msg = self.validate_pool_registration(
    pool_address, base_account, quote_account, pool_program
)
# → returns False, "pool_program unknown or invalid: None"
```

`validate_pool_registration()` checks against `KNOWN_PROGRAMS` and rejects `None` → returns
`registration_failed`.

---

## Why the Other Extractors Work

Every other extraction path explicitly sets `pool_program`:

| Method | Sets pool_program |
|--------|-------------------|
| `_extract_raydium_amm` | ✅ `pool_program = RAYDIUM_AMM_PROGRAM` |
| `_extract_raydium_cpmm` | ✅ `"pool_program": RAYDIUM_CPMM_PROGRAM` |
| `_extract_orca_whirlpool` | ✅ `"pool_program": ORCA_WHIRLPOOL_PROGRAM` |
| `_extract_pumpfun_v1` | ✅ `result["pool_program"] = PUMPFUN_V1_PROGRAM` |
| `_extract_vaults_by_mint` | ❌ **missing** |

`_extract_vaults_by_mint` is the PumpSwap fallback path (called from `_extract_from_pool_data`
when `owner == PUMPSWAP_PROGRAM`).

---

## Transaction Accounts (Evidence)

The `[IX_ACCOUNTS_SHAPE]` log line shows accounts parsed from the launch tx:

```
[0] 9Z6BHfj4xHxFTuSYbkTboYa2ox9N69McUhRa8oh2ubF3  ← pool candidate
[1] ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw  ← shared PDA (known bad actor)
[2] Ff6cEiKLDKgrnoX5rqn6AfRxfJCcXjzFVsGjERpzuWzh  ← base vault (token)
[3] 2izfNJ5bACaQDKyqGUYicSKhFE963TnkBo9tWxiqpump  ← token mint
[4] So11111111111111111111111111111111111111112      ← SOL mint
```

The pool is `9Z6BHfj4`. It's a PumpSwap pool. `_extract_vaults_by_mint` is called, finds
vaults successfully, but returns without `pool_program` → registration fails.

---

## Fix

**File**: `src/core/pool_discovery.py`
**Location**: `_extract_vaults_by_mint` return dict, line ~409

```python
# BEFORE
return {
    "base_account": base_vault["address"],
    "quote_account": quote_vault["address"],
    "base_token": base_vault["mint"],
    "quote_token": quote_vault["mint"],
    "base_decimals": base_vault["decimals"],
    "quote_decimals": quote_vault["decimals"],
}

# AFTER
return {
    "base_account": base_vault["address"],
    "quote_account": quote_vault["address"],
    "base_token": base_vault["mint"],
    "quote_token": quote_vault["mint"],
    "base_decimals": base_vault["decimals"],
    "quote_decimals": quote_vault["decimals"],
    "pool_program": PUMPSWAP_PROGRAM,
}
```

> **Note**: `_extract_vaults_by_mint` is only called for PumpSwap pools (from
> `_extract_from_pool_data` when `owner == PUMPSWAP_PROGRAM`), so hardcoding
> `PUMPSWAP_PROGRAM` here is correct.

---

## Impact

- **All PumpSwap tokens** hit this bug — they all exhaust 12 attempts and never get vault data
- `_extract_vaults_by_mint` is **only** called for PumpSwap; Raydium/Orca/PumpFun V1 are unaffected
- This is **separate** from the authority PDA issue (which affects data correctness); this bug
  prevents registration entirely

---

## Verification After Fix

```bash
# Restart listener, then watch logs for new PumpSwap token:
grep "POOL_EXTRACTED\|POOL_REJECTED\|registration_failed" listener.log | tail -20

# Confirm token appears in DB:
sqlite3 database/flex_complete_database.db \
  "SELECT mint, pool_program, vault_validation_status FROM token_pool_accounts ORDER BY created_at DESC LIMIT 5;"
```

Expected: new PumpSwap tokens show `pool_program = <PUMPSWAP_PROGRAM>` and
`vault_validation_status = validated`.
