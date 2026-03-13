# FLEX Database Structure Documentation

**Database:** `/database/flex_complete_database.db` (SQLite)

## Overview

The FLEX database is organized into several interconnected domains:

1. **Organization & Developer Intelligence** - Core dev farm detection and organization analysis
2. **Token Analysis** - Token-level rug/risk assessment
3. **Funding Network Analysis** - Wallet and funder relationships
4. **Launch Prediction** - Launch probability and wave detection
5. **RPC & Operational Metrics** - Performance and cost tracking
6. **Webhook & Monitoring** - Real-time event tracking

---

## Core Tables

### Organization & Developer Intelligence

#### `dev_organizations`
**Purpose:** Central table tracking detected developer organizations/farms

| Column | Type | Description |
|--------|------|-------------|
| `organization_id` | INTEGER PK | Unique org identifier |
| `operator_wallet` | TEXT UNIQUE | Primary wallet operator address |
| `cluster_size` | INTEGER | Number of wallets in org network |
| `wallet_count` | INTEGER | Total unique wallets in org |
| `creator_count` | INTEGER | Total creator addresses in org |
| `token_count` | INTEGER | Total tokens launched by org |
| `token_list` | TEXT (JSON) | Array of mint addresses: `["mint1", "mint2", ...]` |
| `creator_list` | TEXT (JSON) | Array of creator addresses |
| `wallet_list` | TEXT (JSON) | Array of wallet addresses |
| `organization_score` | REAL | 0-1 composite org risk score |
| `degree_centrality` | REAL | Operator's network degree centrality |
| `betweenness_centrality` | REAL | Operator's betweenness centrality |
| `pagerank_score` | REAL | PageRank score in funding network |
| `total_volume_sol` | REAL | Total SOL transferred within org |
| `avg_edge_weight` | REAL | Average edge weight in org network |
| `cluster_strength` | REAL | 0-100 coordination strength indicator |
| `farm_cluster_id` | INTEGER FK | Reference to `farm_clusters` if applicable |
| `detected_at` | REAL | Unix timestamp when org was detected |
| `updated_at` | REAL | Unix timestamp of last update |

**Key Indexes:**
- `idx_dev_orgs_score` - Query by risk score (DESC)
- `idx_dev_orgs_operator` - Query by operator wallet
- `idx_dev_orgs_token_count` - Query by token count (DESC)
- `idx_dev_orgs_detected_at` - Query by detection date (DESC)

**Sample Query:**
```sql
SELECT * FROM dev_organizations
ORDER BY organization_score DESC
LIMIT 100;
```

---

#### `dev_organization_members`
**Purpose:** Individual members of each organization (wallets, creators, tokens)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Member identifier |
| `organization_id` | INTEGER FK | Parent org |
| `member_address` | TEXT | Wallet or creator address |
| `member_type` | TEXT | 'wallet' \| 'creator' \| 'token' |
| `degree_centrality` | REAL | Member's degree centrality |
| `betweenness_centrality` | REAL | Member's betweenness centrality |
| `pagerank_score` | REAL | Member's PageRank score |
| `token_count` | INTEGER | Tokens launched by this member |
| `total_volume_sol` | REAL | Total SOL sent by this member |
| `role_confidence` | REAL | 0-1 confidence in member role |
| `detected_at` | REAL | Unix timestamp of detection |

**Relationships:**
- One org can have many members
- Members are wallets (funders), creators, or tokens
- Enables per-member analysis within an organization

---

### Launch Prediction & Signals

#### `master_launch_signals`
**Purpose:** Composite launch prediction signals for each organization

| Column | Type | Description |
|--------|------|-------------|
| `signal_id` | INTEGER PK | Signal identifier |
| `organization_id` | INTEGER FK UNIQUE | Parent org (1-to-1 with org) |
| `launch_probability` | REAL | 0-100 probability of imminent launch |
| `launch_wave_score` | REAL | 0-100 wave detection score |
| `seed_concentration` | REAL | 0-1 seed funding concentration |
| `funder_overlap_score` | REAL | 0-1 funding overlap across tokens |
| `organization_momentum` | REAL | 0-100 activity momentum score |
| `creator_reuse_score` | REAL | 0-1 creator reuse coefficient |
| `operator_activity_score` | REAL | 0-100 operator activity level |
| `reputation_adjustment` | REAL | -50 to +50 reputation modifier |
| `master_launch_score` | REAL | 0-100 final composite score |
| `alert_level` | TEXT | 'CRITICAL' \| 'HIGH' \| 'WATCH' \| 'LOW' |
| `computed_at` | REAL | Unix timestamp of computation |

