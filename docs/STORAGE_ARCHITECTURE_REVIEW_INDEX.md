# FLEX Storage Architecture Review — Document Index

**Date**: March 10, 2026  
**Status**: Complete  
**Total Pages**: 1,943 lines across 2 documents (56 KB)

---

## Document Overview

### 1. STORAGE_ARCHITECTURE_REVIEW.md (797 lines, 23 KB)

**Purpose**: Initial critical analysis rejecting the proposed manual partitioning strategy

**Audience**: Architecture decision-makers, senior engineers

**Key Content**:
- Executive summary with recommendation: ❌ REJECT partitioning
- Section 1: Evaluation of proposed partitioning architecture
  - SQLite has no native partitioning support
  - UNION ALL queries don't benefit from partition pruning
  - Correctness risks at month boundaries
  - Operational complexity scorecard
- Section 2: Simpler alternatives for SQLite
  - ✅ DELETE + VACUUM recommended approach
  - Time-based retention strategy
  - Single table comparison vs partitioned
  - Pragmatic 2-tier retention (hot + Parquet warm)
- Section 3: Long-term scaling strategy
  - SQLite viability timeline (2-3 years)
  - Performance degradation timeline
  - Migration triggers to PostgreSQL
  - Recommended scaling sequence (2026-2028+)
- Section 4: Migration strategy
  - Dual-write pattern for zero-downtime migration
  - PostgreSQL partitioning (when needed)
- Section 5: Alternative architectures
  - Columnar storage (Parquet)
  - Hybrid architecture (SQLite + Parquet + Glacier)
  - TimescaleDB alternative
- Section 6: Final recommendations
  - DO NOT implement manual SQLite partitioning
  - DO implement time-based retention
  - DO implement Parquet export for warm storage
  - Migration path to PostgreSQL (future)

**Use this document for**:
- Understanding why partitioning was rejected
- High-level architecture decisions
- Long-term scaling strategy

---

### 2. STORAGE_ARCHITECTURE_DETAILED_REVIEW.md (1,146 lines, 33 KB)

**Purpose**: Comprehensive technical review of the simplified DELETE + VACUUM architecture

**Audience**: Implementation engineers, DevOps, database architects

**Key Content**:
- Executive summary with recommendation: ✅ APPROVE with optimizations
- Section 1: Architecture evaluation
  - DELETE + VACUUM is correct SQLite retention strategy
  - How VACUUM works (30-300ms for 5 GB)
  - Comparison table: DELETE+VACUUM vs partitioning
  - 90-day retention window steady-state analysis
  - **CRITICAL FINDING**: "5 GB" assumption likely incorrect; true steady-state ~170 GB
  - Row deletion atomicity (ACID-compliant)
  - Index safety during VACUUM
  - Transaction log reclamation

- Section 2: SQLite performance considerations
  - **Impact on write throughput**: <0.001% if scheduled correctly
  - Index optimization for block_time queries
  - Recommended index additions (covering index)
  - WAL mode and PRAGMA tuning recommendations
  - Concurrent access patterns during VACUUM
  - Safe read/write behavior during cleanup

- Section 3: Storage lifecycle strategy
  - **Cleanup schedule options**:
    - Option 1: Daily cleanup (RECOMMENDED)
    - Option 2: Weekly cleanup (acceptable)
    - Option 3: Monthly cleanup (not recommended)
  - Parquet export strategy for warm storage
  - Why Parquet > second SQLite DB
  - Storage projection with Parquet archival
  - Complete cleanup job implementation code

- Section 4: Monitoring and operational safeguards
  - **Required metrics** (6 different tracking categories):
    - Database size tracking (5 metrics)
    - Cleanup performance tracking (3 metrics)
    - Query latency monitoring (2 metrics)
  - Operational safeguards (3 safety mechanisms):
    - Pre-cleanup verification with checksums
    - Cleanup frequency limiter (prevent duplicates)
    - Database corruption detection (PRAGMA integrity_check)
  - Alerting thresholds (6 conditions)
  - Implementation code for all monitoring

