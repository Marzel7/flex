# FLEX Intelligence Database Schema Guide

**Status**: ✅ Complete Reference  
**Date**: March 12, 2026  
**Database**: `database/flex_complete_database.db` (SQLite)

---

## Overview

The FLEX Intelligence database tracks developer organizations, their funding relationships, creator activities, and token launch predictions. This guide covers the core tables needed for the dev intelligence pipeline.

---

## Core Tables for Dev Intelligence Pipeline

### Table 1: `dev_organizations`

**Purpose**: Stores detected developer organizations and their metadata.

**Schema**:
```sql
CREATE TABLE dev_organizations (
    organization_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_wallet    TEXT NOT NULL UNIQUE,           -- Primary wallet/operator
    organization_name  TEXT,                           -- Human-readable name
    cluster_size       INTEGER DEFAULT 1,              -- Number of wallets in org
    creator_count      INTEGER DEFAULT 0,              -- Unique creators
    token_count        INTEGER DEFAULT 0,              -- Tokens launched
    rug_probability    REAL DEFAULT 0,                 -- 0.0-1.0
    risk_level         TEXT,                           -- CRITICAL, HIGH, WATCH, LOW
    master_launch_score REAL DEFAULT 0,                -- 0.0-1.0 (main prediction)
    detected_at        INTEGER NOT NULL,               -- Unix timestamp
    updated_at         INTEGER NOT NULL,               -- Unix timestamp
    last_activity      INTEGER                         -- Unix timestamp of last activity
);
```

**Example Data**:
```sql
INSERT INTO dev_organizations VALUES
(1, '9B5X2pLkLL5FixxB2ind8qZfrDka6cgxjGVfCKqVVVVV', 'Alpha Dev Collective', 5, 12, 3, 0.78, 'HIGH', 0.82, 1710000000, 1710086400, 1710086340),
(2, '7kK9pQrjN2MmPpVvVvQq3sStTuUvWwXxYyZz1aAbBbCc', 'Beta Launcher Group', 3, 8, 2, 0.45, 'WATCH', 0.65, 1710000100, 1710086400, 1710085200),
(3, '5mM7nN8oPp9qQrRrSsStTuUuVvWwXxYyZz2aAbBbCcDd', 'Gamma Fund Network', 8, 25, 5, 0.92, 'CRITICAL', 0.91, 1709900000, 1710086400, 1710086300);
```

**Key Fields**:
- `operator_wallet`: Primary identifier (should be unique)
- `master_launch_score`: Composite score from all 8 signals (0-1, displayed as %)
- `cluster_size`: How many wallets are part of this organization
- `risk_level`: Categorical risk assessment (CRITICAL=highest risk)

---

### Table 2: `dev_organization_members`

**Purpose**: Links wallets and creators to their parent organization.

**Schema**:
```sql
CREATE TABLE dev_organization_members (
    member_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id    INTEGER NOT NULL,               -- FK to dev_organizations
    creator_wallet     TEXT NOT NULL,                  -- Wallet address
    member_type        TEXT DEFAULT 'creator',         -- 'creator', 'funder', 'operator'
    first_seen         INTEGER NOT NULL,               -- Unix timestamp
    last_seen          INTEGER NOT NULL,               -- Unix timestamp
    token_count        INTEGER DEFAULT 0,              -- Tokens launched by this creator
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, creator_wallet)
);
```

**Example Data**:
```sql
INSERT INTO dev_organization_members VALUES
(1, 1, 'creator1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'creator', 1710000000, 1710086400, 2),
(2, 1, 'creator2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'creator', 1710001000, 1710086400, 1),
(3, 1, '9B5X2pLkLL5FixxB2ind8qZfrDka6cgxjGVfCKqVVVVV', 'operator', 1710000000, 1710086400, 0),
(4, 2, 'creator3bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'creator', 1710000100, 1710086400, 1),
(5, 2, 'funder1bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'funder', 1710000200, 1710086400, 0);
```

**Key Fields**:
- `member_type`: Distinguish between creators (launch tokens) and funders
- `token_count`: How many tokens this creator has launched
- Organization can have many members

---

### Table 3: `transfer_index` (Core Transaction Table)