**Key Indexes:**
- `idx_mls_score` - Query by master score (DESC) - Used for leaderboards
- `idx_mls_alert` - Query by alert level
- `idx_mls_org_id` - Direct org lookup

**Used By:** Launch Radar page (top candidates), Alert systems

---

#### `org_launch_predictions`
**Purpose:** Time-series launch predictions with signal breakdown

| Column | Type | Description |
|--------|------|-------------|
| `prediction_id` | INTEGER PK | Prediction identifier |
| `organization_id` | INTEGER FK | Parent org |
| `prediction_date` | TEXT | 'YYYY-MM-DD' prediction date |
| `launch_probability` | REAL | 0-100 final score |
| **Signal Components (normalized points):** | | |
| `signal_recency` | REAL | 0-30: days since last funding |
| `signal_scale` | REAL | 0-20: org size composite |
| `signal_launch_rate` | REAL | 0-20: historical tokens/day |
| `signal_funding_velocity` | REAL | 0-15: SOL/active_day |
| `signal_coordination` | REAL | 0-10: avg_composite_weight |
| `signal_network_risk` | REAL | 0-5: avg rug_probability |
| **Raw Signal Inputs (for debugging):** | | |
| `days_since_last_funding` | REAL | Raw days since activity |
| `org_token_count` | INTEGER | Snapshot of token_count |
| `org_creator_count` | INTEGER | Snapshot of creator_count |
| `org_wallet_count` | INTEGER | Snapshot of wallet_count |
| `avg_tokens_launched` | REAL | AVG tokens per creator |
| `funding_velocity_sol` | REAL | SOL moved / active days |
| `avg_composite_weight` | REAL | Network coordination metric |
| `avg_rug_probability` | REAL | AVG rug% of org tokens |
| `computed_at` | REAL | Unix timestamp |

**Key Indexes:**
- `idx_org_predictions_org_date` - Query by org + date range
- `idx_org_predictions_probability` - Leaderboard queries

---

#### `org_launch_windows`
**Purpose:** Time-windowed launch probability predictions

| Column | Type | Description |
|--------|------|-------------|
| `window_id` | INTEGER PK | Window identifier |
| `organization_id` | INTEGER FK | Parent org |
| `prediction_date` | TEXT | 'YYYY-MM-DD' |
| `prob_launch_24h` | REAL | 0-100: launch in next 24h |
| `prob_launch_72h` | REAL | 0-100: launch in next 72h |
| `prob_launch_7d` | REAL | 0-100: launch in next 7 days |
| `signal_burst_24h` | REAL | Burst events in 24h |
| `signal_recency_24h` | REAL | Hours since last activity |
| `signal_velocity_72h` | REAL | SOL moved in 72h |
| `signal_coordination_72h` | REAL | Avg coordination in 72h |
| `signal_reputation_7d` | REAL | Reputation score (0-100) |
| `computed_at` | REAL | Unix timestamp |

**Used By:** Developer Fingerprint page (3-window predictions), Risk dashboard

---

### Organization Activity & Risk

#### `org_snapshots`
**Purpose:** Daily snapshots of organization activity

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER PK | Snapshot identifier |
| `organization_id` | INTEGER FK | Parent org |
| `snapshot_date` | TEXT | 'YYYY-MM-DD' snapshot date |
| `active_funders` | INTEGER | Wallets sending SOL in 24h |
| `active_creators` | INTEGER | Creators receiving SOL in 24h |
| `burst_count` | INTEGER | 1-hour windows with 3+ transfers |
| `weighted_volume` | REAL | SUM(SOL * time_density) in 24h |
| `graph_density` | REAL | 0-1 network connectivity |
| `launch_count` | INTEGER | Tokens launched in 24h |
| `rug_count` | INTEGER | Tokens with rug_prob > 0.7 in 24h |
| `computed_at` | REAL | Unix timestamp |

**Used By:**
- Organization Detail page (7-day activity chart)
- Developer Fingerprint (momentum/expansion charts)

