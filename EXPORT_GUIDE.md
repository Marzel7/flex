# Flex Database Export Guide

## Files Available

### 1. **flex_complete_database.db** (2.9 GB)
✅ **RECOMMENDED FOR CLUSTERING ANALYSIS**

Complete SQLite database with all 47 tables including:
- `creator_funders` - 43,019 funder-creator relationships
- `funder_networks` - 41,734 computed funder clusters  
- `network_coordinators` - 659 identified coordinators
- `token_analysis` - Token data with creators
- `creator_outgoing_transfers` - 7,400 transfer records
- All supporting tables and metadata

**How to use:**
```bash
# Direct import into analyzer
python3 cross_funding_network_analyzer.py --db flex_complete_database.db

# Or query directly
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM creator_funders;"
```

### 2. **flex_complete_analysis_data.xlsx** (8.17 MB)
Spreadsheet export with 5 sheets:
- **Creators** (1,711 rows) - Token creators with mint addresses
- **Creator-Funders** (43,019 rows) - All funding relationships
- **Funder-Networks** (41,734 rows) - Computed network clusters
- **Coordinators** (659 rows) - Recipient hub coordinators
- **Creator-Outgoing** (7,400 rows) - Creator SOL transfers

**Use case:** For spreadsheet analysis, sharing with non-technical stakeholders

### 3. **flex_addresses_export.xlsx** (459 MB)
Original export (address summaries only)
- Not recommended for clustering - missing relationship data

## Data Available for Clustering

✅ **What you can cluster:**
- Creator-Funder relationships (43,019 records)
- Funder co-funding patterns (591 clusters identified)
- Recipient hub coordination (659 coordinators)
- Funder network expansion (41,734 network records)

❌ **What's not populated:**
- `creator_recipients_unified` (0 rows)
- `creator_sol_transfers` (0 rows) 
- `creator_networks` (0 rows) - requires `creator_sol_transfers`
- `unified_creator_clusters` (0 rows) - requires full relationships

## Running the Analyzer

```bash
# With complete database
python3 cross_funding_network_analyzer.py

# Or load from DataFrame if analyzing Excel exports
import pandas as pd

# Load creator-funder relationships
creator_funders = pd.read_excel('flex_complete_analysis_data.xlsx', 
                                 sheet_name='Creator-Funders')

# Load network data
funder_networks = pd.read_excel('flex_complete_analysis_data.xlsx',
                                 sheet_name='Funder-Networks')

# Run clustering on loaded data
from cross_funding_network_analyzer import CrossFundingClusterAnalyzer
analyzer = CrossFundingClusterAnalyzer(db_path=None)  # Use dataframes instead
```

## Database Schema - Key Tables

### creator_funders (43,019 rows)
```
creator_address    - Creator wallet address
funder_address     - Funder wallet address  
amount_sol         - SOL transferred
first_detected_at  - When first detected
is_cex            - Is CEX wallet?
cex_exchange      - Which exchange
source_type       - Detection method
total_inflows     - Total SOL received
total_outflows    - Total SOL sent
net_change        - Net SOL position
```

### funder_networks (41,734 rows)
```
primary_funder     - Lead funder in cluster
connected_funders  - List of related funders
network_size      - Number of connected funders
creators_served   - How many creators funded
total_volume_sol  - Total SOL moved
transfer_chain    - Transaction path
detected_at       - Detection timestamp
```

### network_coordinators (659 rows)
```
coordinator_address - Address receiving from 2+ creators
creator_count      - Number of creators funding
creators_linked    - Specific creators
total_sol_moved    - Total SOL received
network_confidence - Confidence score (0-1)
is_cex            - Is CEX wallet?
cex_exchange      - Which exchange
```

## Statistics

- **Total Creators**: 1,339 (from 1,711 tokens)
- **Unique Funders**: 42,016
- **Creator-Funder Relationships**: 43,019
- **Identified Funder Clusters**: 591
- **Network Coordinators**: 659
- **Largest Funder Cluster**: 6,485 connected funders

## For Your Analysis

1. **Start with** `flex_complete_database.db`
   - Has everything you need
   - 2.9 GB is manageable
   - Run analyzer directly against it

2. **If you need spreadsheets**, use `flex_complete_analysis_data.xlsx`
   - 8.17 MB is small
   - Contains all key relationships
   - Easy to pivot/filter in Excel

3. **Import guide:**
   ```python
   import sqlite3
   import pandas as pd
   
   # From database
   conn = sqlite3.connect('flex_complete_database.db')
   creator_funders = pd.read_sql_query(
       'SELECT * FROM creator_funders', conn)
   
   # From Excel
   creator_funders = pd.read_excel(
       'flex_complete_analysis_data.xlsx', 
       sheet_name='Creator-Funders')
   ```

## Missing Data Explanation

Some tables are empty because:
- `creator_recipients_unified` - Requires upstream data from another analysis phase
- `creator_sol_transfers` - Would need on-chain transaction parsing
- `creator_networks` - Depends on `creator_sol_transfers`

But the core data you need (**creator_funders** + **funder_networks**) is fully populated and ready for clustering analysis.

---

**Export Date**: Feb 20, 2026
**Database Size**: 2.9 GB
**Spreadsheet Size**: 8.17 MB
**Format**: SQLite + Excel
