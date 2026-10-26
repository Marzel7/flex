# CREATE audit authority — isolated DEV qualification

Status: **PASS for the prototype contract; not deployed or wired into runtime.**
The prototype is disposable-path-only. It is not another service, queue,
provider interface, production schema initializer or application writer.

## Frozen boundaries

The inspected installed shared writer runs the callback inside a transaction,
then calls `service_commit()`, then records its diagnostic commit timestamp.
Those diagnostics are separate from the database transaction. Existing CREATE
`acked_at` is dispatcher-start seconds. The compact handoff marker explicitly
means `BEFORE_OPS_SUBMIT / ATTEMPTED`. Neither is a historical OPS commit clock.

The accepted activation implementation is unchanged:
`runtime/listener/src/ops/walkback_create_handoff.py`, inspected SHA256
`00543c3a7d1ac2d8630af687809f0379f89bcf594934d7d9e6471980f78f0363`.
Tests call its actual `_activate` and dispatcher with disposable callbacks.

These are distinct boundaries:

1. **Core migration commit:** canonical migration becomes visible after its
   Core writer commits. `migrated_at` remains the accepted logical UTC migration
   timestamp; it is not claimed to be the physical commit instant.
2. **Handoff initiation:** existing pre-submit receipt; not OPS visibility.
3. **OPS migration handoff commit:** the accepted OPS queue callback commits,
   with a new prospective HANDOFF audit event in that same transaction.
4. **OPS anchor activation commit:** the accepted activation callback commits,
   with an ANCHOR receipt in that same transaction.
5. **Dispatcher acknowledgement:** a later Core transaction, which may be
   interrupted after the OPS commit. It does not establish the original anchor
   commit time or replace the OPS receipt.

No independent Core/OPS transaction is treated as atomic with another.

## Exact Core selection

```sql
CREATE INDEX dev_create_migration_time_v1
ON token_analysis(migrated_at,mint)
WHERE migrated_at IS NOT NULL;

SELECT mint,migrated_at
FROM token_analysis INDEXED BY dev_create_migration_time_v1
WHERE migrated_at>=? AND migrated_at<?
ORDER BY migrated_at,mint
LIMIT ?;
```

The query selects every canonical row in the half-open **logical UTC migration
window**, including rows lacking signatures, handoffs or OPS work. It does not
use operational-owner selection as a proxy. The measured plan is:

```text
SEARCH token_analysis USING COVERING INDEX dev_create_migration_time_v1
(migrated_at>? AND migrated_at<?)
```

Capture uses one bounded SQLite read snapshot: 128 rows fetched per step,
4,096 total rows, a 4 MiB output ceiling and a cooperative two-second budget.
Overflow raises HOLD; partial results cannot be returned as complete. A crash
or connection replacement discards the incomplete run and restarts the indexed
window in a **new snapshot**, never mixes old continuation with new source
state. A concurrent migration committed after the snapshot starts is excluded
from that snapshot and included in a later run.

The bound is explicit, not an assertion that every future window fits. No
absolute lock-duration or WAL ceiling follows from the cooperative timer. A
read snapshot can pin WAL; the prototype checks the existing pressure threshold.
Future production integration must keep the existing file/disk guards.

Index construction is one-time schema-owner work, not a database copy. It
necessarily reads existing rows when built on a populated table. This task
builds it only on synthetic disposable Core. A production index build, its
physical storage and live WAL effects have **not** been qualified or authorized.

## Durable OPS visibility and ordering

The four compact prototype tables hold owner/version/quota metadata, committed
transaction identities, HANDOFF/ANCHOR events and optional later UTC readback
observations. They reside in the **same existing OPS store** as the mutations.
An event cannot become visible without its accompanying mutation committing.
Rollback removes both. A missing audit receipt never means a successful write.

A future integration must invoke these hooks inside the **existing shared
writer callback**, after its accepted mutation and before its commit. It must
first validate the existing physical paired-generation authority; the prototype
metadata generation check does not replace that guard. Its direct transaction
context is a test harness, not a proposed production transaction owner.