- Section 5: Long-term migration path
  - Growth timeline (12-36+ months)
  - **Year 1 (2026)**: SQLite single table
  - **Year 2 (2027)**: SQLite steady-state
  - **Year 3+ (2028)**: Consider PostgreSQL
  - Migration strategy (4 steps):
    - Step 1: Prepare PostgreSQL instance
    - Step 2: Dual-write setup
    - Step 3: Read switch
    - Step 4: Decommission SQLite
  - Estimated timeline (6 weeks, 80 hours, zero downtime)

- Section 6: Final recommendations
  - ✅ APPROVAL with required optimizations
  - Pre-deployment checklist (15 items)
  - Recommended implementation code
  - Optimization order (3 phases)
  - Final comparison scorecard

**Use this document for**:
- Implementation planning and coding
- Performance tuning decisions
- Monitoring and alerting setup
- Operational procedures
- Cost estimation

---

## Quick Reference: Key Findings

### Critical Issues Addressed

1. **Database Size Math**
   - Original plan: 5-6 GB steady-state
   - Actual calculation: 57 GB/month × 3 months = ~171 GB
   - **ACTION**: Verify actual ingestion rate with production data

2. **Index Strategy**
   - ✓ Existing indexes already optimal
   - Recommendation: Add covering index for monitoring (optional)

3. **Cleanup Impact**
   - Daily cleanup: 100-500ms blocking
   - Throughput impact: <0.001% if scheduled at 2 AM UTC
   - ✓ Negligible for production

4. **Warm Storage**
   - ❌ REJECT: Second SQLite database (duplicate VACUUM overhead)
   - ✅ APPROVE: Parquet export to S3 Glacier (50x compression, $4/TB/month)

### Performance Summary

| Metric | Value |
|--------|-------|
| Query latency | 2-5 ms |
| Cleanup duration | 100-500 ms (daily) |
| Throughput impact | <0.001% |
| Operational overhead | 15 min/week |
| Storage (hot, 90-day) | ~170 GB (verify) |
| Storage (warm, Parquet) | ~5-10 GB/month |
| Partition overhead | 0% (single table) |

### Timeline to PostgreSQL

- **2026**: SQLite excellent (no changes needed)
- **2027**: SQLite good (monitor, no changes)
- **2028**: Evaluate PostgreSQL (if latency >100ms)
- **2029+**: Migrate only if necessary (dual-write, zero downtime)

---

## Implementation Roadmap

### Phase 1: Immediate (This Week)
- [ ] Review both documents (priority: Detailed Review, Section 2-4)
- [ ] Clarify database size with production data
- [ ] Review pre-deployment checklist
- **Effort**: 4 hours (reading + discussion)

### Phase 2: Implementation (Week 1-2)
- [ ] Create index on block_time (1h)
- [ ] Implement cleanup_with_verification() (3h)
- [ ] Add StorageAlerts class (4h)
- [ ] Implement monitoring metrics (4h)
- **Effort**: 16 hours

### Phase 3: Operations Setup (Week 2-3)
- [ ] Set up cron job (1h)
- [ ] Configure alerting (2h)
- [ ] Document runbook (3h)
- [ ] Team training (2h)
- **Effort**: 8 hours

### Phase 4: Testing & Deployment (Week 3-4)
- [ ] Staging validation (4h)
- [ ] Production rollout (2h)
- [ ] Monitoring verification (ongoing)
- **Effort**: 6+ hours

**Total Effort**: ~30 hours (4 engineer-days)

---

## Document Cross-References

### When to Read Each Document

**Start with STORAGE_ARCHITECTURE_REVIEW.md if**:
- You want to understand the architecture decision
- You're making a go/no-go decision
- You need to present to stakeholders
- You want context on alternatives

