# EP1.3 — Immutable Evidence Normalization

Status: **IMPLEMENTED**

EP1.3 transforms retained acquisition artifacts into operation-neutral,
immutable Evidence. The Evidence Platform remains isolated and
non-authoritative. No production consumer reads normalized Evidence.

## Runtime path

```text
Shared acquisition
    ↓
Asynchronous mirror
    ↓
RawArtifact
    ↓
AcquisitionNormalizer
    ↓
EvidenceRecord + EvidenceProvenance
```

Normalization executes only when `EVIDENCE_NORMALIZATION_ENABLED=1`; the flag
defaults off. It runs under the existing single Evidence writer's database
ownership. It contains no HTTP client, RPC method, production database import,
Operation detector, primitive, governance action or projection.

## Fact families

The frozen version-1 contract is implemented for:

1. `TransactionFact`
2. `AccountParticipationFact`
3. `InstructionFact`
4. `BalanceFact`
5. `NativeMovementFact`
6. `TokenMovementFact`
7. `AccountCloseFact`
8. `ProgramEventFact`
9. `LaunchFact`
10. `AddressHistoryObservation`
11. `TransactionVerificationObservation`
12. `ExternalRegistryObservation`

Contract field allowlists reject semantic or unapproved fields. Objective
decoding retains raw instruction data alongside decoded fields. Movement facts
are emitted only from explicit decoded instructions; balance-delta inference is
not performed. Launch facts require an objectively decoded creation event with
a mint and creator.

## Identity and provenance

The implementation uses the unchanged EP1.3B identities:

- `logical_fact_id` identifies what happened and excludes provider/parser data;
- `evidence_id` identifies one artifact/parser-scoped immutable observation.

Every record includes the fact and parser schema versions, normalized payload
digest, raw artifact digest, verification state, source/provider identity and
complete acquisition provenance. Each record references exactly one artifact.
Exact and historical canonicalized artifacts expose distinct provenance quality
states; original bytes are never fabricated.

## Storage and immutability

The isolated Evidence database schema version 2 adds:

- `normalized_evidence_records`;
- `normalized_evidence_provenance`;
- `normalization_status`.

Records and provenance are append-only and protected by update/delete triggers.
Status is deliberately separate mutable workflow state. One fact append
transaction inserts Evidence, inserts provenance, marks completion and commits.
An identity collision with non-identical content aborts the transaction.

## Status workflow

Status supports:

```text
PENDING · RUNNING · COMPLETE · FAILED · UNSUPPORTED · RETRY
```

It records parser/schema versions, attempts, error, artifact representation,
start/completion times and fact count. A malformed artifact fails independently;
the raw envelope remains committed, unrelated batch items continue, and the
failure is observable. Unsupported acquisition methods are classified without
poisoning intake.

## Replay and disagreement

- Same artifact + same parser produces identical payloads and Evidence IDs.
- Reprocessing inserts no duplicate immutable records.
- A parser upgrade produces new Evidence IDs while retaining logical IDs.
- Provider disagreement retains separate observations with a common logical ID.
- Neither parser nor provider observations overwrite prior Evidence.
- Replay reads only the retained Artifact Store and performs no RPC.

## Health and metrics

The standalone Evidence health model exposes status counts, artifacts awaiting
normalization, parser/schema versions, legacy-artifact ratio, throughput,
latency, fact generation, replay, malformed, unsupported and failure counters.
It remains disabled with the Evidence Platform by default.

## Performance report

Normalization is downstream of the durable mirror and cannot add producer
handoff latency or RPC credits. Focused deterministic fixtures normalize a
transaction containing accounts, balances, native/token movements, close and
creation events within the existing local test gate. Metrics record per-artifact
latency and generated facts so production-like capacity can be characterized
without changing acquisition.

No optimization, concurrency expansion or replay optimization is included.

## Validation

```bash
python -m pytest -q tests/test_ep*.py
python -m pytest -q tests/test_x78_0_creator_funding_lease_poisoning.py \
  tests/test_x78_2_job_boundary_regression.py \
  tests/test_x78_2_detached_descendant_reproduction.py \
  tests/test_x78_3_rpc_cache_nested_write_reproduction.py \
  tests/test_x78_4_write_retry.py \
  tests/test_x78_4_cancellation_grace_period_reproduction.py \
  tests/test_x78_4_process_job_end_to_end.py \
  tests/test_x76_3_extractor_concurrency.py \
  tests/test_x78_3_sequential_stress.py
```

The EP0 compatibility fixture gate remains the production behavior authority.
With Evidence flags off, runtime behavior is unchanged.
