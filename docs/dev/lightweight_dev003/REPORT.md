# FLEX LIGHTWEIGHT DEV003 — Compact RPC telemetry

## FLEX_LIGHTWEIGHT_DEV003_STATUS

QUALIFIED_NOT_DEPLOYED — isolated DEV implementation and provider-free qualification complete. No production database writes, schema migration, flag changes, service restarts, provider calls, live replay, raw retirement or compaction. `RPC_COMPACT_ENABLED` defaults to0. No module import opens a database or starts a worker.

Branch: `codex/lightweight-dev003-compact-telemetry`. Frozen baseline is peeled FLEX_STABLE_V2 `2d825bbcf27f802bd0bfcef90cedeba43cc968eb`; contract parent is DEV002 `b9f9b6c4f13d0d606b5ca15a18250d76997d04d6`. Runtime deployment gate: QUALIFIED_NOT_DEPLOYED; no production deployment was authorized or attempted.

## EXACT_SOURCE_AND_SCHEMA_DELTA

Candidate adds `src/metrics/rpc_compact.py` and `rpc_compact_api.py`; patches the existing recorder, RPC metrics API, savings/efficiency helpers and their Flask registration in `src/core/main.py`. The same narrow patch is included in each self-contained frozen listener/resolver/Walkback package, preserving each package's own baseline. It is not a whole-tree replacement. `source_identity.json` records installed/candidate hashes; read-only patch checks pass against all three installed services, whose files remained unchanged throughout qualification.

Explicit DEV-only migration creates six tables: rpc_compact_meta, rpc_compact_counts, rpc_compact_cells, rpc_compact_days, rpc_compact_resets and rpc_compact_pins. It adds two owned-table indexes (expiry and overflow) and seven triggers (two transactional dimension-count triggers; five source identity/immutability/pin guards). It changes no rpc_metrics columns and replaces no existing SQL views. Installation is explicit, atomic and idempotent; the recorder hook never installs a schema. OPS is untouched.

A final mode=ro metadata check at2026-10-22T20:52:14Z verified authoritative Core inode549513179, schema_version849,17 telemetry columns and15 RPC views. Accepted view definitions match the DEV002 evidence. No production telemetry rows were sampled during DEV003. Existing source/schema metadata is context, not a claim that production backfill has been certified.

## SUMMARY_CONTRACT

Seven elapsed days remain the supported detailed-query window. Raw request persistence, queue limits, existing flush interval/size and priority/write-lane ownership remain in place. No raw purge is implemented: historical records remain present. Compact mode rejects rolling historical queries beyond168h instead of returning truncated values. A new `/metrics/rpc/database/daily?start_day=YYYY-MM-DD&end_day=YYYY-MM-DD` endpoint supplies explicit UTC day-aligned timestamp summaries; savings/efficiency daily reports preserve DATE(recorded_at).

Separate timestamp and recorded_at populations retain independent daily/lifetime cells. Joint dimensions preserve section, provider, method, source_file, optimization_layer, HTTP status_code and cache_action within declared bounds. Mergeable counters preserve requests, paid calls, error/429 totals, internal spent/saved credits, positive-credit statistics, latency count/sum/min/max, cache-action counts and savings, and first/last clocks/dates. Retired detail could not recreate raw errors, PIDs or exact request ordering; this DEV retires none.

Thirty-day reporting preserves31 inclusive UTC date labels, including the partial current day, matching the existing default days=30 API. Six preceding observed global day totals per clock seed rolling trends; these can be older than36 calendar days when history has gaps. Lifetime observed-day membership uses bounded bitmaps, not elapsed-date-span estimates. Old day summaries expire in bounded owned-table batches; lifetime counters and explicitly named checkpoints do not expire with those summaries.

Long labels, reserved labels, unsupported status labels or exhausted dimension capacity route into explicit overflow bins. Global counters remain exact; affected dimension breakdowns visibly lose detail. Known cache actions retain separate overflow bins so cache filters and hit/savings totals remain meaningful. JSON report metadata and Flask `X-RPC-Dimensions-Overflow` headers expose overflow; incomplete state reports unknown rather than false.

