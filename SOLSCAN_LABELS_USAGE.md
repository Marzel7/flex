# Solscan Labels - Quick Reference

## What's New?

Addresses that appear in transactions are now automatically looked up on Solscan and tagged with their labels.

### Before
```
Recipient: 5g7yNHy... (unknown)
Amount: 0.001 SOL
```

### After
```
[LABEL] 🏷️ Recipient labeled: 5g7y... (GMGN Fees Vault 5)
Amount: 0.001 SOL
```

## Log Output

### Tag Locations

Look for `[LABEL]` logs in the extraction output:

```
[REALTIME_FUNDING] 🔍 Extracting creator funding for 5omhas...
[LABEL] 🏷️ Funder labeled: 8iBa... (Binance Hot Wallet)      ← Inbound
[LABEL] 🏷️ Recipient labeled: 5g7y... (GMGN Fees Vault 5)    ← Outbound
[REALTIME_FUNDING] ✓ Complete
```

### Log Format

- **Funders:** `[LABEL] 🏷️ Funder labeled: ADDRESS... (LABEL_NAME)`
- **Recipients:** `[LABEL] 🏷️ Recipient labeled: ADDRESS... (LABEL_NAME)`

## Database Queries

### See All Labeled Addresses

```bash
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT address, label_name, category, synced_at
FROM address_labels
ORDER BY synced_at DESC
LIMIT 20;
EOF
```

### Find Specific Label

```bash
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT address, label_name, category
FROM address_labels
WHERE label_name LIKE '%GMGN%';
EOF
```

### Get All Fee Vaults

```bash
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT address, label_name
FROM address_labels
WHERE category = 'feevault';
EOF
```

### Show Recipients for a Creator

```bash
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT
    al.label_name,
    al.category,
    cr.amount_sol,
    cr.first_detected_at
FROM creator_receivers cr
LEFT JOIN address_labels al ON cr.receiver_address = al.address
WHERE cr.creator_address = '5omhas...'
ORDER BY cr.amount_sol DESC;
EOF
```

## Python Usage

### Check If Address Is Labeled

```python
from solscan_address_tagger import get_address_label

label = get_address_label("5g7yNHyNQZh...")
if label:
    print(f"Address is: {label['label_name']} ({label['category']})")
else:
    print("Address not labeled")
```

### Manually Save a Label

```python
from solscan_address_tagger import save_address_label

save_address_label(
    address="5g7yNHyNQZh...",
    label_name="GMGN Fees Vault 5",
    category="feevault",
    description="GMGN fee aggregator for trading interface",
    source="manual"
)
```

### Get All Fee-Related Addresses

```python
import sqlite3

conn = sqlite3.connect("pumpswap_tokens.db")
cursor = conn.cursor()

cursor.execute("""
    SELECT address, label_name
    FROM address_labels
    WHERE category IN ('feevault', 'router', 'fee')
    ORDER BY label_name
""")

for addr, label in cursor.fetchall():
    print(f"{label}: {addr[:16]}...")

conn.close()
```

## Troubleshooting

### No Labels Appearing

**Check 1:** Verify extraction is running
```bash
tail -50 listener.log | grep LABEL
```

**Check 2:** Check if addresses are in database
```bash
sqlite3 pumpswap_tokens.db "SELECT COUNT(*) FROM address_labels;"
```

**Check 3:** Look for API errors
```bash
tail -50 listener.log | grep "SOLSCAN\|LABEL\|Error"
```

### Labels Not Saving

**Problem:** Labels look up correctly but don't appear in database

**Solution:** Check database permissions
```bash
ls -la pumpswap_tokens.db
sqlite3 pumpswap_tokens.db ".schema address_labels"
```

### Specific Address Never Gets Labeled

**Reason:** Not all addresses have Solscan labels
- Only labeled accounts appear in Solscan's database
- Generic user wallets won't have labels
- Some obscure programs might not be labeled

