# Solscan Label Tagging System

## Overview

The system now automatically looks up and tags addresses with their Solscan labels when they appear in transactions. This provides clear visibility into what fee vaults, routers, and other labeled services are involved in creator transactions.

**Example:** When a creator sends 0.001 SOL to "GMGN Fees Vault 5", the system:
1. Detects the outgoing transfer
2. Looks up the recipient's Solscan label
3. Stores it in the database
4. Logs it with a clear tag: `[LABEL] 🏷️ Recipient labeled: 5g7yN... (GMGN Fees Vault 5)`

## What Gets Tagged

### Funders
When a funder sends SOL to a creator, the system checks if the funder has a Solscan label:
```
[LABEL] 🏷️ Funder labeled: 8iBa3q... (Binance Hot Wallet)
[LABEL] 🏷️ Funder labeled: vePRo... (Jito Bundle Router)
```

### Recipients
When a creator sends SOL to someone, the system checks if the recipient has a Solscan label:
```
[LABEL] 🏷️ Recipient labeled: 5g7y... (GMGN Fees Vault 5)
[LABEL] 🏷️ Recipient labeled: 2xis... (0slot.trade Fee Router)
[LABEL] 🏷️ Recipient labeled: 3aYN... (Pump.fun Fee Account)
```

## Database Storage

Labels are stored in the `address_labels` table:

```sql
CREATE TABLE address_labels (
    address TEXT PRIMARY KEY,
    label_name TEXT,              -- "GMGN Fees Vault 5"
    category TEXT,                -- "feevault", "router", "cex", etc.
    description TEXT,             -- Additional details from Solscan
    risk_level TEXT,              -- Future use for risk assessment
    tags TEXT,                    -- Additional tags
    source TEXT,                  -- "solscan", "helius", "cache"
    synced_at TIMESTAMP           -- When last updated
)
```

### Example Records

```sql
-- GMGN Fee Vault
INSERT INTO address_labels
VALUES (
    '5g7yNHyNQZhgKmJvG3...',
    'GMGN Fees Vault 5',
    'feevault',
    'Fee aggregator for GMGN trading interface',
    NULL, NULL,
    'solscan',
    CURRENT_TIMESTAMP
);

-- System Program (standard)
INSERT INTO address_labels
VALUES (
    '11111111111111111111111111111111',
    'System Program',
    'system',
    'Solana native system program',
    NULL, NULL,
    'solscan',
    CURRENT_TIMESTAMP
);
```

## How It Works

### 1. Label Detection During Transaction Processing

When funders or recipients are encountered in the `extract_for_creator()` method:

```python
# In _save_funder()
label_info = await tag_funder_if_labeled(self.session, funder)
if label_info and label_info.get("label_name"):
    formatted = format_address_with_label(funder, label_info)
    print(f"[LABEL] 🏷️ Funder labeled: {formatted}", flush=True)

# In _save_recipient()
label_info = await tag_recipient_if_labeled(self.session, recipient)
if label_info and label_info.get("label_name"):
    formatted = format_address_with_label(recipient, label_info)
    print(f"[LABEL] 🏷️ Recipient labeled: {formatted}", flush=True)
```

### 2. Three-Layer Lookup

When `tag_address_if_needed()` is called:

**Layer 1: Check Database Cache**
```python
# If label already saved, return immediately (fast)
existing = get_address_label(address)
if existing:
    return existing
```

**Layer 2: Query Solscan API**
```python
# If not in database, try Solscan
label_info = await lookup_solscan_label(session, address)
if label_info:
    # Save for next time
    save_address_label(address, label_info['label_name'], ...)
```

**Layer 3: Memory Cache**
```python
# For frequently accessed addresses, cache in memory
LABEL_CACHE[address] = (label_name, category, timestamp)
```

### 3. Graceful Failures