## LIFETIME_COUNTER_INTEGRITY

Persisted source row IDs define event identity. Counter updates and a monotonic high-water cursor share one SQLite savepoint/transaction. A retry after rollback resumes the same prefix; a retry after commit skips it. Gaps are supported. Two distinct persisted rows are two events, including actual provider retries; this does not invent unavailable network-request identifiers or deduplicate billable retries.

Within each slice, repeated keys and reset updates are coalesced in memory. Source rows and summary rows are never unioned for the same report: recent queries use detail, daily historical queries use certified day cells, and all-time queries use certified lifetime cells. Read snapshots freeze cursor/readiness/counters together. A committed unprocessed tail produces explicit BACKFILL_INCOMPLETE, never partial totals. Materialized source identities/values are fenced against mutation; pinned rows are protected before cursor coverage as well.

Lifetime scope is **retained instrumented history**, not all requests ever made. Missing/deleted/uninstrumented earlier history is not fabricated. Global integer overflow fails closed. Dimension overflow does not reset lifetime accounting.

## RESET_BASELINE_INTEGRITY

Reset timestamp boundary, elapsed-time origin and provider baseline remain separate values. Compact-mode recorder writes publish them atomically, and snapshot reads cannot combine generations; feature-off persistence order/behavior remains unchanged. Failed generation writes roll back, close the existing write-lane connection and surface HOLD. Counter readers reject an uncertified generation.

An existing accepted reset can seed initial backfill. Later resets bootstrap through the existing timestamp index in bounded slices, splitting timestamp ties by row ID. Bootstrap IDs <= captured watermark and subsequently summarized IDs > watermark are disjoint. Late inserted events are included only if their event timestamp satisfies the original strict reset predicate. Named historical checkpoints remain bounded/protected; only the existing single active recorder checkpoint follows a new reset. No provider polling is introduced.

Recorder reset savings preserve every non-none cache action, including full_scan/custom actions, rather than counting only skip/refresh savings. External provider baselines and internal credit accounting retain their distinct meanings. Existing daily/monthly projections retain their elapsed-time denominators and response aliases.

## API_COMPATIBILITY

The existing savings/efficiency helper functions return their existing dataclass fields. Compact-mode adapters reproduce the ten qualified daily/lifetime view calculations with their existing SQL null handling, integer division, COALESCE, ratio definitions and rounding. View-definition fingerprints prevent silent semantic drift. Existing source latency remains the unweighted average of per-section means; it is not silently replaced with a globally weighted mean. Floating regrouping is mathematically equivalent, with normal floating-point precision limits; no bitwise equivalence for arbitrary latency histories is claimed.

The database API's all-time helper uses durable timestamp counters in compact mode. Existing current-detail endpoints and session/deque p95 behavior remain. Legacy destructive telemetry reset controls explicitly return unsupported in compact mode; current baseline-reset controls remain available. Incomplete summaries, generation mismatch, changed view contracts and exceeded bounds return explicit HOLD/unsupported states rather than empty metrics. Unrelated Flask routes are unaffected.

Existing SQL views remain raw-backed for direct SQL clients. This milestone does not authorize removing their raw inputs. A later retirement milestone must account for any direct SQL consumers; the qualified API adapters do not claim arbitrary external SQL has been migrated.

## HISTORICAL_WINDOW_BEHAVIOUR

* Rolling <=168h: existing detailed semantics and clock predicates.
* Beyond168h: explicit UNSUPPORTED_HISTORICAL_ROLLING_WINDOW, with day-aligned alternative.
* Historical daily: canonical YYYY-MM-DD inputs, UTC alignment, inclusive current plus30 preceding date labels; greater horizons explicitly unsupported.
* Lifetime/reset totals: durable certified counters, independent of summary expiry.
* Old reset dimensional rankings/arbitrary historical test intervals: not reconstructed from daily totals; unsupported where the detail contract cannot be honored.
* Historical p95: explicitly unsupported; current session percentile behavior remains. Count/sum/min/max cannot reconstruct its distribution.

## BACKFILL_FEASIBILITY

