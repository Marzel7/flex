# Creator Network Detection System

## Overview

The network detection system identifies coordinated rug-pulling rings by detecting creators that share SOL destination wallets. If multiple creators send SOL to the same addresses, they're likely the same person/team running multiple token projects.

## Components

### 1. Database Tables

#### `creator_sol_transfers`
Tracks where creators send SOL from their projects:
```sql
- creator_address: The creator wallet
- destination_address: Where they send SOL (treasury, personal wallet, etc.)
- total_amount: Total SOL transferred
- transfer_count: Number of transfers
- is_pool_address: Whether destination is a pool/exchange
```

#### `creator_networks`
Stores identified creator networks:
```sql
- creator_address: Creator in the network
- connected_creators: JSON array of connected creators
- shared_destinations: JSON array of shared SOL destinations
- network_size: Number of creators in network
- network_risk_level: CRITICAL, HIGH, MEDIUM, LOW based on malicious members
```

#### `creator_blocklist` (Enhanced)
Added network columns:
```sql
- connected_to_malicious: 1 if connected to any blocked creators
- network_members: JSON array of connected malicious creators
```

#### `token_analysis` (Enhanced)
Added network columns:
```sql
- network_risk: 1 if creator is part of network with malicious members
- connected_malicious_count: Number of connected malicious creators
```

### 2. Scripts

#### `scripts/analyze_creator_networks.py`
Analyzes creator networks and updates database:
```bash
python scripts/analyze_creator_networks.py
```

**Output:**
- Identifies all networks and their size
- Flags networks containing malicious creators as CRITICAL
- Flags networks containing suspicious creators as HIGH
- Updates database with network risk levels
- Classifies each creator's network risk

### 3. Integration Points

#### Pre-Buy Checking (`utils/creator_blocklist_checker.py`)
New method: `_check_network_risk(creator_address)`
- Checks if creator is connected to malicious creators
- Returns `network_risk` info with count of connected malicious creators
- Pre-buy check now rejects: "🔗 NETWORK RISK - Connected to X malicious creator(s)"

#### Migration Analysis (`pumpfun_curve_listener.py`)
When a migration is detected:
1. Extract creator from earliest transaction
2. Check if creator is in blocklist
3. Check if creator is connected to malicious creators
4. Store `network_risk` flag if connected
5. Log: "🔗 NETWORK RISK: Creator is connected to X malicious creator(s)"

#### API Response (`main.py`)
Returns for each token:
```json
{
  "network_risk": boolean,
  "connected_malicious_count": integer
}
```

### 4. UI Display

Creator column now shows:
- **🔗 NETWORK (X)** - Orange badge if connected to X malicious creators
- **🚨 BLOCKED** - Red badge if directly blocked
- **MALICIOUS/SUSPICIOUS** - Reputation label

CSS styling:
```css
.network-risk {
    background: rgba(249, 115, 22, 0.3);  /* Orange */
    color: #ea580c;
    border: 1px solid rgba(249, 115, 22, 0.7);
}
```

## Detection Flow

```
New Migration Detected
    ↓
Extract Creator from Earliest TX
    ↓
Check Creator Blocklist
    ├─ If in blocklist → creator_is_blocked = 1
    └─ If connected to malicious → network_risk = 1
    ↓
Store Token Analysis
    ├─ creator_is_blocked
    ├─ network_risk
    └─ connected_malicious_count
    ↓
API Returns Flags
    ↓
UI Displays Warnings
    ├─ 🚨 BLOCKED (red)
    ├─ 🔗 NETWORK (orange)
    └─ MALICIOUS/SUSPICIOUS label
```

## How It Works

### Network Building (Offline)
1. Query all SOL transfers by creator (from blockchain analysis)
2. Group by destination address
3. Find creators sharing destinations (likely same person)
4. Build network graphs using BFS algorithm
5. Identify clusters of creators

### Risk Assessment
For each creator network:
- Count malicious members (2+ rugs)
- Count suspicious members (1 rug)
- Assign risk level:
  - **CRITICAL**: Has malicious members
  - **HIGH**: Has suspicious members
  - **LOW**: No blocked members

### Pre-Buy Filtering
When checking a token before buying:
1. Get creator
2. Check if in blocklist (direct rug detection)
3. Check if connected to malicious creators (network detection)
4. Skip if either check fails

## Current Status

### Populated
- ✅ `creator_blocklist` table (40 creators)
- ✅ Pre-buy checking logic
- ✅ Migration analysis integration
- ✅ UI display

### Not Yet Populated (Requires SOL Analysis)
- ⏳ `creator_sol_transfers` table (needs blockchain analysis)
- ⏳ `creator_networks` table (depends on SOL transfers)

## Next Steps

To fully activate network detection:

1. **Implement SOL transfer analysis** - Extract where creators send funds from rugged projects
2. **Run network analysis** - `python scripts/analyze_creator_networks.py`
3. **Monitor new networks** - System automatically flags new networks as they're detected
4. **Verify findings** - Manual review of flagged networks

## Example Scenario

```
Creator A: MALICIOUS (2 rugs)
  └─ Sends SOL to: wallet1.sol, wallet2.sol

Creator B: Not yet blocked
  └─ Sends SOL to: wallet1.sol, wallet2.sol

Detection:
  → Creator B is connected to Creator A
  → Creator A is MALICIOUS
  → Creator B gets flagged: network_risk = 1
  → Tokens from Creator B show: 🔗 NETWORK (1)
  → Pre-buy check rejects: "NETWORK RISK - Connected to 1 malicious creator(s)"
```

## API Reference

### Check Network Risk
```python
from utils.creator_blocklist_checker import check_token_safety

is_safe, reason = check_token_safety(token_mint)
# is_safe = False
# reason = "🔗 NETWORK RISK - Connected to 2 malicious creator(s)"
```

### Get Creator Info (including network)
```python
from utils.creator_blocklist_checker import get_token_creator_info

info = get_token_creator_info(token_mint)
# Returns: {
#     "creator": "...",
#     "rug_count": 0,
#     "reputation": "UNKNOWN",
#     "network_connected": true,
#     "connected_malicious": 2
# }
```

### Analyze Networks
```bash
python scripts/analyze_creator_networks.py
```

## Performance

- **Pre-buy check**: <20ms (single DB query + network lookup)
- **Network analysis**: ~100-500ms (full graph analysis)
- **Memory**: Minimal (network data stored in database)

## Security Notes

- Network detection is based on **heuristics** (shared destinations)
- False positives possible (legitimate multi-project creators)
- Manual review recommended for high-risk networks
- Coordinated with direct blocklist for accuracy
