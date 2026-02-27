# Step 3 Phase 1: Build Version & Stability State Implementation

## Overview

This document specifies the implementation of build versioning and stability tracking for the networks_release table. These were defined in Step 3 but require logic implementation before UI migration.

**Status**: Design specification (ready for implementation)
**Timeline**: Phase 1 (before UI migration to networks_release)
**Complexity**: Medium (20-30 lines SQL, straightforward logic)

---

## Problem Statement

### Current State
- `build_version` exists but defaults to 1 and never increments
- `stability_state` exists but is never populated
- No tracking of network size changes or evolution
- No historical comparison capability

### Why It Matters
1. **Release Maturity**: Professional systems track versioning
2. **Network Evolution**: Know which networks are growing vs shrinking
3. **Data Quality**: Stability signals reliability of network membership
4. **Debugging**: Version diffs enable root cause analysis of changes
5. **UI Signals**: Communicate to users that networks change over time

---

## Implementation Strategy

### Architecture Pattern

The build process requires a **snapshot-and-compare** pattern:

```
Phase A: Snapshot previous state
  ↓
Phase B: Compute new state (existing Phases 1-4)
  ↓
Phase C: Compare and assign versions
  ↓
Phase D: Compute stability based on deltas
  ↓
Phase E: Atomically commit all changes
```

### Phase A: Snapshot Previous State

**Goal**: Preserve previous build for comparison

```sql
-- Before starting build process:
-- Create snapshot table (drop if exists to start fresh)
DROP TABLE IF EXISTS networks_release_prev;

CREATE TABLE networks_release_prev AS
SELECT network_name, network_size, network_type, build_version
FROM networks_release;

-- Or use INSERT if you want to preserve history:
DELETE FROM networks_release_prev WHERE snapshot_date < date('now', '-30 days');
INSERT INTO networks_release_prev (network_name, network_size, network_type, old_build_version, snapshot_date)
SELECT network_name, network_size, network_type, build_version, CURRENT_TIMESTAMP
FROM networks_release;
```

**Why separate table**:
- Avoids modifying state before comparison
- Enables rollback if logic fails
- Preserves state for audit trail (optional)

### Phase B: Existing Build Logic (Unchanged)

Phases 1-4 from OPTIMIZATION_STEP3_NETWORKS_RELEASE.md:
1. Compute network sizes from network_membership
2. Tag with CEX funders
3. Tag with infra funders
4. Classify network types

These populate a **temporary working table** or INSERT INTO networks_release with version=1, type='organic' defaults.

### Phase C: Version Incrementing Logic

**Decision Tree**:
```
IF network doesn't exist in previous build:
    build_version = 1  (new network)
    reason = "new network detected"

ELSE IF network exists in previous build:
    IF network_size changed:
        build_version = old_version + 1
        reason = "size changed"
    ELSE IF network_type changed:
        build_version = old_version + 1
        reason = "type changed"
    ELSE:
        build_version = old_version
        reason = "no substantive change"
```

**SQL Implementation**:

```sql
UPDATE networks_release nr
SET build_version = CASE
  WHEN old.network_name IS NULL THEN 1  -- New network
  WHEN nr.network_size != old.network_size THEN old.build_version + 1  -- Size changed
  WHEN nr.network_type != old.network_type THEN old.build_version + 1  -- Type changed
  ELSE old.build_version  -- No change
END
FROM networks_release_prev old
WHERE nr.network_name = old.network_name;

-- Verify results:
SELECT
  network_name,
  network_size,
  network_type,
  build_version,
  stability_state
FROM networks_release
ORDER BY build_version DESC
LIMIT 10;
```

**Edge Cases Handled**:
- New networks: version = 1 (no previous state)
- Network rename: Treated as new (different name_key)
- Size 0 → N: Triggers increment (legitimate growth)
- Type change: Triggers increment (CEX/infra status changed)
- No-op builds: Version preserved (idempotent)

### Phase D: Stability State Logic

**Delta Calculation**:
```
delta_pct = (new_size - old_size) / old_size * 100
```

