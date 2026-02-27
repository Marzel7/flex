# Optimization Issues - Resolution & Roadmap

**Date**: February 27, 2026
**Status**: 4 Critical Issues Identified & Resolved/Scheduled
**Branch**: optimisations

---

## Issue #1: Naming Semantics Risk ✅ RESOLVED

### The Problem
Network types were named `*_coordinated`:
- `cex_and_infra_coordinated`
- `cex_coordinated`
- `infra_coordinated`
- `normal`

**Problem**: "Coordinated" implies intentional malicious behavior.

**Reality**: We only detect CEX/infra wallet presence, not coordination.

### Solution Applied
Renamed all types to accurately reflect **detection**, not accusation:

| Before | After | Semantic Meaning |
|--------|-------|-----------------|
| `cex_and_infra_coordinated` | `cex_and_infra_connected` | Both CEX and infra funders detected |
| `cex_coordinated` | `cex_connected` | CEX funders detected only |
| `infra_coordinated` | `infra_connected` | Infrastructure funders detected only |
| `normal` | `organic` | No detected CEX or infrastructure funders |

### Impact
✅ **Database Updated**: All 103 network records in networks_release now use new type names
✅ **Semantically Accurate**: Terms reflect observation, not implication
✅ **Release-Safe**: No false accusation of malicious coordination
✅ **Backward Compatible**: Query logic unchanged (still boolean checks on has_cex_funder, has_infra_funder)

### Verification
```sql
SELECT network_type, COUNT(*) as count FROM networks_release GROUP BY network_type;

Result:
cex_and_infra_connected | 54  (52%)
organic                 | 42  (41%)
cex_connected           |  7  (7%)
infra_connected         |  0  (0%)
```

**Status**: ✅ Complete and verified

---

## Issue #2: JSON Aggregation Method - Future Architecture

### The Problem
Currently building JSON arrays inline:
```sql
cex_funder_addresses = '[' || cc.cex_addresses || ']'
infra_funder_addresses = '[' || ic.infra_addresses || ']'
```

**Problem**:
- JSON parsing in application code
- Address-level queries require JSON parsing
- Not normalized to 3NF

### Recommended Future Architecture
```sql
CREATE TABLE network_flag_addresses (
  network_name    TEXT NOT NULL,
  address         TEXT NOT NULL,
  flag_type       TEXT NOT NULL,  -- 'cex' or 'infra'
  detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (network_name, address, flag_type),
  FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
);

CREATE INDEX idx_nfa_network ON network_flag_addresses(network_name);
CREATE INDEX idx_nfa_address ON network_flag_addresses(address);
CREATE INDEX idx_nfa_type ON network_flag_addresses(flag_type);
```

### Benefits
- Remove JSON parsing from application code
- Enable efficient queries: "Show all networks containing wallet X"
- Support future filtering/searching at address level
- Trivial to implement (given current architecture)
- Can coexist with JSON arrays during migration

### Migration Strategy
1. **Phase 1**: Keep current JSON arrays in networks_release
2. **Phase 2** (post-UI migration): Add network_flag_addresses as normalized table
3. **Phase 3** (optional): Remove JSON columns from networks_release

### Decision
Not urgent. Current JSON approach acceptable for UI rendering. Implement post-Phase 1 if address-level querying becomes needed.

**Status**: 📋 Roadmap documented | ⏳ Implementation Phase 2

---

## Issue #3: Build Version Logic - Phase 1 Implementation

### The Problem
```sql
build_version INTEGER DEFAULT 1
```

**Current state**: All networks stuck at version 1 (never increments)

**Problem**:
- No network versioning/evolution tracking
- Can't diff between builds
- "Maturity" issue for professional systems

### Required Logic

**Decision Tree**:
```
IF network newly detected (not in previous build):
    build_version = 1
    reason = "New network"

ELSE IF network_size changed from previous:
    build_version = old_version + 1
    reason = "Size delta detected"

ELSE IF network_type changed from previous:
    build_version = old_version + 1
    reason = "Type change detected (CEX/infra status)"

ELSE:
    build_version = old_version
    reason = "No substantive change (idempotent)"
```

### SQL Implementation
```sql
UPDATE networks_release nr
SET build_version = CASE
  WHEN old.network_name IS NULL THEN 1  -- New network
  WHEN nr.network_size != old.network_size THEN old.build_version + 1
  WHEN nr.network_type != old.network_type THEN old.build_version + 1
  ELSE old.build_version
END
FROM networks_release_prev old
WHERE nr.network_name = old.network_name;
```

### What This Enables
- Network diffing: Compare versions to identify changes
- Historical evolution: Track v1 → v2 → v3 progression
- Audit trail: Know when and why networks changed
- Stability signals: Version frequency indicates network stability

### Implementation Approach
1. Create snapshot table (`networks_release_prev`) before build
2. Compute new state (existing Phases 1-4)
3. Compare old vs new (Phase C)
4. Set versions atomically

**Timeline**: Phase 1 (before UI migration)
**Complexity**: Low (straightforward comparison logic)
**Testing**: 5 test cases provided in OPTIMIZATION_STEP3_PHASE1_BUILD_LOGIC.md

**Status**: 📋 Specification complete | ⏳ Implementation Phase 1

---