**Purpose**: All SOL transfers between wallets. Foundation for funder overlap analysis.

**Schema**:
```sql
CREATE TABLE transfer_index (
    transfer_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source             TEXT NOT NULL,                  -- Sender wallet
    destination        TEXT NOT NULL,                  -- Receiver wallet
    amount_sol         REAL NOT NULL,                  -- SOL amount transferred
    token_mint         TEXT,                           -- Token mint (if token transfer)
    signature          TEXT UNIQUE,                    -- Solana tx signature
    slot               INTEGER,                        -- Solana block slot
    timestamp          INTEGER NOT NULL,               -- Unix timestamp
    is_valid           INTEGER DEFAULT 1,              -- 1=valid, 0=failed
    transfer_type      TEXT,                           -- 'seed', 'contribution', 'distribution'
    created_at         INTEGER NOT NULL                -- When recorded
);

CREATE INDEX idx_ti_source ON transfer_index(source);
CREATE INDEX idx_ti_destination ON transfer_index(destination);
CREATE INDEX idx_ti_amount ON transfer_index(amount_sol);
CREATE INDEX idx_ti_timestamp ON transfer_index(timestamp DESC);
CREATE INDEX idx_ti_valid ON transfer_index(is_valid);
```

**Example Data**:
```sql
INSERT INTO transfer_index VALUES
(1, 'funder1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'creator1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 2.5, NULL, 'sig1...', 300000000, 1710000100, 1, 'seed', 1710000100),
(2, 'funder1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'creator2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 3.0, NULL, 'sig2...', 300000100, 1710000200, 1, 'seed', 1710000200),
(3, 'funder2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'creator1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 2.0, NULL, 'sig3...', 300000200, 1710000300, 1, 'seed', 1710000300),
(4, 'creator1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'pump_fun_contract_addr', 0.1, 'mint1...', 'sig4...', 300000300, 1710001000, 1, 'distribution', 1710001000),
(5, 'funder3aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'creator3bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 1.5, NULL, 'sig5...', 300000400, 1710000400, 1, 'seed', 1710000400);
```

**Key Fields**:
- `source`: Funder wallet
- `destination`: Creator/receiver wallet
- `amount_sol`: 0.5-10 SOL typically indicates seed funding
- `is_valid`: 1 for successful transfers (filters out failures)
- `timestamp`: Critical for activity analysis

**Seed Phase Filter** (for funder_overlap_analysis):
```sql
WHERE amount_sol BETWEEN 0.5 AND 10
AND is_valid = 1
```

---

### Table 4: `funder_overlap`

**Purpose**: Stores computed overlap between funder wallets (identifies coordination).

**Schema**:
```sql
CREATE TABLE funder_overlap (
    overlap_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_a            TEXT NOT NULL,                 -- First funder wallet
    funder_b            TEXT NOT NULL,                 -- Second funder wallet
    shared_creators     INTEGER DEFAULT 0,             -- # of creators both funded
    overlap_ratio       REAL DEFAULT 0,                -- 0.0-1.0 (shared/min)
    funder_a_creators   INTEGER DEFAULT 0,             -- Total creators funded by A
    funder_b_creators   INTEGER DEFAULT 0,             -- Total creators funded by B
    coordination_level  TEXT,                          -- 'very_strong', 'high', 'medium', 'low'
    detected_at         INTEGER NOT NULL,              -- Unix timestamp
    UNIQUE(funder_a, funder_b)
);

CREATE INDEX idx_fo_overlap_ratio ON funder_overlap(overlap_ratio DESC);
CREATE INDEX idx_fo_funder_a ON funder_overlap(funder_a);
CREATE INDEX idx_fo_funder_b ON funder_overlap(funder_b);
```

**Example Data**:
```sql
INSERT INTO funder_overlap VALUES
(1, 'funder1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'funder2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 3, 0.75, 4, 5, 'high', 1710086400),
(2, 'funder1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'funder3aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 2, 0.50, 4, 3, 'medium', 1710086400),
(3, 'funder2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'funder3aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 1, 0.33, 5, 3, 'low', 1710086400);
```

