# EP3.0C — WATCHTOWER Shadow Corpus Materialization

Status: **BLOCKED — CORPUS MATERIALIZED, ACCEPTANCE NOT MET**

Update: the two generic software blockers documented below were resolved by
EP3.0D. See
`ep3_0d_generic_evidence_compatibility_launch_materialization.md`. Missing
historical artifacts remain an explicit data-availability constraint.

EP3.0C was executed against the frozen comparison population using only local,
previously acquired transaction-cache bodies. It performed no RPC, detector,
runtime evaluation, governance action, identity change, or production write.
The isolated corpus is stored locally under
`database/evidence_platform/watchtower_shadow/` and is intentionally ignored
by Git.

The materializer and its synthetic validation are implemented but must not be
committed as a completed EP3.0C milestone until the blockers below are resolved.

## Frozen population

| Object | Count |
|---|---:|
| Canonical treasuries | 62 |
| Approved launches | 176 |
| Provisioning edges | 5,937 |
| Canonical entities | 69 |

Population digest:
`c030deb095a59f61ce4e1e41b6b726efd0b124797d2fa82d4d886162cba4005d`

No population expansion or candidate generation occurred.

## Materialized corpus

| Item | Count |
|---|---:|
| Raw cached artifacts materialized | 4,577 |
| Required cached artifacts unavailable | 1,510 |
| Normalization complete | 4,167 |
| Normalization failed | 410 |
| Immutable Evidence records | 94,501 |
| Primitive observations | 60,436 |
| Primitive Evidence references | 444,616 |

Primitive quality:

- `PROVEN`: 32,237
- `INCOMPLETE`: 28,199
- `CONFLICTING`: 0 in the materialized source set

Primitive types materialized:

- `BEHAVIOURAL_TIMING`: 12,479
- `DIRECT_COUNTERPARTY`: 8,074
- `REPEATED_COUNTERPARTY`: 33
- `SHARED_TRANSACTION`: 4,167
- `SYSTEM_TRANSFER`: 7,485
- `WALLET_FRESH_AT_EVENT`: 25,927
- `WSOL_CLOSE`: 2,271

No approved v1 Primitive outside the immutable Evidence supplied to the engine
was synthesized.

## Deterministic replay

An independent database was rebuilt from retained content-addressed artifacts.
No source database or RPC was consulted during replay.

| Projection | Semantic digest |
|---|---|
| Evidence | `2fb059199d702ae9e02848f248633741bcfeb8ed5b9f12f2ae02d2f3ddb85031` |
| Primitives | `e20d6f765b057d5f3ceecba15d0c1a9a4442e5a74d8c1d122c8b4533031473fb` |
| Primitive inputs | `cd0b67c4c5c10b14f7dd819430bca63ec7db18119a2e1d32a7bd52e62d4b70f9` |

All three replay digests matched. Replay used zero additional RPC and took
147,886 ms. Initial Primitive generation took 78,812 ms.

## Blocking defects

### 1. Integer-only Evidence normalization rejects provider token metadata

All 410 normalization failures have the same cause:

```text
$.parsed_fields.tokenAmount.uiAmount uses float;
immutable Evidence requires integer units
```

The raw cached provider payload is valid, but `InstructionFact.parsed_fields`
currently copies the provider's floating-point display value into the immutable
payload. Fixing this requires an explicitly approved parser policy (for example,
retaining raw integer amount and decimals while excluding the derived UI float).
EP3.0C may not silently change frozen Evidence semantics.

### 2. Pump creation transactions do not produce `LaunchFact`

The generic normalizer derives `LaunchFact` only when a provider instruction is
already parsed with a `create` type and explicit `mint` and `creator` fields.
The cached Pump instructions are custom/raw instructions, so the current parser
produced zero `LaunchFact` records.

Consequently no launch can produce the launch-dependent approved Primitives
(`LAUNCH_SIGNER`, `LAUNCH_ACTIVATION`, or `ECONOMIC_FUNDING`) from this corpus.
Adding a Pump instruction decoder is parser implementation work and must be
approved separately; copying legacy WATCHTOWER launch rows into Evidence would
violate the operation-independent Evidence boundary.

### 3. Required historical raw artifacts are absent

Of the transaction signatures referenced by the frozen comparison population,
1,510 have no replayable body available to this no-RPC milestone. This includes:

- 1,411 provisioning-edge references absent from the transaction cache;
- 9 provisioning-edge cache entries with no transaction body;
- 69 launch signature references absent from the transaction cache;
- 53 launch rows with no recorded source signature;
- 2 launch rows with invalid/truncated recorded signatures.

These cannot be recovered under EP3.0C's no-RPC constraint.

## Per-object readiness

- Evidence-complete launches: **0 / 176**
- Primitive-complete launches: **0 / 176**
- Ready-for-runtime launches: **0 / 176**
- Treasuries observed in the bounded transaction corpus: **5 / 62**
- Canonical entities observed in the bounded transaction corpus: **5 / 69**

Every missing launch, edge, treasury, and entity has an explicit reason in the
local `coverage.json`. No missing object is treated as observed or complete.

## Health

- Artifact Store: healthy; 4,577 artifacts and metadata records; zero missing metadata.
- Intake Queue: healthy and empty after materialization.
- Mirror: healthy; zero dropped events; 2,570 back-pressure events durably spooled and recovered.
- Evidence database: `quick_check=ok`.
- Normalization: degraded because of the 410 deterministic parser failures.
- Primitive Engine: healthy for successfully normalized Evidence.

## Required decision

EP3.0C cannot satisfy its Evidence-complete and Primitive-complete acceptance
gates under the current constraints. Do not resume EP3.1.

The next approved work must separately address:

1. the float-bearing parsed-instruction normalization defect;
2. operation-neutral Pump launch fact extraction from raw transaction artifacts;
3. authorization for historical artifact recovery if complete 176-launch
   coverage is mandatory (this necessarily conflicts with EP3.0C's no-new-RPC
   rule when no local artifact exists).

Until those are resolved, the materialized corpus is useful for coverage and
replay validation but is not a complete WATCHTOWER runtime comparison corpus.
