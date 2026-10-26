# Prospective CREATE coverage — qualified DEV integration

**PASS, ready for a separately scoped read-only deployment preflight.**
No production schema, source, configuration, services or records were changed.
The opt-in is `FLEX_V2_CREATE_AUDIT_ENABLED=1` for listener, creator resolver and
Walkback together. Default-off runtime behavior and accepted processing remain;
a present coverage schema cannot be silently used with the flag disabled.

## Existing owners and callbacks

- `walkback_queue.ensure_create_coverage_schema(conn)` is the explicit existing
  OPS schema-owner extension. It delegates versioned DDL inside the caller's
  accepted writer transaction; ordinary startup does not initialize anything.
- `dev004_core_base_schema.initialize_create_coverage_index(conn)` extends the
  accepted Core owner explicitly. No base schema definition, historical token
  row, price table or existing index is rewritten.
- `walkback_queue.enqueue_migration` attaches the receipt beside its actual queue
  insertion, before legacy helper commits. Existing managed callback commit
  ownership is unchanged. A pre-existing queue can receive a first prospective
  handoff receipt only with matching, newly admitted PENDING/RETRY Core intent
  after the instrumentation boundary. Legacy/duplicate handoffs are not backfilled.
- `walkback_create_handoff._activate` records only its `ACTIVATED` transition,
  in the same OPS transaction. `ALREADY_COMMITTED` never creates an activation
  receipt or relabels the original source.

The three accepted packages contain identical evidence-owner and activation
modules, with narrowly applied enqueue hooks in each existing queue variant.
No attribution predicate, canonical CREATE producer, migration completion rule,
provider endpoint/budget, terminal owner, receipt or journal lifecycle changed.
Tests execute the accepted enqueue mutation body under the real shared writer;
unrelated attribution integrations are isolated, not requalified as new logic.

## Schema v1 and generation authority

`create_coverage_owner_v1` stores version, start boundary and finite quotas.
`create_coverage_commits_v1` stores accepted writer transaction identity, original
physical generation and pre-commit UTC label. `create_coverage_events_v1` stores
immutable handoff/activation identity, accepted proof/digest and source metadata.
Quota, parent and immutability triggers protect the audit namespace without
changing global foreign-key settings or canonical table definitions.

Every enabled write validates the accepted physical paired-generation manifest,
exact Core/OPS role, retirement fence and accepted writer transaction identity.
No Core V1 fallback or implicit database creation is allowed. Missing schema,
wrong role/generation, flag downgrade, incompatible objects and quota pressure
fail closed before acknowledgement. They do not create a second writer/service.

Event identity survives qualified fresh compact recovery. Original transaction
generations and first sources remain immutable. A replay of the same canonical
identity reuses existing evidence; it does not stamp a replacement generation.
Timing comparisons across different original generations remain unverifiable.

## Provenance and ordering

The accepted proof/digest and exact activated OPS anchor are recorded together.
PumpPortal additionally records an independently verified committed receipt
identity, read by deterministic bounded filename lookup. A missing/conflicting
receipt does not rewrite canonical data or force acquisition: its origin remains
unknown in the audit. Historical/uninstrumented anchors remain unknown rather
than being labelled PumpPortal/RPC from a matching field alone.

For matching same-generation HANDOFF and ANCHOR receipts, serialized OPS commit
orders establish **BEFORE_HANDOFF**, **AT_HANDOFF** (same atomic visibility), or
**AFTER_HANDOFF**. Missing receipts remain **UNVERIFIABLE**. A pre-commit staged
UTC label is not an exact physical commit timestamp. No historical time or first
source is manufactured. No cross-database transaction is claimed.

## Read-only reader

`scripts/flex_v2_create_coverage_audit.py` requires explicit Core/OPS paths, pair
manifest, authority, expected generation and UTC window. It opens query-only
connections, does not load credentials/providers, and never initializes schema.

Core selection uses the exact partial index:

```sql
CREATE INDEX dev_create_migration_time_v1
ON token_analysis(migrated_at,mint)
WHERE migrated_at IS NOT NULL;
```

It selects every canonical migration in one bounded Core snapshot, including
missing signatures/handoffs. Fractional UTC endpoints preserve integer timestamp
membership. The plan is an indexed covering time-range search plus exact-mint
primary-key reads for canonical evidence. It never scans historical token rows.
An authorized one-time production index build would read existing rows and must
be qualified during preflight; no such build occurred here.

The OPS evidence snapshot joins by indexed immutable event identity. The five
mutually exclusive source categories always sum to the exact captured denominator.
Uninstrumented other sources remain explicitly unknown, not falsely verified;
`OTHER_VERIFIED_SOURCE` is reserved for independently qualified supported proof.
All four availability categories, including unverifiable timing, are reported.
Core membership and OPS evidence snapshots are explicit separate boundaries,
not a fictitious atomic cross-database snapshot. No provider-request savings
are inferred.

## Bounds, recovery and remaining preflight gates

- Capture: 128 rows/fetch, 4,096 total, 4 MiB returned data and cooperative
  two-second budget. Overflow invalidates the whole result, never truncates it.
- OPS: 4,096 events / 4 MiB logical payload; 1,024 bytes/event; 128 new events/TX.
  Commit metadata is bounded by event count. No unbounded archive or age deletion.
- Existing 499,000,000-byte process/file safeguards and 2 GB + 64 MiB floor remain.
  WAL pressure is cooperative at 48 MiB, not an absolute WAL/lock guarantee.
- Protected audit records are not disposable seven-day telemetry. Finite quota
  is a firebreak, not indefinite unattended retention. Quota failure rolls back
  the mutation and evidence together; production preflight must establish an
  acceptable bounded observation horizon and operational capacity plan.
- The accepted terminal-aware recovery renderer preserves the new flag for all
  three roles, validates schema/index and source hashes, and rejects audit-unaware
  fallback after initialization. It also pins the already accepted retry-only
  listener source. The fresh-pair contract preserves original audit generations.

Before any deployment, freeze the actual runtime manifest and preimages, stage a
coverage-aware fallback, qualify initial index/schema preparation/storage, and
quiesce all affected writers for coherent flag/source activation. This task does
not authorize that sequence, restart services, freeze Golden, or promote V2.

## Qualification

**86 passed, zero failures**: existing 27 audit-authority controls; 33 integrated
controls; 13 terminal-recovery and 13 final-consolidation controls. Covers real
shared-writer ownership/fencing, actual accepted dispatcher crash after OPS commit
before Core ACK, restart/readback, duplicates, delayed activation, pre-existing
queue with new migration, immutable first source, legacy unknowns, concurrency,
wrong generation, storage/quota rejection, no V1 fallback, read-only CLI, bounded
complete denominators, package coherence and selected-row fresh-pair recovery.
No provider calls, production writes or historical replay occurred. Large
historical acceptance matrices were reused unchanged.

Final disposable suite: 719 files, 39,901,871 bytes combined; largest file
1,228,800 bytes. These are fixture measurements, not production growth estimates.