**Sample Query:**
```sql
SELECT * FROM org_snapshots
WHERE organization_id = ?
ORDER BY snapshot_date DESC
LIMIT 7;  -- Last 7 days
```

---

#### `org_risk_scores`
**Purpose:** Current risk assessment for each organization

| Column | Type | Description |
|--------|------|-------------|
| `risk_id` | INTEGER PK | Risk assessment ID |
| `organization_id` | INTEGER FK UNIQUE | Parent org (1-to-1) |
| `risk_score` | REAL | 0-100 composite risk |
| `rug_probability` | REAL | 0-1 weighted org-level rug% |
| `instability_score` | REAL | 0-100 snapshot volatility |
| `confidence` | REAL | 0-1 signal strength |
| `component_rug_prob` | REAL | rug% × 40 (weighted) |
| `component_instability` | REAL | instability × 0.25 |
| `component_token_velocity` | REAL | velocity × 0.2 |
| `component_blocked_ratio` | REAL | blocked% × 0.15 |
| `blocked_creator_count` | INTEGER | Count of blocked creators |
| `total_creator_count` | INTEGER | Total creators in org |
| `token_velocity` | REAL | tokens / active_days |
| `computed_at` | REAL | Unix timestamp |

**Used By:** Organization Detail page (risk panel), Alert systems

---

### Token Analysis

#### `token_analysis`
**Purpose:** Per-token rug probability, risk, and cluster assignment

| Column | Type | Description |
|--------|------|-------------|
| `mint` | TEXT PK | Token mint address |
| `analyzed_at` | REAL | Unix timestamp of analysis |
| `total_txs` | INTEGER | Total transactions |
| `total_events` | INTEGER | Total events parsed |
| `events_parsed` | INTEGER | Events processed |
| **Metrics (pre-migration):** | | |
| `mint_concentration` | REAL | 0-1 mint authority concentration |
| `unique_minters_ratio` | REAL | 0-1 minter diversity |
| `sell_suppression_ratio` | REAL | 0-1 sell suppression indicator |
| `mint_velocity_sec` | REAL | Mints per second |
| `buy_size_variance` | REAL | Buy order variance |
| `sell_volume_concentration` | REAL | 0-1 sell concentration |
| `creator_activity_ratio` | REAL | Creator activity intensity |
| **Metrics (post-migration):** | | |
| `post_migration_*` | REAL | Same metrics post-migration to PumpSwap |
| `post_migration_coverage` | REAL | Data coverage % post-migration |
| **Risk Assessment:** | | |
| `rug_probability` | REAL | 0-1 rug probability |
| `risk_level` | TEXT | 'CRITICAL' \| 'HIGH' \| 'MEDIUM' \| 'LOW' |
| `rug_indicator` | TEXT | Specific rug pattern detected |
| **Price & Market Data:** | | |
| `price_current` | REAL | Current token price (SOL) |
| `price_highest` | REAL | Highest price observed |
| `price_updated_at` | REAL | Unix timestamp of price |
| `price_source` | TEXT | 'dexscreener' \| 'jupiter' \| 'dex' |
| `market_cap_current` | REAL | Current market cap (SOL) |
| `market_cap_highest` | REAL | Highest market cap |
| `market_cap_highest_at` | REAL | Timestamp of peak market cap |
| **Creation & Migration:** | | |
| `created_at` | REAL | Token creation timestamp |
| `migration_tx` | TEXT | Migration transaction signature |
| `bonding_curve_pda` | TEXT | Pump.fun bonding curve PDA |
| `create_tx_signature` | TEXT | Token creation tx signature |
| **Creator Data:** | | |
| `earliest_tx_creator` | TEXT | Earliest creator address |
| `creator_is_blocked` | INTEGER | 0/1 creator blocked flag |
| **Network Data:** | | |
| `pool_address` | TEXT | DEX pool address |
| `cluster_id` | TEXT | Farm cluster assignment |
| `cluster_name` | TEXT | Farm cluster name |
| `cluster_risk_multiplier` | REAL | Cluster risk modifier |
| `network_funder_address` | TEXT | Associated funder network |
| `network_name` | TEXT | Network name |
| `network_tier` | TEXT | Network classification tier |
| `network_is_cex` | INTEGER | 0/1 CEX network flag |

**Key Indexes:** None listed but heavily used for token detail pages

**Sample Count:** 2,192 tokens analyzed

---

### Funding Network Analysis