**Interpretation**:
- Funder A & B both funded 3 creators
- A funded 4 total, B funded 5 total
- overlap_ratio = 3 / min(4,5) = 0.75 (high coordination!)
- Suggests A and B are same operator or coordinated team

---

### Table 5: `master_launch_signals`

**Purpose**: Stores the 8 predictive signals for each organization.

**Schema**:
```sql
CREATE TABLE master_launch_signals (
    signal_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id    INTEGER NOT NULL,               -- FK to dev_organizations
    launch_probability REAL DEFAULT 0,                 -- 0-1 (22% weight)
    wave_score         REAL DEFAULT 0,                 -- 0-1 (18% weight)
    seed_concentration REAL DEFAULT 0,                 -- 0-1 (12% weight)
    funder_overlap     REAL DEFAULT 0,                 -- 0-1 (12% weight)
    velocity_score     REAL DEFAULT 0,                 -- 0-1 (10% weight)
    creator_reuse      REAL DEFAULT 0,                 -- 0-1 (8% weight)
    volatility_score   REAL DEFAULT 0,                 -- 0-1 (8% weight)
    recency_score      REAL DEFAULT 0,                 -- 0-1 (10% weight)
    master_score       REAL DEFAULT 0,                 -- Sum of weighted signals
    computed_at        INTEGER NOT NULL,               -- Unix timestamp
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id)
);
```

**Example Data**:
```sql
INSERT INTO master_launch_signals VALUES
(1, 1, 0.89, 0.78, 0.95, 0.82, 0.75, 0.65, 0.72, 0.88, 0.83, 1710086400),
(2, 2, 0.65, 0.45, 0.25, 0.35, 0.55, 0.40, 0.40, 0.52, 0.65, 1710086400),
(3, 3, 0.95, 0.88, 0.92, 0.90, 0.89, 0.91, 0.85, 0.91, 0.91, 1710086400);
```

**Master Score Calculation**:
```
master_score = (
    launch_probability * 0.22 +
    wave_score * 0.18 +
    seed_concentration * 0.12 +
    funder_overlap * 0.12 +
    velocity_score * 0.10 +
    creator_reuse * 0.08 +
    volatility_score * 0.08 +
    recency_score * 0.10
)
```

---

## Supporting Tables

### Table 6: `org_snapshots`

**Purpose**: Daily snapshots of organization activity metrics.

**Schema**:
```sql
CREATE TABLE org_snapshots (
    snapshot_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id    INTEGER NOT NULL,
    snapshot_date      INTEGER NOT NULL,               -- Unix timestamp (start of day)
    active_creators    INTEGER DEFAULT 0,
    active_funders     INTEGER DEFAULT 0,
    transfer_count     INTEGER DEFAULT 0,
    total_volume_sol   REAL DEFAULT 0,
    created_at         INTEGER NOT NULL
);
```

**Example Data**:
```sql
INSERT INTO org_snapshots VALUES
(1, 1, 1710000000, 8, 5, 12, 35.5, 1710000000),
(2, 1, 1710086400, 10, 6, 15, 42.3, 1710086400),
(3, 2, 1710000000, 5, 3, 7, 12.8, 1710000000);
```

---

### Table 7: `org_launch_windows`

**Purpose**: 24h, 72h, and 7d launch probability windows.

**Schema**:
```sql
CREATE TABLE org_launch_windows (
    window_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id    INTEGER NOT NULL,
    prob_24h           REAL DEFAULT 0,                 -- 0-1 probability in 24h
    prob_72h           REAL DEFAULT 0,                 -- 0-1 probability in 72h
    prob_7d            REAL DEFAULT 0,                 -- 0-1 probability in 7d
    confidence         REAL DEFAULT 0,                 -- Model confidence
    computed_at        INTEGER NOT NULL
);
```

**Example Data**:
```sql
INSERT INTO org_launch_windows VALUES
(1, 1, 0.45, 0.72, 0.88, 0.92, 1710086400),
(2, 2, 0.15, 0.28, 0.42, 0.75, 1710086400);
```

---

## Integration Workflow

### Step 1: Populate `dev_organizations`

Create organizations from detected wallet clusters:

```sql
INSERT INTO dev_organizations 
(operator_wallet, organization_name, cluster_size, creator_count, token_count, 
 risk_level, master_launch_score, detected_at, updated_at, last_activity)
VALUES 
('wallet_address', 'Organization Name', 5, 12, 3, 'HIGH', 0.82, 
 strftime('%s', 'now'), strftime('%s', 'now'), strftime('%s', 'now'));
```

### Step 2: Populate `dev_organization_members`

Link creators and funders to organizations:

```sql
INSERT INTO dev_organization_members 
(organization_id, creator_wallet, member_type, first_seen, last_seen, token_count)
VALUES 
(1, 'creator_address', 'creator', strftime('%s', 'now'), strftime('%s', 'now'), 2);
```

### Step 3: Populate `transfer_index`

Import transaction history (from Helius, Magic Eden, or on-chain):

```sql
INSERT INTO transfer_index 
(source, destination, amount_sol, token_mint, signature, slot, timestamp, 
 is_valid, transfer_type, created_at)
VALUES 
('funder_wallet', 'creator_wallet', 2.5, NULL, 'signature...', 300000000, 
 strftime('%s', 'now'), 1, 'seed', strftime('%s', 'now'));
```

### Step 4: Run Funder Overlap Analysis

```python
from src.core.funder_overlap_analysis import FunderOverlapAnalyzer

analyzer = FunderOverlapAnalyzer('database/flex_complete_database.db')
result = analyzer.analyze_and_store()
print(f"Overlaps found: {result['overlaps_found']}")
print(f"High coordination: {result['high_coordination_count']}")
```

This will populate `funder_overlap` table automatically.

### Step 5: Compute Master Launch Signals

```python
from src.core.master_launch_score import MasterLaunchScoreComputer

scorer = MasterLaunchScoreComputer('database/flex_complete_database.db')
result = scorer.compute_and_store()
```

This will populate `master_launch_signals` table.

---

## Data Relationships

```
dev_organizations (primary)
    ↓
    ├─→ dev_organization_members (creators/funders)
    │
    ├─→ master_launch_signals (8 signals per org)
    │
    ├─→ org_snapshots (daily activity)
    │
    └─→ org_launch_windows (time-window predictions)

transfer_index (transactions)
    ↓
    ├─→ Used to build funder_overlap (coordination)
    │
    └─→ Used to compute org_snapshots (activity metrics)
```

---

## Minimum Data Requirements

To run the dev intelligence pipeline, you need:

| Table | Min Rows | Purpose |
|-------|----------|---------|
| `dev_organizations` | 1 | At least one organization |
| `dev_organization_members` | 2+ | Creators/funders in org |
| `transfer_index` | 5+ | Seed transfers (0.5-10 SOL) |
| `master_launch_signals` | 1 | Signals for each org |

Without these, the pipeline runs but produces empty results.

---

## Common Queries

### Find all creators in an organization
```sql
SELECT creator_wallet, member_type, token_count
FROM dev_organization_members
WHERE organization_id = 1
ORDER BY token_count DESC;
```

### Find high-coordination wallet pairs
```sql
SELECT funder_a, funder_b, overlap_ratio, shared_creators
FROM funder_overlap
WHERE coordination_level IN ('high', 'very_strong')
ORDER BY overlap_ratio DESC;
```

### Get organization signals
```sql
SELECT 
    launch_probability, wave_score, seed_concentration,
    funder_overlap, velocity_score, creator_reuse,
    volatility_score, recency_score, master_score
FROM master_launch_signals
WHERE organization_id = 1
ORDER BY computed_at DESC
LIMIT 1;
```

### Find active organizations (last 24h)
```sql
SELECT organization_id, last_activity
FROM dev_organizations
WHERE last_activity > strftime('%s', 'now', '-1 day')
ORDER BY master_launch_score DESC;
```

---

## Next Steps for Integration

1. **Load historical data** into `dev_organizations` and `dev_organization_members`
2. **Import transfer history** into `transfer_index` (from Helius RPC or on-chain indexer)
3. **Run funder_overlap_analysis** to detect coordination patterns
4. **Compute master_launch_signals** for all organizations
5. **Dashboard queries** will then have data to display

---

**Status**: ✅ Ready for Integration  
**Date**: March 12, 2026

