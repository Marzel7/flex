# EP3.0 blocker — executable Operation Contract is not frozen

Status: **RESOLVED BY EP3.0A**

EP3.0A formalizes the missing machine-readable schemas, identities, protocols,
lifecycle dimensions, registry semantics and replay/storage contract. See
`ep3_0a_operation_runtime_contract_v1.md`.

EP3.0 requires an Operation Contract Runtime capable of loading and executing
any valid Operation Contract, while prohibiting new architectural decisions.
The retained X78.25 architecture defines the conceptual layers and top-level
contract categories, but it does not define an executable contract schema or
runtime input/output protocols.

Implementing the requested runtime would therefore require inventing permanent
semantics that have not been approved.

## What is frozen

X78.25 establishes that each Operation contributes:

- name/identity;
- version;
- evidence requirements;
- primitive requirements;
- topology;
- behaviour modules;
- confidence rules;
- governance rules;
- monitoring rules.

It also establishes a plugin-style architectural direction and the generic
lifecycle state names.

EP3.0 adds presentation schema and lifecycle status to that conceptual list.

## Missing executable contract

No frozen source defines the machine-checkable shape of:

### Operation Contract

- canonical field names and types;
- required versus optional fields;
- contract identifier/version identity formula;
- compatibility rules;
- dependency syntax and version constraints;
- lifecycle-state validation rules;
- serialization and digest rules;
- whether contracts are data files, Python plugins, signed packages or another
  representation;
- loader discovery path, trust boundary and failure isolation.

### Behaviour Module

- callable interface;
- primitive input selection;
- configuration/parameter contract;
- `BehaviourObservation` fields and identity;
- deterministic generation timestamp policy;
- missing/conflicting primitive handling;
- module version coexistence and dependency rules.

### Topology Runtime

- operation-local node and edge schema;
- edge provenance requirements;
- topology revision identity;
- how a contract declares roles without placing assumptions in the runtime;
- handling of incomplete or conflicting topology;
- replay and version coexistence rules.

### Detector Runtime

- detector callable interface;
- exact Evidence, Primitive, Behaviour and Topology input envelope;
- `DetectorResult` schema and identity;
- permitted result states;
- deterministic conflict/failure behavior;
- whether confidence declarations may be loaded but not executed in EP3.0.

### Candidate Lifecycle

The state names are frozen, but the transition graph is not sufficient to
implement lifecycle infrastructure safely. Missing decisions include:

- whether every adjacent transition is permitted in both directions;
- the destination of `REACTIVATED` after reactivation;
- whether non-canonical confirmed Operations may become dormant;
- rejection/invalid-transition behavior;
- event identity, actor, reason and timestamp contracts;
- persistence, replay and concurrency rules;
- whether lifecycle events are mutable state, immutable history, or both.

### Presentation and policy loaders

- presentation schema format and validation;
- allowable presentation vocabulary;
- governance-policy schema;
- whether governance and confidence fields are opaque declarations in EP3.0 or
  executable runtime inputs;
- monitoring-policy schema;
- prohibited hard-coded label enforcement.

### Registries and storage

- registry entry identity and collision behavior;
- deterministic ordering;
- active-version activation transaction;
- persistence location and isolation boundary;
- lifecycle of draft/deprecated/disabled versions;
- immutable runtime-output storage versus ephemeral evaluation.

## Scope contradiction

EP3.0 requires every loaded contract to contain an identity contract,
confidence model and governance policy while simultaneously prohibiting
identity, confidence scoring and governance. This is implementable only after
the frozen contract states whether those sections are:

- validated opaque declarations;
- loaded but never executed;
- optional in EP3.0; or
- executable interfaces whose effects are suppressed.

Choosing among these alternatives is architectural, not mechanical.

## Minimal unblock

Approve an **EP3.0A — Operation Runtime Contract Formalization** milestone that
freezes, without implementing any Operation:

1. canonical Operation Contract JSON/schema and deterministic contract ID;
2. registry/version/dependency/lifecycle rules;
3. Behaviour Module callable and `BehaviourObservation` schema;
4. topology declaration and output schemas;
5. detector callable and `DetectorResult` schema;
6. lifecycle event/state persistence and transition table;
7. presentation, governance, confidence and monitoring loader semantics;
8. runtime output identity, storage, replay and failure contracts;
9. plugin discovery and trust/isolation policy.

Once approved, EP3.0 can implement the runtime without embedding an accidental
first Operation into generic infrastructure.

## Work intentionally not performed

No runtime, registry, loader, schema, storage, lifecycle, test, production
change, commit or push was created. EP0–EP2 remain unchanged.