If Solscan API is unavailable:
- ✅ System continues normally
- ✅ Non-blocking (doesn't slow down extraction)
- ✅ Labels already in database are still used
- ❌ New labels won't be discovered that session
- 🔄 Can be retried on next extraction

## Log Output Examples

### Successful Label Detection

```
[REALTIME_FUNDING] 🔍 Extracting creator funding for 5omhas...
[REALTIME_FUNDING] ✓ Inbound: 3 funders (105.50 SOL)
[LABEL] 🏷️ Funder labeled: 8iBa3q2N... (Binance Hot Wallet)
[LABEL] 🏷️ Funder labeled: 5g7yNHy... (Coinbase Custody)
[REALTIME_FUNDING] ✓ Outbound: 12 recipients (0.15 SOL)
[LABEL] 🏷️ Recipient labeled: 5g7y... (GMGN Fees Vault 5)
[LABEL] 🏷️ Recipient labeled: 2xis... (0slot.trade Fee Router)
```

### When Transaction Was Filtered (Pre-fix)

Before the label tagging system, GMGN wasn't logged because:
1. Transaction involved Pump.Fun token operations → Entire tx filtered
2. GMGN transfer was skipped with the rest
3. No logging output for GMGN

**Now:** Labels are captured at transaction parsing time, before filtering:
- ✅ Labels stored even if transaction is filtered
- ✅ Can still see who received payments even in excluded txs
- ✅ Better visibility into payment routing

## Usage

### Query Addresses by Label

```sql
-- Find all fee vault transfers
SELECT address, label_name, category
FROM address_labels
WHERE category = 'feevault'
ORDER BY synced_at DESC;

-- Find all CEX-related addresses
SELECT address, label_name
FROM address_labels
WHERE category IN ('cex', 'exchange')
ORDER BY label_name;

-- Find all addresses seen by a creator
SELECT address_labels.*
FROM address_labels
JOIN creator_receivers ON address_labels.address = creator_receivers.receiver_address
WHERE creator_receivers.creator_address = '5omhas...'
ORDER BY label_name;
```

### Check if Address is Labeled

```python
from solscan_address_tagger import get_address_label

label = get_address_label("5g7yNHyNQZh...")
if label:
    print(f"Labeled as: {label['label_name']} ({label['category']})")
```

### Force Refresh Label

```python
from solscan_address_tagger import save_address_label

# Save/update a label
save_address_label(
    address="5g7yNHyNQZh...",
    label_name="GMGN Fees Vault 5",
    category="feevault",
    description="GMGN fee aggregator",
    source="manual"
)
```

## Benefits

### For Analysis
- 🏷️ **Identify infrastructure** - Know which addresses are protocol services, not users
- 💰 **Fee tracking** - See where creators send funds (fee vaults, routers, etc.)
- 🔍 **Pattern detection** - Identify preferred fee aggregators or infrastructure
- 🚀 **Risk assessment** - Fee routing to unusual addresses can be a red flag

### For Visibility
- 📊 **Clear logs** - See labeled addresses in transaction logs
- 📝 **Database records** - Persistent storage of all discovered labels
- 🔄 **Reusability** - Once cached, no more API calls needed
- ⚡ **Fast lookups** - In-memory caching for recent addresses

### For Debugging
- 🔎 **Understand transactions** - See exact fee routing
- 📍 **Track address roles** - What is each address doing?
- 🧪 **Compare data** - Validate against Solscan labels
- 📋 **Historical record** - Audit trail of who sent where

## Configuration

### API Settings

```python
# solscan_address_tagger.py
SOLSCAN_ACCOUNT_DETAILS = "https://api.solscan.io/account"  # API endpoint
CACHE_TTL = 3600  # Cache for 1 hour
```

### Cache Settings

```python
LABEL_CACHE: Dict[str, tuple] = {}  # In-memory cache
# Stored as: {address: (label_name, category, timestamp)}
```

## Future Enhancements

### 1. Batch Label Lookup
```python
# Look up multiple addresses at once
labels = await lookup_labels_batch(['addr1', 'addr2', 'addr3'])
```

### 2. Label Categories for Risk Scoring
```python
# Different risk weights for different label types
CATEGORY_RISK_WEIGHTS = {
    'feevault': 0.0,      # Neutral
    'router': 0.0,        # Neutral
    'cex': -0.5,          # Positive (lower risk)
    'spam': 0.8,          # Negative (higher risk)
}
```

### 3. Real-time Label Updates
```python
# Periodic refresh of label cache from Solscan
async def refresh_labels_periodically():
    while True:
        await fetch_latest_labels()
        await asyncio.sleep(3600)  # Every hour
```

### 4. UI Display
Display labels in the creator details modal:
```
Recipient Addresses
├─ 8iBa3q2N... (Binance Hot Wallet) - 50.00 SOL
├─ 5g7yNHy... (GMGN Fees Vault 5) - 0.001 SOL ← NOW LABELED
├─ 2xis... (0slot.trade Fee Router) - 0.001 SOL ← NOW LABELED
└─ 3aYN... (Unknown) - 0.099 SOL
```

## Troubleshooting

### Labels Not Appearing

**Problem:** `[LABEL]` logs not showing up for known addresses

**Causes:**
1. Solscan API is rate limited (returns 403)
2. Address doesn't have a Solscan label
3. Label lookup is timing out

**Solution:**
```python
# Check if label exists in database
from solscan_address_tagger import get_address_label
label = get_address_label("address_here")
if label:
    print(f"Label exists: {label}")
else:
    print("No label in database")

# Manually save a label
from solscan_address_tagger import save_address_label
save_address_label("address", "Label Name", "category")
```

### API Rate Limiting

The system handles this gracefully:
- Catches `429` responses
- Falls back to database cache
- Continues processing without stopping
- Retries on next extraction

```python
try:
    label = await lookup_solscan_label(session, address)
except Exception as e:
    # Continue anyway
    print(f"[LABEL] ⚠ Could not lookup: {e}")
    pass
```

## Summary

The Solscan Label Tagging system provides:

✅ **Automatic detection** of infrastructure, fee vaults, and protocol addresses
✅ **Persistent storage** in address_labels database table
✅ **Clear logging** with [LABEL] tags in extraction output
✅ **Three-layer caching** (DB, memory, Solscan API)
✅ **Graceful failures** - continues even if API unavailable
✅ **Queryable database** - analyze all labeled addresses
✅ **Non-blocking** - label lookup happens asynchronously

This solves the original problem: **GMGN Fees Vault 5 and other fee addresses are now tagged and logged**, even if the transaction itself was filtered for other reasons.

---

**Example: Before vs After**

**Before (Problem):**
```
Creator sends 0.001 SOL to 5g7yNHy...
Transaction filtered (Pump.Fun token ops)
→ No log output for GMGN
→ Why did creator send to that address? Unknown.
```

**After (Solution):**
```
Creator sends 0.001 SOL to 5g7yNHy...
[LABEL] 🏷️ Recipient labeled: 5g7y... (GMGN Fees Vault 5)
→ Clear log showing what the address is
→ Saved in address_labels table for analysis
→ Visible in UI when implemented
```