**Then read STORAGE_ARCHITECTURE_DETAILED_REVIEW.md if**:
- You're implementing the solution
- You need performance details
- You're setting up monitoring
- You need to understand tuning options

**Reference sections**:
- Architecture decisions → Review.md Section 1
- Performance tuning → Detailed Review Section 2
- Monitoring setup → Detailed Review Section 4
- Long-term planning → Both documents Section 5

---

## Key Recommendations Summary

### ✅ APPROVED
- Single SQLite table with 90-day retention
- DELETE + VACUUM for space reclamation
- Daily cleanup at 2 AM UTC
- Parquet export for warm storage (180-270 day range)
- Standard monitoring with alerts
- Clear path to PostgreSQL (2028+)

### ❌ REJECTED
- Manual monthly partitioning (too complex)
- Second SQLite database for warm storage (unnecessary overhead)
- Weekly cleanup (too infrequent, wastes space)
- No monitoring (risky)

### ⏳ OPTIONAL (Phase 2)
- Covering index for monitoring queries
- mmap_size PRAGMA tuning for cleanup
- Parquet export to S3 Glacier (nice-to-have)

---

## Questions Answered

### From Initial Review Request

1. **Whether DELETE + VACUUM is the correct retention strategy for SQLite**
   - ✅ YES, it's the standard, recommended approach
   - See: Detailed Review, Section 1.1-1.4

2. **Whether daily cleanup could impact indexing throughput**
   - ✅ MINIMAL impact (<0.001% if scheduled correctly)
   - See: Detailed Review, Section 2.1

3. **Whether additional indexes should be added for block_time retention queries**
   - ✅ OPTIONAL covering index for monitoring (not required)
   - See: Detailed Review, Section 2.2

4. **Whether WAL or PRAGMA tuning could improve performance**
   - ✅ YES, recommend adding mmap_size=30MB for cleanup
   - See: Detailed Review, Section 2.3

5. **Whether exporting old data to Parquet is a good archival strategy**
   - ✅ YES, superior to second SQLite DB (50x compression)
   - See: Detailed Review, Section 3.2

6. **What monitoring metrics should be added for storage growth**
   - ✅ 5 metric categories with 11 specific metrics
   - See: Detailed Review, Section 4.1

7. **At what scale migration to PostgreSQL becomes necessary**
   - ✅ When query latency >100ms OR VACUUM >5min OR table >50GB
   - Unlikely before 2028 (2 years)
   - See: Detailed Review, Section 5.1

---

## File Locations

Both documents are located in the FLEX project root:

```
/Users/kevinkeaveney/Dev/claude/flex/
├── STORAGE_ARCHITECTURE_REVIEW.md (23 KB)
├── STORAGE_ARCHITECTURE_DETAILED_REVIEW.md (33 KB)
└── STORAGE_ARCHITECTURE_REVIEW_INDEX.md (this file)
```

---

## Additional Resources in Repository

Related documents that may be useful:

- `PHASE3.2_STORAGE_MANAGEMENT_PLAN.md` — Original partitioning plan (rejected)
- `PHASE3_OPTIMIZATION_IMPLEMENTATION_STATUS.md` — Phase 3.1 results
- `PHASE3_TECHNICAL_ARCHITECTURE_REVIEW.md` — Overall Phase 3 architecture
- `PHASE3_OPTIMIZATION_ROADMAP.md` — Implementation roadmap

---

## Approval Status

| Document | Status | Approver | Date |
|----------|--------|----------|------|
| STORAGE_ARCHITECTURE_REVIEW.md | Draft | Pending | 2026-03-10 |
| STORAGE_ARCHITECTURE_DETAILED_REVIEW.md | Draft | Pending | 2026-03-10 |
| Recommendation | ✅ Ready | Pending | 2026-03-10 |

---

**Report prepared by**: Senior Database Systems Engineer  
**Date**: March 10, 2026  
**Next review**: After 30 days of production operation