Backfill reads INTEGER PRIMARY KEY keysets, never OFFSET or a full database copy. Default slice16 rows; qualified maximum128 rows. Existing flusher integration permits at most four such slices/512 rows per flush with a cooperative250ms work budget; individual collection slices use50ms checks. Lock waiting remains governed by the existing bounded SQLite/write-lane timeouts. A single bounded operation can finish after the cooperative clock check; these are not real-time scheduling guarantees.

Source field widths are bounded in SQL projections; unsupported stored types or overlong recorded clocks produce HOLD instead of lossy certification. One slice holds at most512 pending cells and at most256 observed clock/day memberships. Reset index reads additionally have50ms/100,000-VM-step cancellation budgets. Expiry handles at most128 old day cells per pass. There is no autonomous historical acquisition, provider replay or added background service.

SQLite rollback/restart tests include process exit before commit and immediately after commit. Prefix guards, dimension-count metadata, lifetime/day cells and cursors recover together. A full production backfill was neither attempted nor certified. Future activation must keep compact readers disabled until explicit migration, initial checkpoint/pin setup and bounded backfill certification succeed; the implementation never deletes raw history to catch up.

## PROVIDER_FREE_TEST_RESULTS

**43 tests passed;0 failures;0 errors;0 skipped.** Command:

`/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python -m unittest discover -s tests/lightweight -v`

Coverage includes the retained22 algebra/equivalence checks on explicit fixture populations, plus actual compact-view comparisons, inclusive detail/day boundaries, trend context, lifetime day membership, independent clocks, late rows, retries/gaps, crash/rollback/restart, dimension overflow, provider/method/status retention, unsupported windows/p95, pins, actual savings/efficiency helper return types, Flask metadata, FastAPI guard bodies, generation atomicity, source fencing, storage budgets and WAL behavior. Fixtures are synthetic/retained test values, not invented production history. Application servers and provider clients are never started.

Measured final run:500-row flush processed500 rows in0.054755s;4,200-dimension fixture database15,728,640bytes with11,483,284 encoded summary bytes; WAL412,032bytes with a reader held open. Measurements are fixture evidence, not production throughput/space forecasts. All modified source/package files pass syntax checks; narrow read-only patch checks against installed corrections pass.

## STORAGE_BOUNDS

New aggregate encoded payload hard ceiling24MiB; each cell <=4096 encoded bytes; dimension key <=768 encoded characters, with label byte limits; <=4096 keys per clock/scope/day including overflow reservations. O(1) transactional group counts avoid a per-event dimension scan. Day cells retain31 labels, seed context6 observed days per clock, lifetime keys remain capped. Bitmaps use fixed512-byte chunks within SQLite's date domain. Pins <=1024 with bounded reasons; reset checkpoints <=30 with <=8192-byte payloads. Capacity failure pauses summary certification and preserves raw persistence.

No new standalone persistent data file is added. Report sorting/grouping uses an in-memory SQLite workspace with temp_store=MEMORY. The summary writer pauses at an observed shared-WAL size of64MiB; this is a refusal threshold, not a global WAL cap on unrelated existing writers. No explicit checkpoint is issued. No fixture or newly created artifact approached500,000,000bytes.

New unbounded storage paths:0. The pre-existing raw append path remains intentionally present because deletion is not authorized. Seven-day detail is the query/retention contract, not a claim that older raw rows have been removed. Hard encoded limits do not equate to exact SQLite allocated-page limits. No filesystem space was recovered; internal reuse and physical shrinking remain separate future milestones.

## CANDIDATE_SHA

a7029be2ad41eed49b944d455855a8116c00acd7

This is the qualified executable-source/test commit. A subsequent report-only commit does not alter the candidate code.

## READY_FOR_DEPLOYMENT_PREFLIGHT

YES — source candidate ready for a separately scoped deployment preflight. This is not approval to install schema, enable the flag, run production backfill, restart services, deploy, retire rows or compact databases. No production functional or throughput acceptance is claimed.

## NEXT_ACTION

Review this candidate and perform deployment preflight as a separate authorized milestone, preserving installed overlays and defining migration/backfill/pin/rollback acceptance before any activation. Production history remains untouched.