HANDOFF is keyed by generation, mint and accepted canonical migration signature,
with canonical migration time retained in its payload. It records acceptance of
that migration intent in the OPS transaction, not an invented first creation
time for an already existing queue row. Duplicate keys must have identical
payloads. Unresolved or conflicting notification signatures are not substituted
for canonical migration identity.

ANCHOR is recorded only when the accepted producer returns `ACTIVATED`; exact
anchor readback, accepted proof and digest are retained. `ALREADY_COMMITTED`
reuses an existing matching activation receipt. If none exists, the result is
`LEGACY_UNTIMED`: no new activation provenance or historical time is invented.
A later PumpPortal proof cannot relabel an anchor first activated through the
accepted strict RPC path. Uninstrumented/unsupported writers remain unknown.

SQLite serializes OPS writers. Commit-order IDs allocated in the same write
transaction give these exact relations for retained same-generation receipts:

- Anchor transaction precedes handoff transaction: **BEFORE_HANDOFF**.
- Both events share a transaction: **AT_HANDOFF**, with simultaneous visibility.
- Anchor transaction follows handoff transaction: **AFTER_HANDOFF**.
- Either receipt is missing: **UNVERIFIABLE**, not an inferred zero or timestamp.

Allocation order is not compared across databases. These records establish
visibility order even if wall clocks move; they do not reveal an exact physical
UTC commit instant.

## UTC bounds and crash recovery

The staged UTC value is explicitly pre-commit. A later durable observation
records the actual readback time after commit returned. Together they provide a
UTC bracket under the normal clock assumption, never a claimed commit point.
A backward clock yields `CLOCK_UNCERTAIN`. A crash before the observation leaves
`UPPER_UNAVAILABLE`; a later recovery can record its real readback time as
`RECOVERED_READBACK`, without backdating or rewriting the first source.

The event itself is already durable after OPS commit, independently of that
optional observation or a Core dispatcher ACK. The accepted dispatcher test
crashes after OPS commit before ACK, restarts, verifies the same receipt/source,
and only then acknowledges through the unchanged accepted path.

## Bounds and retention

- At most 4,096 events and 4 MiB logical event payload per prototype store.
- Each payload at most 1,024 bytes; at most 128 new events per transaction.
- Commit rows are bounded by event rows; observations are bounded by commit rows.
- No automatic event deletion, archive, historical backfill or retry reset.
- Quota failure rolls back the callback and receipt together; it cannot publish
  an unaudited success. Production saturation policy requires separate review.
- Existing 2 GB plus 64 MiB disk check, 48 MiB cooperative WAL pressure check and
  499,000,000-byte test-process file cap remain explicit.
- These are finite quotas, not indefinite sustainable retention. No absolute
  aggregate physical database/WAL bound or production growth claim is made.

Measured disposable qualification: 54 files, 2,461,696 bytes total; largest file
376,832 bytes. The populated 2,000-row Core fixture with its new index was
352,256 bytes. The 1,000-event OPS fixture used 376,832 bytes. These measurements
are fixture results, not production size estimates.

## Qualification and limits

`tests/test_v2_create_audit_authority.py`: **26 passed, zero failures**.
Controls cover complete indexed membership, absent operational dependencies,
concurrent migration/read snapshot, interrupted capture/restart, delayed delivery,
before/at/after handoff, concurrent OPS writers, no uncommitted visibility,
ALREADY_COMMITTED, immutable first-source attribution, real accepted dispatcher
crashes before ACK, quota/payload/batch/disk/WAL refusal, repeat initialization,
generation conflicts and clock uncertainty. No provider, port or background
service was started. Existing application source was not changed.

An authorized future index can query retained historical canonical timestamps.
It cannot reconstruct overwritten historical state or absent migrations. New
OPS receipts qualify **prospective** instrumented transitions only. Historical
commit times, first source and uninstrumented ALREADY_COMMITTED cases cannot be
backfilled as facts. Per-mint HTTP request accounting remains outside this
contract; no request savings are claimed.

Stop here. Review/authorize any later runtime schema-owner/shared-writer
integration separately. No production deployment, promotion or Golden freeze.
