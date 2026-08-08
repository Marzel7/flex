# EP3.1 blocker — runtime modules cannot consume immutable observation payloads

Status: **RUNTIME DEFECT RESOLVED BY EP3.0B; SHADOW DATA PREREQUISITE REMAINS**

EP3.0B implemented the approved immutable observation input contract. Behaviour,
Topology, and Detector stages now receive complete immutable upstream values,
retain their references, and can replay from a deterministic evaluation
snapshot without querying Evidence or Primitive storage.

This document remains as the defect record. The interface findings below
describe the pre-EP3.0B runtime. EP3.1 is still not ready to resume because the
materialized WATCHTOWER Evidence and Primitive shadow corpus identified below
does not yet exist.

EP3.1 cannot be implemented faithfully against Operation Runtime Contract v1
without violating its explicit constraints. No WATCHTOWER contract, module,
detector, production integration, Evidence change, Primitive change or runtime
change has been made.

## Blocking contradiction

EP3.1 requires WATCHTOWER Behaviour Modules to derive existing behaviour from
immutable Evidence and Generic Primitives. The frozen interfaces expose only
identifiers:

- `BehaviourModuleInput` contains `primitive_refs`, but no Primitive
  observations, payloads or immutable resolver interface.
- `TopologyModuleProtocol.generate` receives Primitive/Evidence reference
  strings, but no source/destination values, timestamps, amounts, mechanisms,
  subjects or quality states.
- `DetectorInput` contains Behaviour Observation and Topology Revision
  references, but no observations/revision payloads and no immutable resolver.

Consequently a real module cannot read:

- `SYSTEM_TRANSFER.output_payload.source` or `destination`;
- `WSOL_CLOSE` mechanism details;
- `WALLET_FRESH_AT_EVENT` state;
- `BEHAVIOURAL_TIMING` values;
- `REPEATED_COUNTERPARTY` counts;
- Behaviour Observation measured values;
- operation-local topology nodes and edges;
- Primitive quality, missing-input or contradiction state.

EP3.0's synthetic fixture can prove orchestration and persistence because its
fixture implementations manufacture known outputs from reference counts. That
does not provide the data plane required by a real Operation.

## Why EP3.1 cannot work around this

A WATCHTOWER implementation could obtain the missing values only by directly
opening the Evidence database, Primitive store or legacy WATCHTOWER database,
or by importing creator-funding/walkback internals. EP3.1 explicitly prohibits
all of those paths. Doing so would also make the Operation depend on a consumer
or storage implementation rather than on the frozen runtime input contract.

Embedding Primitive payloads in contract parameters would be mutable state,
not a contract. Reconstructing roles from identifier strings would invent
evidence. Neither is valid.

## Shadow-data prerequisite is also absent

Read-only characterization on 8 August 2026 found:

- no `database/evidence_platform/evidence.db`;
- no Evidence Platform files under the default Evidence path;
- no enabled Evidence Platform, mirror, normalization or Primitive flags in
  the active repository configuration;
- legacy WATCHTOWER currently has 62 confirmed treasury rows;
- legacy WATCHTOWER currently has 176 launch rows;
- legacy WATCHTOWER currently has 5,937 provisioning-edge rows;
- canonical WATCHTOWER currently has 69 entity rows.

Therefore there is no immutable live corpus on which to run the required
Legacy WATCHTOWER versus Evidence WATCHTOWER shadow comparison. EP3.1 forbids
creating new Evidence or connecting production acquisition, so this cannot be
repaired inside this milestone.

## Primitive expressiveness versus accessibility

Primitive Contract v1 appears capable of representing significant parts of
the WATCHTOWER behaviour:

- wrap-close funding: `WSOL_CLOSE`;
- explicit funding: `SYSTEM_TRANSFER` / `DIRECT_COUNTERPARTY`;
- creator freshness: `WALLET_FRESH_AT_EVENT`;
- funding/launch timing: `LAUNCH_ACTIVATION` / `BEHAVIOURAL_TIMING`;
- creator rotation and provisioning reuse: `REPEATED_COUNTERPARTY`;
- larger independent funding: `ECONOMIC_FUNDING`.

The immediate defect is not necessarily a missing Primitive. It is that the
Operation interfaces cannot access the immutable payloads of those Primitives.
Identity parity also requires a source for the reviewed legacy treasury
registry. No corresponding immutable `ExternalRegistryObservation` corpus is
currently available, and an Operation is forbidden from reading
`wt_confirmed_treasuries` directly.

## Minimal architectural decision required

An explicitly approved contract amendment must define an immutable runtime
data plane. A minimal design would need to freeze one of these approaches:

1. value envelopes: pass the complete immutable, versioned Primitive,
   Behaviour and Topology observations in module inputs; or
2. read-only resolver: pass a deterministic snapshot/resolver whose content is
   fixed by input IDs and watermarks, prohibits network/production access, and
   participates in the evaluation input digest.

The amendment must specify:

- payload types exposed to each runtime stage;
- ordering and version coexistence;
- missing/collision behavior;
- snapshot and watermark semantics;
- input-digest inclusion;
- replay identity;
- proof that modules cannot query mutable or undeclared state.

Separately, a shadow-data milestone must populate immutable Evidence and
Primitive coverage from already acquired artifacts, or explicitly authorize a
non-authoritative historical mirror. EP3.1 itself must not silently substitute
legacy tables for immutable Evidence.

## Recommendation

Open **EP3.0B — Immutable Runtime Observation Input Contract** to formalize the
data plane only. Do not implement WATCHTOWER semantics in that amendment.

After EP3.0B is implemented and an immutable WATCHTOWER shadow corpus exists,
resume EP3.1 unchanged. The existing EP3.0 orchestration, registry, lifecycle,
storage and non-authority guarantees can remain intact.

## Work intentionally not performed

- no WATCHTOWER Contract;
- no WATCHTOWER Behaviour Modules;
- no WATCHTOWER Detector;
- no runtime, Evidence or Primitive modification;
- no legacy database adapter;
- no production read/write integration;
- no comparison result fabricated from absent Evidence;
- no commit or push.