**State Assignment**:
```
IF old_size IS NULL OR old_size = 0:
    stability = 'new'
    reason = "first detection"

ELSE IF delta_pct > +10:
    stability = 'growing'
    reason = f"size increased {delta_pct:.1f}%"

ELSE IF delta_pct < -10:
    stability = 'shrinking'
    reason = f"size decreased {delta_pct:.1f}%"

ELSE IF delta_pct in [-10, +10]:
    stability = 'stable'
    reason = f"size stable ±{abs(delta_pct):.1f}%"
```

**Threshold Rationale**:
- ±10% = meaningful signal without noise
- Allows for 1-2 creator joins/leaves in small networks without classification change
- Larger networks need proportional growth to trigger "growing"
- Sensitive enough to catch coordinated expansion campaigns

**SQL Implementation**:

```sql
UPDATE networks_release nr
SET stability_state = CASE
  WHEN old.network_size IS NULL THEN 'new'
  WHEN old.network_size = 0 THEN 'new'
  WHEN (nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) > 0.1 THEN 'growing'
  WHEN (nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) < -0.1 THEN 'shrinking'
  ELSE 'stable'
END
FROM networks_release_prev old
WHERE nr.network_name = old.network_name;

-- Verify results:
SELECT
  stability_state,
  COUNT(*) as count,
  AVG(network_size) as avg_size,
  MIN(build_version) as min_version,
  MAX(build_version) as max_version
FROM networks_release
GROUP BY stability_state
ORDER BY count DESC;
```

**Edge Cases**:
- Small networks: ±10% still meaningful (1 creator change in 10-creator network = 10%)
- Growing networks: Could be legitimate user interest or coordinated expansion
- Shrinking networks: Could be cleanup or detection error
- New networks: First build always 'new' (no historical data)

### Phase E: Atomic Commit

**Transaction Pattern** (if using a build script):

```python
import sqlite3

db = sqlite3.connect('pumpswap_tokens.db')
try:
    # Phase A: Snapshot
    db.execute('DROP TABLE IF EXISTS networks_release_prev')
    db.execute('CREATE TABLE networks_release_prev AS SELECT ... FROM networks_release')

    # Phase B: Compute new state (existing logic)
    # [Phases 1-4 from OPTIMIZATION_STEP3_NETWORKS_RELEASE.md]

    # Phase C: Update versions
    db.execute('''UPDATE networks_release nr SET build_version = ... FROM networks_release_prev old''')

    # Phase D: Update stability
    db.execute('''UPDATE networks_release nr SET stability_state = ... FROM networks_release_prev old''')

    # Update timestamp
    db.execute('''UPDATE networks_release SET last_built_at = CURRENT_TIMESTAMP''')

    db.commit()
    print("Build successful")

except Exception as e:
    db.rollback()
    print(f"Build failed: {e}")
    raise
finally:
    db.close()
```

---

## Complete Build Process (All Phases)

**Full SQL script** (can be run in sequence):

```sql
-- ========== PHASE A: SNAPSHOT PREVIOUS STATE ==========
DROP TABLE IF EXISTS networks_release_prev;
CREATE TABLE networks_release_prev AS
SELECT network_name, network_size, network_type, build_version
FROM networks_release;

-- ========== PHASE B: COMPUTE NEW STATE (Existing) ==========
-- [Phases 1-4 from OPTIMIZATION_STEP3_NETWORKS_RELEASE.md]
-- Assumes: networks_release is populated with network_size, network_type

-- ========== PHASE C: UPDATE BUILD VERSIONS ==========
UPDATE networks_release nr
SET build_version = CASE
  WHEN old.network_name IS NULL THEN 1
  WHEN nr.network_size != old.network_size THEN old.build_version + 1
  WHEN nr.network_type != old.network_type THEN old.build_version + 1
  ELSE old.build_version
END
FROM networks_release_prev old
WHERE nr.network_name = old.network_name;

-- ========== PHASE D: UPDATE STABILITY STATES ==========
UPDATE networks_release nr
SET stability_state = CASE
  WHEN old.network_size IS NULL THEN 'new'
  WHEN old.network_size = 0 THEN 'new'
  WHEN (nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) > 0.1 THEN 'growing'
  WHEN (nr.network_size - old.network_size) / CAST(old.network_size AS FLOAT) < -0.1 THEN 'shrinking'
  ELSE 'stable'
END
FROM networks_release_prev old
WHERE nr.network_name = old.network_name;

-- ========== PHASE E: FINALIZE BUILD ==========
UPDATE networks_release SET last_built_at = CURRENT_TIMESTAMP;

-- ========== VERIFY BUILD ==========
SELECT
  network_type,
  stability_state,
  COUNT(*) as count,
  AVG(network_size) as avg_size,
  MIN(build_version) as min_version,
  MAX(build_version) as max_version
FROM networks_release
GROUP BY network_type, stability_state
ORDER BY network_type, stability_state;
```

