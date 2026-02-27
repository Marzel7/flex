# network_evidence Rollup Table — Design Document

## Objective

Add a `network_evidence` rollup table that aggregates evidence across a network for efficient UI reads.

**Constraints:**
- ✅ Must not break `networks_release`
- ✅ Must remain idempotent
- ✅ Must be transaction-safe
- ✅ Read-only for UI (precomputed, no live calculations)

---

## Data Model

### Source Tables (Evidence)

**coordinated_creator_edges**
```
creator_a              TEXT
creator_b              TEXT
bridge_funder          TEXT
first_seen_block_time  INTEGER
evidence_tx            TEXT (transaction hash)
confidence             INTEGER (0-100)
created_at             TIMESTAMP
```

This table contains creator-to-creator coordination evidence:
- Two creators coordinated via a bridge funder
- Evidence transaction showing the coordination
- Confidence score (higher = stronger evidence)

### network_evidence Rollup Table (NEW)

```sql
CREATE TABLE IF NOT EXISTS network_evidence (
  network_name           TEXT PRIMARY KEY,

  -- Evidence counts
  total_edges            INTEGER DEFAULT 0,        -- Total coordinated creator edges
  total_evidence_txs     INTEGER DEFAULT 0,        -- Distinct evidence transactions
  average_confidence     REAL DEFAULT 0.0,         -- Mean confidence score (0-100)

  -- Evidence types/categories
  high_confidence_edges  INTEGER DEFAULT 0,        -- Edges with confidence >= 75
  medium_confidence_edges INTEGER DEFAULT 0,       -- Edges with 50-74 confidence
  low_confidence_edges   INTEGER DEFAULT 0,        -- Edges with < 50 confidence

  -- Time-based evidence
  earliest_evidence_time INTEGER,                   -- Unix timestamp of first evidence
  latest_evidence_time   INTEGER,                   -- Unix timestamp of most recent
  evidence_span_days     INTEGER,                   -- Duration from earliest to latest

  -- Bridge funders
  unique_bridge_funders  INTEGER DEFAULT 0,        -- Count of distinct bridge funders
  bridge_funder_list     TEXT,                      -- JSON array of bridge funders

  -- Network risk scoring
  evidence_risk_score    REAL DEFAULT 0.0,         -- 0-100, higher = more suspicious

  -- Metadata
  evidence_version       INTEGER DEFAULT 1,        -- Incremented on changes
  last_updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_changed_at        TIMESTAMP,                 -- Only set when evidence actually changed

  FOREIGN KEY(network_name) REFERENCES networks_release(network_name)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_network_evidence_risk
  ON network_evidence(evidence_risk_score DESC);

CREATE INDEX IF NOT EXISTS idx_network_evidence_updated
  ON network_evidence(last_updated_at DESC);
```

---

## Evidence Aggregation Logic

### Phase: Aggregate Evidence (Before or After networks_release)

The rollup computes:

1. **Total Edges**: COUNT of all coordinated_creator_edges within network
2. **Evidence Transactions**: COUNT(DISTINCT evidence_tx)
3. **Average Confidence**: AVG(confidence) across all edges
4. **Confidence Buckets**: Count edges by confidence ranges
5. **Time Range**: MIN(first_seen_block_time) → MAX(first_seen_block_time)
6. **Bridge Funders**: DISTINCT bridge_funders within network
7. **Risk Score**: Composite of frequency + confidence + span

### Risk Score Formula

```
evidence_risk_score = (
  (total_edges / max_possible_edges) * 40 +        -- 40% from frequency
  (average_confidence / 100) * 40 +                 -- 40% from confidence
  CASE
    WHEN evidence_span_days <= 1 THEN 20            -- 20% spike bonus (concentrated)
    WHEN evidence_span_days <= 7 THEN 15
    WHEN evidence_span_days <= 30 THEN 10
    ELSE 5
  END
) CAPPED AT 100
```

---

## Integration with build_networks_release()

### New Phase: Phase F — Evidence Rollup

**Placement**: After Phase E (Finalize networks_release)

**Pseudo-code:**
```python
# Phase F: Aggregate evidence
print("🔄 Phase F: Aggregate network evidence...")

# 1. Snapshot previous evidence state
db.execute('DROP TABLE IF EXISTS network_evidence_prev')
db.execute('CREATE TABLE network_evidence_prev AS SELECT * FROM network_evidence')

# 2. Compute new evidence state
db.execute('''
  WITH network_edges AS (
    SELECT
      nm.network_name,
      COUNT(*) as total_edges,
      COUNT(DISTINCT cce.evidence_tx) as total_evidence_txs,
      AVG(cce.confidence) as avg_confidence,
      ...
    FROM network_membership nm
    JOIN coordinated_creator_edges cce
      ON (nm.creator_address = cce.creator_a OR nm.creator_address = cce.creator_b)
    GROUP BY nm.network_name
  )
  INSERT OR REPLACE INTO network_evidence
  SELECT ... FROM network_edges
''')

# 3. Update evidence_version (like build_version)
# Only increment if actual values changed (idempotent)

# 4. Set last_changed_at based on delta comparison
```

