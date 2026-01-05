# SOL Destination Address Analysis & Creator Network Mapping

## Overview
The system now tracks and analyzes SOL transfer destinations to build a network graph of creator relationships. This allows you to:

1. **Find all destinations a creator sends SOL to**
2. **Identify which creators share the same SOL destinations** (potential coordinated activity)
3. **Detect aggregation hubs** (addresses receiving from multiple creators)
4. **Build creator relationship networks** through shared fund destinations

## Database Storage

### Creator SOL Transfers Table
All outgoing SOL transfers are stored in `creator_sol_transfers`:
- `creator_address` - Source wallet creating tokens
- `counterparty_address` - Destination address receiving SOL
- `transfer_type` - 'outgoing' (we focus on this for destination analysis)
- `total_amount` - Total SOL sent to this destination
- `transfer_count` - Number of times sent to this address
- `is_treasury` - Flagged if >5 transfers to same address (potential treasury)

## Tools Available

### 1. `analyze_sol_destinations.py` - Destination Analysis
**Purpose**: Analyze all SOL destination addresses and their usage patterns.

```bash
# Show all destinations with creator usage stats
python3 analyze_sol_destinations.py

# Show only shared destinations (used by multiple creators)
python3 analyze_sol_destinations.py --frequent
python3 analyze_sol_destinations.py --suspicious

# Show all creators sending to a specific address
python3 analyze_sol_destinations.py <destination_address>
```

**Output Example**:
```
ALL SOL DESTINATIONS (9 total)
Destination                    | Creators | Total SOL | Transfers | Treasury
─────────────────────────────────────────────────────────────────────────────
6fcpd6kmkkr...rpfgaa          |    1     |  81.9733  |    15     |    🏦
hvbcknaac4m...ewztuc          |    1     |  50.0000  |     1     |
8cyjozvydot...zazrh7o         |    1     |  15.0000  |     3     |
```

### 2. `find_creator_connections.py` - Creator Network Analysis
**Purpose**: Find which creators share SOL destinations (potential coordinated activity).

```bash
# Show all creators connected to a specific creator
python3 find_creator_connections.py <creator_address>

# Show entire creator network
python3 find_creator_connections.py --network
python3 find_creator_connections.py --all-connections
```

**Output**: Shows creator pairs that send to the same destinations with detailed comparison.

### 3. `sol_network_analysis.py` - Network Overview
**Purpose**: Comprehensive network statistics and aggregation hub detection.

```bash
# Show overall network summary
python3 sol_network_analysis.py

# Find aggregation hubs (destinations with multiple sources)
python3 sol_network_analysis.py --aggregation

# Show all creators sending to a destination
python3 sol_network_analysis.py <destination_address>
```

**Output Example**:
```
SOL TRANSFER NETWORK SUMMARY

Network Size:
  Creators (sources): 5
  Destinations (sinks): 23
  Total connections: 47
  
Fund Flow:
  Total SOL transferred: 1,234.5678
  Average per creator: 246.9136
  
Network Topology:
  Aggregation hubs (destinations with multiple sources): 3
```

## How It Works

### Data Flow
```
analyze_creator_wallet.py (analyzes wallet)
    ↓
    Stores SOL transfers in database
    ↓
creator_sol_transfers table (9 outgoing transfers)
    ↓
Query tools analyze relationships
    ↓
find_creator_connections.py (creator networks)
analyze_sol_destinations.py (destination analysis)
sol_network_analysis.py (network overview)
```

### Example Network Discovery

**Step 1**: Analyze Creator A
```
python3 analyze_creator_wallet.py CreatorA_address
```
Finds CreatorA sends SOL to destinations: [addr1, addr2, addr3]

**Step 2**: Analyze Creator B  
```
python3 analyze_creator_wallet.py CreatorB_address
```
Finds CreatorB sends SOL to destinations: [addr1, addr3, addr4]

**Step 3**: Query for connections
```
python3 find_creator_connections.py CreatorA_address
```
Shows: "CreatorB shares 2 destinations with CreatorA!"

**Step 4**: Check aggregation hubs
```
python3 sol_network_analysis.py --aggregation
```
Shows which addresses are receiving from multiple creators (potential treasury accounts)

## Detection Capabilities

### Coordinated Activity Patterns
- **Shared Treasuries**: Multiple creators sending to the same address
- **Network Clustering**: Groups of creators with overlapping destinations
- **Fund Aggregation**: Single address receiving from many creators
- **Profit Extraction**: Identifying where creators consolidate funds

### Risk Indicators
- 🏦 **Treasury**: Address receives >5 transfers from creator (aggregation point)
- **Multi-source funding**: Address receives from multiple creators (suspicious network)
- **High frequency**: Rapid fund movement through aggregation points

## Example Usage Workflow

```bash
# 1. Analyze a new creator
python3 analyze_creator_wallet.py 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA --full

# 2. Check what destinations they use
python3 analyze_sol_destinations.py

# 3. Look for other creators using same destinations  
python3 find_creator_connections.py 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA

# 4. Identify aggregation hubs
python3 sol_network_analysis.py --aggregation

# 5. Query specific suspicious address
python3 sol_network_analysis.py hvbcknaac4mvgj7vs7g2bawnhaw63btfsp9z45ewztuc
```

## Database Queries

### Find all destinations for a creator
```sql
SELECT counterparty_address, total_amount, transfer_count, is_treasury
FROM creator_sol_transfers
WHERE creator_address = '...' AND transfer_type = 'outgoing'
ORDER BY total_amount DESC;
```

### Find creators sharing a destination
```sql
SELECT DISTINCT creator_address, total_amount
FROM creator_sol_transfers
WHERE counterparty_address = '...' AND transfer_type = 'outgoing';
```

### Find multi-source aggregation points
```sql
SELECT counterparty_address, COUNT(DISTINCT creator_address) as sources
FROM creator_sol_transfers
WHERE transfer_type = 'outgoing'
GROUP BY counterparty_address
HAVING sources > 1
ORDER BY sources DESC;
```

## Next Steps

1. **Analyze more creators** - Run `analyze_creator_wallet.py` on multiple creator addresses
2. **Build the network** - Tools automatically aggregate relationships
3. **Identify patterns** - Use tools to find suspicious clusters
4. **Export data** - Consider JSON export for visualization tools (Gephi, D3.js)

## Notes

- All destination data is automatically stored when analyzing a creator wallet
- The database maintains relationships and can be queried historically
- Treasury detection is automatic (>5 transfers = 🏦 flag)
- Network tools work best with 3+ analyzed creators
