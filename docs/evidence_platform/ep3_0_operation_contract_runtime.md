# EP3.0 — Operation Contract Runtime

Status: **IMPLEMENTED — GENERIC RUNTIME ONLY**

EP3.0 implements the machine-readable contracts frozen by EP3.0A. It loads no
real Operation and has no production composition-root integration. The runtime
is an isolated interpreter of explicitly registered implementations and
immutable Evidence/Primitive references.

## Runtime boundary

```text
Immutable Evidence references
            +
Generic Primitive references
            ↓
Validated Operation Contract v1
            ↓
Behaviour Runtime
            ↓
Operation-local Topology Runtime
            ↓
Detector Runtime
            ↓
Immutable Detector Result
            ↓
Lifecycle Recommendation (never execution)
```

The runtime performs no RPC, acquisition, fact normalization or Primitive
generation. Inputs are supplied explicitly with versions and watermarks. A
required input missing from an evaluation fails closed.

## Registries and loading

Contracts load only from JSON objects, bytes or explicit paths and pass both
JSON Schema and semantic validation before registration. Contract documents
cannot name Python classes or import paths. Executable implementations are
resolved only from process-owned, explicit registries:

- Behaviour module ID and exact version;
- Detector ID and exact version;
- Topology implementation version;
- Presentation schema version.

The contract registry provides the Operation and Version registry projections:
contracts are grouped by stable `contract_id`, versions coexist deterministically,
and only one version may be active. DRAFT and DEPRECATED contracts do not
execute; SHADOW and ACTIVE contracts do. Activation and rollback retain the
EP3.0A transition semantics.

## Evaluation

An `EvaluationRequest` freezes:

- contract selection;
- subjects and observation window;
- versioned Evidence availability;
- versioned Primitive availability;
- immutable input references;
- Evidence and Primitive watermarks;
- optional current three-dimensional candidate state.

Behaviour modules receive only their declared Primitive references. Runtime
validation rejects producer identity mismatches and references outside the
evaluation input. Topology nodes must use contract-local roles; every topology
edge must match a declared role/Primitive rule. Detector inputs are generated
deterministically from the complete evaluated input.

Identity, confidence, governance and monitoring sections remain
`LOAD_VALIDATE_ONLY`. A Detector may emit a governance or lifecycle
recommendation, but the runtime stores it without applying it. There is no
identity promotion, canonical mutation or governance execution path.

## Persistence and replay

`OperationRuntimeStore` is a separate SQLite store with append-only tables for:

- immutable contract versions;
- activation history;
- Behaviour Observations;
- Topology Revisions;
- Detector Inputs;
- Detector Results;
- Lifecycle Recommendations;
- Evidence/Primitive/runtime reference edges.

All runtime tables reject updates and deletes with database triggers. Exact
replay is idempotent. Reuse of an output identity with different content fails
as a collision. Different producer or contract versions coexist.

The store is deliberately not attached to a production database. EP3.0 does
not add a worker, queue, API, service startup hook, feature flag or consumer.

## Frozen exclusions

EP3.0 contains no:

- WATCHTOWER or 3SW2 contract;
- unknown-operation discovery;
- creator-funding or walkback integration;
- governance decision or identity promotion;
- canonical Operator or role assumption;
- global confidence or similarity model;
- production database access;
- RPC or network access.

## Validation contract

The EP3.0 suite proves contract loading, deterministic registries, version
coexistence, dependency failure, Behaviour execution, topology validation,
Detector execution, lifecycle recommendation safety, append-only persistence,
idempotent replay, presentation/policy loading and production isolation using
only a synthetic fixture contract.

EP3.0 is therefore ready for a later, separately authorized Operation contract
milestone. No Operation is authoritative merely because the runtime exists.