#### `creator_funders`
**Purpose:** Mapping of which funders have seeded which creators

| Column | Type | Description |
|--------|------|-------------|
| `creator_address` | TEXT | Creator wallet address |
| `funder_address` | TEXT | Funder wallet address |
| `amount_sol` | REAL | SOL amount transferred |
| `first_detected_at` | TIMESTAMP | When relationship first seen |
| `is_cex` | BOOLEAN | CEX wallet flag |
| `cex_exchange` | TEXT | CEX name if applicable |
| `cex_type` | TEXT | CEX classification |
| `source_type` | TEXT | 'original_sender' or other |
| `is_classified` | INTEGER | Classification complete flag |
| `fully_analyzed` | INTEGER | Analysis complete flag |
| `total_inflows` | REAL | Total inbound SOL |
| `total_outflows` | REAL | Total outbound SOL |
| `net_change` | REAL | Net SOL change |
| `last_analyzed` | TIMESTAMP | Last analysis timestamp |

**Key Indexes:**
- `idx_creator_funders_creator` - Query by creator
- `idx_creator_funders_funder` - Query by funder
- `idx_creator_funders_analyzed` - Filter by analysis status

**Sample Size:** Thousands of creator-funder relationships

---

#### `funding_networks`
**Purpose:** Grouped networks of coordinated funders

| Column | Type | Description |
|--------|------|-------------|
| `network_id` | INTEGER PK | Network identifier |
| `network_name` | TEXT | Human-readable network name |
| `network_type` | TEXT | 'shared' or other classification |
| `total_members` | INTEGER | Number of members in network |
| `total_tokens_funded` | INTEGER | Tokens funded by network |
| `total_creators_funded` | INTEGER | Creators funded by network |
| `total_sol` | REAL | Total SOL in network |
| `created_at` | TIMESTAMP | Network creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

---

#### `creator_funding_graph`
**Purpose:** Edge list of creator-funder relationships

| Column | Type | Description |
|--------|------|-------------|
| `creator_address` | TEXT | Creator address (from) |
| `funder_address` | TEXT | Funder address (to) |
| `first_seen` | TIMESTAMP | When relationship first seen |
| `last_seen` | TIMESTAMP | Most recent activity |
| `inbound_sol` | REAL | Total SOL received |
| `inbound_tx_count` | INTEGER | Number of transactions |

**Used By:** Cluster Explorer (network visualization), Dev Clusters analysis

---

#### `wallet_clusters`
**Purpose:** Detected clusters of coordinated wallet activity

| Column | Type | Description |
|--------|------|-------------|
| `cluster_id` | INTEGER PK | Cluster identifier |
| `funder_wallet` | TEXT UNIQUE | Primary funder wallet |
| `creator_addresses` | TEXT (JSON) | Array of funded creators |
| `creator_count` | INTEGER | Count of creators |
| `confidence_score` | REAL | 0-100 coordination confidence |
| `avg_transfer_sol` | REAL | Average transfer amount |
| `transfer_stddev` | REAL | Standard deviation of transfers |
| `days_active` | INTEGER | Days of activity |
| `first_transfer_ts` | INTEGER | Unix timestamp of first transfer |
| `last_transfer_ts` | INTEGER | Unix timestamp of last transfer |
| `has_burst` | BOOLEAN | 2+ creators in same 1-hour window |
| `wallet_age_days` | REAL | Age of funder wallet |
| `detected_at` | REAL | Cluster detection timestamp |
| `updated_at` | REAL | Last update timestamp |

**Key Indexes:**
- `idx_wallet_clusters_confidence` - Highest confidence clusters
- `idx_wallet_clusters_detected` - Recent clusters

**Used By:** Cluster Explorer, Dev Clusters analysis

---

### Launch Waves

#### `launch_waves`
**Purpose:** Detected coordinated launch waves across multiple creators/tokens

