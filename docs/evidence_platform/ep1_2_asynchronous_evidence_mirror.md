# EP1.2 — Asynchronous Evidence Mirror

EP1.2 is the first live producer connection to the isolated Evidence Platform.
It passively copies completed responses from EP1.1 shared acquisition. Creator
funding remains authoritative and no production consumer reads Evidence.

## Runtime topology

```text
RPC provider
    |
    v
EP1.1 SharedTransactionAcquisition
    |                         \
    | authoritative response  \ non-waiting copy
    v                           v
Creator Funding          bounded mirror handoff
                              |
                              v
                    content-addressed artifact
                              |
                              v
                     EP1.0 durable intake
                              |
                              v
                  EP1.0 single writer (separate)
```

The mirror contains no RPC client. It cannot request, retry, paginate or fail
over against a provider. It only receives responses already acquired by EP1.1.

## Feature flags

All existing flags remain off by default. EP1.2 adds:

```text
EVIDENCE_MIRROR_ENABLED=0
EVIDENCE_MIRROR_BUFFER_SIZE=1000
EVIDENCE_MIRROR_RETRY_SECONDS=1.0
EVIDENCE_MIRROR_SPOOL_PATH=database/evidence_platform/mirror_spool
```

Live mirroring requires all of:

```text
EVIDENCE_PLATFORM_ENABLED=1
EVIDENCE_MIRROR_ENABLED=1
EVIDENCE_QUEUE_ENABLED=1
EVIDENCE_ARTIFACT_STORE_ENABLED=1
```

The Evidence writer remains an independent process and does not need to be
available for producer handoff.

## Acquisition envelope

Each completed provider response is represented by a writer-compatible EP1.0
envelope with `payload_type=acquisition/response`. The immutable acquisition
section contains:

- acquisition and correlation identities;
- provider, method and operation-neutral purpose;
- creator and launch correlation context;
- transaction signatures present in the request or normalized response;
- cursor, retry count and cache state;
- sanitized request digest and response digest;
- acquisition timestamp and raw parser version;
- content-addressed artifact reference.

Provider credentials are removed from URLs before request identity is computed
or persisted. Under the EP1.3B amendment, new acquisitions retain the exact
provider response body already read by EP1.1 and mark it
`EXACT_PROVIDER_ARTIFACT`. This adds no provider request and does not change the
parsed response returned to creator funding. Parsed-only inputs and historical
EP1.2 artifacts remain valid, are never rewritten, and are identified as
`CANONICALIZED_RESPONSE_REPRESENTATION` (or by the absence of the newer marker
for pre-amendment envelopes). No fact normalization or interpretation occurs.

## Replay contract

The intake queue and artifact store are durable and idempotent. Envelope/message
identity is derived from acquisition identity, provider attempt, retry count and
response digest.

If artifact or intake publication fails, the complete `MirrorItem` is written to
the dedicated mirror spool. Replay reconstructs the same artifact and envelope
without invoking acquisition or RPC. Successful replay removes the spool entry;
failed replay leaves it intact.

The normal acquisition path performs only a bounded `put_nowait`. If that
bounded handoff is already full, the event is synchronously committed to the
small durable emergency spool instead of waiting for Evidence writer, artifact,
or detector work. This exceptional handoff is measured as back-pressure and
never silently discarded. A storage failure is exposed as `mirror_dropped` and
degraded health.

## Health and metrics

The isolated Evidence health surface now includes `mirror` with:

- in-memory queue depth and capacity;
- durable spool depth;
- last successful publish and last error;
- mirror counters and latency distributions.

Metrics include:

- `mirror_handoff`, `mirror_published`, `mirror_failures`;
- `mirror_retries`, `mirror_backpressure`, `mirror_spooled`;
- `mirror_dropped`, `mirror_recovered`, `mirror_replay_failures`;
- producer handoff, publish latency and freshness distributions.

Production RPC metrics are not changed.

## RPC equivalence

The EP1.2 observer subclasses the EP1.1 transport and calls the exact EP1.1
`request_once` implementation once. Only after that call completes is its
returned response passed to `publish_nowait`.

Tests prove, with the mirror both disabled and enabled:

- the provider call count remains one;
- request payload and response are unchanged;
- the mirror module contains no HTTP/RPC client;
- replay creates no provider call;
- EP1.1 retry/failover equivalence tests remain unchanged.

Therefore EP1.2 adds zero RPC requests, duplicate acquisitions and RPC credits.

## Performance report

Normal producer work is bounded to:

1. construct an immutable mirror item from the completed response;
2. perform `Queue.put_nowait`;
3. record a process-local handoff sample.

Artifact compression, filesystem intake, retry and replay execute on the daemon
mirror thread. A deterministic slow-artifact test injects 200 ms of persistence
latency and verifies producer handoff remains below 50 ms. No database, queue,
artifact or writer wait exists in the normal acquisition path.

## Failure report

| Failure | Production effect | Mirror behavior |
|---|---|---|
| Mirror disabled | None; exact EP1.1 transport | No files or threads |
| Writer stopped | None | Intake remains pending |
| Intake unavailable/full | None | Complete item spooled; retry counted |
| Artifact unavailable | None | Complete item spooled; health degraded |
| Handoff buffer full | No Evidence-component wait | Emergency durable spool |
| Replay failure | None | Spool retained; failure counted |
| Spool storage failure | None | Explicit dropped counter and degraded health |

No failure changes creator-funding output, production database state, queue
ownership, retries, cache, pagination, timeout or Operation behavior.

## Validation

```bash
python -m pytest -q tests/test_ep1_2_asynchronous_evidence_mirror.py
python -m pytest -q tests/test_ep1_1_shared_transaction_acquisition.py tests/test_ep0_1_compatibility_freeze.py tests/test_ep1_0_evidence_foundation.py
python -m pytest -q tests/test_x78_0_creator_funding_lease_poisoning.py tests/test_x78_2_job_boundary_regression.py tests/test_x78_2_detached_descendant_reproduction.py tests/test_x78_3_rpc_cache_nested_write_reproduction.py tests/test_x78_4_write_retry.py tests/test_x78_4_cancellation_grace_period_reproduction.py tests/test_x78_4_process_job_end_to_end.py tests/test_x76_3_extractor_concurrency.py tests/test_x78_3_sequential_stress.py
```

Disabling `EVIDENCE_MIRROR_ENABLED` returns the factory to the concrete EP1.1
`SharedTransactionAcquisition` class and requires no cleanup or migration.
