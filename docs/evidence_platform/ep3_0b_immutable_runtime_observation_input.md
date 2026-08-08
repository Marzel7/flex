# EP3.0B — Immutable Runtime Observation Input Contract

Status: **implemented and validated**

EP3.0B repairs the Operation Runtime data plane without adding an Operation or
activating production Evidence. Runtime references remain audit provenance;
runtime stages now also receive the immutable observations those references
identify.

## Data plane

```text
orchestration resolver
  -> RuntimeEvaluationSnapshot
       -> EvidenceInputWindow
       -> PrimitiveInputWindow
  -> BehaviourObservation objects
  -> TopologyRevision object
  -> DetectorInput
  -> DetectorResult
```

`EvidenceInputWindow` and `PrimitiveInputWindow` are deterministically ordered,
deduplicated by immutable identity, collision checked, deep-frozen, and capped
at 10,000 supplied observations each. They preserve normalized payloads,
versions, quality and failure states, observation windows, subjects, and
provenance. Window digests cover the complete serialized observations rather
than reference lists alone.

`RuntimeEvaluationSnapshot` binds the two window digests to the contract digest,
contract/module/topology/detector versions, subjects, observation window,
watermarks (through the window digests), and evaluation timestamp. Replaying a
materialized snapshot therefore needs no Evidence or Primitive store query.

## Ownership rule

Runtime orchestration resolves data. Operation modules evaluate data.

The module protocols expose value objects only. They expose no database,
repository, RPC, cache, or resolver handle:

- Behaviour receives its declared Primitive subset plus the declared Evidence
  window and module definition.
- Topology receives complete Primitive and Behaviour observations plus the
  contract topology definition.
- Detector receives complete Evidence, Primitive, Behaviour, and Topology
  values.

Subject selection for Evidence is owned by the orchestration caller because
normalized Evidence families do not share a universal subject field. The
runtime still bounds supplied Evidence by contract family/version, observation
time, identity collision rules, watermarks, and the fixed maximum. Primitive
selection additionally enforces declared type/version and subject overlap.

## Provenance

- Behaviour outputs retain Evidence and Primitive references.
- Topology revisions retain node/edge Evidence and Primitive references and
  the Behaviour Observation references used to build the revision.
- Detector results retain Evidence, Primitive, Behaviour, and Topology
  references.
- Every stage uses the materialized snapshot digest as its input digest.

References not present in the snapshot or approved upstream outputs fail
closed during runtime validation.

## Validation

Synthetic fixtures prove that modules can consume transaction direction,
amount, signature, time, signers, and quality from supplied immutable values;
topology can create directional edges; and the detector can inspect complete
Behaviour and Topology objects. Conflicting quality and missing inputs survive
through the detector result.

The tests also cover deterministic input ordering, identity collisions,
bounded windows, deep immutability, JSON serialization, deterministic replay,
idempotent persistence, and replay after connection creation has been disabled.
No RPC, production database, Operation implementation, or production consumer
is involved.

## EP3.1 readiness

The EP3.0 runtime data-plane defect is resolved. EP3.1 must **not** resume yet:
there is still no approved, materialized WATCHTOWER Evidence and Primitive
shadow corpus. The next milestone is shadow activation and coverage validation
of the existing EP1/EP2 pipeline. Only after that corpus is healthy can EP3.1
perform a meaningful parity comparison.
