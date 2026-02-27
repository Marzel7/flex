# Database Migrations (Phase 10+)

This directory contains all database schema migrations for the Flex system.

---

## Overview

Starting with **Version 1.0** (Phase 10), the database schema is versioned and managed through idempotent migrations.

### Key Principles

1. **Idempotency** - All migrations use `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`
   - Safe to run multiple times
   - Safe to re-run after partial failures
   - Can be applied in any order without side effects

2. **Schema Versioning** - `SCHEMA_VERSION` in `system_metadata` table tracks the latest applied schema
   - Incremented only when structural changes occur
   - Stored as TEXT in `system_metadata` table
   - Read by build pipeline for validation

3. **Transactional Safety** - All migrations applied within single transaction
   - All-or-nothing semantics
   - Consistent state preserved across crashes

4. **No Destructive Changes** - Only additive schema changes in Phase 10+
   - Never drop columns
   - Never drop tables
   - Never modify existing column types

---

## Migration Files

### Version 10 (Current)

**File**: `system_metadata_v10.sql`

**Changes**:
- Add `system_metadata` table (version tracking and build metadata)
- Stores: SYSTEM_VERSION, SCHEMA_VERSION, BUILD_PIPELINE_VERSION, LAST_BUILD_*

**Status**: Applied
**Date**: February 27, 2026

**Schema Version After**: 10

---

## How to Apply Migrations

### Automatic (Recommended)

Migrations are applied automatically at the **start** of every build via `build_networks_release.py`:

```bash
python3 build_networks_release.py
# Automatically applies all migrations in migrations/ directory
```

### Manual (Operational Use)

Apply a specific migration to your database:

```bash
# Apply single migration
sqlite3 your_database.db < migrations/system_metadata_v10.sql

# Apply all migrations
for migration in migrations/*.sql; do
    sqlite3 your_database.db < "$migration"
done
```

### Verify Schema Version

Check current schema version in any SQLite database:

```bash
sqlite3 your_database.db "SELECT value FROM system_metadata WHERE key = 'SCHEMA_VERSION';"
# Output: 10
```

---

## Schema Evolution Policy

### When Schema Changes Required

1. **Identify Need** - Determine what structural change is necessary
2. **Create Migration File** - `migrations/<feature>_v<N>.sql`
3. **Increment SCHEMA_VERSION** - Next version number
4. **Update Build Pipeline** - Code reads metadata for validation
5. **Add Release Notes** - Document schema changes
6. **Test Migration** - Verify idempotency on test databases
7. **Apply in Next Build** - Integrated into `build_networks_release.py`

### Version Numbering

- `SCHEMA_VERSION` matches Phase/Release version
- Phase 10 → SCHEMA_VERSION = "10"
- Release 1.0.1 → SCHEMA_VERSION = "10" (no schema change)
- Release 1.1.0 (with schema) → SCHEMA_VERSION = "11"

### Example: Adding a New Table

**File**: `migrations/new_feature_v11.sql`

```sql
-- Version 11: New Feature
CREATE TABLE IF NOT EXISTS new_feature_table (
    id TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Note: Only set SCHEMA_VERSION in build_networks_release.py
-- Never hardcode it in migration files
```

**Code Change** (in `build_networks_release.py`):

```python
# After applying all migrations:
db.execute("""
    INSERT OR REPLACE INTO system_metadata (key, value, updated_at)
    VALUES ('SCHEMA_VERSION', '11', CURRENT_TIMESTAMP)
""")
```

---

## Backward Compatibility

All migrations are **backward compatible**:

- Phase 10+ migrations work with Phase 9A databases
- Phase 9A databases can be upgraded to Phase 10+ schema
- No data loss or incompatibility

### Example: Upgrading from Phase 9A to Phase 10

```bash
# 1. Backup old database
cp your_database.db your_database.db.backup

# 2. Run current build (applies Phase 10 migrations automatically)
python3 build_networks_release.py

# 3. Verify schema
sqlite3 your_database.db "SELECT value FROM system_metadata WHERE key = 'SCHEMA_VERSION';"
# Output: 10

# 4. Verify no data loss
sqlite3 your_database.db "SELECT COUNT(*) FROM network_alerts;"
```

---

## Migration Safety Checklist

Before applying any migration:

- ✅ Tested on copy of production database
- ✅ Idempotent (can run multiple times safely)
- ✅ Uses `IF NOT EXISTS` for new tables/indexes
- ✅ Uses `IF NOT EXISTS` for new columns (no direct ALTER)
- ✅ Preserves all existing data
- ✅ No performance regression
- ✅ Transaction scope correct

---

## Troubleshooting

### Migration Failed Midway

**Issue**: Database locked or migration crashed mid-run

**Solution**:
```bash
# 1. Close all connections to database
# 2. Verify database integrity
sqlite3 your_database.db "PRAGMA integrity_check;"

# 3. Re-run migration (safe due to IF NOT EXISTS)
sqlite3 your_database.db < migrations/system_metadata_v10.sql

# 4. Verify success
sqlite3 your_database.db ".schema system_metadata"
```

### Schema Mismatch

**Issue**: Build fails with "no such column" error

**Solution**:
```bash
# 1. Check current schema version
sqlite3 your_database.db "SELECT value FROM system_metadata WHERE key = 'SCHEMA_VERSION';"

# 2. Apply missing migrations
for migration in migrations/*.sql; do
    sqlite3 your_database.db < "$migration"
done

# 3. Update SCHEMA_VERSION (done by build pipeline)
# 4. Re-run build
python3 build_networks_release.py
```

---

## Roadmap

### Version 11 (Planned)

Expected additions:
- Evidence aggregation enhancements
- Performance metric tables

### Version 12+ (Future)

To be determined based on operational needs

---

## See Also

- [PHASE10_FINAL_STATUS.md](../PHASE10_FINAL_STATUS.md) - Phase 10 implementation details
- [RUNBOOK.md](../RUNBOOK.md) - Operational procedures
- [RELEASE_NOTES_1.0.0.md](../RELEASE_NOTES_1.0.0.md) - Features and known limitations
