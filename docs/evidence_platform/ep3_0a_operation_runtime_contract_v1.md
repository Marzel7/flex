# EP3.0A — Operation Runtime Contract v1

Status: **FORMALIZED AND IMPLEMENTATION READY**

EP3.0A converts the conceptual X78.25 architecture into repository-owned,
machine-readable contracts. It implements no Operation and executes no runtime
module, identity policy, confidence model, governance policy or monitor.

## Authoritative resources

- `operation_contract_v1.schema.json` — Operation Contract and declarative
  section schema.
- `runtime_output_v1.schema.json` — Behaviour, topology, detector and lifecycle
  output schemas.
- `formalization.py` — canonical identities, protocol types, lifecycle graphs,
  semantic validation and reference registry semantics.

## Contract identity and serialization

An Operation version is identified by:

```text
(contract_id, contract_version, contract_digest)
```

`contract_id` is a stable lowercase namespace identifier. `contract_version`
uses semantic `major.minor.patch` form. `contract_digest` is SHA-256 over the
EP1 canonical JSON encoding of the complete contract excluding the digest field
itself. Reordered object keys do not affect identity. Array ordering remains
meaningful and is not silently normalized.

Reloading identical content is idempotent. Reusing an ID/version with different
content is always a collision, including for drafts. Editing a registered draft
therefore creates a new version rather than silently replacing history.

## Executable and declarative separation

Executable EP3 sections are limited to:

- referenced Behaviour modules;
- operation-local topology rules;
- referenced Detector evaluation.

The following sections are declarative and machine-constrained to
`LOAD_VALIDATE_ONLY`:

- `identity_contract`;
- `confidence_model`;
- `governance_policy`;
- `monitoring_policy`.

Governance additionally requires `automatic_execution=false`. Contracts contain
IDs, versions, parameters and data; they cannot specify Python classes, import
paths, callbacks or arbitrary executable entrypoints. EP3.0 resolves module and
detector IDs only through pre-registered implementations.

`DetectorResult.confidence_output` is nullable, contract-local interpretation
output. The generic runtime defines no global scoring semantics and does not
compute it independently. A governance recommendation is also immutable output,
never an action.

## Dependency model

Contracts declare Evidence and Primitive requirements with required/optional
flags and version constraints. Existing EP1/EP2 integer schema versions support
exact constraints such as `1`; semantic versions support exact, `>=`, caret and
tilde constraints. Range syntax is rejected for opaque integer versions.

Behaviour modules must declare only Primitive types already required by the
contract. Topology edge rules must reference declared operation-local roles and
declared Primitive dependencies. Activation rechecks every dependency.

Contracts reference Behaviour and Detector implementations by stable ID and
exact semantic version. Presentation schema versions are also registry-checked.
Missing or incompatible required dependencies fail closed.

## Contract lifecycle and registry

Contract states:

```text
DRAFT → SHADOW → ACTIVE → DEPRECATED → DISABLED
   └──────→ DISABLED
SHADOW ───→ DEPRECATED | DISABLED
ACTIVE ───→ DISABLED
```

`DISABLED` is terminal. Multiple immutable versions may coexist, but only one
version per `contract_id` may be `ACTIVE`. SHADOW versions may evaluate and
persist outputs but are never authoritative.

Activation is atomic at the registry boundary and fails on missing dependencies
or another active version. Ordinary transitions cannot reactivate deprecated
versions. Rollback is the explicit exception: with exactly one current active
version, it deprecates that version and activates a previously published SHADOW
or DEPRECATED target after dependency validation. No contract or runtime output
is deleted.

Registries required by EP3.0:

- Contract Registry;
- Version Registry;
- Behaviour Module Registry;
- Detector Registry;
- Presentation Registry.

Duplicate exact registrations are idempotent; conflicting registrations fail.
Registry listing and version selection are deterministic.

## Behaviour protocol

`BehaviourModuleInput` contains only contract/module identity, subjects,
observation window, Primitive references and parameters. A module consumes
Primitives and returns a `BehaviourObservation` containing:

- deterministic observation ID;
- contract and module versions;
- subjects and parameters;
- measured values;
- Evidence and Primitive references;
- missing inputs and closed quality state;
- input digest and generation time.

No identity, topology, governance or Operation state mutation is available to
the protocol.

## Topology protocol

Topology is contract-local. `TopologyNode.local_role` may use any role declared
by that contract, but the role never enters Evidence or Primitive storage.
`TopologyEdge` records source, destination, Primitive type, cardinality,
temporal constraint, required/optional status and immutable input references.

A `TopologyRevision` is a deterministic immutable revision over sorted nodes,
edges, subjects and an input digest. The generic runtime provides no default
roles and makes no treasury/controller assumptions.

## Detector protocol

`DetectorInput` freezes:

- contract/detector versions;
- subjects and observation window;
- Evidence and Primitive watermarks;
- Evidence and Primitive references;
- Behaviour observation references;
- optional Topology revision reference;
- complete input digest.

`DetectorResult` keeps identity, topology, behaviour, contact, infrastructure,
funding and temporal evidence classes separate. Supporting, contradictory and
missing inputs remain explicit. Candidate lifecycle and governance fields are
recommendations only. The result cannot mutate Evidence, Primitives, identity
or governance.

## Candidate lifecycle

Candidate state has three independent dimensions.

Maturity:

```text
UNKNOWN → OBSERVED → BEHAVIOURAL_CLUSTER → INVESTIGATE
             ↘ DISMISSED ←───────────────↙
DISMISSED → OBSERVED
```

Governance identity:

```text
UNCONFIRMED ↔ REVIEW
UNCONFIRMED → CONFIRMED_OPERATION → CANONICAL
REVIEW → CONFIRMED_OPERATION
CONFIRMED_OPERATION → REVIEW
CANONICAL → REVIEW
```

Activity:

```text
ACTIVE → DORMANT → REACTIVATED → ACTIVE
                  ↘ DORMANT
ACTIVE | DORMANT | REACTIVATED → HISTORICAL
```

A `LifecycleRecommendation` must change exactly one dimension through an
allowed edge. It is immutable, references a Detector Result and always has
`automatic_execution=false`. Detector output alone never transitions state.
EP3.0 stores recommendations; a later authorized governance consumer may decide
whether to apply one.

## Presentation and policies

Presentation contains only rendering metadata: role/topology/evidence labels,
section order, allowed action identifiers and contract-version display. There
are no generic default role labels.

Confidence supports `DISABLED`, `CATEGORICAL`, `RULE_BASED` and `NUMERIC`
declarations without global meaning. Governance recommendation vocabulary is
closed. Monitoring declares triggers, minimum evidence state, reevaluation
conditions and optional staleness/dormancy windows; EP3.0A does not run a live
monitor.

## Runtime persistence contract

EP3.0 will create isolated append-only stores for:

- Behaviour Observations;
- Topology Revisions and their nodes/edges;
- Detector Inputs;
- Detector Results;
- Lifecycle Recommendations;
- Contract registry versions and immutable activation history.

Every immutable output stores its deterministic ID, producer versions, input
digest/watermarks, ordered Evidence/Primitive/runtime references, payload and
generation time. Update/delete is prohibited. Registry current-state pointers
may change only through append-only activation events and a transactional
projection; historical content is never rewritten.

## Replay

Runtime output IDs hash canonical content excluding their own ID and
`generated_at`. Re-running identical contract/module/detector versions over the
same watermarks, subjects and inputs produces identical IDs and payloads. New
producer versions coexist under new IDs. Persistence treats exact replay as an
idempotent duplicate and rejects ID collisions with different content.

## EP3.0 readiness

**READY.** EP3.0 can now implement loaders, registries, runtimes, append-only
storage and replay mechanically against these schemas. No further semantic
decision is required about contract identity, module communication, topology,
Detector output, lifecycle, declarative policies, version coexistence,
activation, rollback or persistence.

EP3.0 remains prohibited from loading a real Operation definition.
