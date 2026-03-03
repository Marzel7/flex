# Webhook Creator Transaction Tracking

## Feature Overview

The webhook system now automatically tracks transaction signatures for watched creators when they receive transfers via the webhook stream.

## How It Works

### Flow

```
Helius Webhook (SOL Transfer)
    ↓
webhook_handler.py processes transfer
    ↓
Check if destination address is in creator_watch
    ↓
If YES → Save to creator_tx_ledger
    - creator_pubkey: The watched creator
    - signature: Transaction signature
    - blockTime: Block timestamp
    - delta_sol_lamports: Amount transferred
    - tx_type: 'transfer_in'
    - source: 'webhook'
```

### Key Benefits

✅ **No RPC calls needed** - Uses webhook stream directly
✅ **Real-time tracking** - Signatures saved immediately
✅ **Deduplication** - UNIQUE constraint on signature prevents duplicates
✅ **Activity tracking** - Enables monitoring creator transaction counts
✅ **Optional** - Only saves if creator is in creator_watch table

## Implementation Details

### Function: `save_creator_signatures()`

Located in [webhook_handler.py](webhook_handler.py)

```python
def save_creator_signatures(conn: sqlite3.Connection, dest: str, sig: str, block_time: int, amount_lamports: int):
    """
    Save transaction signature to creator_tx_ledger if destination is a watched creator.
    """
```

Called from the main webhook processing loop whenever a new transfer is stored.

### Database Table: `creator_tx_ledger`

```sql
CREATE TABLE creator_tx_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_pubkey TEXT NOT NULL,        -- The watched creator address
    signature TEXT NOT NULL UNIQUE,      -- TX signature (prevents duplicates)
    slot INTEGER,                        -- Solana slot
    blockTime INTEGER,                   -- Block timestamp
    delta_sol_lamports INTEGER,          -- Amount transferred in lamports
    fee_lamports INTEGER,                -- TX fee (if tracked)
    compute_units INTEGER,               -- Compute units (if tracked)
    compute_units_consumed INTEGER,      -- Actual CUs (if tracked)
    counterparty TEXT,                   -- Sender address (optional)
    tx_type TEXT,                        -- 'transfer_in' for webhooks
    source TEXT,                         -- 'webhook' for webhook-sourced TXs
    is_confirmed INTEGER DEFAULT 1,      -- Always 1 for webhooks
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(creator_pubkey) REFERENCES creator_watch(creator_pubkey)
);
```

### Indexes

- `idx_creator_tx_ledger` - Fast lookup by creator_pubkey
- `idx_signature` - Fast lookup by transaction signature
- UNIQUE constraint on signature - Prevents duplicate saves

## Monitoring

### Check for Creator TX Saves

```bash
# Count webhook-sourced creator TXs
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM creator_tx_ledger WHERE source = 'webhook';"

# See creators with most webhook TXs
sqlite3 flex_complete_database.db "
SELECT creator_pubkey, COUNT(*) as tx_count
FROM creator_tx_ledger
WHERE source = 'webhook'
GROUP BY creator_pubkey
ORDER BY tx_count DESC
LIMIT 10;"

# Get recent creator TXs from webhooks
sqlite3 flex_complete_database.db "
SELECT creator_pubkey, signature, blockTime, delta_sol_lamports
FROM creator_tx_ledger
WHERE source = 'webhook'
ORDER BY blockTime DESC
LIMIT 10;"
```

### Check Logs

```bash
tail -f flask.log | grep "WEBHOOK_CREATOR"
```

Expected output when creator matches:
```
[WEBHOOK_CREATOR] ABC123... - Saved tx for creator XYZ789...
```

## Current Status

✅ **Feature implemented and running**
- Webhook handler checks each transfer destination
- Automatically saves signatures for watched creators
- No configuration needed
- Zero RPC overhead

### Current Metrics

- **Watched creators**: 1,351
- **Webhook transfers saved**: Growing (checked on demand)
- **Deduplication**: Enabled (no duplicate signatures)

## Example Data

When a webhook transfer goes to a watched creator:

```
creator_pubkey: 5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ
signature:      5myHBt9NKKF9fc3KUxHXwjNUa63KPUeBkXGKAkyXG5az4XSkvThqCe6Mid7v1nykKtsETrZ62iuo7NoYNFP75ELc
blockTime:      1772564581
delta_sol_lamports: 1000000 (0.001 SOL)
tx_type:        transfer_in
source:         webhook
```

## Integration Points

### Webhook Processing Flow

1. ✅ Webhook arrives at `/helius/webhook`
2. ✅ Parsed by `extract_system_transfers()`
3. ✅ Saved to `sol_transfers` table
4. ✅ Activity updated in `address_activity`
5. ✅ **NEW**: Signature saved to `creator_tx_ledger` if creator is watched
6. ✅ Address queued in `work_queue` for analysis

### No Breaking Changes

- Existing webhook processing unchanged
- Works alongside current systems
- Optional - only activates for watched creators
- No performance impact (single DB check per transfer)

## Future Enhancements

Possible extensions (not yet implemented):

1. **Counterparty tracking** - Store sender address in `counterparty` field
2. **Fee tracking** - Calculate and save TX fees
3. **Activity scoring** - Use creator TX count for priority calculation
4. **Real-time alerts** - Notify when creator receives webhook transfer
5. **Analytics** - Track creator activity from webhook stream

---

**Deployed**: 2026-03-03
**Status**: ✅ Production Ready
**RPC Cost**: Zero (uses webhook stream)
