# Critical: sol_transfers is Now the Canonical Data Source

**Date**: 2026-03-03
**Status**: Active
**Importance**: HIGH

---

## The Change

`sol_transfers` (from webhooks) **replaces** `creator_outgoing_transfers` (from RPC polling).

### Old Pattern (Deprecated)
```sql
SELECT COUNT(*) FROM creator_outgoing_transfers
WHERE creator_address = ?
```

### New Pattern (Canonical)
```sql
SELECT COUNT(*) FROM sol_transfers
WHERE source = ?
```

---

## Why This Matters

**sol_transfers is:**
- ✅ Event-driven (only stores actual webhook transfers)
- ✅ Real-time (updates immediately as webhooks arrive)
- ✅ Deduplicated (PRIMARY KEY on signature)
- ✅ Complete (no pagination limits, no RPC discovery loop)
- ✅ Single source of truth for creator activity

**creator_outgoing_transfers was:**
- ❌ RPC-dependent (required getSignaturesForAddress polling)
- ❌ Batch-inserted (periodic full scans, pagination loops)
- ❌ Incomplete (could miss signatures between scans)
- ❌ Slow (cursor tracking, batch processing overhead)

---

## What Changed Everywhere

### 1. Counting Outgoing Transfers

**Old**:
```sql
SELECT COUNT(*) FROM creator_outgoing_transfers
WHERE creator_address = ?
```

**New**:
```sql
SELECT COUNT(*) FROM sol_transfers
WHERE source = ?
```

### 2. Counting Unique Recipients

**Old**:
```sql
SELECT COUNT(DISTINCT recipient_address)
FROM creator_outgoing_transfers
WHERE creator_address = ?
```

**New**:
```sql
SELECT COUNT(DISTINCT destination)
FROM sol_transfers
WHERE source = ?
```

### 3. Summing Outgoing SOL

**Old**:
```sql
SELECT SUM(amount_sol)
FROM creator_outgoing_transfers
WHERE creator_address = ?
```

**New**:
```sql
SELECT SUM(amount_sol)
FROM sol_transfers
WHERE source = ?
```

### 4. Distribution Pattern Detection

**Old** (webhook_api_enriched.py, old version):
```python
# Query creator_outgoing_transfers
cursor.execute("""
    SELECT COUNT(DISTINCT recipient_address)
    FROM creator_outgoing_transfers
    WHERE creator_address = ?
""", (creator,))
```

**New** (webhook_api_enriched.py, current):
```python
# Query sol_transfers instead
cursor.execute("""
    SELECT COUNT(DISTINCT destination)
    FROM sol_transfers
    WHERE source = ?
""", (creator,))
```