| Column | Type | Description |
|--------|------|-------------|
| `wave_id` | INTEGER PK | Wave identifier |
| `wave_hour` | INTEGER UNIQUE | Epoch time / 3600 (hourly bucket) |
| `wave_start_ts` | INTEGER | Unix timestamp of wave start |
| `wave_end_ts` | INTEGER | Unix timestamp of wave end |
| `funder_count` | INTEGER | Unique funders in wave |
| `creator_count` | INTEGER | Unique creators in wave |
| `transfer_count` | INTEGER | Total transfers in wave |
| `avg_amount` | REAL | Average transfer amount |
| `min_amount` | REAL | Minimum transfer |
| `max_amount` | REAL | Maximum transfer |
| `amount_stddev` | REAL | Std dev of transfers |
| `funders_list` | TEXT (JSON) | Array of funder addresses |
| `creators_list` | TEXT (JSON) | Array of creator addresses |
| `wave_intensity` | REAL | 0-100 coordination strength |
| `coordination_signal` | REAL | Multi-funder coordination score |
| `pump_fun_confidence` | REAL | 0-100 Pump.fun pattern match |
| `is_pump_fun_wave` | BOOLEAN | Confirmed Pump.fun wave |
| `is_verified_launch` | BOOLEAN | Verified token launch flag |
| `detected_at` | REAL | Unix timestamp of detection |
| `updated_at` | REAL | Last update timestamp |

**Key Indexes:**
- `idx_waves_pump_fun_confidence` - Pump.fun waves (DESC)
- `idx_waves_creator_count` - Waves by creator count
- `idx_waves_intensity` - High intensity waves

**Used By:** Launch Waves page, Wave Dashboard

---

### RPC & Performance Metrics

#### `rpc_metrics`
**Purpose:** RPC call monitoring and cost tracking

| Column | Type | Description |
|--------|------|-------------|
| Various columns tracking RPC calls, costs, cache hits | | Helius RPC optimization |

---

#### `helius_usage_snapshots`
**Purpose:** Hourly snapshots of Helius API usage

| Columns | | Cost, credits, call count, timestamp |

---

### Webhook & Event Tracking

#### `funder_webhook_events`
**Purpose:** Real-time funder activity captured via webhook

| Column | Type | Description |
|--------|------|-------------|
| Event data from Helius webhook | | Creator outgoing transfers, funding events |

---

## Key Relationships

```
dev_organizations
  ├─ master_launch_signals (1:1 via organization_id)
  ├─ org_launch_predictions (1:N via organization_id)
  ├─ org_launch_windows (1:N via organization_id)
  ├─ org_risk_scores (1:1 via organization_id)
  ├─ org_snapshots (1:N via organization_id, snapshot_date)
  └─ dev_organization_members (1:N via organization_id)
        └─> member_address links to creator_funding_graph

creator_funders
  ├─ Links creators → funders (1:N)
  └─ creator_funding_graph (edge list representation)

wallet_clusters
  └─ funder_wallet links to creator_addresses (JSON array)

token_analysis
  └─ cluster_id links to dev_farm_ecosystems or wallet_clusters

launch_waves
  ├─ funders_list (JSON array of addresses)
  └─ creators_list (JSON array of addresses)
```

---

## Data Population

### Sources of Data

1. **Listener (`pumpfun_curve_listener.py`)**
   - Captures real-time Pump.Fun launches
   - Populates `token_analysis`, `dev_organizations`, `dev_organization_members`
   - Status: Currently disabled in config

2. **Webhook (`funder_webhook_events`)**
   - Helius webhook captures creator outgoing transfers
   - Real-time funder activity tracking
   - Status: Active via Helius integration

3. **Batch Analysis**
   - Post-processing of captured data
   - Computes signals, predictions, risk scores
   - Populates `master_launch_signals`, `org_launch_predictions`, `org_risk_scores`

4. **Price System**
   - `token_price_snapshots` - Multi-source price aggregation
   - Dexscreener, Jupiter, DEX pools
   - Updates via background worker

---

## Important Notes

### Current State
- **Empty tables:** `dev_organizations` (0 rows), `master_launch_signals` (0 rows)
- **Populated tables:** `token_analysis` (2,192), `org_snapshots`, `org_launch_windows`
- **Reason:** Listener is disabled; no new PumpFun launches being captured

### To Enable Live Data Flow
1. Enable listener in config
2. Restart `pumpfun_curve_listener`
3. New launches will populate `dev_organizations` → `master_launch_signals`
4. Dashboard queries will return data

### Performance Considerations
- **Large tables:** `token_analysis` (2K+), `creator_funders` (10K+), snapshots (50K+)
- **Indexed queries:** Always use indexed columns (org_id, timestamp, score fields)
- **JSON columns:** Parse with `json_extract()` for filtered queries
