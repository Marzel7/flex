# EP1.1 — Shared Transaction Acquisition Layer

EP1.1 extracts creator funding's existing blockchain transport into an
operation-neutral boundary. It does not produce Evidence and does not change
creator-funding interpretation, queue ownership, worker lifecycle, RPC policy,
or any Operation.

## Architecture

```text
creator_funding_worker
        |
        v
RealTimeCreatorFundingExtractor (authoritative interpretation)
        |
        v
SharedTransactionAcquisition
  - provider sequence
  - request execution
  - retry/failover policy
  - timeout and concurrency bounds
  - response normalization
  - cache interface
  - RPC metric emission
  - correlation telemetry
        |
        v
existing Helius/Public Solana endpoints

Evidence Platform (EP1.0)     [disconnected]
```

SNS/domain enrichment is intentionally outside this boundary: it is metadata
enrichment, not blockchain transaction acquisition.

## Compatibility adapter

`RealTimeCreatorFundingExtractor._post_rpc` remains available with its existing
return contract and delegates transport to `json_rpc_legacy`. Existing parsing,
funding classification, pagination stop conditions, cache keys and values,
database writes, and post-extraction enrichment remain in the extractor.

The legacy policy is preserved:

- endpoint order: Helius, then public Solana;
- five outer attempts;
- 30-second JSON-RPC timeout;
- eight-request semaphore;
- retryable HTTP/RPC status handling and exponential waits;
- identical JSON-RPC payloads;
- existing enhanced-history page sizes and cursors;
- existing one-attempt semantics for enhanced HTTP and enrichment probes;
- existing `RPCCache` keys, values and hit/miss behavior;
- existing RPC metric section, provider, method, cache and credit fields.

## Correlation telemetry

Every executed transaction request receives a UUID acquisition identity and an
immutable metadata record containing:

- purpose;
- creator and launch context;
- request type, provider and method;
- page number and cursor where applicable;
- request timestamp;
- cache state;
- retry count.

The same acquisition identity is retained across failover/retry attempts for a
logical JSON-RPC request. Metadata is task-local via `contextvars`, so concurrent
future consumers cannot overwrite each other's correlation context.

This metadata is transient operational telemetry. It is not an Evidence fact,
is not persisted to the Evidence database, and is not sent to the EP1.0 queue.

## RPC equivalence report

Deterministic transport tests verify:

- identical provider request order;
- identical request count on success, failover and non-retryable failure;
- identical payload object at each provider;
- identical retry index in RPC metrics;
- one correlation identity across attempts;
- unchanged cache get/set contract;
- unchanged normalized successful output.

No live RPC replay is used by the test suite, so validation consumes zero RPC
credits. Because payloads, page sizes, cursor order, stop conditions, provider
order and retry conditions are unchanged, production RPC request and credit
counts are unchanged by construction.

## Performance comparison

The hot path replaces inline `aiohttp` orchestration with one local async method
call plus creation of a frozen metadata object. It adds no network request,
sleep, database operation, queue operation, artifact write or Evidence write.
Network timeout and concurrency bounds are unchanged. The deterministic suite
therefore gates structural overhead while production latency remains governed by
the same remote requests and retry waits.

## Safety and rollback

- EP1.0 files and feature flags are unchanged.
- No `src.evidence` import exists in the acquisition package.
- No new worker, queue, RPC endpoint, or producer is introduced.
- Creator funding remains authoritative.

Rollback is a code-only reversion to the inline request implementation; no data,
schema, queue or Evidence cleanup is required.

## Validation

Run:

```bash
python -m pytest -q tests/test_ep1_1_shared_transaction_acquisition.py
python -m pytest -q tests/test_ep0_1_compatibility_freeze.py tests/test_ep1_0_evidence_foundation.py
python -m pytest -q tests/test_x78_3_sequential_stress.py tests/test_x76_3_extractor_concurrency.py tests/test_x78_4_process_job_end_to_end.py
```
