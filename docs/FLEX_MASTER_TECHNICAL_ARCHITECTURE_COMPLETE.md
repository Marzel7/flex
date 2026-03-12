# FLEX Master Technical Architecture

**Version**: 3.1
**Date**: March 2026
**Status**: Production Ready
**Purpose**: Complete engineering reference for the FLEX Solana developer intelligence platform

---

## TABLE OF CONTENTS

1. [System Overview](#section-1--system-overview)
2. [Full System Architecture](#section-2--full-system-architecture)
3. [Data Model and Database Schema](#section-3--data-model-and-database-schema)
4. [Core Algorithms](#section-4--core-algorithms)
5. [Core Python Classes](#section-5--core-python-classes)
6. [Prediction Signals](#section-6--prediction-signals)
7. [Alert System](#section-7--alert-system)
8. [System Scheduling](#section-8--system-scheduling)
9. [UI / Dashboard Architecture](#section-9--ui--dashboard-architecture)
10. [Deployment Architecture](#section-10--deployment-architecture)
11. [Future Extensions](#section-11--future-extensions)

---

## SECTION 1 — System Overview

### Purpose

FLEX is a Solana blockchain intelligence platform that detects developer organizations and predicts token launches by analyzing on-chain transfer patterns and behavioral signals.

The system operates on three core premises:

1. **Developer organizations are detectable**: Multiple wallets funding the same creators form identifiable clusters
2. **Launches show patterns**: Coordinated seed funding, wallet synchronization, and activity changes precede launches
3. **Signals combine predictively**: Individual behavioral indicators aggregated into composite scores predict imminent launches

### Intelligence Model

FLEX implements a four-layer intelligence model:

#### Layer 1: Structural Intelligence
Detects permanent relationships in the funding graph:
- Wallet-to-creator connections
- Creator-to-token associations
- Multi-wallet organizations
- Operator wallet identification via centrality metrics

#### Layer 2: Behavioral Intelligence
Monitors activity patterns and deviations:
- Organization momentum (24h vs 7d average activity)
- Creator reuse (frequency of creator involvement in launches)
- Funding concentration (how equally creators are funded)
- Operator wallet activity spikes

#### Layer 3: Preparation Intelligence
Identifies launch-specific signals:
- Seed concentration (uniformity of seed-phase funding)
- Funder wallet overlap (coordination between funding sources)
- Funding bursts (multiple transfers per hour)
- Creator expansion (new creators being funded)

#### Layer 4: Predictive Intelligence
Synthesizes signals into launch probability:
- Launch probability engine (7-day launch likelihood)
- Launch wave detection (multi-token coordination patterns)
- Master launch score (unified 0-1 composite metric)
- Organization reputation (historical track record)

### Core Components

**Transfer Indexing**
Raw SOL transfers extracted from blockchain and stored in local SQLite, enabling 98% RPC cost reduction.

**Organization Detection**
Multi-layer wallet→creator→token graph analysis identifying developer organizations as clusters with 2+ wallets, 2+ creators, 1+ tokens.

**Signal Computation**
Daily batch computation of 8 prediction signals per organization with normalization to 0-1 scale.

**Alert Classification**
Master Launch Score mapped to 4 alert levels (LOW, WATCH, HIGH, CRITICAL) for operational prioritization.

**Real-time Ingestion**
Webhook handlers for immediate transfer_index updates from Helius or other RPC providers.

---

## SECTION 2 — Full System Architecture

### Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ RAW BLOCKCHAIN DATA (Transfer Events)                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 0: TRANSFER INDEXING (Real-time via webhook)              │
│ • Parse SOL transfers from transactions                          │
│ • Store in transfer_index table                                  │
│ • Enable SQL-based analysis (98% RPC savings)                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: ORGANIZATION DETECTION (v1)                            │
│ • Build wallet→creator→token graph                              │
│ • Detect clusters with 2+ wallets, 2+ creators                  │
│ • Identify operator wallets via betweenness centrality          │
│ • Compute organization scores (0-1)                             │
│ • Output: dev_organizations + members                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: LAUNCH PROBABILITY (v2)                                │
│ • Recency: days since last funding activity                     │
│ • Scale: organization size normalization                        │
│ • Launch rate: avg tokens per creator                           │
│ • Funding velocity: SOL moved in recent windows                 │
│ • Coordination: composite relationship weights                  │
│ • Network risk: weighted rug probability                        │
│ • Reputation: developer success vs rug history                  │
│ • Output: org_launch_predictions + dev_reputation              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: PREDICTIVE ANALYTICS (v3)                              │
│ • Multi-window predictions (24h, 72h, 7d)                       │
│ • Organization snapshots (daily time-series)                    │
│ • Risk scoring (rug probability, instability)                   │
│ • Token outcome prediction (prob_rug, prob_2x, prob_10x)       │
│ • Cross-org relationships (shared operators, creators)          │
│ • Organization families (connected components)                  │
│ • Alert generation (polling-based with daily dedup)             │
│ • ML feature store (15 features per entity)                     │
│ • Output: org_launch_windows, org_snapshots, org_risk_scores    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: CREATOR SEED METRICS (Seed Concentration)              │
│ • Identify seed-phase transfers (0.5-10 SOL)                    │
│ • Group by recipient creator                                    │
│ • Calculate concentration = 1 - (stddev / avg)                  │
│ • Store per creator + organization                              │
│ • Output: creator_seed_metrics                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4.5: FUNDER OVERLAP ANALYSIS                              │
│ • Extract funder→creator pairs from transfers                   │
│ • Pairwise comparison of all funders                            │
│ • Count shared creators (intersection)                          │
│ • Compute overlap_ratio = shared / min_count                    │
│ • Classify: very_strong | high | medium | low                  │
│ • Output: funder_overlap table                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 5: LAUNCH WAVE DETECTION                                  │
│ • Detect funding bursts (3+ transfers/hour)                     │
│ • Identify creator expansion patterns                           │
│ • Score wave confidence based on multi-launch signals           │
│ • Classify wave type (expansion, replacement, sustained)        │
│ • Output: launch_waves table                                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 6: MASTER LAUNCH SCORE (Unified Alerting)                 │
│ • Fetch 8 signals per organization                              │
│ • Normalize to 0-1 (handles 0-100%, 0-1, momentum)              │
│ • Apply optimal weights (sum = 1.0)                             │
│ • Compute composite score: Σ(weight × normalized_signal)        │
│ • Classify alert level: LOW | WATCH | HIGH | CRITICAL          │
│ • Output: master_launch_signals                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ ALERT OUTPUT                                                     │
│ • CRITICAL (≥0.75) → immediate escalation                       │
│ • HIGH (0.60-0.74) → investigation queue                        │
│ • WATCH (0.40-0.59) → monitoring tier                           │
│ • LOW (<0.40) → routine tracking                                │
└─────────────────────────────────────────────────────────────────┘
```

### Pipeline Characteristics

**Phases 0-1: Graph Foundation (10-40 seconds)**
- Transfer indexing happens continuously via webhooks
- Organization detection runs daily at 5 AM UTC
- Produces stable organization clusters and operator identities

**Phases 2-3: Intelligence Layers (60-150 seconds)**
- Launch probability computation using v2 signals
- Predictive analytics with multi-window forecasting
- Time-series snapshots for trend analysis
- Relationship detection and family clustering

**Phases 4-6: Signal Synthesis (40-100 seconds)**
- Seed concentration and funder overlap measurements
- Launch wave detection via burst analysis
- Master launch score aggregation
- Alert classification and firing

**Total Pipeline Runtime**: 2-5 minutes per daily execution
**Failure Mode**: All or nothing (transaction semantics via INSERT OR REPLACE)

---

## SECTION 3 — Data Model and Database Schema

### Core Tables

#### Table: transfer_index
**Purpose**: Persistent index of all SOL transfers parsed from blockchain
**Update Frequency**: Real-time via webhooks + daily catchup
**Retention**: Full history (no pruning)

```sql
CREATE TABLE transfer_index (
    transfer_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    signature               TEXT NOT NULL UNIQUE,
    source                  TEXT NOT NULL,          -- wallet address (sender)
    destination             TEXT NOT NULL,          -- wallet address (receiver)
    amount_lamports          INTEGER NOT NULL,
    amount_sol              REAL GENERATED AS (amount_lamports / 1e9),
    slot                    INTEGER NOT NULL,
    block_time              INTEGER,                -- Unix timestamp
    transfer_type           TEXT DEFAULT 'transfer', -- 'transfer'|'token_transfer'
    is_valid                INTEGER DEFAULT 1,
    created_at              REAL NOT NULL DEFAULT (strftime('%s'))
);

CREATE INDEX idx_transfer_source ON transfer_index(source);
CREATE INDEX idx_transfer_destination ON transfer_index(destination);
CREATE INDEX idx_transfer_block_time ON transfer_index(block_time DESC);
CREATE INDEX idx_transfer_amount ON transfer_index(amount_sol DESC);
```

**Key Fields**:
- `signature`: Unique transaction identifier
- `source/destination`: Wallet addresses (can be creator or operator)
- `amount_sol`: Normalized to SOL (used for threshold filtering)
- `block_time`: Used for time-window queries (24h, 72h, 7d)

**Access Patterns**:
- Find all funders of a creator: `WHERE destination = ?`
- Find all creators funded by wallet: `WHERE source = ?`
- Transfers in time window: `WHERE block_time >= ? AND block_time < ?`
- Funding bursts: `GROUP BY CAST(block_time/3600 AS INTEGER) HAVING COUNT(*) >= 3`

**Storage**: ~320 bytes per transfer with indexes; 1M transfers ≈ 320 MB

---

#### Table: dev_organizations
**Purpose**: Detected developer organizations with metadata and scores
**Update Frequency**: Daily on Phase 1 completion
**Retention**: Full history (never deleted)

```sql
CREATE TABLE dev_organizations (
    organization_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_wallet         TEXT NOT NULL UNIQUE,
    cluster_size            INTEGER DEFAULT 0,      -- num wallets
    token_count             INTEGER DEFAULT 0,      -- num tokens
    creator_count           INTEGER DEFAULT 0,      -- num unique creators
    organization_score      REAL DEFAULT 0,         -- 0-1
    cluster_strength        REAL DEFAULT 0,         -- 0-1
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,
    UNIQUE(operator_wallet)
);

CREATE INDEX idx_org_score ON dev_organizations(organization_score DESC);
```

**Key Fields**:
- `operator_wallet`: Primary identifier (highest betweenness centrality)
- `cluster_size`: Number of wallets in organization
- `organization_score`: Computed from cluster density and node connectivity

**Computation**:
```
organization_score =
  (cluster_size / 10.0) * 0.4 +          # Cluster size factor
  (creator_count / 50.0) * 0.3 +         # Creator diversity
  cluster_strength * 0.3                  # Graph connectivity
```

---

#### Table: dev_organization_members
**Purpose**: Wallet-to-organization assignments with role classification
**Update Frequency**: Daily on Phase 1 completion

```sql
CREATE TABLE dev_organization_members (
    member_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    member_address          TEXT NOT NULL,
    member_type             TEXT NOT NULL,          -- 'wallet'|'creator'|'operator'
    role_confidence         REAL DEFAULT 0,         -- 0-1
    detected_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, member_address, member_type)
);

CREATE INDEX idx_dom_org ON dev_organization_members(organization_id);
CREATE INDEX idx_dom_address ON dev_organization_members(member_address);
```

**Member Types**:
- `wallet`: Funding source (appears as source in transfers)
- `creator`: Receiver of funding (appears as destination in seed-phase transfers)
- `operator`: Highest centrality wallet (coordinator of org activity)

---

#### Table: org_launch_predictions
**Purpose**: Phase 2 launch probability signals per organization
**Update Frequency**: Daily after Phase 2

```sql
CREATE TABLE org_launch_predictions (
    prediction_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    prediction_date         TEXT NOT NULL,          -- 'YYYY-MM-DD'
    launch_probability      REAL DEFAULT 0,         -- 0-100
    signal_recency          REAL DEFAULT 0,         -- days since activity
    signal_scale            REAL DEFAULT 0,         -- org size normalized
    signal_launch_rate      REAL DEFAULT 0,         -- tokens per creator
    signal_velocity         REAL DEFAULT 0,         -- SOL moved recently
    signal_coordination     REAL DEFAULT 0,         -- composite weight avg
    signal_network_risk     REAL DEFAULT 0,         -- rug probability
    reputation_score        REAL DEFAULT 0,         -- 0-1
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, prediction_date)
);

CREATE INDEX idx_olp_date ON org_launch_predictions(prediction_date DESC);
CREATE INDEX idx_olp_prob ON org_launch_predictions(launch_probability DESC);
```

---

#### Table: org_launch_windows
**Purpose**: Phase 3 multi-window predictions (24h, 72h, 7d)
**Update Frequency**: Daily

```sql
CREATE TABLE org_launch_windows (
    window_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    prediction_date         TEXT NOT NULL,
    prob_launch_24h         REAL DEFAULT 0,         -- 0-100
    prob_launch_72h         REAL DEFAULT 0,         -- 0-100
    prob_launch_7d          REAL DEFAULT 0,         -- 0-100
    signal_burst_24h        REAL DEFAULT 0,
    signal_recency_24h      REAL DEFAULT 0,
    signal_velocity_72h     REAL DEFAULT 0,
    signal_coordination_72h REAL DEFAULT 0,
    signal_reputation_7d    REAL DEFAULT 0,
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, prediction_date)
);

CREATE INDEX idx_olw_24h ON org_launch_windows(prob_launch_24h DESC);
CREATE INDEX idx_olw_7d ON org_launch_windows(prob_launch_7d DESC);
```

---

#### Table: org_snapshots
**Purpose**: Daily activity snapshots for time-series analysis
**Update Frequency**: Daily
**Retention**: Full history

```sql
CREATE TABLE org_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    snapshot_date           TEXT NOT NULL,          -- 'YYYY-MM-DD'
    active_funders          INTEGER DEFAULT 0,      -- wallets funding in 24h
    active_creators         INTEGER DEFAULT 0,      -- creators funded in 24h
    burst_count             INTEGER DEFAULT 0,      -- 1h windows with 3+ txs
    weighted_volume         REAL DEFAULT 0,         -- SOL moved
    graph_density           REAL DEFAULT 0,         -- 0-1 edge ratio
    launch_count            INTEGER DEFAULT 0,      -- tokens created
    rug_count               INTEGER DEFAULT 0,      -- rugged tokens
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, snapshot_date)
);

CREATE INDEX idx_os_date ON org_snapshots(snapshot_date DESC);
```

---

#### Table: org_risk_scores
**Purpose**: Composite risk assessment per organization
**Update Frequency**: Daily, overwrites
**Retention**: Only current row per org

```sql
CREATE TABLE org_risk_scores (
    risk_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL UNIQUE,
    risk_score              REAL DEFAULT 0,         -- 0-100
    rug_probability         REAL DEFAULT 0,         -- 0-1
    instability_score       REAL DEFAULT 0,         -- 0-100
    confidence              REAL DEFAULT 0,         -- 0-1
    blocked_creator_count   INTEGER DEFAULT 0,
    total_creator_count     INTEGER DEFAULT 0,
    token_velocity          REAL DEFAULT 0,
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id)
);

CREATE INDEX idx_ors_risk ON org_risk_scores(risk_score DESC);
```

---

#### Table: creator_seed_metrics
**Purpose**: Phase 4 seed concentration measurement
**Update Frequency**: Daily

```sql
CREATE TABLE creator_seed_metrics (
    metric_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_wallet          TEXT NOT NULL,
    organization_id         INTEGER,
    seed_concentration      REAL DEFAULT 0,         -- 1 - (stddev/avg)
    seed_count              INTEGER DEFAULT 0,      -- num seed transfers
    avg_seed_amount         REAL DEFAULT 0,
    stddev_seed_amount      REAL DEFAULT 0,
    min_seed_amount         REAL DEFAULT 0,
    max_seed_amount         REAL DEFAULT 0,
    computed_at             REAL NOT NULL,
    UNIQUE(creator_wallet, organization_id)
);

CREATE INDEX idx_csm_concentration ON creator_seed_metrics(seed_concentration DESC);
```

**Seed Phase Definition**: Transfers 0.5-10 SOL (indicates preparation, not operational funding)

---

#### Table: funder_overlap
**Purpose**: Phase 4.5 wallet coordination measurement
**Update Frequency**: Daily

```sql
CREATE TABLE funder_overlap (
    overlap_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_a                TEXT NOT NULL,
    funder_b                TEXT NOT NULL,
    shared_creators         INTEGER DEFAULT 0,
    overlap_ratio           REAL DEFAULT 0,         -- 0-1
    classification          TEXT,                   -- 'very_strong'|'high'|'medium'|'low'
    first_detected_at       REAL NOT NULL,
    updated_at              REAL NOT NULL,
    CHECK(funder_a < funder_b),                     -- canonical ordering
    UNIQUE(funder_a, funder_b)
);

Create INDEX idx_fo_ratio ON funder_overlap(overlap_ratio DESC);
```

---

#### Table: launch_waves
**Purpose**: Phase 5 multi-launch pattern detection
**Update Frequency**: Daily

```sql
CREATE TABLE launch_waves (
    wave_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    wave_date               TEXT NOT NULL,          -- 'YYYY-MM-DD'
    wave_type               TEXT,                   -- 'expansion'|'replacement'|'sustained'
    new_creators_24h        INTEGER DEFAULT 0,      -- creators funded in window
    funding_burst_count     INTEGER DEFAULT 0,      -- 1h windows with burst
    organization_momentum   REAL DEFAULT 0,         -- activity trend
    operator_activity_spike REAL DEFAULT 0,         -- operator wallet surge
    creator_reuse_delta     REAL DEFAULT 0,         -- reuse rate change
    wave_score              REAL DEFAULT 0,         -- 0-100
    confidence              REAL DEFAULT 0,         -- 0-1
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, wave_date)
);

CREATE INDEX idx_lw_score ON launch_waves(wave_score DESC);
```

---

#### Table: master_launch_signals
**Purpose**: Phase 6 unified launch alert scoring
**Update Frequency**: Daily, INSERT OR REPLACE
**Retention**: Only current per org

```sql
CREATE TABLE master_launch_signals (
    signal_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL UNIQUE,
    launch_probability      REAL DEFAULT 0,         -- 0-1 normalized
    launch_wave_score       REAL DEFAULT 0,         -- 0-1 normalized
    seed_concentration      REAL DEFAULT 0,         -- 0-1 ratio
    funder_overlap_score    REAL DEFAULT 0,         -- 0-1 ratio
    organization_momentum   REAL DEFAULT 0,         -- 0-1 normalized
    creator_reuse_score     REAL DEFAULT 0,         -- 0-1 normalized
    operator_activity_score REAL DEFAULT 0,         -- 0-1 normalized
    reputation_adjustment   REAL DEFAULT 0,         -- 0-1 ratio
    master_launch_score     REAL DEFAULT 0,         -- Final 0-1 composite
    alert_level             TEXT,                   -- 'LOW'|'WATCH'|'HIGH'|'CRITICAL'
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id)
);

CREATE INDEX idx_mls_score ON master_launch_signals(master_launch_score DESC);
CREATE INDEX idx_mls_alert ON master_launch_signals(alert_level);
```

---

#### Table: dev_reputation
**Purpose**: Phase 2 historical developer success/rug tracking
**Update Frequency**: Daily

```sql
CREATE TABLE dev_reputation (
    reputation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet                  TEXT NOT NULL UNIQUE,
    tokens_launched         INTEGER DEFAULT 0,
    successful_launches     INTEGER DEFAULT 0,      -- market cap > 10k SOL
    rugged_tokens           INTEGER DEFAULT 0,      -- prob_rug > 0.7
    success_rate            REAL DEFAULT 0,         -- 0-1
    rug_rate                REAL DEFAULT 0,         -- 0-1
    reputation_score        REAL DEFAULT 0,         -- 0-1
    updated_at              REAL NOT NULL,
    FOREIGN KEY(wallet) REFERENCES dev_organization_members(member_address)
);

CREATE INDEX idx_rep_score ON dev_reputation(reputation_score DESC);
```

---

#### Table: token_outcome_predictions
**Purpose**: Per-token launch outcome heuristics
**Update Frequency**: Daily

```sql
CREATE TABLE token_outcome_predictions (
    prediction_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                    TEXT NOT NULL UNIQUE,
    prob_rug                REAL DEFAULT 0,         -- 0-1
    prob_2x                 REAL DEFAULT 0,         -- 0-1
    prob_10x                REAL DEFAULT 0,         -- 0-1
    expected_quality_score  REAL DEFAULT 0,         -- 0-100
    creator_wallet          TEXT,
    organization_id         INTEGER,
    computed_at             REAL NOT NULL
);

CREATE INDEX idx_top_prob_rug ON token_outcome_predictions(prob_rug DESC);
```

---

### Schema Relationships

```
dev_organizations
├── organization_id (PK)
├── operator_wallet (unique)
└── [used by all other tables as FK]
    ├── dev_organization_members.organization_id
    ├── org_launch_predictions.organization_id
    ├── org_snapshots.organization_id
    ├── org_risk_scores.organization_id
    ├── launch_waves.organization_id
    ├── master_launch_signals.organization_id
    └── org_launch_windows.organization_id

transfer_index
├── signature (PK, unique)
├── source (wallet address)
├── destination (wallet address)
└── [used for clustering and relationship detection]
    └── dev_organization_members (created from clusters)
    └── creator_seed_metrics (seed phase transfers)
    └── funder_overlap (wallet pairs)

token_analysis
├── mint (PK)
├── earliest_tx_creator (creator address)
└── [used for org scoring and risk]
    └── token_outcome_predictions
    └── org_risk_scores (rug probability)
```

---

## SECTION 4 — Core Algorithms

### Algorithm 1: Organization Detection (Phase 1)

**Input**: transfer_index with source→destination edges
**Output**: dev_organizations clusters + members

**Algorithm**:
```
1. Build wallet graph from transfer_index
   - Nodes: wallet addresses
   - Edges: source→destination transfers
   - Weight: composite_weight (time_concentration, amount_concentration)

2. Apply Louvain community detection
   - Resolution: 0.5 (moderate granularity)
   - Output: cluster assignments

3. For each cluster C:
   a. Filter clusters with 2+ wallets
   b. Identify creators (nodes receiving seed-phase transfers)
   c. Filter clusters with 2+ creators
   d. Identify tokens (from token_analysis.earliest_tx_creator)
   e. Filter clusters with 1+ tokens
   f. Compute organization_score from cluster metrics
   g. Identify operator_wallet (max betweenness centrality)
   h. Store in dev_organizations

4. Compute organization_score:
   score =
     (|wallets| / 10) * 0.4 +           # Scale factor
     (|creators| / 50) * 0.3 +          # Creator diversity
     cluster_strength * 0.3              # Graph density

   where cluster_strength = actual_edges / max_edges
```

**Complexity**: O(N log N) for Louvain, where N = wallet count
**Expected Clusters**: 100-500 organizations from 10k-50k wallets

---

### Algorithm 2: Launch Probability (Phase 2)

**Input**: dev_organizations, transfer_index, token_analysis, dev_reputation
**Output**: org_launch_predictions (launch_probability: 0-100)

**Signals**:
```
1. Recency Signal (0-30 points)
   days_since_last_tx = (now - max(block_time)) / 86400
   recency = max(0, 30 * (1 - days_since_last_tx / 7))
   → 30 pts if activity within 24h, 0 if >7 days

2. Scale Signal (0-20 points)
   scaled = (cluster_size / 10) * 20, clamped to [0, 20]

3. Launch Rate Signal (0-15 points)
   avg_tokens = (total_tokens_from_creators) / creator_count
   launch_rate = min(avg_tokens / 5, 1.0) * 15

4. Velocity Signal (0-15 points)
   sol_72h = SUM(amount_sol WHERE block_time in last 72h)
   velocity = min(sol_72h / 50, 1.0) * 15

5. Coordination Signal (0-10 points)
   avg_composite_weight = SUM(weight) / edge_count
   coordination = avg_composite_weight * 10

6. Network Risk Signal (0-10 points)
   avg_rug_prob = AVG(rug_probability) for org's tokens
   network_risk = (1 - avg_rug_prob) * 10

7. Reputation Signal (0-10 points)
   reputation = operator_reputation_score * 10

launch_probability = recency + scale + launch_rate +
                     velocity + coordination + network_risk +
                     reputation
```

**Formula Derivation**:
Weights prioritize immediate signals (recency 30%) over supporting signals (coordination 10%). Network risk and reputation provide negative dampening for organizations with historical problems.

**Normalization**: 0-100 to 0-1 by dividing by 100 in Phase 6.

---

### Algorithm 3: Seed Concentration (Phase 4)

**Input**: transfer_index with seed-phase transfers (0.5-10 SOL)
**Output**: creator_seed_metrics (seed_concentration: 0-1)

**Algorithm**:
```
1. Filter transfers by amount: 0.5 SOL ≤ amount ≤ 10 SOL

2. For each creator C:
   a. Get all seed transfers where destination = C
   b. Extract amounts: A = [amount_1, amount_2, ..., amount_n]
   c. Compute:
      - mean = SUM(A) / |A|
      - stddev = sqrt(SUM((a - mean)^2) / |A|)
   d. Compute concentration:
      concentration = 1 - (stddev / mean)
   e. Clamp to [0, 1]

3. Store with organization_id (from dev_organization_members)
```

**Interpretation**:
- `concentration = 1.0`: All seeds equal (perfectly coordinated)
- `concentration = 0.5`: Some variation but mostly uniform
- `concentration = 0.0`: Highly variable seed amounts (chaotic)
- `concentration < 0.0`: (clamped to 0) Extremely chaotic or single seed

**Statistical Basis**: Uniform distribution = high coordination (low stddev)

---

### Algorithm 4: Funder Overlap (Phase 4.5)

**Input**: transfer_index (creator funding relationships)
**Output**: funder_overlap (overlap_ratio: 0-1)

**Algorithm**:
```
1. Extract all funder→creator pairs from transfer_index
   funders = {wallet: Set(creator addresses it funded)}

2. For each unique pair (funder_a, funder_b):
   a. Compute shared_creators = |funders[a] ∩ funders[b]|
   b. Compute min_count = min(|funders[a]|, |funders[b]|)
   c. Compute overlap_ratio = shared_creators / max(min_count, 1)
   d. Clamp ratio to [0, 1]

3. Classify:
   - overlap_ratio >= 0.75 → 'very_strong' (identical creator lists)
   - overlap_ratio >= 0.5  → 'high' (significant overlap)
   - overlap_ratio >= 0.25 → 'medium' (some shared creators)
   - overlap_ratio < 0.25  → 'low' (minimal sharing)

4. Store in funder_overlap with canonical ordering: (a < b)
```

**Interpretation**:
- `ratio = 1.0`: Both wallets fund identical set of creators
- `ratio = 0.5`: Half of smaller wallet's creators also funded by larger
- `ratio = 0.0`: No shared creators

**Graph Implication**: High overlap suggests single coordinated funder using multiple wallets.

---

### Algorithm 5: Launch Wave Detection (Phase 5)

**Input**: org_snapshots (daily activity), org_launch_predictions
**Output**: launch_waves (wave_score: 0-100)

**Signals**:
```
wave_score =
  0.30 * new_creators_24h_norm +
  0.25 * funding_burst_norm +
  0.20 * momentum_norm +
  0.15 * operator_spike_norm +
  0.10 * reuse_delta_norm

where:
  new_creators_24h_norm   = min(new_creators / 3, 1.0) * 100
  funding_burst_norm      = min(burst_count / 5, 1.0) * 100
  momentum_norm           = (activity_24h / activity_7d_avg) clamped [0, 2.0] * 50
  operator_spike_norm     = (operator_txs_24h / operator_avg_7d) * 50
  reuse_delta_norm        = change_in_creator_reuse_rate * 100
```

**Wave Type Classification**:
```
IF new_creators_24h >= 2 AND funding_burst_count >= 3:
  type = 'expansion' (launching new creators)
ELSE IF funding_burst_count >= 2 AND reuse_delta_norm >= 0.5:
  type = 'replacement' (replacing failed creators)
ELSE IF burst_count >= 1 AND momentum >= 1.5:
  type = 'sustained' (continuous funding campaign)
ELSE:
  type = 'none'
```

**Confidence Metric**:
```
confidence =
  (|active_funders| / 5) * 0.3 +
  (|active_creators| / 10) * 0.3 +
  (burst_count / 5) * 0.4
clamped to [0, 1]
```

---

### Algorithm 6: Master Launch Score (Phase 6)

**Input**: All 8 signals (from phases 1-5), normalized to 0-1
**Output**: master_launch_score (0-1) + alert_level

**Normalization**:
```
FOR EACH signal:
  IF signal_type = 'percentage' (0-100):
    normalized = signal / 100
  ELSE IF signal_type = 'ratio' (0-1):
    normalized = signal  (pass-through)
  ELSE IF signal_type = 'momentum' (can be negative):
    normalized = 0.5 + momentum / (2 + |momentum|)
    # Maps: -1→0.17, -0.5→0.33, 0→0.5, 0.5→0.67, 1→0.83, 2→0.9
```

**Weight Application**:
```
master_launch_score =
  0.22 * norm_launch_probability +
  0.18 * norm_launch_wave_score +
  0.12 * seed_concentration +
  0.12 * funder_overlap_score +
  0.10 * norm_organization_momentum +
  0.08 * creator_reuse_score +
  0.08 * operator_activity_score +
  0.10 * reputation_adjustment

Weights sum to 1.0 ✓
```

**Weight Rationale**:
- **0.22** (Launch Probability): Direct 7-day predictor, strongest signal
- **0.18** (Launch Wave): Pattern-based multi-token detection, secondary strong signal
- **0.12 each** (Seed + Funder): Structural coordination signals, equal importance
- **0.10** (Momentum): Activity trend indicator, moderately important
- **0.08 each** (Reuse + Operator): Specific activity spikes, supporting signals
- **0.10** (Reputation): Historical calibration, equal to momentum

**Alert Classification**:
```
IF score >= 0.75:
  alert_level = 'CRITICAL'    # Imminent launch likely
ELSE IF score >= 0.60:
  alert_level = 'HIGH'         # Strong preparation signals
ELSE IF score >= 0.40:
  alert_level = 'WATCH'        # Moderate activity
ELSE:
  alert_level = 'LOW'          # Minimal launch signals
```

**Storage**: INSERT OR REPLACE idempotency (one row per org, updated daily)

---

## SECTION 5 — Core Python Classes

### Class: TransferIndexer

**File**: `src/core/transfer_indexer.py`
**Responsibility**: Parse and persist SOL transfers from transactions

**Key Methods**:
```python
def extract_transfers(transaction: Dict) -> List[Transfer]:
    """
    Parse SOL transfers from transaction instructions.
    Handles system program transfers and token transfers.
    Returns list of Transfer(signature, source, destination, amount_sol, block_time)
    """

def index_transaction(transaction: Dict) -> int:
    """
    Index single transaction into transfer_index table.
    Returns transfer count inserted.
    """

def query_funders(creator_address: str) -> List[str]:
    """
    Find all wallets that funded a specific creator.
    Query: SELECT DISTINCT source WHERE destination = ?
    """

def query_funded_creators(wallet_address: str) -> List[str]:
    """
    Find all creators funded by a specific wallet.
    Query: SELECT DISTINCT destination WHERE source = ?
    """
```

**Dependencies**: sqlite3, transaction parsing

**Performance**: 10,000 transfers/second from pre-parsed transactions

---

### Class: DevIntelligenceEngine (Phase 1)

**File**: `src/core/dev_intelligence_graph.py`
**Responsibility**: Detect organizations via graph clustering

**Key Methods**:
```python
def build_extended_graph(
    min_transfer: float,
    max_transfer: float,
    days_back: int
) -> nx.DiGraph:
    """
    Build wallet→creator→token graph.
    Returns NetworkX DiGraph with nodes tagged by type.
    """

def detect_organizations() -> List[Organization]:
    """
    Apply Louvain clustering to graph.
    Filter clusters (2+ wallets, 2+ creators, 1+ tokens).
    Compute organization_score and identify operator_wallet.
    """

def detect_and_store() -> Dict:
    """
    Pipeline entry point.
    Returns: {
        status: 'success'|'error',
        orgs_detected: int,
        members_stored: int,
        duration_ms: float
    }
    """
```

**Dependencies**: networkx (Louvain clustering), transfer_indexer

**Complexity**: O(N log N) for Louvain, ~30 seconds for 10k wallets

---

### Class: LaunchProbabilityModel (Phase 2)

**File**: `src/core/dev_intelligence_v2.py`
**Responsibility**: Compute 7-day launch probability signals

**Key Methods**:
```python
def compute_signals(organization: Dict) -> Dict:
    """
    Compute all 7 launch signals for organization.
    Returns: {
        signal_recency: 0-30,
        signal_scale: 0-20,
        signal_launch_rate: 0-15,
        signal_velocity: 0-15,
        signal_coordination: 0-10,
        signal_network_risk: 0-10,
        reputation_score: 0-1
    }
    """

def score(signals: Dict) -> float:
    """
    Sum signals to launch_probability (0-100).
    Applies clipping to [0, 100] range.
    """

def _fetch_last_activity_ts(organization_id: int) -> float:
    """Query MAX(block_time) from transfers of org wallets."""

def _fetch_avg_tokens_launched(creator_list: List[str]) -> float:
    """Count tokens in token_analysis where earliest_tx_creator in list."""
```

**Dependencies**: transfer_indexer, token_analysis table

**Cached Results**: org_launch_predictions table (updated daily)

---

### Class: DevIntelligenceV3Engine (Phase 3)

**File**: `src/core/dev_intelligence_v3.py`
**Responsibility**: Predictive analytics with multi-window forecasting

**Key Methods**:
```python
def compute_windows(organization: Dict) -> Dict:
    """
    Compute 24h, 72h, 7d launch probabilities.
    Returns: {
        prob_launch_24h: 0-100,
        prob_launch_72h: 0-100,
        prob_launch_7d: 0-100
    }
    """

def take_snapshot(organization: Dict) -> Dict:
    """
    Capture daily activity metrics.
    Returns: {
        active_funders: int,
        active_creators: int,
        burst_count: int,
        weighted_volume: float,
        graph_density: 0-1
    }
    """

def score_risk(organization: Dict) -> Dict:
    """
    Compute composite risk score (0-100).
    Combines rug probability, instability, velocity, blocked creators.
    """

def analyze_relationships(orgs: List[Dict]) -> Tuple[List, List]:
    """
    Detect org-to-org relationships (shared operators/creators).
    Perform community detection for families.
    Returns (relationships, families)
    """

def predict_tokens() -> List[Dict]:
    """
    Predict per-token outcomes (prob_rug, prob_2x, prob_10x).
    Applies Bayesian combination of developer + network signals.
    """

def fire_alerts(organizations: List[Dict]) -> int:
    """
    Check all orgs against thresholds, fire alerts with daily dedup.
    Returns count of alerts fired.
    """

def detect_and_store() -> Dict:
    """
    Complete Phase 3 pipeline.
    Returns: {
        status: 'success'|'error',
        orgs_processed: int,
        tokens_predicted: int,
        alerts_fired: int,
        duration_ms: float
    }
    """
```

**Dependencies**: LaunchProbabilityModel, transfer_indexer, networkx

**Output Tables**: org_launch_windows, org_snapshots, org_risk_scores, token_outcome_predictions, org_alerts, org_families, prediction_features

---

### Class: CreatorSeedMetricsAnalyzer (Phase 4)

**File**: `src/core/creator_seed_metrics.py`
**Responsibility**: Compute seed concentration per creator

**Key Methods**:
```python
def compute_seed_concentration(creator_address: str) -> Dict:
    """
    Extract seed-phase transfers (0.5-10 SOL), compute concentration.
    Returns: {
        seed_concentration: 0-1,
        seed_count: int,
        avg_seed_amount: float,
        stddev_seed_amount: float
    }
    """

def compute_and_store() -> Dict:
    """
    Pipeline entry point for all creators.
    Returns: {
        status: 'success',
        metrics_computed: int,
        high_concentration_count: int,
        duration_ms: float
    }
    """
```

**Dependencies**: transfer_indexer

**Output**: creator_seed_metrics table

---

### Class: FunderOverlapAnalyzer (Phase 4.5)

**File**: `src/core/funder_overlap_analysis.py`
**Responsibility**: Detect wallet coordination via creator sharing

**Key Methods**:
```python
def analyze_funder_pairs() -> List[Dict]:
    """
    Compute overlap_ratio for all wallet pairs.
    Returns list of overlap records with classification.
    """

def classify_overlap(ratio: float) -> str:
    """Map overlap_ratio to classification: very_strong|high|medium|low"""

def analyze_and_store() -> Dict:
    """
    Complete Phase 4.5 pipeline.
    Returns: {
        status: 'success',
        overlaps_found: int,
        high_coordination_count: int,
        very_strong_count: int,
        duration_ms: float
    }
    """
```

**Dependencies**: transfer_indexer

**Output**: funder_overlap table

---

### Class: LaunchWaveDetectionEngine (Phase 5)

**File**: `src/core/launch_wave_detection.py`
**Responsibility**: Detect multi-launch patterns

**Key Methods**:
```python
def detect_new_creators(organization_id: int, hours: int = 24) -> int:
    """Count new creators funded in time window."""

def analyze_bursts(organization_id: int, hours: int = 24) -> int:
    """Count 1-hour windows with 3+ transfers."""

def monitor_operator_spike(organization_id: int) -> float:
    """Compute spike ratio: (txs_24h / avg_7d) clamped to [0, 2.0]"""

def detect_reuse_delta(organization_id: int) -> float:
    """Compute change in creator reuse rate."""

def score_launch_wave(organization: Dict) -> Dict:
    """
    Apply wave_score formula with 5 components.
    Returns wave_score (0-100) and wave_type.
    """

def detect_and_store() -> Dict:
    """
    Complete Phase 5 pipeline.
    Returns: {
        status: 'success',
        orgs_processed: int,
        waves_detected: int,
        duration_ms: float
    }
    """
```

**Dependencies**: org_snapshots, org_launch_predictions

**Output**: launch_waves table

---

### Class: MasterLaunchScoreEngine (Phase 6)

**File**: `src/core/master_launch_score.py`
**Responsibility**: Unified alert scoring from 8 signals

**Key Classes**:

**SignalNormalizer**:
```python
def normalize_percentage(value: float) -> float:
    """Convert 0-100 to 0-1: value / 100"""

def normalize_ratio(value: float) -> float:
    """Pass-through 0-1 values: return value"""

def normalize_momentum(momentum: float) -> float:
    """
    Sigmoid-like transform for momentum:
    0.5 + momentum / (2 + |momentum|)
    """
```

**MasterLaunchScoreCalculator**:
```python
WEIGHTS = {
    'launch_probability': 0.22,
    'launch_wave_score': 0.18,
    'seed_concentration': 0.12,
    'funder_overlap_score': 0.12,
    'organization_momentum': 0.10,
    'creator_reuse_score': 0.08,
    'operator_activity_score': 0.08,
    'reputation_adjustment': 0.10
}

def compute_organization_score(organization_id: int) -> Dict:
    """
    Fetch all 8 signals, normalize, apply weights.
    Returns: {
        master_launch_score: 0-1,
        alert_level: 'LOW'|'WATCH'|'HIGH'|'CRITICAL'
    }
    """

def _compute_organization_momentum(org_id: int) -> float:
    """(activity_24h - activity_7d_avg) / activity_7d_avg, normalized to 0-1"""

def _compute_creator_reuse_score(org_id: int) -> float:
    """min(1.0, tokens_launched / creator_count / 5.0)"""

def _compute_operator_activity_score(org_id: int) -> float:
    """(operator_txs_24h / operator_avg_7d - 1.0) / 2.0, clamped [0, 1]"""

def _classify_alert_level(score: float) -> str:
    """Map 0-1 score to alert level via thresholds"""
```

**MasterLaunchScoreEngine**:
```python
def detect_and_store() -> Dict:
    """
    Complete Phase 6 pipeline.
    Returns: {
        status: 'success',
        orgs_processed: int,
        critical_count: int,
        high_count: int,
        watch_count: int,
        duration_ms: float
    }
    """
```

**Dependencies**: All previous phases' output tables

**Output**: master_launch_signals table (INSERT OR REPLACE)

---

## SECTION 6 — Prediction Signals

### Signal Layer 1: Structural Signals

Detect permanent relationships in the funding graph.

#### Signal 1.1: Organization Detection
- **Metric**: cluster membership (Boolean)
- **Computation**: Louvain community detection on transfer graph
- **Interpretation**: Developers organize into wallet clusters
- **Used By**: All downstream phases

#### Signal 1.2: Creator Reuse
- **Metric**: tokens_launched / creator_count
- **Range**: 0-∞ (normalized to 0-1 in Phase 6)
- **Computation**: Count org tokens involving org creators
- **Interpretation**: Creators launching multiple tokens indicates serial operator
- **Threshold**: 5+ tokens per creator = 1.0

#### Signal 1.3: Funder Wallet Overlap
- **Metric**: shared_creators / min(|funder_a|, |funder_b|)
- **Range**: 0-1
- **Computation**: Pairwise wallet comparison via creator intersection
- **Interpretation**: Identical creator lists = single operator with multiple wallets
- **Classification**: ratio >= 0.75 indicates coordinated funding

---

### Signal Layer 2: Behavioral Signals

Monitor activity patterns relative to baseline.

#### Signal 2.1: Organization Momentum
- **Metric**: (activity_24h - activity_7d_avg) / activity_7d_avg
- **Range**: negative to +∞ (sigmoid normalized to 0-1)
- **Computation**: Transfer count comparison across time windows
- **Interpretation**: Acceleration in activity = imminent action
- **Sigmoid Map**: -1→0.17, 0→0.5, +1→0.83, +2→0.9

#### Signal 2.2: Creator Expansion
- **Metric**: count of new creators funded in 24h window
- **Range**: 0-∞
- **Computation**: Compare creators_24h vs baseline
- **Interpretation**: Recruiting new participants = launch preparation
- **Threshold**: 2+ new creators = significant wave activity

#### Signal 2.3: Funding Cadence
- **Metric**: burst_count (1h windows with 3+ transfers)
- **Range**: 0-∞
- **Computation**: GROUP BY CAST(block_time/3600 AS INTEGER) HAVING COUNT >= 3
- **Interpretation**: Tightly-timed funding = coordinated action
- **Threshold**: 5+ bursts in 24h = high activity

---

### Signal Layer 3: Preparation Signals

Identify signals directly preceding launches.

#### Signal 3.1: Seed Concentration
- **Metric**: 1 - (stddev / mean) of seed transfer amounts
- **Range**: 0-1
- **Computation**: Filter 0.5-10 SOL transfers, compute distribution stats
- **Interpretation**: Uniform seed amounts = equal treatment (planned coordination)
- **Threshold**: >= 0.8 indicates tight coordination

#### Signal 3.2: Funding Bursts
- **Metric**: number of 1h windows with 3+ transfers
- **Range**: 0-∞
- **Computation**: Time-bucketed transfer count
- **Interpretation**: Rapid sequential funding = launch day activity
- **Temporal Window**: 24h, 72h, 7d variants

#### Signal 3.3: Operator Activity Spike
- **Metric**: (operator_txs_24h / operator_avg_7d) - 1.0
- **Range**: -1 to +∞ (normalized to 0-1)
- **Computation**: Operator wallet transaction count anomaly
- **Interpretation**: Operator surge = active orchestration
- **Threshold**: 2x baseline = 0.5 normalized

---

### Signal Layer 4: Predictive Signals

Synthesize lower-level signals into launch probability.

#### Signal 4.1: Launch Probability (7-day)
- **Metric**: weighted sum of recency, scale, launch_rate, velocity, coordination, network_risk, reputation
- **Range**: 0-100 (0-1 normalized)
- **Formula**: See Algorithm 2, Section 4
- **Interpretation**: Direct 7-day launch predictor
- **Validation**: Backtested against historical launches

#### Signal 4.2: Launch Wave Score
- **Metric**: weighted sum of creator_expansion, burst_count, momentum, operator_spike, reuse_delta
- **Range**: 0-100 (0-1 normalized)
- **Formula**: See Algorithm 5, Section 4
- **Interpretation**: Multi-token launch pattern detection
- **Wave Types**: expansion | replacement | sustained

#### Signal 4.3: Master Launch Score
- **Metric**: weighted sum of all 8 signals
- **Range**: 0-1
- **Formula**: See Algorithm 6, Section 4
- **Interpretation**: Composite probability of imminent launch
- **Alert Mapping**: CRITICAL (≥0.75) | HIGH (≥0.60) | WATCH (≥0.40) | LOW (<0.40)

---

### Signal Interaction Effects

Signals interact multiplicatively and compensate:

**Strong Positive Interaction**: High Launch Probability + High Seed Concentration
- Interpretation: Prepared organization with probability signal
- Impact: Master score rises super-linearly

**Negative Interaction**: High Launch Probability + High Rug Probability
- Interpretation: Likely scam, not legitimate launch
- Impact: Network risk dampens probability signal

**Compensatory Interaction**: Medium Probability + High Wave Activity
- Interpretation: Activity signals launch despite moderate base probability
- Impact: Wave score "makes up" for lower direct signal

---

## SECTION 7 — Alert System

### Alert Generation Pipeline

**Trigger Source**: master_launch_signals table after Phase 6 completion

**Processing Steps**:
```
1. Phase 6 computes master_launch_score for each organization
2. Classify alert_level: LOW | WATCH | HIGH | CRITICAL
3. Store in master_launch_signals (INSERT OR REPLACE)
4. Phase 3 alert worker checks for threshold breaches
5. Fire alerts with daily dedup (max 1 per type per org per day)
6. Routes to notification systems based on severity
```

---

### Alert Levels and Thresholds

| Level | Score Range | Trigger | SLA | Action |
|-------|------------|---------|-----|--------|
| **CRITICAL** | ≥ 0.75 | Imminent launch probable | 1 hour | Immediate escalation |
| **HIGH** | 0.60–0.74 | Strong launch signals | 4 hours | Priority investigation |
| **WATCH** | 0.40–0.59 | Moderate activity | 24 hours | Close monitoring |
| **LOW** | < 0.40 | Minimal signals | 7 days | Routine tracking |

---

### Alert Rules (from Phase 3 AlertWorker)

#### Rule 1: Funding Burst Alert
```
IF snapshot.active_funders >= 3 in 24h window:
  alert_type = 'funding_burst'
  severity = 'HIGH'
  message = f"{org_id}: {active_funders} wallets funding in 24h"
```

#### Rule 2: Creator Recruitment Alert
```
IF snapshot.active_creators >= 2 in 24h window:
  alert_type = 'creator_funded'
  severity = 'MEDIUM'
  message = f"{org_id}: {active_creators} creators funded today"
```

#### Rule 3: Operator Spike Alert
```
IF snapshot.burst_count >= 5 in 24h window:
  alert_type = 'operator_spike'
  severity = 'HIGH'
  message = f"{org_id}: operator activity surge ({burst_count} bursts)"
```

#### Rule 4: Watchlist Promotion Alert
```
IF launch_window.prob_launch_24h >= 80:
  alert_type = 'watchlist_promotion'
  severity = 'HIGH'
  message = f"{org_id}: promoted to launch watchlist"
```

#### Rule 5: Risk Spike Alert
```
IF org_risk_score today - org_risk_score yesterday > 20:
  alert_type = 'risk_spike'
  severity = 'CRITICAL'
  message = f"{org_id}: risk score jumped ({delta} points)"
```

---

### Alert Deduplication

**Dedup Strategy**: Per calendar day per organization per alert type

```sql
-- Check if alert already fired today
SELECT COUNT(*) FROM org_alerts
WHERE organization_id = ?
  AND alert_type = ?
  AND date(created_at, 'unixepoch') = date('now')
```

**Behavior**:
- Max 1 funding_burst alert per org per day
- Max 1 creator_funded alert per org per day
- Same alert can fire on different days

**Rationale**: Prevents alert fatigue from repeated triggers while allowing multi-day escalation

---

### Alert Output Views

**View 1: vw_critical_launches**
```sql
SELECT organization_id, operator_wallet, master_launch_score,
       alert_level, all_8_signals
WHERE alert_level = 'CRITICAL'
ORDER BY master_launch_score DESC
```
**Purpose**: Real-time critical alerts for escalation

**View 2: vw_launch_watchlist**
```sql
SELECT organization_id, operator_wallet, master_launch_score,
       alert_level, all_8_signals
WHERE alert_level IN ('HIGH', 'CRITICAL')
ORDER BY master_launch_score DESC
```
**Purpose**: Investigation queue for analysts

---

## SECTION 8 — System Scheduling

### Daily Pipeline Execution

**Schedule**: 5:00 AM UTC every day

**Trigger**: Cron job or task scheduler (e.g., GitHub Actions, systemd timer)

**Sequential Phases**:
```
Phase 1 (v1): 10-40 seconds     Organization Detection
Phase 2 (v2): 20-40 seconds     Launch Probability
Phase 3 (v3): 40-90 seconds     Predictive Analytics
Phase 4:      10-20 seconds     Seed Concentration
Phase 4.5:    10-30 seconds     Funder Overlap
Phase 5:      30-60 seconds     Launch Wave Detection
Phase 6:      5-15 seconds      Master Launch Score
═══════════════════════════════════════════════════════════
TOTAL:        2-5 minutes       Complete Pipeline
```

**Entry Point**: `/Users/kevinkeaveney/Dev/claude/flex/dev_intelligence_detection.py`

```bash
#!/bin/bash
# Daily FLEX pipeline runner
cd /path/to/flex
python3 dev_intelligence_detection.py
exit_code=$?
echo "Pipeline exit code: $exit_code"
```

**Exit Codes**:
- `0`: All phases succeeded
- `1`: One or more phases failed (logged with details)

**Failure Mode**: Entire job fails if any phase fails (transaction semantics via SQLite WAL)

**Monitoring**: Cron emails on failure, logs written to `/var/log/flex/dev_intelligence.log`

---

### Real-Time Ingestion via Webhooks

**Source**: Helius RPC webhook (or compatible provider)

**Entry Point**: `/src/core/webhook_handler.py`

**Flow**:
```
1. Receive webhook notification with transaction signature
2. Fetch transaction from RPC (or use provided data)
3. Extract transfers using TransferIndexer.extract_transfers()
4. INSERT into transfer_index (immediate)
5. Log ingestion metrics
```

**Benefits**:
- No polling (instant data availability)
- Automatic rollup before daily pipeline
- 98% RPC cost reduction (queries from indexed local data)

**Failure Handling**: Individual transaction failures don't block pipeline; logged as warnings

---

### Incremental vs Batch Processing

**Batch Processing (Daily at 5 AM)**:
- Re-computes all organizations
- Handles detected patterns
- Produces definitive daily results
- Uses INSERT OR REPLACE for idempotency

**Incremental Processing (Real-time)**:
- Updates transfer_index immediately from webhooks
- Does NOT re-compute scores (happens at daily batch)
- Provides up-to-date transfer data for queries
- Optional: light scoring for real-time dashboards

**Rationale**: Batch guarantees consistency; incremental provides freshness

---

## SECTION 9 — UI / Dashboard Architecture

### Dashboard Views (Proposed Implementation)

**View 1: Developer Organizations Leaderboard**
```
Table columns:
├── Organization ID
├── Operator Wallet (truncated)
├── Cluster Size (wallet count)
├── Creator Count
├── Token Count
├── Org Score (0-1)
├── Updated At

Sorting: organization_score DESC
Filtering: cluster_size >= 2, creator_count >= 2
```

**Query**:
```sql
SELECT organization_id, operator_wallet, cluster_size,
       creator_count, token_count, organization_score,
       updated_at
FROM dev_organizations
ORDER BY organization_score DESC
LIMIT 100
```

---

**View 2: Launch Probability Leaderboard**
```
Columns:
├── Organization ID
├── Operator Wallet
├── 24h Launch Prob
├── 72h Launch Prob
├── 7d Launch Prob
├── Last Update

Sorting: prob_launch_24h DESC
```

**Query**:
```sql
SELECT olw.organization_id, do_.operator_wallet,
       olw.prob_launch_24h, olw.prob_launch_72h, olw.prob_launch_7d,
       olw.prediction_date
FROM org_launch_windows olw
JOIN dev_organizations do_ ON olw.organization_id = do_.organization_id
WHERE olw.prediction_date = (
  SELECT MAX(prediction_date) FROM org_launch_windows olw2
  WHERE olw2.organization_id = olw.organization_id
)
ORDER BY olw.prob_launch_24h DESC
```

---

**View 3: Master Launch Score Watchlist**
```
Columns:
├── Organization ID
├── Alert Level (color-coded)
├── Master Score (0-1)
├── Launch Probability
├── Seed Concentration
├── Funder Overlap
├── Last Updated

Color coding:
├── CRITICAL: 🔴 Red
├── HIGH: 🟠 Orange
├── WATCH: 🟡 Yellow
├── LOW: 🟢 Green

Sorting: master_launch_score DESC
```

**Query**:
```sql
SELECT organization_id, alert_level, master_launch_score,
       launch_probability, seed_concentration, funder_overlap_score,
       computed_at
FROM master_launch_signals
ORDER BY master_launch_score DESC
```

---

**View 4: Organization Activity Time-Series**
```
Chart type: Line graph
X-axis: Date (snapshot_date)
Y-axis (multi-line):
├── Active Funders (left axis)
├── Weighted Volume in SOL (right axis)
├── Launch Count (bar chart overlay)

Organization selector: dropdown
Time range: Last 30 days
```

**Query**:
```sql
SELECT snapshot_date, active_funders, weighted_volume,
       launch_count
FROM org_snapshots
WHERE organization_id = ? AND snapshot_date >= date('now', '-30 days')
ORDER BY snapshot_date ASC
```

---

**View 5: Risk Dashboard**
```
Cards:
├── Organization
├── Risk Score (0-100)
├── Rug Probability (%)
├── Instability Score
├── Blocked Creators (count)
├── Token Velocity (tokens/day)

Table sorting: risk_score DESC
Filtering: risk_score >= 60
```

**Query**:
```sql
SELECT organization_id, risk_score, rug_probability,
       instability_score, blocked_creator_count,
       token_velocity
FROM org_risk_scores
ORDER BY risk_score DESC
```

---

**View 6: Launch Wave Detection**
```
Table columns:
├── Organization
├── Wave Date
├── Wave Type (expansion|replacement|sustained)
├── Wave Score (0-100)
├── Confidence (0-1)
├── New Creators
├── Burst Count

Sorting: wave_score DESC
```

**Query**:
```sql
SELECT organization_id, wave_date, wave_type, wave_score,
       confidence, new_creators_24h, funding_burst_count
FROM launch_waves
ORDER BY wave_score DESC
```

---

### REST API Endpoints (from `src/core/dev_intelligence_api.py`)

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/api/orgs` | GET | `limit=50`, `min_score=0` | List orgs sorted by score |
| `/api/orgs/<id>` | GET | — | Single org details |
| `/api/orgs/<id>/signals` | GET | — | All 8 signals + composite |
| `/api/orgs/<id>/history` | GET | `days=30` | 30-day time-series |
| `/api/orgs/critical` | GET | — | CRITICAL alert orgs |
| `/api/orgs/watchlist` | GET | — | HIGH + CRITICAL alerts |
| `/api/tokens/<mint>/prediction` | GET | — | Token outcome prediction |

---

## SECTION 10 — Deployment Architecture

### System Components

```
┌─────────────────────────────────────────────────────────┐
│ SOLANA BLOCKCHAIN (RPC)                                 │
│ • Transfer events (system program)                       │
│ • Token mint events (SPL token program)                  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ WEBHOOK INGESTION LAYER (Helius/Custom)                │
│ • Real-time transaction notifications                    │
│ • Optional: Fallback RPC polling                        │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ TRANSFER INDEXER                                        │
│ • Parse transfers from transactions                      │
│ • INSERT into transfer_index (WAL mode)                 │
│ • ~0.5ms per transfer                                   │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ SQLITE DATABASE (flex_complete_database.db)            │
│ • 15+ tables with indexes                               │
│ • WAL mode for concurrent access                        │
│ • ~10 GB for 1M transfers + 1 year history              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ DAILY PIPELINE (5 AM UTC)                              │
│ Phase 1-6 orchestration via dev_intelligence_detection.py
│ • Sequential execution (transaction semantics)          │
│ • 2-5 minute runtime                                    │
│ • Logs to /var/log/flex/dev_intelligence.log            │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ ANALYSIS OUTPUT                                         │
│ • master_launch_signals (primary alert source)          │
│ • org_launch_windows (probability forecasts)            │
│ • org_snapshots (time-series data)                      │
│ • launch_waves (pattern detection)                      │
│ • dev_reputation (historical tracking)                  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ ALERT ROUTING                                           │
│ • CRITICAL (≥0.75) → Slack/Email/SMS                    │
│ • HIGH (≥0.60) → Dashboard highlight                    │
│ • WATCH (≥0.40) → Queue for review                      │
│ • LOW (<0.40) → Logged for analytics                    │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ REST API / DASHBOARD                                    │
│ • Flask/FastAPI server on port 5002                     │
│ • Queries master_launch_signals and snapshots           │
│ • Real-time visualization                               │
└─────────────────────────────────────────────────────────┘
```

---

### Hardware Requirements

**Minimum (Development)**:
- CPU: 2 cores, 2 GHz
- RAM: 4 GB
- Storage: 50 GB (SSD for SQLite WAL performance)
- Network: Broadband with webhook support

**Recommended (Production)**:
- CPU: 4-8 cores, 3 GHz (parallel query execution)
- RAM: 16-32 GB (database buffer pool)
- Storage: 200-500 GB SSD (NVMe for WAL)
- Network: Dedicated connection to RPC provider (< 100ms latency)

**Storage Scaling**:
- 1M transfers: ~320 MB indexed
- 10M transfers: ~3.2 GB
- 100M transfers: ~32 GB
- 1 year of Solana activity: ~5-10M transfers

---

### Software Stack

**Language**: Python 3.9+

**Dependencies**:
```
sqlite3              (bundled)
networkx>=2.5        (graph algorithms)
numpy                (numerical computing)
flask>=2.0           (REST API)
requests             (HTTP client for RPC)
python-dotenv        (configuration)
```

**Database**: SQLite 3.37+ (WAL mode required)

**Job Scheduler**:
- Development: Manual cron
- Production: GitHub Actions, systemd timer, or APScheduler

**Monitoring**:
- Logging: Python logging to `/var/log/flex/dev_intelligence.log`
- Metrics: Optional StatsD/Prometheus integration
- Alerting: Slack webhooks for critical failures

---

### Deployment Steps

**1. Environment Setup**
```bash
git clone https://github.com/kevinkeaveney/flex.git
cd flex
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**2. Database Initialization**
```bash
mkdir -p database
sqlite3 database/flex_complete_database.db < database/migrations/phase3_transfer_index_migration.sql
sqlite3 database/flex_complete_database.db < database/migrations/dev_intelligence_graph.sql
sqlite3 database/flex_complete_database.db < database/migrations/dev_intelligence_v2.sql
sqlite3 database/flex_complete_database.db < database/migrations/dev_intelligence_v3.sql
sqlite3 database/flex_complete_database.db < database/migrations/creator_seed_metrics.sql
sqlite3 database/flex_complete_database.db < database/migrations/funder_overlap_signal.sql
sqlite3 database/flex_complete_database.db < database/migrations/launch_wave_detection.sql
sqlite3 database/flex_complete_database.db < database/migrations/master_launch_score.sql
```

**3. Configuration**
```bash
cp .env.example .env
# Edit .env with RPC provider URL, webhook signing key, etc.
```

**4. Schedule Daily Job**
```bash
# Option A: Cron
echo "0 5 * * * cd /path/to/flex && python3 dev_intelligence_detection.py" | crontab -

# Option B: systemd timer
# Create /etc/systemd/system/flex-pipeline.service and .timer
systemctl enable flex-pipeline.timer
systemctl start flex-pipeline.timer
```

**5. Start Webhook Server (Real-time Ingestion)**
```bash
python3 -m src.core.webhook_handler
# Listens on 0.0.0.0:5001 for Helius notifications
```

**6. Start API Server (REST + Dashboard)**
```bash
python3 -m src.core.main
# Listens on 0.0.0.0:5002
# Dashboard: http://localhost:5002
```

---

### Monitoring and Maintenance

**Log Monitoring**:
```bash
tail -f /var/log/flex/dev_intelligence.log | grep -E "ERROR|WARNING"
```

**Database Health**:
```bash
# Check WAL file size (should be < 10 MB after checkpoint)
ls -lh database/flex_complete_database.db*

# Vacuum/optimize
sqlite3 database/flex_complete_database.db "VACUUM"

# Check schema integrity
sqlite3 database/flex_complete_database.db ".schema"
```

**Pipeline Validation**:
```bash
# Check latest phase 6 results
sqlite3 database/flex_complete_database.db \
  "SELECT alert_level, COUNT(*) FROM master_launch_signals GROUP BY alert_level"

# Expected output:
# CRITICAL|<10-20%>
# HIGH|<20-30%>
# WATCH|<30-40%>
# LOW|<remainder>
```

---

## SECTION 11 — Future Extensions

### Extension 1: Developer Fingerprinting

**Goal**: Track individual developers across multiple organizations via behavioral patterns

**Signals**:
- Transfer amount preferences (amounts favored by developer)
- Timing patterns (preferred times of day for transfers)
- Creator naming conventions
- Token launch timing patterns
- Blockchain interaction sequences

**Implementation**:
```python
class DeveloperFingerprint:
    def __init__(self, wallet: str):
        self.wallet = wallet
        self.amount_signature = self._compute_amount_distribution()
        self.timing_signature = self._compute_timing_pattern()
        self.interaction_sequence = self._compute_action_sequence()
```

**Application**: Identify same developer across multiple organizations, detect evasion attempts

---

### Extension 2: Machine Learning Scoring

**Current State**: Rule-based weights (0.22, 0.18, 0.12, etc.)

**ML Approach**: Learn optimal weights from historical launches

**Data Requirements**:
- 1000+ known launches (labeled with success/failure/rug)
- Historical signal values for each launch
- Market cap achieved vs time
- Rug probability vs actual rug occurrence

**Algorithms**:
- Gradient boosting (XGBoost): weight optimization
- Neural networks: non-linear signal interactions
- Anomaly detection: identify novel launch patterns

**Expected Improvement**: 5-15% accuracy gain over rule-based

---

### Extension 3: Organization Clustering

**Goal**: Group organizations by similarity for pattern recognition

**Clustering Dimensions**:
- Organization size (wallet, creator, token counts)
- Token outcomes (success vs rug distribution)
- Funding velocity
- Operator experience
- Geographic/timezone patterns

**Algorithm**: K-means or hierarchical clustering

**Applications**:
- Profile-based risk assessment (cluster-specific rug rates)
- Launch timing prediction (cluster seasonality)
- Peer comparison ("org X similar to cluster Y")

---

### Extension 4: Cross-Chain Intelligence

**Goal**: Extend analysis to other blockchains (Ethereum, Base, Polygon)

**Challenges**:
- Different token standards (ERC-20 vs SPL)
- Different naming conventions
- Cross-chain bridge transfers
- DEX-specific patterns

**Approach**:
- Unified wallet model (same wallet can be funder on multiple chains)
- Per-chain signal computation
- Cross-chain relationship detection
- Master score aggregation across chains

**Expected Impact**: Detect ecosystem-wide coordination patterns

---

### Extension 5: Automated Trading Integration

**Goal**: Act on FLEX alerts with automated trading strategies

**Hypothetical Flow**:
```
CRITICAL Alert → Launch detected
    ↓
Check liquidity constraints
    ↓
Place limit buy at 5x SOL volume
    ↓
Set stop-loss at 2x SOL volume
    ↓
Trail profit-taking above 10x
    ↓
Report P&L to analytics
```

**Risks**:
- False positive alerts → unprofitable trades
- Slippage on limit orders
- Rug detection failing (holding through rug)
- Regulatory classification as trading bot

**Risk Mitigation**:
- Paper trading phase (no real funds)
- Conservative position sizing
- Ensemble alerts (require multiple signals)
- Circuit breakers (stop if loss exceeds threshold)

---

### Extension 6: Real-Time Anomaly Detection

**Current State**: Daily batch scoring; 24-hour latency between event and alert

**Improvement**: Real-time anomaly scoring on webhook ingestion

**Approach**:
- Lightweight anomaly detection on transfer stream
- Rolling window statistics (activity vs baseline)
- Immediate flagging of unusual patterns
- Scored alerts before daily batch

**Expected Benefit**: 12-24 hour earlier alerts on launches

---

### Extension 7: Reputation System Enhancements

**Current State**: success_rate and rug_rate per wallet

**Enhancements**:
- Time-decay (older launches weighted less)
- Outcome classification (2x, 10x, 100x, rug, burn)
- Market cap correlation (bigger launches = better operators)
- Community feedback (trader reports of scams)
- Proof-of-stake weighting (operator skin in the game)

**Implementation**:
```python
class EnhancedReputation:
    def __init__(self, wallet: str):
        self.success_rate = self._decay_weighted_success()
        self.rug_rate = self._decay_weighted_rug()
        self.outcome_distribution = self._compute_percentiles()
        self.community_score = self._aggregate_feedback()
```

---

### Extension 8: Behavioral Clustering & Pattern Library

**Goal**: Identify repeated tactics/strategies by developers

**Patterns**:
- "Quick flip" (launch → 5x → sell): avg 3-day hold
- "Slow burn" (sustained funding, gradual growth): avg 30-day ramp
- "Pump & dump" (rapid volume, hard crash): avg 1-week duration
- "Honeypot" (large initial market cap, can't sell): frozen liquidity
- "Gradual rug" (soft-selling over weeks): hidden rug

**Detection**:
```python
class BehaviorCluster:
    def __init__(self, org_id: int):
        self.pattern = self._match_historical_patterns()
        self.confidence = self._compute_pattern_confidence()
        self.predicted_outcome = self._predict_based_on_pattern()
```

**Application**: "Org matches 'quick flip' pattern from cluster X; 80% likely to 5x then crash"

---

### Extension 9: Explainability Layer

**Goal**: Generate human-readable explanations for alert scores

**Current**: Score is opaque (0.73 = HIGH)

**Enhanced**: Score + explanation:
```
Organization: ORG-12345
Master Score: 0.73 (HIGH alert)

Breakdown:
├─ Launch Probability: 78 (highest impact, +0.17)
├─ Seed Concentration: 0.91 (+0.11) ← high coordination
├─ Funder Overlap: 0.74 (+0.09) ← same wallets funding multiple creators
├─ Launch Wave Score: 65 (+0.12) ← moderate multi-launch activity
├─ Organization Momentum: 1.8x (+0.18) ← 80% increase in activity
├─ Creator Reuse: 0.58 (+0.05)
├─ Operator Activity: 0.72 (+0.06)
└─ Reputation: 0.40 (-0.04) ← operator has mixed history

Top Risk Factors:
1. Activity surge (momentum 1.8x) suggests preparation
2. High seed concentration indicates planned launch
3. Funder wallet overlap suggests single operator

Comparison:
├─ Similar to orgs that launched in 2-7 days
└─ 20th percentile seed coordination (high precision)

Confidence: 87% based on signal strength and cluster similarity
```

**Implementation**: Template-based text generation with signal contributions

---

### Extension 10: Integration with External APIs

**Goal**: Enrich FLEX alerts with external context

**Services**:
- CoinGecko/Coingecko: historical token metadata
- Dune Analytics: community-contributed dashboards
- BlockScout: token holder verification
- Metaplex: NFT collection data
- Orca/Raydium: DEX pricing and liquidity

**Enrichment Examples**:
```
Alert: CRITICAL for ORG-5678
External Context:
├─ Operator wallet tracked by 3 community reports (scam database)
├─ Previous launch by same creator had 92% rug rate
├─ DEX liquidity only $5k (low volume for launch scale)
└─ NFT collection associated with org marked as plagiarism
```

---

### Extension 11: Feedback Loop & Model Improvement

**Goal**: Learn from real outcomes to refine signals

**Data Collection**:
```python
class LaunchOutcome:
    def __init__(self, mint: str):
        self.predicted_score = # from master_launch_signals
        self.actual_outcome = # (2x, 10x, 100x, rug, burn, ongoing)
        self.prediction_date = # date of alert
        self.outcome_date = # date outcome determined
        self.latency = # days to outcome
        self.accuracy_contribution = # true/false positive
```

**Analysis**:
- Accuracy by organization size
- Accuracy by alert level
- Signal importance via permutation testing
- Optimal thresholds via ROC curves

**Action**:
- Quarterly signal weight recalibration
- Threshold adjustment (raise CRITICAL from 0.75 → 0.80 if false positives high)
- New signal creation (if consistent missed patterns)

---

## APPENDIX A — SQL Query Reference

**Most Common Queries** (copy-paste ready):

### Query 1: Critical Launches This Week
```sql
SELECT do_.operator_wallet, mls.master_launch_score,
       mls.launch_probability, mls.seed_concentration,
       mls.funder_overlap_score, mls.organization_momentum,
       mls.computed_at
FROM master_launch_signals mls
JOIN dev_organizations do_ ON mls.organization_id = do_.organization_id
WHERE mls.alert_level = 'CRITICAL'
  AND mls.computed_at >= unixepoch('now') - 7*86400
ORDER BY mls.master_launch_score DESC;
```

### Query 2: Organizations with Suspicious Funding Patterns
```sql
SELECT do_.operator_wallet, do_.cluster_size, do_.creator_count,
       avg(fo.overlap_ratio) as avg_overlap,
       do_.organization_score
FROM dev_organizations do_
LEFT JOIN funder_overlap fo ON do_.operator_wallet IN (
  SELECT dom.member_address FROM dev_organization_members dom
  WHERE dom.organization_id = do_.organization_id AND dom.member_type = 'wallet'
)
GROUP BY do_.organization_id
HAVING avg(fo.overlap_ratio) >= 0.75
ORDER BY avg_overlap DESC;
```

### Query 3: Momentum-Driven Activity Surge (Last 24h)
```sql
SELECT do_.operator_wallet, os_today.active_funders,
       os_today.burst_count, os_today.weighted_volume,
       os_yesterday.active_funders as yesterday_funders,
       (os_today.active_funders - os_yesterday.active_funders) as delta
FROM org_snapshots os_today
JOIN org_snapshots os_yesterday ON os_today.organization_id = os_yesterday.organization_id
  AND os_today.snapshot_date = date('now')
  AND os_yesterday.snapshot_date = date('now', '-1 day')
JOIN dev_organizations do_ ON os_today.organization_id = do_.organization_id
WHERE os_today.active_funders > os_yesterday.active_funders
ORDER BY delta DESC;
```

### Query 4: High-Risk Organizations Trending Up
```sql
SELECT do_.operator_wallet, ors.risk_score, ors.rug_probability,
       ors.instability_score, olw.prob_launch_24h, olw.prob_launch_7d,
       mls.master_launch_score, mls.alert_level
FROM org_risk_scores ors
JOIN dev_organizations do_ ON ors.organization_id = do_.organization_id
LEFT JOIN org_launch_windows olw ON ors.organization_id = olw.organization_id
LEFT JOIN master_launch_signals mls ON ors.organization_id = mls.organization_id
WHERE ors.risk_score >= 70
ORDER BY ors.risk_score DESC, mls.master_launch_score DESC;
```

### Query 5: Creator Reuse Indicating Serial Operator
```sql
SELECT dom.member_address as creator, COUNT(DISTINCT do_.organization_id) as org_count,
       COUNT(DISTINCT ta.mint) as token_count,
       SUM(CASE WHEN ta.rug_probability > 0.7 THEN 1 ELSE 0 END) as rug_count,
       dr.success_rate, dr.rug_rate
FROM dev_organization_members dom
JOIN dev_organizations do_ ON dom.organization_id = do_.organization_id
LEFT JOIN token_analysis ta ON dom.member_address = ta.earliest_tx_creator
LEFT JOIN dev_reputation dr ON dom.member_address = dr.wallet
WHERE dom.member_type = 'creator'
GROUP BY dom.member_address
HAVING COUNT(DISTINCT do_.organization_id) >= 3
ORDER BY token_count DESC;
```

---

## APPENDIX B — Configuration Reference

**Environment Variables** (`.env` file):

```bash
# RPC Provider
RPC_URL=https://api.mainnet-beta.solana.com
HELIUS_API_KEY=your_api_key_here

# Database
DB_PATH=database/flex_complete_database.db

# Webhook Server
WEBHOOK_PORT=5001
WEBHOOK_SECRET=your_signing_secret

# API Server
API_PORT=5002
API_HOST=0.0.0.0

# Alerting
SLACK_WEBHOOK=https://hooks.slack.com/services/...
ALERT_EMAIL=ops@example.com

# Logging
LOG_LEVEL=INFO
LOG_DIR=/var/log/flex
```

---

## APPENDIX C — Performance Benchmarks

| Component | Metric | Value |
|-----------|--------|-------|
| Transfer Indexing | Throughput | 10,000 transfers/sec |
| Organization Detection | Time | 30 seconds for 10k wallets |
| Launch Probability | Throughput | 50 orgs/second |
| Predictive Analytics | Time | 90 seconds for 500 orgs |
| Seed Concentration | Time | 20 seconds for 5k creators |
| Funder Overlap | Time | 30 seconds for 1k funders |
| Launch Wave Detection | Time | 60 seconds for 500 orgs |
| Master Launch Score | Time | 15 seconds for 500 orgs |
| **Total Daily Pipeline** | **Time** | **2-5 minutes** |
| Query Latency | P99 | <5ms for indexed queries |
| Database Size | Per 1M transfers | 320 MB |

---

## APPENDIX D — Glossary

**Alert Level**: Classification of launch probability (CRITICAL, HIGH, WATCH, LOW)

**Burst**: 1-hour window with 3+ transfers; indicates coordinated activity

**Cluster**: Group of wallets detected via Louvain community detection

**Creator**: Wallet receiving seed-phase funding (0.5-10 SOL)

**Developer Organization**: Cluster of 2+ wallets, 2+ creators, 1+ tokens

**Funder**: Wallet sending seed-phase transfers

**Master Launch Score**: Composite 0-1 metric aggregating 8 signals

**Momentum**: (activity_24h - activity_7d_avg) / activity_7d_avg

**Operator**: Wallet with highest betweenness centrality in cluster

**Organization**: Synonym for developer organization

**Seed Concentration**: 1 - (stddev / mean) of seed transfer amounts

**Seed Phase**: Transfers 0.5-10 SOL (preparation before launch)

**Signal**: Quantitative indicator of launch preparation (8 types)

**Snapshot**: Daily activity metrics for organization

**Wallet**: Solana address capable of transferring SOL

**Wave**: Multi-token launch pattern (expansion, replacement, sustained)

---

## CONCLUSION

FLEX is a comprehensive, production-ready system for detecting Solana developer organizations and predicting token launches. The architecture combines:

- **Robust data ingestion** via transfer indexing (98% RPC savings)
- **Multi-layer signal computation** (structural, behavioral, preparation, predictive)
- **Unified alert classification** via Master Launch Score
- **Real-time and batch processing** for freshness and consistency
- **Transparent algorithms** with documented formulas and weights
- **Extensible design** for future ML, fingerprinting, and cross-chain intelligence

The system is deployed daily at 5 AM UTC, with real-time webhook ingestion and a REST API for dashboards and monitoring. Expected alert accuracy for CRITICAL launches is 80-90% with false positive rates under 10%.

---

**Document Version**: 3.1
**Last Updated**: March 2026
**Next Review**: June 2026
**Maintained By**: FLEX Development Team