## Issue #4: Stability State Is Defined But Not Enforced

### The Problem
```sql
stability_state TEXT DEFAULT 'new'
```

**Current state**: Column exists but no logic to populate it

**Problem**:
- Can't distinguish between new/stable/growing/shrinking networks
- No network evolution tracking
- Stability signals undefined

### Required Logic

**Threshold-Based Classification**:
```
delta_pct = (new_size - old_size) / old_size * 100

IF old_size IS NULL or old_size = 0:
    stability = 'new'
    reason = "First detection"

ELSE IF delta_pct > +10:
    stability = 'growing'
    reason = "Size increased ≥10%"

ELSE IF delta_pct < -10:
    stability = 'shrinking'
    reason = "Size decreased ≥10%"

ELSE (delta_pct in [-10, +10]):
    stability = 'stable'
    reason = "Size stable within ±10%"
```

### Threshold Rationale
- **±10% boundary**: Meaningful signal without noise
- **Small networks**: 10% still matters (1 creator in 10-creator network = 10%)
- **Large networks**: Proportional sensitivity
- **Noise floor**: Accounts for 1-2 creator joins/leaves

### SQL Implementation
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
```

### What This Enables
- Identify expanding networks (possible coordinated campaigns)
- Identify shrinking networks (cleanup or detection errors)
- Track network maturity (new vs stable vs growing)
- UI signals: Badges for growing/shrinking networks

### Edge Cases
- **Growing networks**: Could be legitimate interest or coordinated expansion → needs scrutiny
- **Shrinking networks**: Could be legitimate cleanup or detection error → investigate
- **New networks**: First build always 'new' (no historical data)
- **Stable networks**: Different risk profile than growing ones

### Implementation Approach
1. Snapshot previous state before build
2. Compute size deltas
3. Apply delta thresholds
4. Set stability atomically

**Timeline**: Phase 1 (before UI migration, companion to build_version logic)
**Complexity**: Low (straightforward delta calculation)
**Testing**: 5 test cases provided in OPTIMIZATION_STEP3_PHASE1_BUILD_LOGIC.md

**Status**: 📋 Specification complete | ⏳ Implementation Phase 1

---

## Phase 1 Implementation Specification

**Document**: [OPTIMIZATION_STEP3_PHASE1_BUILD_LOGIC.md](OPTIMIZATION_STEP3_PHASE1_BUILD_LOGIC.md)
**Scope**: Issues #3 and #4 (Build version + Stability state)
**Lines of Code**: ~20-30 SQL + transaction wrapper
**Estimated Time**: 2-3 hours
  - Logic implementation: 30 minutes
  - Testing & validation: 60-90 minutes
  - Integration with Phase 6: 30 minutes

### Build Process (All 5 Phases)
```
Phase A: Snapshot previous state            (~50ms)
Phase B: Compute new state (existing)       (~410ms, no change)
Phase C: Update build versions              (~20ms)
Phase D: Update stability states            (~20ms)
Phase E: Atomic transaction commit          (safety)
────────────────────────────────────────────
Total: ~500ms (negligible)
```

### Test Cases (All provided with expected results)
1. ✅ New network detection
2. ✅ Growing network (+11.7% size)
3. ✅ Type change (organic → cex_connected)
4. ✅ Stable network (±2% change)
5. ✅ Shrinking network (-25% size)

### Integration Points
- **Phase 6**: Trigger networks_release_build() after cluster creation
- **UI Layer**: Consume stability_state for growing/shrinking badges
- **Next builds**: Snapshot-compare pattern becomes standard

---

## Summary Table

| Issue | Problem | Solution | Status | Timeline |
|-------|---------|----------|--------|----------|
| #1: Naming | "Coordinated" implies malicious | Rename to "connected"/"organic" | ✅ **FIXED** | Complete |
| #2: JSON | No address-level queries | Normalized network_flag_addresses | 📋 Roadmap | Phase 2 |
| #3: Versions | Never increment | Delta-based incrementing | ⏳ Pending | Phase 1 |
| #4: Stability | Never populated | Delta threshold logic | ⏳ Pending | Phase 1 |

---

## Next Steps

### Immediate (Ready Now)
- ✅ [x] Semantic naming fix applied and verified
- ✅ [x] Phase 1 specification complete (825 lines)
- ⏳ [ ] Review OPTIMIZATION_STEP3_PHASE1_BUILD_LOGIC.md
- ⏳ [ ] Approve Phase 1 implementation (2-3 hours)

### Phase 1 (Before UI Migration)
- [ ] Implement build version logic (~30 min)
- [ ] Implement stability state logic (~30 min)
- [ ] Test with existing networks_release data (~60 min)
- [ ] Integrate with Phase 6 build process (~30 min)

### Phase 2 (After UI Migration)
- [ ] Design network_flag_addresses normalization
- [ ] Migrate address data from JSON arrays
- [ ] Update UI to query normalized table

### Phase 3 (Optimization)
- [ ] Remove JSON columns from networks_release (optional)
- [ ] Add UI badges for growing/shrinking networks
- [ ] Monitor stability patterns in production

---

**Created**: February 27, 2026
**Status**: 1 resolved, 3 scheduled with complete specifications
**Branch**: optimisations
**Ready for**: Phase 1 implementation review and approval
