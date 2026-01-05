# Creator & SOL Destination Analysis - Quick Reference

## Stored Data
✅ All SOL destination addresses are stored in the database
✅ Addresses are validated (44 chars, valid Base58 encoding)
✅ Creator ↔ Destination relationships are tracked
✅ Network relationships can be queried across creators
✅ Both incoming and outgoing SOL transfers tracked  

## Analysis Tools

### 1. Analyze Creator Wallet
```bash
python3 analyze_creator_wallet.py <creator_address>
```
- Fetches transaction history from Helius API
- **Automatically stores** all SOL destinations in database
- Shows transaction analysis, risk assessment
- Displays SOL transfers in table format

### 2. View Stored Wallet Data
```bash
python3 query_creator_wallets.py <creator_address>
```
- Retrieves previously analyzed wallet data
- Shows all stored SOL transfer accounts
- Displays treasury flagged addresses (🏦)

### 3. Analyze SOL Destinations
```bash
# All destinations with creator usage
python3 analyze_sol_destinations.py

# Shared destinations only (multiple creators)
python3 analyze_sol_destinations.py --frequent

# Creators sending to a specific address
python3 analyze_sol_destinations.py <destination_address>
```

### 4. Find Creator Connections
```bash
# Show all creators connected to one creator
python3 find_creator_connections.py <creator_address>

# Show all creator pairs with shared destinations
python3 find_creator_connections.py --network
```

### 5. Network Overview
```bash
# Overall network stats
python3 sol_network_analysis.py

# Find aggregation hubs (multi-source addresses)
python3 sol_network_analysis.py --aggregation

# Show all creators sending to an address
python3 sol_network_analysis.py <destination_address>
```

## Typical Workflow

```bash
# 1. Analyze a creator (automatically stores SOL destinations)
python3 analyze_creator_wallet.py CreatorA_address

# 2. Analyze another creator
python3 analyze_creator_wallet.py CreatorB_address

# 3. Check if they're connected
python3 find_creator_connections.py CreatorA_address

# 4. Find all shared destinations
python3 sol_network_analysis.py --aggregation

# 5. See all creators using a destination
python3 sol_network_analysis.py shared_destination_address
```

## Key Indicators

| Flag | Meaning |
|------|---------|
| 🏦 Treasury | Address receives >5 transfers from creator (profit extraction point) |
| Shared destination | Multiple creators send to same address (coordination indicator) |
| Aggregation hub | Address receives from 2+ creators (money laundering risk) |

## Database Schema

```sql
creator_sol_transfers (
  creator_address,        -- Creator wallet
  transfer_type,          -- 'outgoing' or 'incoming'
  counterparty_address,   -- SOL destination address
  total_amount,           -- Total SOL sent
  transfer_count,         -- Number of transfers
  is_treasury,            -- Flag if >5 transfers
  first_transfer_timestamp,
  last_transfer_timestamp
)
```

## Network Statistics Example

```
Network Size: 3 creators → 8 destinations
Total SOL: 250.5 SOL
Aggregation hubs: 2 addresses with multiple sources
```

This means:
- 3 different creators analyzed
- They send SOL to 8 different destinations
- 250.5 total SOL transferred across network
- 2 addresses receive from multiple creators (suspicious)

## Data Validation

**Address Format Validation:**
- All Solana addresses are exactly 44 characters
- Valid Base58 encoding (no 0, O, I, or l characters)
- Extracted from validated transaction descriptions
- Invalid/corrupted addresses are filtered out before storage

**Implementation Details:**
- `is_valid_solana_address()` - Validates address format
- `analyze_sol_transfers()` - Extracts and validates SOL destinations from transaction data
- Only transfers with valid addresses are stored
- Treasury detection: Addresses with >5 transfers flagged as 🏦
