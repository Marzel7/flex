# EP2.0 — Generic Primitive Engine

Status: **IMPLEMENTED — PRIMITIVE CONTRACT V1**

EP2.0 transforms immutable normalized Evidence into deterministic,
operation-neutral Primitive Observations. It does not establish identity,
topology, attribution, confidence or governance, and no production consumer
reads primitives.

## Approved scope

Primitive Contract v1 contains exactly the eleven types frozen by X78.26:

1. `SYSTEM_TRANSFER`
2. `LAUNCH_SIGNER`
3. `WSOL_CLOSE`
4. `DIRECT_COUNTERPARTY`
5. `PROGRAM_INTERACTION`
6. `WALLET_FRESH_AT_EVENT`
7. `LAUNCH_ACTIVATION`
8. `ECONOMIC_FUNDING`
9. `SHARED_TRANSACTION`
10. `REPEATED_COUNTERPARTY`
11. `BEHAVIOURAL_TIMING`

The following are recorded as deferred candidates and are not implemented:

```text
TOKEN_TRANSFER · ACCOUNT_CREATION · TRANSACTION_SIGNER · FEE_PAYER
LAUNCH_CREATOR · ACCOUNT_CLOSE · PROGRAM_REUSE
```

Their absence is not an EP2.0 failure. They require a future consumer-backed
contract before becoming first-class primitives.

## Runtime boundary

```text
normalized_evidence_records (read only)
    ↓
PrimitiveEngine v1
    ↓
primitive_observations (append only)
    └─ primitive_evidence_inputs (append only)
```

`EVIDENCE_PRIMITIVE_ENGINE_ENABLED` defaults off. When explicitly enabled, the
engine runs under the isolated Evidence writer's existing database ownership
after normalization. It imports no acquisition, RPC, production database,
Operation, detector, creator-funding, walkback or governance component.

## Primitive observation contract

Every observation contains:

- deterministic primitive ID;
- primitive type and version;
- ordered immutable Evidence IDs;
- subjects and explicit parameters;
- observation window;
- output payload and digest;
- one closed quality state;
- missing inputs and failure state;
- generation timestamp.

Primitive identity is the SHA-256 digest of canonical JSON containing primitive
type, version, parameters, ordered Evidence IDs and output payload. Generation
time is deliberately excluded. Replaying identical Evidence therefore creates
the same ID and payload and inserts no duplicate.

## Quality contract

Only these states exist:

```text
PROVEN · DISPROVEN · INCOMPLETE · CONFLICTING · UNVERIFIABLE
```

There is no confidence or probability. Conflicting provider observations remain
immutable Evidence and cause dependent primitives to carry `CONFLICTING` rather
than choosing a provider. Missing evidence creates an `INCOMPLETE` or
`UNVERIFIABLE` observation with explicit missing inputs.

## Parameter discipline

Operation thresholds are prohibited. `WALLET_FRESH_AT_EVENT` records its
reference-event freshness policy. `ECONOMIC_FUNDING` uses an explicit
`UNFILTERED` amount policy and reports timing relative to the launch; it does not
define “large.” `REPEATED_COUNTERPARTY` records its count parameter, and
`BEHAVIOURAL_TIMING` records event scope and ordering.

## Storage and versioning

Evidence database schema version 3 adds append-only primitive observation and
Evidence-link tables. Update/delete triggers protect both. Primitive versions
coexist: changing logic requires a new version and never overwrites v1.
Identity-collision validation compares the complete stored observation, not only
its output digest.

## Replay, failures and health

Replay reads normalized Evidence only. Each generator is isolated so malformed
or unsupported Evidence for one primitive cannot poison unrelated primitive
generation. Health exposes backlog, type/version counts, closed quality-state
counts, throughput, latency, generation, replay and failure metrics.

## Performance

The engine performs one deterministic ordered Evidence read followed by one
append transaction. It performs zero RPC and does not touch the Artifact Store
or production databases. The test fixture exercises all eleven primitives,
replay and a second primitive version. Runtime metrics retain latency and fact
generation rates for capacity characterization; no optimization or concurrent
worker architecture is introduced in EP2.0.

## Validation

```bash
python -m pytest -q tests/test_ep*.py tests/test_ep2_0_generic_primitive_engine.py
```

EP0–EP1 compatibility tests remain authoritative for production equivalence.
All Evidence flags remain off by default and no primitive is authoritative.
