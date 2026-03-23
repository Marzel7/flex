# TX Data Enrichment: Fixing Incomplete Helius RPC Responses

## Problem

When a token migration is detected, the listener fetches the migration transaction from Helius RPC to extract pool candidates via follow-on discovery. However, **Helius sometimes returns transaction metadata without the `meta.accounts` array**, even when requesting `encoding: jsonParsed`.

This causes:
- `[TX_DATA_VALIDATION]` to report `has_meta_accounts=False`
- Follow-on discovery to be skipped (no account metadata to work from)
- Pool resolution to fall through to slower RPC strategies
- Users see delayed or missed pool detection

### Example Log Evidence

```
[TX_DATA_VALIDATION] has_meta=True has_blockTime=True has_transaction=True has_meta_accounts=False keys=['slot', 'transaction', 'meta', 'version', 'blockTime']
[CACHED_TX_DIAGNOSTICS] reason=no_amm_program_in_tx accounts=25 writable=0 amm_present=False meta_owners=0 inner_ix=1
[FOLLOW_ON_CHECK] follow_on_max_txs=0 tx_data=True cached_count=0
(No [FOLLOW_ON_DISCOVERY] log appears)
```

The TX data HAS `meta`, `blockTime`, `transaction` — but `meta.accounts` is None. This typically happens with:
- **V0 transactions** (with loaded addresses)
- **Older transactions** where Helius hasn't fully indexed metadata
- **High-traffic periods** where the RPC response is truncated

## Solution: TX Data Enrichment

When `meta.accounts` is missing, we **reconstruct it** from the available data in the transaction:

```
meta.accounts = [
    accountKeys from transaction.message.accountKeys,
    loaded addresses from meta.loadedAddresses.writable,
    loaded addresses from meta.loadedAddresses.readonly
]
```

This synthetic account list is sufficient for follow-on discovery to work — it gives the extraction logic the account pubkeys it needs to scan for pool program calls.

## Implementation

### Code Change: `src/core/pumpfun_curve_listener.py` (~line 2806)

After the `[TX_DATA_VALIDATION]` checkpoint, added enrichment logic:

```python
# ENRICHMENT: If meta.accounts is missing, reconstruct from accountKeys + loadedAddresses
if has_meta and not has_meta_accounts:
    try:
        message = tx_data.get('transaction', {}).get('message', {})
        account_keys = message.get('accountKeys', [])
        loaded_addresses = tx_data.get('meta', {}).get('loadedAddresses', {})

        # Build full account list (accountKeys + loaded addresses)
        all_accounts = []
        all_accounts.extend(account_keys)  # Original accounts
        all_accounts.extend(loaded_addresses.get('writable', []))  # Writable loaded
        all_accounts.extend(loaded_addresses.get('readonly', []))   # Readonly loaded

        # Create synthetic meta.accounts with owner info where available
        synthetic_accounts = [{'pubkey': addr} for addr in all_accounts]

        if synthetic_accounts:
            tx_data['meta']['accounts'] = synthetic_accounts
            log_print(
                f"🔧 [TX_DATA_ENRICHMENT] Reconstructed meta.accounts from accountKeys + loadedAddresses: "
                f"{len(synthetic_accounts)} accounts",
                flush=True
            )
    except Exception as e:
        log_print(
            f"⚠️ [TX_DATA_ENRICHMENT] Failed to reconstruct accounts: {e}",
            flush=True
        )
```

### Diagnostic Logs

**Before (broken flow):**
```
[TX_DATA_VALIDATION] has_meta=True has_blockTime=True has_transaction=True has_meta_accounts=False
[CACHED_TX_DIAGNOSTICS] reason=no_amm_program_in_tx
[FOLLOW_ON_CHECK] follow_on_max_txs=0 tx_data=True cached_count=0
```

**After (with enrichment):**
```
[TX_DATA_VALIDATION] has_meta=True has_blockTime=True has_transaction=True has_meta_accounts=False
🔧 [TX_DATA_ENRICHMENT] Reconstructed meta.accounts from accountKeys + loadedAddresses: 42 accounts
✅ [TX_DATA_ENRICHMENT] has_meta_accounts now=True
[FOLLOW_ON_CHECK] follow_on_max_txs=10 tx_data=True cached_count=0
[FOLLOW_ON_DISCOVERY] Starting search for ...
```

## Why This Works

1. **V0 transactions have loaded addresses** — they split accounts into `accountKeys` (regular) and `loadedAddresses` (additional). The RPC response includes all of these; we just need to assemble them.

2. **Follow-on discovery only needs the pubkeys** — it scans the account list looking for pool program calls. It doesn't need the owner/lamports metadata that's missing from the synthetic accounts.

3. **Best-effort enrichment** — if reconstruction fails for any reason, follow-on just doesn't run (same behavior as before), so we don't break anything.

4. **Non-invasive** — we only modify `meta.accounts` when it's None. Real metadata (when Helius provides it) is untouched.

## Testing & Validation

When the next token migration is detected:

1. Check if `[TX_DATA_VALIDATION] has_meta_accounts=False` appears
2. Look for `[TX_DATA_ENRICHMENT] Reconstructed...` log
3. Verify `[FOLLOW_ON_DISCOVERY] Starting search...` now appears
4. Follow-on discovery should proceed and find the pool (or exhaust retries if pool creation is delayed)

### Expected Log Sequence

```
🔴 [RETRY_START] curve=ABC... creator=XYZ... tx_data=YES
🔴 [TX_DATA_VALIDATION] has_meta=True has_blockTime=True has_transaction=True has_meta_accounts=False
🔧 [TX_DATA_ENRICHMENT] Reconstructed meta.accounts from accountKeys + loadedAddresses: 42 accounts
✅ [TX_DATA_ENRICHMENT] has_meta_accounts now=True
[FOLLOW_ON_CHECK] follow_on_max_txs=10 tx_data=True cached_count=0
[FOLLOW_ON_DISCOVERY] Starting search for bonding_curve
[FOLLOW_ON_DISCOVERY] Scanning anchor=bonding_curve
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] Found candidate ABC... from anchor=bonding_curve at offset=1
[FOLLOW_ON_DISCOVERY] ✅ Found valid pool ABC...
[FOLLOW_ON_SUCCESS] Found pool ABC...
```

## Files Modified

- `src/core/pumpfun_curve_listener.py` — Added TX data enrichment logic in `_retry_pool_discovery()` method (~line 2806)

## Deployment

- Listener restarted with enrichment code active
- No database changes required
- No configuration changes required
- Backward compatible (only enriches when data is incomplete)

## Related

- See `FINAL_VALIDATION_FRAMEWORK.md` for complete diagnostic checkpoint guide
- The enrichment runs between `[TX_DATA_VALIDATION]` and `[FOLLOW_ON_CHECK]` checkpoints