**Check:** Search on Solscan.io directly
```
https://solscan.io/account/ADDRESS_HERE
```

## Examples

### Example 1: Track GMGN Fees

```bash
# See all GMGN fee payments
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT
    cr.creator_address,
    SUM(cr.amount_sol) as total_gmgn_fees,
    COUNT(*) as num_payments
FROM creator_receivers cr
JOIN address_labels al ON cr.receiver_address = al.address
WHERE al.label_name LIKE '%GMGN%'
GROUP BY cr.creator_address
ORDER BY total_gmgn_fees DESC;
EOF
```

### Example 2: Find Fee Spending by Category

```bash
# How much do creators spend on different fee types?
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT
    al.category,
    COUNT(DISTINCT cr.creator_address) as num_creators,
    COUNT(*) as num_payments,
    SUM(cr.amount_sol) as total_sol
FROM creator_receivers cr
JOIN address_labels al ON cr.receiver_address = al.address
GROUP BY al.category
ORDER BY total_sol DESC;
EOF
```

### Example 3: Identify Suspicious Recipients

```bash
# Recipients sending to unusual fee vaults or routers
sqlite3 pumpswap_tokens.db << 'EOF'
SELECT
    cr.creator_address,
    al.label_name,
    al.category,
    cr.amount_sol,
    cr.first_detected_at
FROM creator_receivers cr
LEFT JOIN address_labels al ON cr.receiver_address = al.address
WHERE al.category NOT IN ('cex', 'automation')
   OR al.label_name LIKE '%unknown%'
   OR al.address IS NULL
ORDER BY cr.amount_sol DESC
LIMIT 20;
EOF
```

## Advanced Queries

### Find All Infrastructure Used by Creators

```sql
SELECT
    al.label_name,
    al.category,
    COUNT(DISTINCT cr.creator_address) as creators_using_it,
    COUNT(DISTINCT cr.receiver_address) as num_addresses,
    SUM(cr.amount_sol) as total_volume
FROM creator_receivers cr
JOIN address_labels al ON cr.receiver_address = al.address
GROUP BY al.label_name
ORDER BY total_volume DESC;
```

### Track Fee Patterns Per Creator

```sql
SELECT
    cr.creator_address,
    al.category,
    SUM(cr.amount_sol) as sol_spent,
    COUNT(*) as num_transfers
FROM creator_receivers cr
LEFT JOIN address_labels al ON cr.receiver_address = al.address
WHERE cr.creator_address IN (
    SELECT DISTINCT creator_address FROM creator_receivers
    WHERE amount_sol > 0.01  -- Meaningful amounts
)
GROUP BY cr.creator_address, al.category
ORDER BY cr.creator_address, sol_spent DESC;
```

## Database Schema

### address_labels Table

```sql
CREATE TABLE address_labels (
    address TEXT PRIMARY KEY,           -- Solana address
    label_name TEXT,                   -- "GMGN Fees Vault 5"
    category TEXT,                     -- "feevault", "router", "cex"
    description TEXT,                  -- Details from Solscan
    risk_level TEXT,                   -- Future use
    tags TEXT,                         -- Additional tags
    source TEXT,                       -- "solscan" or "manual"
    synced_at TIMESTAMP                -- Discovery timestamp
);
```

### Linked Tables

Labels are automatically linked to:
- `creator_receivers` - Where creators sent funds
- `creator_funders` - Where creators received funds (future)

## Summary

Use `[LABEL]` logs to:
- ✅ Identify what each address is (fee vault, router, CEX, etc.)
- ✅ Track payments to known infrastructure
- ✅ Spot unusual fee destinations
- ✅ Analyze creator payment patterns

Query the `address_labels` table to:
- ✅ Find all addresses of a certain type
- ✅ Analyze fee spending
- ✅ Identify infrastructure patterns
- ✅ Build risk profiles

---

**Questions?** Check `SOLSCAN_LABEL_TAGGING.md` for detailed documentation.
