# OIP v2.2C.4 — Compact Application Path and Migration Readiness

## Machine restart recovery

The recovered repository is on `classification-attribution-axis` at
`075913eb12ba625e1c4452ee4d0812b1eaf15404`, the committed v2.2C.3 milestone.
It is not pushed; the remote remains at `d2d75c6f`. Existing unrelated working
tree changes were preserved.

No configured supervisor socket or matching project process survived the full
machine restart. No process was started or stopped. The Data volume has
71,612,719,104 bytes available.

The committed reports, checkpoint, and downstream caches are intact. The final
authority database is `indexed_authority_compact.sqlite` (924,835,840 bytes,
integrity `ok`): 401,050 authority events, 346,730 indexed current rows, 671,092
subject memberships, and 616,444 current memberships.

An earlier rejected/intermediate `indexed_authority.sqlite` remains at a
400,000-event checkpoint with empty projections and a 235 MB WAL. Recovery on a
temporary copy confirmed that WAL does not expose committed final rows. It is
preserved but is not the database used by the completed validator.

## Recovered semantic gates

- Authority and clean replay digest: `6e2bd05c…d93e`, zero difference.
- Historical compact gate: 12,398,192 exact relations.
- Current compact gate: 6,457,475 exact relations, digest `831e909d…a051`.
- Discovery: 44,475 candidates in both modes, with the expected 206 corrected
  subjects and no unrelated population change.
- Motifs: 4,367 persisted versus 4,365 current-authority.
- Relationships: 686 in both modes; every relationship ID and type count shared.
- Persisted v2.2C.3 report: 21 focused and EP2 tests passed.

No multi-hour validator was rerun.

## Compact query-plan diagnosis

The 280.072-second path was not slow because integer keys are inherently slow.
Its plan scanned the compact reverse index, repeatedly resolved external
Primitive IDs, checked selection one row at a time, and built a global temporary
B-tree for external-ID ordering:

```text
SCAN compact_inputs_by_evidence
SEARCH primitive_identity BY INTEGER PRIMARY KEY
SEARCH selected_primitives BY PRIMARY KEY
SEARCH evidence_identity BY INTEGER PRIMARY KEY
USE TEMP B-TREE FOR ORDER BY
```

The accepted path resolves selected external IDs once into a temporary integer
key set, drives the compact primary key `(primitive_key,evidence_key)` from that
bounded set, resolves Evidence IDs at the edge, and sorts only each Primitive's
Evidence list where the external contract requires ordering.

```text
SCAN selected_primitive_keys
SEARCH compact relation USING PRIMARY KEY (primitive_key=?);
SEARCH evidence_identity USING INTEGER PRIMARY KEY
```

No compatibility view is used for full-corpus access.

## Real consumer benchmark

| Consumer | Canonical | Compact integer-key | Result |
|---|---:|---:|---|
| Current-authority full stream | 17.349s | 11.261s | Compact 35.1% faster |
| Discovery hydration | 29.655s | 11.694s | Compact 60.6% faster |

Both full streams contain exactly 6,457,475 pairs and share digest
`c764b62a…fe57`. All 92,217 Discovery Primitive objects and 370,299 hydrated
provenance pairs are exactly equal.

Bounded consumers:

- Primitive → Evidence, 100 Primitives: 504 rows in 0.051s.
- Evidence → Primitive, 100 Evidence IDs: 4,378 rows in 0.113s.
- One indexed subject: 18,747 Primitives in 1.377s.
- High-fan-out Primitive: 317 Evidence relations.
- Exact pair: present through indexed integer joins.

## Migration sidecar design

Canonical provenance remains authoritative during build. Before the source high
water mark is captured, an append-only delta outbox is installed in the same
canonical transaction domain as provenance writes. The sidecar then:

1. records a generation and immutable source high-water rowid;
2. copies canonical relations in bounded, committed batches;
3. persists its source cursor after each batch;
4. replays delta rows idempotently by sequence;
5. validates count, ordered digest, and both anti-joins;
6. requires an explicit writer pause;
7. drains the final delta and rejects any new sequence at the boundary;
8. changes canonical reader/writer/authority-generation control only after the
   compact transaction is committed and exact;
9. retains canonical provenance and the delta outbox throughout soak.

A crash before control switch leaves canonical readers authoritative. A crash
after sidecar commit but before control switch also leaves canonical readers.
The canonical control row is therefore the sole cutover decision point.

## Shadow simulation

A bounded synthetic fixture proves:

- interrupted build resumes from its persisted cursor;
- writes arriving during build are captured by the canonical outbox;
- replay is idempotent;
- cutover fails closed without a writer pause or complete base build;
- reader switch includes the authority generation;
- count/digest/anti-join equivalence gates cutover;
- rollback switches readers and writers back to canonical;
- canonical relations written before and during migration remain intact.

This is migration control infrastructure only. It has not been installed on a
production database and does not authorize production cutover.

## Disk and working space

- Compact provenance sidecar: 521,883,648 bytes.
- Authority and subject projection: 924,835,840 bytes.
- Authority-event table and indexes: approximately 525.5 MB.
- Subject/cardinality/current-subject structures and indexes: approximately
  399.3 MB.
- Current downstream caches: 122,284,129 bytes combined.
- Available local space: 71,612,719,104 bytes.
- Persistent 5K projection remains 1.78–2.67 GB before transient overhead.
- A rollback-journal upper bound equal to the current sidecar is approximately
  521.9 MB, plus delta/outbox growth and retained canonical storage.

Capacity is sufficient for a controlled migration rehearsal, but this does not
authorize 5K acquisition. Transient journal, delta, frozen canonical, and rollback
headroom must remain reserved through soak.

## Verdicts

### Compact Performance

**A — ACCEPTABLE FOR MIGRATION**

### Migration Readiness

**READY_FOR_CONTROLLED_MIGRATION**

This means a separately approved, shadow-first controlled migration milestone;
it does not mean production cutover is authorized.

### Acquisition

**HOLD_ACQUISITION**

### Canonical Retirement

**KEEP_CANONICAL_FOR_ROLLBACK**

No RPC, acquisition, production interaction, canonical deletion, provenance
deletion, Primitive mutation, authority change, or downstream algorithm change
occurred.
