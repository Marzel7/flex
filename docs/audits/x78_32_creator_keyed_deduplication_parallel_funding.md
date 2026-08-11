# X78.32 — Creator-Keyed Deduplication & Bounded Parallel Creator Funding

Date: 10 August 2026  
Scope: Creator Funding only  
Production ceiling: exactly two extraction slots  
Evidence Platform: unchanged and inactive

## Executive result

Creator Funding now claims at most one HOT row per creator in a batch, enforces a process-local creator-keyed single-flight as a defensive second boundary, and can execute exactly two different creators concurrently. All SQLite mutations continue through independent managed connections and the existing serialized write boundary.

The two-slot deployment is safe in the bounded observed window, but sustained capacity has **not** yet been proven adequate. In the final 482-second sample, 43 rows arrived while the worker had durably reported 29 completions, one retry, and zero failures. Six completions used the known-creator fast path. Some completions from the current batch had not yet reached the batch-level heartbeat, but even the conservative measured result does not satisfy the required sustained-capacity gate.

The deployment therefore remains a capacity improvement, not a readiness declaration. Evidence activation and acquisition remain on hold.

## Baseline

- Branch: `classification-attribution-axis`
- Starting HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`
- Working tree: already dirty with the preserved X78.19–X78.31 programme changes.
- Initial X78.31 HOT census: approximately 1,074 rows / 920 creators; 154 duplicate rows across 36 creators; largest creator 70 rows.
- Initial measured serial rate: approximately 128 completions/hour against approximately 405 arrivals/hour in a short sample.
- X78.31 write-lane and repeated infrastructure-scan repairs were preserved.

Supervised production ownership at final check:

| Process | PID | State |
|---|---:|---|
| Creator Funding | 87504 | RUNNING |
| Creator Resolution | 69682 | RUNNING |
| Listener | 69765 | RUNNING |
| API | 77110 | RUNNING |

Only one supervised Creator Funding process exists. The listener-owned legacy consumer remains disabled.

## Creator-scoped and mint-scoped contract

| Work | Scope |
|---|---|
| authoritative `creator_funders` existence check | creator |
| history/signature/transaction RPC | creator |
| funding and outgoing-transfer extraction | creator |
| CEX, BlockSec, Jito, deBridge, Axiom observations | creator, with mint/create-signature context where explicitly required |
| `creator_funders` persistence and funding metadata | creator |
| second-hop, risk, network membership | creator |
| intelligence refresh | global projection trigger, single-flight/debounced |
| queue terminal transition | creator + mint |
| `token_rescore_queue` handoff | mint |
| `funding_extracted_slot` | mint |

A creator extraction does not directly mark sibling mints complete. Each sibling remains queued and receives its own mint-specific terminal transition and rescore handoff. Once authoritative creator funding exists, it naturally completes through `complete_fast` without repeating creator history RPC or creator-level enrichment.

## Claim deduplication

`_select_ready_rows` now uses a deterministic `ROW_NUMBER() OVER (PARTITION BY creator_address ...)` selection and returns only `creator_rank = 1` before applying the batch limit. The final claim transaction repeats all eligibility predicates and claims only those selected rows.

Properties:

- at most one row per creator in a natural batch;
- sibling rows remain pending rather than occupying extraction slots;
- no extra transaction or race window was added;
- ordering remains newest-first within HOT, with existing priority/attempt tie-breakers;
- the sibling is eligible for the durable fast path on a later cycle.

Final HOT duplicate snapshot:

- 1,132 ready HOT rows;
- 951 distinct HOT creators;
- 181 duplicate rows;
- the largest duplicate creator had 85 rows and already had authoritative funders.

Those rows represent potential avoided deep scans, not avoided mint completion work.

## Creator-keyed single-flight

The process-local flight registry is keyed by full `creator_address`.

- A leader owns the creator flight and one extraction slot.
- A same-creator follower waits outside the slot.
- Different creators may proceed independently.
- The gate is released in `finally` on success, retry, failure, timeout, cancellation, and shutdown cancellation.
- Cancelling a follower cannot cancel its leader (`asyncio.shield`).
- Claimed-batch cancellation cancels and joins only its owned tasks.

The gate is intentionally not treated as authoritative state. Durable `creator_funders` remains the authoritative reuse predicate.

## Shared extractor state audit

| Field | Classification | Finding |
|---|---|---|
| configuration/program constants | IMMUTABLE_SHARED | safe |
| aiohttp `ClientSession` | ASYNC_SAFE_SHARED | one event loop; concurrent coroutine use supported |
| `_rpc_sem` | ASYNC_SAFE_SHARED | global request ceiling of 8 across both creator slots |
| shared acquisition client | ASYNC_SAFE_SHARED | uses the same session/global semaphore |
| `processed_creators` | ASYNC_SAFE_SHARED in current single event loop | check/add contains no await, but remains only an optimization |
| cursor manager | SERIALIZED_PERSISTENCE | creator-keyed state; DB writes remain managed |
| RPC cache | SERIALIZED_PERSISTENCE | shared cache, existing DB boundary retained |
| seen bonding curves | ASYNC_SAFE_SHARED | set operations occur on the single event loop |
| extractor-wide background-task set | SHARED_OBSERVABILITY | not used for per-job cancellation ownership |
| `ExtractionWorkScope` ContextVar | CREATOR_LOCAL | separate tasks and executor futures per extraction |

X78.14's `ExtractionWorkScope` is the decisive cancellation boundary. Each invocation receives a separate ContextVar scope; creator A timeout cancels and waits only for A's tasks/futures. The former singleton-wide task-diff cleanup is not used.

## Provider concurrency

- Both creator slots share one extractor and one `MAX_CONCURRENT_RPC = 8` semaphore.
- The semaphore limits individual shared-acquisition requests globally, not jobs.
- Two creator jobs therefore cannot multiply the existing request ceiling beyond eight.
- Retry counts, timeouts, failover, and provider ordering were unchanged.

Observed PID 87504 traffic during the bounded window:

- 1,961 RPC metric rows;
- 22,990 recorded credits;
- one HTTP 400 enhanced-address response;
- zero recorded retry-policy retries;
- average recorded latency 88.58 ms;
- maximum recorded latency 16,751.27 ms.

There was no 429/rate-limit storm. One creator reached the existing 90-second job timeout, cleaned up immediately, and was retried. The other slot continued and completed unrelated work.

## Database safety

No SQLite connection or transaction is shared between slots. Existing managed connections and `DatabaseWriteService` serialization remain authoritative.

Final serializer snapshot:

| Metric | Value |
|---|---:|
| p50 wait | 0.00 ms |
| p95 wait | 0.48 ms |
| p99 wait | 51.93 ms |
| average commit | 1.09 ms |
| p95 commit | 0.80 ms |
| p99 commit | 4.82 ms |
| serializer depth | 0 |
| maximum observed depth | 3 |

No nested-write, long-holder, or queue-transition error appeared after PID
87504 started. One best-effort second-hop-lite enqueue reported `database is
locked`; the authoritative funding and mint terminal writes had already
committed, the worker continued, and platform DB health remained HEALTHY. This
transient is retained as an operational observation rather than hidden or
misreported as output loss in the authoritative funding path.

## Deployment phases

### Phase 1 — dedupe, one slot

PID 86977 started with `extraction_slots=1`. Deterministic claim tests passed and natural batches contained unique creators. A short window recorded four full completions while eleven new rows arrived. Serial capacity was still insufficient, so the implementation proceeded to the already-tested two-slot gate.

### Phase 2 — exactly two slots

PID 87504 started with `extraction_slots=2`.

The first natural pair demonstrated real overlap:

- creator `3uwqjWcDzpyM…` started;
- creator `7Gd8Q5RVDkji…` started concurrently;
- `7Gd8…` completed in 2.2 s and its slot admitted `78vj…`;
- `3uwqj…` completed independently in 26.5 s;
- all persistence and enrichment completed without cross-cancellation.

No stop condition fired.

## Bounded capacity and freshness sample

Final worker heartbeat at 482 seconds:

- claimed: 35;
- completed: 29;
- fast completed: 6;
- retried: 1;
- failed: 0;
- active slots: 2;
- active creators: 2;
- arrivals since start: 43.

Conservative heartbeat-attributed rates:

- completions: approximately 217/hour;
- arrivals: approximately 321/hour.

The short window is affected by batch-level counter publication and one 90-second outlier. It proves real acceleration and isolation, but does not prove `completion capacity >= arrival rate`.

HOT state at the final queue sample:

| Age | Pending/retry rows |
|---|---:|
| <15m | 43 |
| 15–60m | 186 |
| 1–3h | 566 |
| 3–6h | 337 |

- ready HOT depth: 1,120;
- oldest ready HOT: approximately 5.87 hours;
- depth fell modestly, but 700 stale rows were also expired during the window,
  so that reduction cannot be attributed to completion capacity alone.

This is not freshness success. A longer natural window is required, and the configured ceiling must remain two slots unless a separately approved milestone changes it.

## Platform stability

Final health snapshot:

- Database: HEALTHY, p99 51.93 ms.
- API: HEALTHY, zero errors in five minutes.
- Listener/ingestion: HEALTHY; both feeds connected; ingestion queues empty.
- Creator Resolution: progressing, zero failed.
- Operational snapshot: FRESH.
- Missing creators in last hour: zero.
- WAL: 48.3 MB, below the 64 MB alert threshold and owned by the existing
  watchdog/checkpoint policy.
- Creator Funding: WARNING because oldest HOT age exceeds its freshness threshold, not because the worker is stalled.

## Validation

- 38 focused tests passed.
- Deterministic coverage includes unique-creator batch selection, sibling retention, existing-authoritative zero-deep path, different-creator overlap, same-creator single-flight, retry isolation, cancellation cleanup, X78.14 task/future ownership, X78.17 read boundary, X78.31 fast paths, X78.30 HOT selection, X78.29 accounting, infrastructure separation, and intelligence-refresh single-flight.
- Python compilation and `git diff --check` passed.
- No full regression suite was run.

## Verdicts

- Creator-keyed claim deduplication: **IMPLEMENTED AND VALIDATED**
- Same-creator deep-extraction invariant: **PROVEN IN TESTS; NO PRODUCTION VIOLATION OBSERVED**
- Two-slot safety: **BOUNDED PRODUCTION SAFETY PASS WITH ONE NON-AUTHORITATIVE ENQUEUE TRANSIENT**
- Output semantics: **UNCHANGED**
- Database write boundary: **PRESERVED**
- Provider ceiling: **PRESERVED AT 8 REQUESTS**
- Serial capacity after dedupe: **INSUFFICIENT**
- Two-slot capacity: **IMPROVED, NOT YET SUSTAINABLY SUFFICIENT**
- HOT queue: **ROUGHLY STABLE, FRESHNESS NOT RECOVERED**
- Production services: **HEALTHY; CREATOR FUNDING REMAINS WARNING**
- Evidence activation: **HOLD**
- Acquisition: **HOLD_ACQUISITION**

## Final decision

Keep creator-keyed deduplication and the bounded two-slot worker enabled. Do not increase beyond two slots. Do not claim capacity or freshness recovery from this short window. Continue measuring natural arrivals, completions, HOT depth, and oldest-HOT age; Evidence activation remains blocked until a sustained window demonstrates a stable or catching-up queue without platform-health regression.
