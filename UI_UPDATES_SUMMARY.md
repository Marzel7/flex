# UI Updates: Super Cluster SOL Flow Visualization

## Overview

Enhanced the Flask API endpoints in `main.py` to provide detailed SOL flow information for super clusters, including:
- Root operator identification
- Upstream/downstream SOL flows with addresses
- Network names associated with clusters
- Token risk details for created tokens

## Updated Endpoints

### 1. `/api/super-cluster/<cluster_id>` (Enhanced)

**What Changed:**
- Added network names list
- Added SOL flow examples (funder >> creator pairs)
- Added identified root operators with statistics

**Returns:**
```json
{
  "id": "net_00647",
  "networks": [
    {
      "network_id": 123,
      "network_name": "Network Name Here",
      "total_members": 50,
      "total_sol": 5000.00
    }
  ],
  "sol_flow_examples": [
    {
      "funder": "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS",
      "creator": "123157i3TZqhrbUFPY8pkexuHtCjH3TnuSuugxdabb3P",
      "sol_amount": 12.31,
      "date": "2026-02-14",
      "token_mint": "6RLKHhdsTwWqz7kRkdigL4488sNYJA...",
      "token_risk": "None",
      "rug_probability": null
    }
  ],
  "identified_root_operators": [
    {
      "funder_address": "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS",
      "creators_funded": 18,
      "total_sol_sent": 1216.14,
      "transfer_count": 18,
      "first_transfer": "2026-02-10"
    }
  ]
}
```

### 2. `/api/super-cluster/<cluster_id>/sol-flow` (New)

**Purpose:** Detailed SOL flow visualization showing complete funding chain

**Returns:**
```json
{
  "cluster_id": "net_00647",
  "networks": [
    {
      "network_id": 123,
      "network_name": "Network Name",
      "total_sol": 5000.00
    }
  ],
  "sol_flows": [
    {
      "root_operator": "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS",
      "creators_funded": 18,
      "total_sol_sent": 1216.14,
      "upstream_sources": [
        {
          "funder_address": "upstream_address",
          "creator_address": "root_operator_address",
          "amount_sol": 100.00,
          "first_detected_at": "2026-02-10"
        }
      ],
      "downstream_creators": [
        {
          "creator_address": "creator_address",
          "amount_sol": 50.00,
          "first_detected_at": "2026-02-10",
          "token_mint": "token_address",
          "risk_level": "HIGH",
          "rug_probability": 0.85
        }
      ]
    }
  ],
  "total_root_operators": 10,
  "top_flows_shown": 10
}
```

## Data Structure Explanation

### SOL Flow Chain Format
```
UPSTREAM FUNDER (Original Source)
    ↓
    └─ X SOL → ROOT OPERATOR (Funder of multiple creators)
                    ↓
                    └─ Y SOL → CREATOR A (Creates token)
                    └─ Z SOL → CREATOR B (Creates token)
                    └─ ... (multiple creators)
```

### Example from net_00647
```
[NO UPSTREAM SOURCE]
    └─ ROOT OP: 2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS (1,216.14 SOL)
         ├─ 12.31 SOL → 123157i3TZqhrbUFPY8pkexuHtCjH3TnuSuugxdabb3P
         ├─ 172.93 SOL → HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp
         └─ ... (18 total creators funded)
```

## Root Operator Identification

Root operators are identified as funders that:
1. Fund **multiple creators** (not just one)
2. Have **high SOL volumes** (hundreds or thousands)
3. Funded creators **in the same super cluster**
4. Are **not infrastructure/CEX accounts**

Example root operators in net_00647:
1. `2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS` - 1,216.14 SOL → 18 creators
2. `AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk` - 187.59 SOL → 5 creators

## Network Names

Network names are now fetched from the `funding_networks` table and displayed for each super cluster. Each network represents a coordinated funding group with:
- Network ID (e.g., "net_00028")
- Network name (e.g., "Coordinated Group A")
- Number of members
- Total SOL allocated

## Implementation Details

### Key Changes in main.py

1. **Enhanced `api_super_cluster_details()` function:**
   - Lines 10034-10134: Updated to return `networks`, `sol_flow_examples`, and `identified_root_operators`

2. **New `api_super_cluster_sol_flow()` function:**
   - Lines 10259-10374: Provides detailed upstream/downstream SOL flow data
   - Traces funding chains from original sources through root operators to creators
   - Includes token risk information for each created token

### Database Queries Used

1. **Funding Networks:** Queries `funding_networks` and `funding_network_members` tables
2. **Root Operators:** Groups `creator_funders` by funder_address with HAVING clause
3. **SOL Flows:** Traces both upstream (who funds root ops) and downstream (root ops fund creators)
4. **Infrastructure Filtering:** Excludes `INFRASTRUCTURE_ACCOUNTS` and `CEX_ACCOUNTS`

## Testing

Test endpoints can be accessed at:
```
http://localhost:5002/api/super-cluster/net_00647
http://localhost:5002/api/super-cluster/net_00647/sol-flow
```

## Example Usage in Frontend

```javascript
// Get cluster overview with networks and flows
fetch('/api/super-cluster/net_00647')
  .then(r => r.json())
  .then(data => {
    console.log('Networks:', data.networks);
    console.log('Root Operators:', data.identified_root_operators);
    console.log('Flow Examples:', data.sol_flow_examples);
  });

// Get detailed SOL flow visualization
fetch('/api/super-cluster/net_00647/sol-flow')
  .then(r => r.json())
  .then(data => {
    // Render upstream/downstream flows
    data.sol_flows.forEach(flow => {
      console.log(`${flow.root_operator} funds ${flow.creators_funded} creators`);
    });
  });
```

## UI Display Suggestions

### Super Cluster Overview Card
```
net_00647 - CRITICAL RISK
├─ 23 Networks
├─ 152 Creators
├─ 20,000+ SOL Total
└─ Root Operators: 10 identified
   • 2snHHreXbpJ7... (1,216 SOL → 18 creators)
   • AxiomRXZAq1Jg... (187 SOL → 5 creators)
```

### SOL Flow Diagram
```
UPSTREAM                ROOT OPERATOR           DOWNSTREAM
─────────              ─────────────────────   ──────────────────
                       2snHHreXbpJ7...
                       (1,216.14 SOL)
                             ├─→ 12.31 → Creator A (TOKEN: 6RLK... Risk: None)
                             ├─→ 172.93 → Creator B (TOKEN: HYWo... Risk: None)
                             └─→ ... (16 more creators)
```

## Files Modified

- `main.py`: Updated `api_super_cluster_details()` and added `api_super_cluster_sol_flow()`

## Backward Compatibility

✅ All changes are **backward compatible**. The existing endpoint structure is preserved, with additional fields added:
- Existing fields still returned
- New fields optionally consumed by updated UI
- Old UI will continue to work without changes

## Next Steps

1. Update frontend to display network names
2. Create visual flow diagram component
3. Add root operator detail view
4. Implement SOL flow animation/diagram
5. Add filtering by risk level or creator count
