# Flex Operations Runbook (Version 1.0)

**Version**: 1.0.0
**Date**: February 27, 2026
**System**: Flex - Token Funding Network Analyzer

This runbook covers daily operations, backup/restore, failure handling, and performance monitoring for the Flex system.

---

## Table of Contents

1. [Daily Operations](#daily-operations)
2. [Backup & Restore](#backup--restore)
3. [Failure Handling](#failure-handling)
4. [Performance Monitoring](#performance-monitoring)
5. [Troubleshooting](#troubleshooting)
6. [Emergency Procedures](#emergency-procedures)

---

## Daily Operations

### Running the Build

The Flex build pipeline processes all networks and generates scoring, alerts, and escalations.

#### Standard Build

```bash
# Navigate to project directory
cd /path/to/flex

# Run full build
python3 build_networks_release.py

# Expected output:
# 🔄 Phase A: Snapshot previous state...
# ✅ Snapshot: N previous networks saved
# 🔄 Phase B: Compute new network state...
# ... (more phases) ...
# 📊 Build Metadata Recorded:
#    Version: 1.0.0 (Phase 10)
#    Build: version N
#    Networks: X
#    Alerts: Y
#    Escalations: Z
# ✅ Build complete!
```

#### Build with Performance Profiling

```bash
# Enable detailed phase timing
BUILD_PROFILE=1 python3 build_networks_release.py

# Output includes:
# ⏱️  Build Profile Report (BUILD_PROFILE=1)
# ============================================================
#   Phase A: Snapshot          4.70ms  (1.7%)
#   Phase B: Compute state   239.88ms  (84.8%)
#   ... (all phases) ...
#   TOTAL                    283.13ms
# ============================================================
```

### Verifying Build Success

After build completes, verify all systems:

```bash
# 1. Check build metadata
sqlite3 /path/to/database.db "SELECT key, value FROM system_metadata ORDER BY key;"

# Expected output:
# BUILD_PIPELINE_VERSION|10
# LAST_BUILD_AT|2026-02-27T15:30:45.123456
# LAST_BUILD_ALERTS_INSERTED|1250
# LAST_BUILD_ESCALATIONS_SET|45
# LAST_BUILD_NETWORKS_PROCESSED|10000
# LAST_BUILD_VERSION|25
# SCHEMA_VERSION|10
# SYSTEM_VERSION|1.0.0

# 2. Check network count
sqlite3 /path/to/database.db "SELECT COUNT(*) as networks FROM networks_release;"

# 3. Check latest alerts
sqlite3 /path/to/database.db "SELECT COUNT(*) as alerts FROM network_alerts WHERE created_at >= datetime('now', '-1 day');"

# 4. Check escalations
sqlite3 /path/to/database.db "SELECT COUNT(*) as escalated FROM network_alerts WHERE is_escalated = 1 AND created_at >= datetime('now', '-1 day');"
```

### Checking System Status

View the current version and build state:

```bash
# Version check
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'SYSTEM_VERSION';"
# Output: 1.0.0

# Last build timestamp
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_AT';"
# Output: 2026-02-27T15:30:45.123456

# Schema version
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'SCHEMA_VERSION';"
# Output: 10

# Last build duration
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_DURATION_MS';"
# Output: 283
```

---

## Backup & Restore

### Creating a Backup

#### Full Database Backup

```bash
# Backup SQLite database file (safest method)
cp /path/to/database.db /backup/database.db.backup.$(date +%Y%m%d_%H%M%S)

# Verify backup integrity
sqlite3 /backup/database.db.backup.* "PRAGMA integrity_check;"
# Expected output: ok
```

#### Backup with Metadata

```bash
# Create backup with metadata dump
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp /path/to/database.db /backup/database.db.backup.$TIMESTAMP
sqlite3 /path/to/database.db ".dump system_metadata" > /backup/metadata_$TIMESTAMP.sql

# Store backup info
cat > /backup/BACKUP_INFO_$TIMESTAMP.txt << EOF
Backup Date: $(date)
Database File: database.db.backup.$TIMESTAMP
Metadata Dump: metadata_$TIMESTAMP.sql
System Version: $(sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'SYSTEM_VERSION';")
Schema Version: $(sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'SCHEMA_VERSION';")
Last Build: $(sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_AT';")
EOF
```

#### Automated Daily Backups

```bash
# Add to crontab for daily backups at 2 AM
crontab -e

# Add line:
0 2 * * * /path/to/flex/scripts/backup.sh /path/to/database.db /backup/daily
```

### Restoring from Backup

#### Simple Restore

```bash
# Stop application (if running as service)
# systemctl stop flex  # If using systemd

# Restore from backup
cp /backup/database.db.backup.YYYYMMDD_HHMMSS /path/to/database.db

# Verify restore
sqlite3 /path/to/database.db "SELECT COUNT(*) FROM networks_release;"

# Restart application
# systemctl start flex
```

#### Restore with Verification

```bash
# 1. Create working copy
cp /path/to/database.db /tmp/database.db.working

# 2. Restore from backup
cp /backup/database.db.backup.YYYYMMDD_HHMMSS /path/to/database.db

# 3. Verify integrity
sqlite3 /path/to/database.db "PRAGMA integrity_check;"
# Expected: ok

# 4. Verify key tables exist
sqlite3 /path/to/database.db ".tables"
# Expected: network_alerts network_evidence network_membership
#           network_score_history network_scores networks_release
#           system_metadata listener_settings

# 5. Verify metadata
sqlite3 /path/to/database.db "SELECT COUNT(*) FROM system_metadata;"
# Expected: 8 (or more)

# 6. If all checks pass, remove working copy
rm /tmp/database.db.working
```

---

## Failure Handling

### Build Crashes or Hangs

#### Scenario: Build crashed mid-run

```bash
# 1. Check if database is locked
lsof | grep database.db
# If process exists, kill it (carefully)
kill -9 <PID>

# 2. Verify database integrity
sqlite3 /path/to/database.db "PRAGMA integrity_check;"
# Expected: ok

# 3. Check last metadata to see how far build got
sqlite3 /path/to/database.db "SELECT key, value FROM system_metadata WHERE key LIKE 'LAST_BUILD%';"

# 4. If build was partial, rollback is automatic (transactions)
# Safe to re-run build
python3 build_networks_release.py

# 5. Verify complete build
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_AT';"
```

#### Scenario: Network connectivity loss during funder extraction

```bash
# 1. Kill any running extraction processes
pkill -f "funder.*extractor"

# 2. Check database state
sqlite3 /path/to/database.db "SELECT COUNT(*) FROM creator_funders;"

# 3. Re-run extraction (safe - deduplicates)
python3 realtime_creator_funding_extractor.py <creator_address>

# 4. After extraction completes, re-run build
python3 build_networks_release.py
```

### Build Takes Too Long

#### Diagnostic Check

```bash
# Check build duration
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_DURATION_MS';"

# Acceptable ranges:
# - Small dataset (1k networks): 150-300ms
# - Medium dataset (10k networks): 250-600ms
# - Large dataset (100k networks): 500-2000ms

# If duration exceeds these ranges, enable profiling
BUILD_PROFILE=1 python3 build_networks_release.py

# Review profile output to identify slow phase
# Typical bottleneck: Phase B (compute state), Phase I (smoothing)
```

#### Performance Optimization

```bash
# 1. Run query plan audit
python3 scripts/query_plan_audit.py /path/to/database.db

# 2. Check index usage
sqlite3 /path/to/database.db "SELECT * FROM sqlite_master WHERE type='index';"

# 3. If indexes missing, apply migration
sqlite3 /path/to/database.db < migrations/system_metadata_v10.sql

# 4. Verify indexes created
sqlite3 /path/to/database.db ".indexes"
```

---

## Performance Monitoring

### Query Latency Targets

```
Alert Queries (most user-facing):
  - ACTIVE view: <50ms (p95)
  - With filters: <100ms (p95)
  - CSV export: <500ms (p95)

Network Queries:
  - By score sort: <50ms (p95)
  - By risk_band: <100ms (p95)
  - With band filter: <150ms (p95)

Score History:
  - Biggest movers: <200ms (p95)
  - Build delta join: <300ms (p95)
```

### Build Timing Targets

```
Build Pipeline (end-to-end):
  - Typical: 250-350ms (all phases)
  - Acceptable range: 200-500ms
  - Threshold for investigation: >1000ms

Phase Breakdown (expected):
  - Phase A (Snapshot): 5-15ms
  - Phase B (Compute): 100-300ms (largest)
  - Phase C (Versions): 5-20ms
  - Phase D (Stability): 5-20ms
  - Phase F (Evidence): 10-50ms
  - Phase G (Scores): 10-100ms
  - Phase H (Alerts): 20-200ms
  - Phase I (Smoothing): 50-300ms
  - Phase J (Trends): 10-50ms
  - Phase K (Indexes): 10-100ms
  - Phase L (Metadata): 1-5ms
```

### Monitoring Commands

```bash
# Check last build duration
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_DURATION_MS';"

# Check alert query performance (with timing)
time sqlite3 /path/to/database.db "SELECT COUNT(*) FROM network_alerts WHERE acknowledged = 0 AND suppressed_until IS NULL;"

# Check network query performance
time sqlite3 /path/to/database.db "SELECT COUNT(*) FROM network_scores WHERE risk_band = 'CRITICAL';"

# Check history query performance
time sqlite3 /path/to/database.db "SELECT COUNT(*) FROM network_score_history WHERE build_version = (SELECT MAX(build_version) FROM network_score_history);"

# Enable BUILD_PROFILE for detailed analysis
BUILD_PROFILE=1 python3 build_networks_release.py 2>&1 | grep -A 20 "Profile Report"
```

---

## Troubleshooting

### Error: "no such table: system_metadata"

**Cause**: Database predates Phase 10 migration

**Solution**:
```bash
# Apply Phase 10 migration
sqlite3 /path/to/database.db < migrations/system_metadata_v10.sql

# Verify table created
sqlite3 /path/to/database.db ".schema system_metadata"

# Re-run build (will populate metadata)
python3 build_networks_release.py
```

### Error: "FOREIGN KEY constraint failed"

**Cause**: Orphaned record in dependent table

**Solution**:
```bash
# 1. Identify foreign key issue
sqlite3 /path/to/database.db "PRAGMA foreign_keys = ON;"
sqlite3 /path/to/database.db "SELECT * FROM network_evidence WHERE network_name NOT IN (SELECT network_name FROM networks_release);"

# 2. Delete orphaned records
sqlite3 /path/to/database.db "DELETE FROM network_evidence WHERE network_name NOT IN (SELECT network_name FROM networks_release);"

# 3. Re-run build
python3 build_networks_release.py
```

### Error: "database is locked"

**Cause**: Another process holding lock

**Solution**:
```bash
# 1. Find locking process
lsof | grep database.db

# 2. Identify if it's stale
# If process is stuck or disconnected:
kill -9 <PID>

# 3. Verify integrity after kill
sqlite3 /path/to/database.db "PRAGMA integrity_check;"

# 4. Reopen connections
python3 build_networks_release.py
```

### Query Returns Wrong Results

**Cause**: Index corruption or stale data

**Solution**:
```bash
# 1. Verify database integrity
sqlite3 /path/to/database.db "PRAGMA integrity_check;"

# 2. Rebuild indexes
sqlite3 /path/to/database.db "REINDEX;"

# 3. If still wrong, verify build is current
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_AT';"

# 4. If build is stale, re-run
python3 build_networks_release.py
```

---

## Emergency Procedures

### Complete Database Reset

**Use only as last resort**

```bash
# 1. Backup current database (required)
cp /path/to/database.db /backup/database.db.emergency.$(date +%s)

# 2. Remove database file
rm /path/to/database.db

# 3. Run build (creates fresh database)
python3 build_networks_release.py

# 4. Verify new database
sqlite3 /path/to/database.db "SELECT COUNT(*) FROM networks_release;"
```

### Rolling Back to Previous Build

```bash
# 1. List available backups
ls -lht /backup/database.db.backup.*

# 2. Restore specific backup
cp /backup/database.db.backup.YYYYMMDD_HHMMSS /path/to/database.db

# 3. Verify restored version
sqlite3 /path/to/database.db "SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_VERSION';"

# 4. Resume normal operations
python3 build_networks_release.py
```

---

## Contact & Escalation

### Support Contacts

- **Build Issues**: Check PHASE10_FINAL_STATUS.md
- **Schema Issues**: See migrations/README.md
- **Performance Issues**: Enable BUILD_PROFILE=1 and share output
- **Data Loss**: Use backup restore procedure above

### Logs & Diagnostics

```bash
# Capture full build log with diagnostics
BUILD_PROFILE=1 python3 build_networks_release.py > build_$(date +%Y%m%d_%H%M%S).log 2>&1

# View logs
tail -f build_*.log

# Diagnose slow queries
python3 scripts/query_plan_audit.py /path/to/database.db > query_plan_$(date +%Y%m%d_%H%M%S).md

# Benchmark performance
python3 scripts/benchmark_queries.py > benchmark_$(date +%Y%m%d_%H%M%S).md
```

---

**Last Updated**: February 27, 2026
**Version**: 1.0.0
**Status**: Production Ready ✅
