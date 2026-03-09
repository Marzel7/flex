# Flex - Token Funding Network Analyzer

## Project Overview
Flex is a Solana token analysis dashboard that tracks funding networks, identifies coordinated funder relationships, and detects suspicious pump-and-dump schemes across Pump.Fun tokens.

## Funding Extraction Logic

### Overview
The system uses a 3-step cascade to build complete funding networks:
1. **Creator Funding** - Who funded the creator?
2. **Funder Transfers** - Who funded the funders?
3. **Network Clustering** - Build relationship maps

### Step-by-Step Flow

When a new token is detected in `pumpfun_curve_listener.py` (line 1724-1741):

#### Step 1: Extract Creator Funding
- **File**: `realtime_creator_funding_extractor.py`
- **Function**: `extract_funding_for_new_token(creator_address, created_at, create_tx_sig, mint)`
- **Triggered**: Line 1728 in pumpfun_curve_listener.py
- **What it does**:
  - Finds all funders who sent SOL to the creator
  - Populates `creator_funders` table with:
    - `creator_address` - The token creator
    - `funder_address` - Who funded them
    - `amount_sol` - Amount sent
    - `transaction_signature` - The transfer TX

#### Step 2: Extract Funder Transfers
- **File**: `funder_incoming_extractor.py`
- **Function**: `extract_for_creator()` (wrapped as `extract_funder_transfers_async()`)
- **Triggered**: Line 1734 in pumpfun_curve_listener.py
- **What it does**:
  - For EACH funder from Step 1, extracts their incoming/outgoing transfers
  - Identifies WHERE the funders got their money
  - Populates `funder_incoming_transfers` table with:
    - `funder_address` - The funder
    - `sender_address` - Who sent to the funder
    - `amount_sol` - Amount received
    - `transaction_signature` - The transfer TX

#### Step 3: Update Network Clustering
- **File**: `pumpfun_curve_listener.py`
- **Function**: `update_network_clustering_async()`
- **Triggered**: Line 1741 in pumpfun_curve_listener.py
- **What it does**:
  - Rebuilds network relationships from extracted funding data
  - Updates `super_clusters` table
  - Identifies coordinated funding networks

### Database Tables

**creator_funders** - Direct creator funding
```
creator_address      TEXT - Token creator
funder_address       TEXT - Who funded the creator
amount_sol           REAL - SOL amount
transaction_signature TEXT - TX hash
first_detected_at    TIMESTAMP
is_cex              BOOLEAN
fully_analyzed      INT - 0/1 status
```

**funder_incoming_transfers** - Funder's incoming sources
```
funder_address       TEXT - The funder
sender_address       TEXT - Who sent to the funder
amount_sol           REAL - SOL amount
transaction_signature TEXT - TX hash
block_time           INT - Timestamp
```

### Flow Example

Token detected: `DxoTY4uEXvsvD4ye1wULms7Bou2CbCj9c2VHwLLepump`
Creator: `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`

**Step 1 finds:**
- Funder A sent 0.5 SOL to bwamJzzt
- Funder B sent 0.3 SOL to bwamJzzt
- (942 funders total)

**Step 2 finds:**
- Sender X sent 0.1 SOL to Funder A
- Sender Y sent 0.2 SOL to Funder A
- (Repeats for all 942 funders)

**Step 3 builds:**
- Network relationships showing X,Y → A → bwamJzzt → token

## Self-Funding Detection

The system identifies **self-funding schemes** where:
- Sender distributes to many intermediate funders
- All those funders only send back to the sender
- Sender then creates tokens using this false "support"

**Example**: bwamJzzt
- Creates 942 intermediate wallet addresses
- Sends tiny amounts to each
- Each funder sends funds back to bwamJzzt
- bwamJzzt then creates 23 tokens

The `/funding-hub/<address>` page shows:
- **Self-Funding Intermediates** (in red) - Count of funders that only fund the sender back
- **Third-Party Funded Creators** - Count of OTHER creators these funders actually fund

## UI Pages

### Main Dashboard (`/`)
- Token list with funding progress indicator
- Navigation buttons: Networks, Clusters, Coordinated Funders, Hubs, Validate TX

### Networks (`/networks`)
- Shows atomic funder networks
- Color scheme: Green (Funders) | Yellow (Creators) | Blue (Senders) | Purple (Tokens)

### Clusters (`/clusters`)
- Cross-funding cluster analysis
- Risk multipliers for coordinated networks

### Coordinated Funders (`/coordinated-funders`)
- List of funders supporting multiple creators
- Clickable to view individual funding hubs

### Top Hubs (`/top-funding-hubs`)
- Top 20 distribution senders by funder count
- Shows:
  - Funders Funded (count)
  - Creators Funded (shows "-" if self-funded)
  - Total Tokens Launched
  - SOL Distributed
  - Badge if sender is also a creator

### Individual Hub (`/funding-hub/<address>`)
- Detailed breakdown of sender → funders → creators → tokens
- Separates self-funding intermediaries from legitimate third-party funders
- Shows only third-party funded creators in table

## Settings

Located in `listener_settings` table:
- `listen_to_launches` - Monitor new token launches
- `listen_to_price_updates` - Track price changes
- `auto_extract_funding` - Automatically extract funding on new tokens

## Color Scheme

Consistent across all pages:
- **Cyan** (#06b6d4) - Links, back buttons
- **Purple** (#a78bfa) - Headers, accents
- **Blue** (#3b82f6) - Senders, funders, buttons
- **Yellow** (#fbbf24) - Creators
- **Green** (#22c55e) - Tokens, CLEAN status
- **Red** (#ef4444) - Self-funded, suspicious

## Button Styling

All buttons on main page use consistent blue style:
```
background: rgba(59, 130, 246, 0.2)
color: var(--color-none)  // Blue
border: 1px solid rgba(59, 130, 246, 0.5)
```

Applies to: Networks, Clusters, Coordinated Funders, Hubs, Tokens, Polling, Validate TX

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Flask app, API endpoints, UI pages |
| `pumpfun_curve_listener.py` | WebSocket listener, funding extraction trigger |
| `realtime_creator_funding_extractor.py` | Creator → Funder extraction |
| `funder_incoming_extractor.py` | Funder → Sender extraction |
| `cross_funding_network_analyzer.py` | Network relationship analysis |
| `funder_helius_extractor.py` | Helius API integration for funder data |

## Recent Improvements

1. **Self-Funding Detection** - Identifies circular funding schemes (942-funder example)
2. **Consistent Color Scheme** - All pages use Networks page design system
3. **Top Hubs Dashboard** - Ranks senders by distribution reach
4. **Individual Hub Pages** - Shows complete sender → funders → creators flow
5. **Button Consistency** - All navigation uses same blue color
