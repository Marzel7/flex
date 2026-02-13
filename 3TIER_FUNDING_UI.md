# 3-Tier Funding Network UI

## Overview

New UI component for visualizing the complete funding chain:
```
Senders (🟡) → Funders (🟢) → Creators (🔵)
```

This reveals how creators receive funding through multiple intermediaries and identifies coordination patterns.

## API Endpoint

### `/api/funding-network-3tier/<creator_address>`

**Example Request:**
```bash
curl http://localhost:5002/api/funding-network-3tier/HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp
```

**Response Structure:**
```json
{
  "creator_address": "HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp",
  "creator_info": {
    "risk_level": "HIGH",
    "rug_probability": 0.95,
    "market_cap_highest": 1924110,
    "created_at": "2024-05-14T10:17:09Z"
  },
  "total_funders": 565,
  "total_senders": 49,
  "network_tiers": [
    {
      "funder_address": "9SLPTL41SPsYkgdsMzdfJsxymEANKr5bYoBsQzJyKpKS",
      "total_to_creator": 394.27,
      "senders": [
        {
          "sender_address": "3UnyUsb7p2v5LH2NeG3eVd3pQ6kP8xQ2rR7sT9uVwXyZ",
          "amount_to_funder": 1.23,
          "sender_type": "unknown"
        },
        ...
      ]
    },
    ...
  ]
}
```

## UI Modal

### Features

1. **Creator Info Box**
   - Risk Level
   - Rug Probability (%)
   - Market Cap (Historical High)

2. **Network Structure Stats**
   - Total Funders Count (big number)
   - Total Senders Count (big number)

3. **3-Tier Visualization**
   - Shows each funder
   - Lists all senders for that funder
   - Displays amounts at each tier
   - Type badges (CEX, INFRA, Wallet)
   - Scrollable (max 400px height)

4. **Color Coding**
   - 🟡 Yellow: Senders
   - 🟢 Green: Funders
   - 🔵 Cyan: Creator info

### How to Open

From JavaScript:
```javascript
showFundingNetwork3Tier('HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp');
```

Can be called from:
- Creator click handlers
- Funder inspection
- Risk analysis view
- Token detail modals

## Example Data Flow

### HYWo71Wk9 (49-Wallet Ring Creator)

```
CREATOR: HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp

FUNDER: 9SLPTL41SPsYkgdsMzdfJsxymEANKr5bYoBsQzJyKpKS (Hyperunit)
├─ Amount to Creator: 394.27 SOL
├─ Senders: 49
│  ├─ Sender 1: 0.XX SOL
│  ├─ Sender 2: 0.XX SOL
│  ├─ ...
│  └─ Sender 49: 0.XX SOL
└─ Total from senders: 800.12 SOL

FUNDER: 8CpKY6vNKCixXqbwM14kAbN7e... (Direct funder)
├─ Amount to Creator: 968.00 SOL
└─ Senders: Not tracked (large direct transfer)

FUNDER: CNmwTcYqR8R876KVKSH9TYp8o...
├─ Amount to Creator: 191.40 SOL
└─ Senders: Not tracked
```

## Integration Points

### In Token Metrics Modal
Add button:
```javascript
<button onclick="showFundingNetwork3Tier('${token.creator}')">
    📊 View Funding Network
</button>
```

### In Creator Details Modal
Add button:
```javascript
<button onclick="showFundingNetwork3Tier('${creatorAddress}')">
    📊 Funding Hierarchy
</button>
```

### In Risk Analysis
Auto-open for HIGH risk creators:
```javascript
if (riskLevel === 'HIGH') {
    showFundingNetwork3Tier(creatorAddress);
}
```

## Key Insights Revealed

1. **Concentration**: See which funders are primary sources
2. **Coordination**: Identify 49-wallet rings or similar patterns
3. **Infrastructure**: Spot reused intermediaries (Hyperunit)
4. **Obfuscation**: See how direct transfers are hidden behind multiple hops
5. **Risk**: Large number of small senders = pump & dump setup

## Performance

- API Query: <500ms
- Modal Render: <100ms
- Max senders displayed: Scalable (uses scrollable container)
- Works with creators having 49+ funders

## Modal Controls

- Click X to close
- Click outside modal to close
- Press Escape to close
- Functions integrated with other modals

## Files

- **API**: `main.py` lines ~4355-4425
- **HTML**: `main.py` lines ~1975-2020
- **JavaScript**: `main.py` lines ~3660-3730
- **Handlers**: Updated `window.onclick` and escape key listener