---

## Testing & Validation

### Test Case 1: New Network Detection
```sql
-- Simulate: New network added since last build
INSERT INTO networks_release (network_name, network_size, network_type)
VALUES ('NewNetwork', 15, 'organic');

-- After build: Should have build_version=1, stability='new'
SELECT network_name, build_version, stability_state FROM networks_release WHERE network_name='NewNetwork';
-- Expected: ('NewNetwork', 1, 'new')
```

### Test Case 2: Growing Network
```sql
-- Before: ObsidianDark has 179 creators, version 1
-- After: ObsidianDark has 200 creators
-- Delta: (200-179)/179 = 11.7% → growing

-- Expected: build_version=2, stability='growing'
```

### Test Case 3: Type Change
```sql
-- Before: Network A is 'organic' (no CEX funders), version 1
-- After: Network A is 'cex_connected' (found CEX funder), version should increment
-- Build logic triggers: network_type changed

-- Expected: build_version=2 (incremented due to type change)
```

### Test Case 4: Stable Network
```sql
-- Before: Network B has 50 creators, version 5, type 'organic'
-- After: Network B has 51 creators (2% change), type 'organic'
-- Delta: (51-50)/50 = 2% → within ±10% stable range

-- Expected: build_version=5 (unchanged), stability='stable'
```

### Test Case 5: Shrinking Network
```sql
-- Before: Network C has 100 creators, version 3
-- After: Network C has 75 creators (membership cleanup)
-- Delta: (75-100)/100 = -25% → shrinking

-- Expected: build_version=4 (incremented), stability='shrinking'
```

---

## Integration Points

### Phase 6 Cluster Creation
When Phase 6 builds new networks:
```python
# Create network
new_network = build_network(creators)

# Insert into canonical truth
db.execute(
    'INSERT INTO network_membership VALUES (?, ?, CURRENT_TIMESTAMP)',
    (new_network.name, creator_address)
)

# Rebuild networks_release
trigger_networks_release_build()  # Runs all phases A-E
```

### UI Layer Integration
Once implemented, UI can use stability signals:
```python
# Show growth indicators
networks_growing = db.execute('''
  SELECT network_name, network_size, build_version
  FROM networks_release
  WHERE stability_state = 'growing'
  ORDER BY network_size DESC
''')

# Show new networks
networks_new = db.execute('''
  SELECT network_name, network_size, cex_funder_count
  FROM networks_release
  WHERE stability_state = 'new'
''')
```

---

## Performance Considerations

### Build Time Impact
- Phase A (snapshot): ~50ms
- Phase B (existing): ~410ms (from Step 3)
- Phase C (versioning): ~20ms (scan + update)
- Phase D (stability): ~20ms (scan + update)
- **Total**: ~500ms (acceptable)

### Query Impact
- No regression: New queries on stability_state use existing indexes
- Could add `idx_networks_release_stability` if needed (low priority)

### Storage Impact
- `networks_release_prev` temporary: ~50KB per build (dropped after)
- No permanent storage overhead

---

## Done Criteria ✅

- [ ] Phase A: Snapshot logic defined
- [ ] Phase C: Version incrementing logic tested
- [ ] Phase D: Stability state logic tested
- [ ] Phase E: Atomic build transaction implemented
- [ ] Integration with Phase 6 planned
- [ ] UI migration ready to consume stability_state

---

## Timeline

**Phase 1 (pre-UI migration)**:
1. Implement snapshot + versioning logic (~1 hour)
2. Test with existing networks_release data
3. Verify 3-5 test cases pass
4. Integrate into build process

**Phase 2 (post-UI integration)**:
5. Add UI indicators for stability_state (growing/shrinking badges)
6. Monitor stability changes in production

---

**Created**: February 27, 2026
**Status**: Design specification ready for implementation
**Estimated Implementation Time**: 2-3 hours (logic + testing + integration)