---

## Idempotency Guarantee

The rollup is idempotent because:

1. **Snapshot-Compare Pattern**: Previous state compared to new state
2. **Version Only Increments on Change**: If `total_edges`, `average_confidence`, etc. are identical, version stays same
3. **last_changed_at Only Set on Change**: Only updated when actual evidence data differs
4. **No Side Effects**: Each run computes same result from same inputs

### Idempotent SQL Template

```sql
-- Create temp delta table (like build_networks_release does)
CREATE TEMP TABLE evidence_deltas AS
SELECT
  ne.network_name,
  ne.total_edges,
  old.total_edges as old_total_edges,
  CASE
    WHEN old.network_name IS NULL THEN 1
    WHEN ne.total_edges != old.total_edges THEN 1
    WHEN ne.average_confidence != old.average_confidence THEN 1
    ELSE 0
  END as changed_flag
FROM network_evidence ne
LEFT JOIN network_evidence_prev old ON ne.network_name = old.network_name;

-- Update version only if changed
UPDATE network_evidence
SET
  evidence_version = CASE
    WHEN (SELECT changed_flag FROM evidence_deltas
          WHERE evidence_deltas.network_name = network_evidence.network_name) = 1
    THEN evidence_version + 1
    ELSE evidence_version
  END,
  last_changed_at = CASE
    WHEN (SELECT changed_flag FROM evidence_deltas
          WHERE evidence_deltas.network_name = network_evidence.network_name) = 1
    THEN CURRENT_TIMESTAMP
    ELSE last_changed_at
  END
WHERE network_name IN (SELECT network_name FROM evidence_deltas);
```

---

## Transaction Safety

### Atomic Build Pattern

```python
@contextmanager
def db_transaction(db_path):
    db = sqlite3.connect(db_path)
    try:
        yield db
        db.commit()  # ✅ All phases commit atomically
    except Exception as e:
        db.rollback()  # ✅ Any error rolls back entire build
        raise e
    finally:
        db.close()

# Usage:
with db_transaction(db_path) as db:
    # Phase A: networks_release...
    # Phase F: network_evidence...
    # Entire build succeeds or fails together
```

### Failure Scenarios

| Scenario | Outcome |
|----------|---------|
| Phase F fails mid-rollup | Entire transaction rolls back; networks_release unchanged |
| networks_release OK but evidence fails | Both rollback; DB returns to pre-build state |
| Partial evidence written | SQLite ROLLBACK undoes everything |

---

## Schema Integration

### networks_release → network_evidence Link

The foreign key ensures:
- Every network in `network_evidence` exists in `networks_release`
- Deleting a network cascades (rare, but safe)
- No orphaned evidence records

### UI Read Path

```python
# Capability check controls routing
if app.has_networks_release:
    # UI uses optimized reads from both tables
    SELECT
        nr.*,  # networks_release fields
        ne.*   # network_evidence fields
    FROM networks_release nr
    LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
    WHERE nr.network_name = ?
```

---

## Constraints Satisfied

✅ **Does not break networks_release**
- Separate table
- Foreign key only (no circular deps)
- Can be dropped/rebuilt independently

✅ **Remains idempotent**
- Snapshot-compare pattern
- Version only increments on real changes
- Multiple runs = same result

✅ **Transaction-safe**
- Single db.commit() for entire build
- Rollback on any error
- Atomic all-or-nothing semantics

---

## Implementation Phases

### Phase 1: Table Creation (This PR)
- [ ] Add `network_evidence` table definition
- [ ] Add foreign key to `networks_release`
- [ ] Create indexes for common queries

### Phase 2: Rollup Logic
- [ ] Implement evidence aggregation SQL
- [ ] Add Phase F to `build_networks_release()`
- [ ] Implement idempotent versioning

### Phase 3: Risk Scoring
- [ ] Implement evidence_risk_score formula
- [ ] Calibrate thresholds based on data
- [ ] Add monitoring/alerting

### Phase 4: UI Integration
- [ ] Update endpoints to read network_evidence
- [ ] Display evidence counts in UI
- [ ] Show risk scores alongside networks_release

---

## Monitoring & Verification

After build, verify:

```python
# Check evidence was aggregated
SELECT COUNT(*) FROM network_evidence WHERE total_edges > 0;

# Check risk scores are reasonable (0-100)
SELECT MIN(evidence_risk_score), AVG(evidence_risk_score), MAX(evidence_risk_score)
FROM network_evidence;

# Check version increments only on change
SELECT evidence_version, COUNT(*) FROM network_evidence
GROUP BY evidence_version ORDER BY evidence_version DESC;
```

---

## Next Steps

1. Add table creation to `build_networks_release.py`
2. Implement evidence aggregation phase
3. Test with existing data
4. Validate idempotency (run build twice, no changes)
5. Validate transaction safety (kill build mid-run, verify rollback)
