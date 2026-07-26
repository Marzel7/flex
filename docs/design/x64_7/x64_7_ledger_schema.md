# X64.7 — Phase 6: Canonical CREATE-Event Ledger Schema

Implemented in `src/ops/create_event_ledger.py`, living in the ops
database (`wt_ops_v2.db`) alongside every other X64.x durable-
intelligence table (`wt_anchor_reconciliation_log`,
`wt_watchtower_candidates`, etc.) — not the live/production database,
which is churn-heavy and not the audit-trail home for this project's
established pattern.

## Table: `wt_create_event_ledger`

```sql
CREATE TABLE IF NOT EXISTS wt_create_event_ledger (
    signature                TEXT PRIMARY KEY,
    mint                     TEXT NOT NULL,
    creator                  TEXT,
    slot                     INTEGER,
    block_time               INTEGER,
    observed_at              INTEGER NOT NULL,
    source                   TEXT NOT NULL,
    parser_path              TEXT,
    tx_version               INTEGER,
    instruction_index        INTEGER,
    inner_instruction_index  INTEGER,
    raw_detection_method     TEXT,
    creator_resolution_state TEXT,
    persistence_version      INTEGER NOT NULL DEFAULT 1,
    first_seen_at            INTEGER NOT NULL,
    last_seen_at             INTEGER NOT NULL
);
```

Matches the task's suggested schema exactly (signature as PRIMARY KEY —
already the durable unique identifier per-key, so the task's suggested
`idx_..._mint_signature` UNIQUE index is additive/redundant with the PK
but implemented anyway for explicit documentation of the (mint,
signature) uniqueness property and because a future schema change could
in principle make `signature` non-unique on its own, e.g. if multi-DEX
support required a composite key — kept per the task's exact spec rather
than omitted as "technically redundant").

## Indexes

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_wt_create_event_ledger_mint_signature
  ON wt_create_event_ledger(mint, signature);
CREATE INDEX IF NOT EXISTS idx_wt_create_event_ledger_mint
  ON wt_create_event_ledger(mint);
CREATE INDEX IF NOT EXISTS idx_wt_create_event_ledger_creator
  ON wt_create_event_ledger(creator);
```

All three implemented exactly as specified.

## Conflict/audit table (Phase 8 requirement)

```sql
CREATE TABLE IF NOT EXISTS wt_create_ledger_conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conflict_type   TEXT NOT NULL,   -- SIGNATURE_MINT_MISMATCH | CREATOR_MISMATCH
    signature       TEXT NOT NULL,
    mint            TEXT NOT NULL,
    existing_value  TEXT,
    incoming_value  TEXT,
    detected_at     INTEGER NOT NULL
)
```
Indexed on `signature`. No such table existed before this task; created
per Phase 8's explicit instruction ("add a conflict/audit table if one
does not already exist").

## Design properties confirmed against the task's requirements

- **`signature` is durable**: PRIMARY KEY, never reassigned once written.
- **`mint` is required**: `record_create_event()` refuses (`written:
  False, reason: "mint_required"`) with no mint — the ONE hard
  requirement, verified by test.
- **`creator` is nullable**: no gate on creator anywhere in
  `record_create_event()`'s signature or body — verified by test
  (`test_create_with_creator_null_still_writes_ledger`).
- **Append/upsert is idempotent**: same signature observed twice updates
  `last_seen_at` and fills a NULL creator, never duplicates a row —
  verified by test (`test_duplicate_same_signature_observation_is_idempotent`).
- **First observation is preserved**: `first_seen_at` is set only on
  the initial INSERT, never touched by the enrichment UPDATE path.
- **Later observations may enrich missing fields**: `creator` fills via
  `COALESCE(creator, ?)` on re-observation — never overwrites an
  existing non-NULL value (conflict instead, see below).
- **No attribution fields**: the table has no `subprov`, `treasury`, or
  any attribution-adjacent column — by design, matching the task's
  explicit instruction and this session's established discipline
  throughout every prior X64.x deliverable.

## Conflict rules, as implemented

- **Same signature + different mint** → `SIGNATURE_MINT_MISMATCH`,
  logged to `wt_create_ledger_conflicts`, original row untouched, write
  refused (`written: False`).
- **Same mint + multiple signatures** → both retained as separate rows
  (no dedup key on mint alone); `lookup_create_anchor()` returns
  `confidence='CONFLICT'` when this shape is queried, surfacing the
  ambiguity to the caller rather than silently picking one.
- **Later creator differs from existing non-NULL creator** →
  `CREATOR_MISMATCH`, logged, original creator value untouched, write
  refused.

All three rules independently verified by dedicated tests in
`tests/test_x64_7_create_event_ledger.py` (tests 6, 7, 8).