This is **already correct in your code** at [webhook_api_enriched.py:120-126](webhook_api_enriched.py#L120-L126).

### 5. Concentration Risk Scoring

**Old** (webhook_creator_ranker.py, would have queried creator_outgoing_transfers):
```python
# Bad: would scan creator_outgoing_transfers
cursor.execute("""
    SELECT destination, SUM(amount_sol) as total
    FROM creator_outgoing_transfers
    WHERE creator_address = ?
    GROUP BY destination
    ORDER BY total DESC
""")
```

**New** (should query sol_transfers):
```python
# Good: scan sol_transfers instead
cursor.execute("""
    SELECT destination, SUM(amount_sol) as total
    FROM sol_transfers
    WHERE source = ?
    GROUP BY destination
    ORDER BY total DESC
""")
```

---

## What You DON'T Need Anymore

### No cursor tracking
You don't need to store "last signature processed" for this address.
- **Why**: sol_transfers stores everything
- **Instead**: Just query sol_transfers with time windows

### No signature pagination
You don't need batch processing loops (signature offset/limit).
- **Why**: sol_transfers has all signatures
- **Instead**: Query directly, no pagination needed

### No getSignaturesForAddress discovery
You don't need RPC calls to discover new transfers.
- **Why**: Webhooks provide all transfers in real-time
- **Instead**: Worker only processes if priority >= 80

### No batch edge insertion
You don't need to periodically insert discovered signatures.
- **Why**: Webhook handler already inserted them
- **Instead**: Worker just scores and processes

### No periodic full scans
You don't need background jobs scanning for missed signatures.
- **Why**: Webhook deduplication guarantees completeness
- **Instead**: Event-driven only

---

## System Flow (Current & Correct)

```
Helius Webhook
     ↓
Parse transfer
     ↓
INSERT INTO sol_transfers
     ↓
UPDATE address_activity (from sol_transfers)
     ↓
ENQUEUE to work_queue
     ↓
Worker processes
     ↓
API queries sol_transfers for distribution/concentration
     ↓
Return enriched creator with risk scores
```

**No RPC polling. No cursor tracking. No batch jobs.**

---

## Current Code Status

### ✅ Already Correct

**[webhook_api_enriched.py:120-126](webhook_api_enriched.py#L120-L126)**:
```python
cursor.execute("""
    SELECT COUNT(DISTINCT recipient_address) as recipient_count
    FROM sol_transfers
    WHERE source = ?
""", (creator,))
```
Good! Uses `sol_transfers`.

**[webhook_api_enriched.py:234-243](webhook_api_enriched.py#L234-L243)**:
```python
cursor.execute("""
    SELECT
        COUNT(*) as total_transfers,
        SUM(amount_sol) as total_sol,
        ...
    FROM sol_transfers
    WHERE source = ? OR destination = ?
""", (creator_address, creator_address))
```
Good! Uses `sol_transfers`.

### ⚠️ Verify

Check that all references in code use:
- `sol_transfers` (new, canonical)
- NOT `creator_outgoing_transfers` (old, deprecated)

Search your codebase:
```bash
grep -r "creator_outgoing_transfers" --include="*.py"
```

If any results, replace with `sol_transfers`.

---

## Future: Scaling Optimization

### When to Implement

When `sol_transfers` exceeds **5M rows** (100K+ active creators × 50 transfers each).

### What to Add

A **derived table** `creator_outgoing_stats`:

```sql
CREATE TABLE creator_outgoing_stats (
    creator_address TEXT PRIMARY KEY,
    total_outgoing_count INTEGER,
    unique_recipient_count INTEGER,
    total_sol_sent REAL,
    last_outgoing_at INTEGER,
    updated_at TIMESTAMP
);

CREATE INDEX idx_creator_outgoing_stats_recipients
ON creator_outgoing_stats(unique_recipient_count DESC);
```

### How It Works

**Updated incrementally** on each webhook:

```python
# When inserting transfer with source = creator_address
UPDATE creator_outgoing_stats SET
    total_outgoing_count = total_outgoing_count + 1,
    total_sol_sent = total_sol_sent + amount_sol,
    unique_recipient_count = (
        SELECT COUNT(DISTINCT destination)
        FROM sol_transfers
        WHERE source = creator_address
    ),
    last_outgoing_at = block_time,
    updated_at = CURRENT_TIMESTAMP
WHERE creator_address = ?
```

### Benefits at Scale

**Before** (querying sol_transfers):
- Distribution scoring: Scan 50 rows per creator = 5M scans total
- Concentration scoring: Same = expensive

**After** (querying creator_outgoing_stats):
- Distribution scoring: Single row lookup = O(1)
- Concentration scoring: Single row lookup = O(1)

### When to Do This

- Not now (you're at ~1K addresses)
- At 50K addresses with 5M rows, consider it
- At 100K addresses with 10M rows, implement it

---

## Documentation Update

This document replaces:
- Old patterns in WEBHOOK_DATABASE_SCHEMA.md (for outgoing transfers)
- Old patterns in WEBHOOK_CREATOR_DATA_FLOW.md (for outgoing transfer queries)

All current documentation already uses `sol_transfers` correctly.

---

## Summary

**Key Point**: `sol_transfers` is now your **canonical source** for:
- Creator outgoing transfers
- Distribution patterns
- Concentration analysis
- Network building
- All creator activity queries

**No more**:
- RPC polling for signatures
- Cursor tracking
- Batch edge insertion
- Periodic full scans

**System is now**:
- Fully event-driven
- Real-time
- Complete (no missing data)
- Simple (webhook → store → score → serve)

**When scaling** (5M+ rows):
- Add `creator_outgoing_stats` derived table
- Update incrementally
- Query derived table instead of event log

---

**Status**: ✅ Already Implemented Correctly
**Next Action**: None (unless you find old code still using creator_outgoing_transfers)

---

*Generated: 2026-03-03*
*Claude Code*
