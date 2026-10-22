# FLEX LIGHTWEIGHT DEV002 — RPC telemetry retention contract

## RPC_TELEMETRY_LIGHTWEIGHT_CONTRACT_STATUS

PARTIAL — contract and bounded equivalence qualification complete. Seven-day detail plus compact historical counters is feasible for specified reports. Exact compatibility with every existing query window is not established and cannot be delivered by daily summaries alone. No implementation or retirement is authorized by this document.

RUNTIME_DEPLOYMENT_REQUIRED=false: documentation and isolated in-memory qualification only. No executable runtime, production schema, configuration, service, provider or production record changed.

Isolated branch: `codex/lightweight-dev002-telemetry-contract`, based on peeled `FLEX_STABLE_V2` commit `2d825bbcf27f802bd0bfcef90cedeba43cc968eb`. This branch is a contract target, not an installation target. Preserve accepted installed overlays; never replace runtime trees with the frozen checkout wholesale.

### Frozen and installed authority

At 2026-10-22T20:20:48Z Core is `/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db`, inode 549513179, 35,652,194,304 logical bytes, schema version 849, page size 4096. OPS remains `/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db`; this contract changes neither database. Actual schema metadata, ten telemetry indexes, installed hashes, reset values and query plans are retained in `evidence.json`.

Supervisor read: listener PID 63796, resolver 14452 and Walkback 13612 RUNNING. Funding, Cascade, Watchtower API and other supervised workers STOPPED. Installed roots respectively `/Users/kevinkeaveney/Production/flex-listener`, `flex-main-workers`, `flex-walkback-dev002`. Their underlying Git revisions are bf1bdfc59b4acfed0338a493189d91d212bda50f, eed84eaa0b88a3d0cd93dd9767f8b0d8680bc215 and afbe9e58b11eb2e4d616246da123ce31ab45700c; Git revisions alone do not describe accepted overlays.

All three installed recorders match their respective frozen `runtime/{listener,creator_resolution,walkback}/src/metrics/rpc_metrics_recorder.py` byte-for-byte: SHA256 `6b39824d89b7b2493e4b031449662ee70ec187837440f0bb7d8f85e529d9d46e`. Installed API files are also identical across the three roots; hashes recorded separately. Installed Core schema is authoritative over older handoff/schema expectations. Main checkout and frozen handoffs contain older context, not current runtime authority.

Telemetry columns: id INTEGER PRIMARY KEY AUTOINCREMENT; timestamp REAL NOT NULL; section/provider/method TEXT NOT NULL; status_code INTEGER; latency_ms REAL; credits INTEGER; mode TEXT DEFAULT realtime; retries INTEGER DEFAULT 0; source_file TEXT DEFAULT unknown; error TEXT; process_pid INTEGER; recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP; cache_action TEXT DEFAULT none; credits_saved INTEGER DEFAULT 0; optimization_layer TEXT DEFAULT none.

## EXISTING_API_SEMANTICS

Source paths below are relative to the installed listener root; source files were read, never imported or invoked.

| Consumer | Inputs and exact current semantics | Window / clock |
|---|---|---|
| `src/apis/rpc_metrics_api.py:53` helper; `/metrics/rpc/database` | COUNT all events; SUM credits; error COUNT status >=400; 429 COUNT; MIN/MAX timestamp; by method; by source_file and section; section totals. Source latency is the **unweighted mean of per-section AVG(latency_ms)**, with null section means converted to zero, rounded 2 places. Preserve null SUM behavior and response aliases. | Default 24, integer 1..720 hours, strict timestamp > now-hours. This endpoint's default is genuinely rolling 24h. |
| `/metrics/rpc/untracked` | Same helper without cutoff, all-time local instrumentation compared with latest external usage snapshot; retain mismatch in time scope as existing behavior. No inference that internal credits equal provider billing. | All-time timestamp population; provider snapshot has its own period. |
| `/metrics/rpc/optimizations`, `/components`; recorder get_top_methods/get_optimization_savings/get_component_breakdown | SUM spent; positive saved credits grouped layer/section; paid calls credits>0; average positive credits; top methods by credits; component filter excludes empty/null source_file. Keep joint dimensions needed for rankings. | Default hours=24 means **comparison reset**, not rolling 24h. Other hours rolling timestamp > cutoff; API 1..720h. |
| `/metrics/rpc`, summary, recorder comparisons | Cross-process DB since-reset credits/paid calls/cache skip/refresh/full_scan/savings; latest helius_usage_snapshots credits used/remaining; persisted external baseline. Daily estimate = local credits / elapsed reset hours *24; monthly = daily *30; burn/min based on same elapsed duration. Coverage and untracked are ratios/differences against provider baseline deltas, not proof of provider bill. | Timestamp > comparison_reset_timestamp; UK midnight auto-reset. Comparison elapsed uses comparison_reset_time, a separate persisted value. |
| Recorder sections/source stats | In-memory session request/error counts; section mean/p95 over last 1000 section latencies; source mean/p95 over bounded history deque. Not all-time DB latency percentiles. | Session/reset-scoped, process-local. Preserve existing deque semantics. |
| `src/apis/rpc_savings_api.py:128..344` | daily spent/saved/baseline=spent+saved; savings pct saved/baseline; skip/refresh/full_scan/none events; hit = skip or refresh (not paid-call count); section/day breakdown. | DATE(recorded_at), default days=30; inclusive day >= DATE(now,-30 days) can return **31 date labels**. days currently unbounded. |
| `v_rpc_cache_performance` | All non-none, non-null-recorded_at events by cache_action; count, saved SUM/AVG/MIN/MAX with COALESCE(saved,0), first/last DATE; efficiency_per_event uses existing SQLite arithmetic. | All time, not restricted by dashboard days. |
| `v_rpc_cumulative_savings` | All recorded_at-not-null events: spent/saved/baseline; counts, cache hits; skip/refresh counts and savings; date min/max; percentages. | All time, not restricted by dashboard days. |
| `v_rpc_dashboard_24h`; `v_rpc_efficiency_24h` | Sliding 24h sums, counts, ratios. | recorded_at >= datetime(now,-24h), inclusive, SQLite UTC text semantics. |
| `src/apis/rpc_efficiency_api.py:129..301`, daily/by-section/health | SUM saved / SUM spent efficiency (different denominator from savings pct); section/day counts and hit rates; health thresholds <1/<3/<5/<10; alerts <3/<5. Preserve SQL NULL cases, including CASE ELSE behavior with null ratio. | DATE(recorded_at); daily/by-section default30, health default7; days not clamped. |
| `v_rpc_efficiency_all_time` | Lifetime sums and counts; COUNT(DISTINCT DATE(recorded_at)); first/last date; average daily credits divides by **observed days**, not elapsed calendar span. | All non-null-recorded_at rows, including older than30d. |
| `v_rpc_efficiency_trend_7day`, `v_rpc_rolling_7day_average` | Average of daily aggregates/ratios over current plus 6 preceding **observed day groups**; preserve inner versus outer rounding. | Whole-history grouped views; first displayed day needs 6 earlier observed groups, potentially older than36 calendar days. |
| `v_rpc_week_comparison`, top_savings_sections, hit_rate_trend | This/last sliding7d; top sections sliding30d; hit-rate groups with sliding30d prefilter. No direct installed Python reader found for these three or rolling/trend views; retain them as conditional SQL consumers. | recorded_at rolling boundaries, not UTC calendar boundaries. |
| Comparison test helper / daily credits | Method counts and credits on requested test interval; daily helper uses recorded_at local-midnight string rather than recorder UK timestamp. Main API usage also groups method/source. | Arbitrary test start<=timestamp<=end; do not promise reconstruction outside detail window. |

Exact arithmetic includes SQL COALESCE, COUNT versus paid-call count, filter inequalities, integer division where both operands are integer, ROUND precision and ratio-of-sums versus average-of-ratios. Do not replace SQLite integer division with floating division as part of retention. No returned percent is itself a mergeable counter. Ordering ties currently unspecified remain unspecified.

## SEVEN_DAY_DETAIL_POLICY

Proposed minimum: **168 elapsed hours of detail on both clocks**, plus explicit protected exceptions. A future retirement predicate must require timestamp < frozen cutoff AND valid recorded_at older than the corresponding cutoff AND summary certification AND absence of research/reset/in-flight pins. Equality remains retained. Null, invalid or future clocks are held for explicit qualification; age on one clock alone is insufficient.

Keep the recent detail APIs and in-memory section/history behavior unchanged. Retain provider, method, status, error text, PID, latency, retries and other row detail inside this window. Retired rows' raw error strings, per-request PID sequences and arbitrary request-level forensic chronology are not reconstructible from compact evidence; pin before retirement if required.

Seven days is sufficient for existing 5m/60m/24h operational queries, current UK-day reset comparisons and session percentile reporting **only when the actual requested/reset interval fits the retained detail window**. Seven days does not preserve arbitrary 720h detail filters. No automatic API fallback to truncated data.

## THIRTY_DAY_SUMMARY_POLICY

Proposed compact representation, not runtime DDL:

* Preserve 30 elapsed days of calendar reporting using **31 inclusive UTC DATE(recorded_at) labels** to match the current default days=30 predicate; current partial day explicitly marked partial. If product instead wants exactly30 labels, that is a visible API change.
* Store separate event-clock reporting slices when timestamp-based whole-day reporting is needed. Never substitute event-day totals for ingestion-day totals. Do not form an unbounded event-day x ingestion-day cube.
* Minimum day/section summaries reproduce existing daily savings/efficiency APIs. Provider+method summaries preserve request/cost comparisons; source_file+section retains current latency weighting. To support combined filters/rankings, preserve the corresponding joint keys, not independent marginal totals. A bounded joint key can include provider, method, section, source_file, status_code, cache_action and optimization_layer; preserve case/null/empty values. Mode/retries/PID are detail-only unless a specifically accepted report requires an additional bounded dimension.
* Mergeable evidence: event count; credits non-null count and sum; positive-credit count/sum; saved non-null count/sum and positive-only count/sum; status histogram/error/429 counts; cache action counts/saved sums/min/max; latency non-null count/sum/min/max; first/last timestamps and recorded_at; first/last valid date. Keep null membership so NULL and zero are not conflated. Action comparisons exclude SQL NULL just as today.
* Recompute ratios, average credits, cache averages, hit rates, health and savings from these sufficient statistics at read time with the existing expression/rounding rules. Keep exact integer sums; check overflow. Floating latency regrouping is mathematically equivalent but not guaranteed bit-identical due to floating summation order; preserve final rounding and qualify tolerance separately.
* Retain 6 preceding observed global day totals as bounded trend seed context. They may lie outside the reporting horizon; they are compact exceptions, not older detailed rows. Published/pinned historical conclusions remain separate immutable evidence.
* Daily granularity is the minimum for daily APIs. Hourly summaries can improve exploratory comparisons but **do not solve exact arbitrary-hour cutoffs**: a cutoff falls inside the oldest hour. Recommend daily historical APIs with declared day alignment; add hourly only for an explicitly accepted requirement. No approximate result may masquerade as an existing exact rolling result.

Proposed design bounds for later implementation qualification: <=4096 joint cells per date label and <=31 labels, <=4096 lifetime keys, 6 trend seed totals; inclusive aggregate storage hard ceiling128 MiB across data/indexes; a nominal384-byte cell assumption gives 48,758,784 bytes for126,976 day cells, plus lifetime/seed/index overhead. These are **budgets, not measured production cardinality or proven SQLite sizes**. Bound dimension text lengths without silently normalizing existing labels. Unknown key/cell/byte overflow halts retirement and raises an explicit capacity decision; never silently drop/merge keys or block core detection to force a summary budget. Overflow policy and observed joint-key cardinality must qualify before implementation can pass. No new file >=500,000,000 bytes; no unbounded history or summary table. Retained detail also needs a byte/page budget—time retention alone cannot bound a traffic burst. A future implementation must fail closed on retirement/capacity, not sacrifice monitoring correctness.

## LIFETIME_COUNTER_CONTRACT

Maintain durable exactly-once lifetime event/paid-call/error/429 counts, internal spent/saved credits, cache action statistics, bounded provider/method and required source/section breakdowns. Preserve all-time raw timestamp-population counters separately from recorded_at-not-null savings-population counters. Global min/max clocks/dates and count of observed dates are required. The same names must never silently become last30d totals.

Lifetime counters cannot be reconstructed from only the last30 summaries. Bootstrap once from existing retained telemetry with an idempotent cursor and a declared consistent database boundary; record provenance `retained-telemetry lifetime`, not `all requests ever`. Earlier missing/deleted/uninstrumented history remains unknown. Existing state keys are useful durable scalar storage, but no current materialized summary/cursor contract exists; views scan raw rows. internal_usage_snapshots, usage_reconciliation and helius_usage_snapshots are different accounting authorities, not substitute per-method telemetry.

COUNT(DISTINCT DATE(recorded_at)) needs actual membership, not first/last span. Future monotonic closure plus a qualified late-arrival rule could keep a scalar and a bounded recent set; arbitrary backdated dates require exact bounded membership (for example a fixed bitmap over SQLite's supported date domain, under0.5MiB) or retained pins. Do not invent missing day membership. Preserve malformed-recorded_at NULL-date behavior explicitly before enabling retirement.

Future counter updates must couple inserted-row identity and summary increment atomically, or use an exactly-once durable high-water cursor with immutable-input guarantees; multi-process writers and restart/retry must not double count. Never sum overlapping live rows and lifetime counters twice. Late/backdated inserts, updates and deletes require explicit correction rules and out-of-horizon quarantine/pins. No correction-history table with unlimited growth.

### Bounded feasibility and future backfill requirements

No existing durable RPC summary table was found: the listed views are virtual. Reuse rpc_metrics_state only for a small bounded version/cursor/reset scalar contract after separately authorized implementation; do not accumulate per-day JSON blobs or rewrite large state values on each request. Existing provider snapshots/reconciliation remain untouched.

A future one-time backfill must enumerate existing immutable rows by INTEGER PRIMARY KEY keyset and a fixed initial upper ID, not OFFSET or an unbounded GROUP BY. Proposed qualification limits: <=512 rows and <=50ms read work per slice, <=8 aggregate-key updates per event, <=64KiB encoded summary delta per slice, explicit CPU/time/batch ceilings per invocation, and durable restart cursor/certification. These are proposed acceptance budgets, not proven runtime timings. Split or stop when any bound is hit. Never hold a database snapshot for the entire history or build an intermediate full database copy. Certify disjoint slice ID ranges and exact counts/sums/null memberships before any row becomes eligible; overlap/gap detection and repeat-run idempotence are mandatory. Keep live tail above initial upper ID separate until its cursor catches up. Changes to already summarized rows must be prohibited by demonstrated authority or reconciled explicitly; schema alone does not guarantee immutability.

Future steady-state work can be O(1) bounded accumulator updates per event plus bounded daily expiry of summaries; cardinality caps and live-tail lag remain observable stop conditions for retirement. Do not impose a monitoring outage when counter qualification lags: preserve raw required detail and stop retirement. Raw detail byte pressure then needs an explicit operator capacity decision. This feasibility contract does not claim an implementation passes these budgets.

## RESET_BASELINE_PRESERVATION

Observed exact keys: comparison_reset_timestamp=1792623600.0 (2026-10-21T23:00:00Z / Oct22 UK midnight); comparison_reset_time=2026-10-22T00:02:04.005754+01:00; baseline_helius_credits_today=757471; rpc_metrics_last_reset_day=2026-10-22; manual_monitoring_reset_at absent. Timestamp boundary and elapsed-time origin differ by124.005754 seconds. Preserve both independently; do not repair them under this contract.

Existing automatic UK midnight reset, manual resets, cross-process refresh and external-provider baseline must survive unchanged. Future reset checkpoint must record global counter snapshot, applicable timestamp boundary, elapsed origin, external baseline, epoch/UTC plus Europe/London rules, and event ingestion watermark as one consistent generation. Keep one active checkpoint and bounded comparative checkpoints (e.g.30), explicitly pinned accepted resets outside that cap. Lifetime counters never reset. Do not reinterpret session requests_total or credits_total aliases as lifetime.

Counter subtraction alone is insufficient if a later inserted row has timestamp before reset: it belongs to lifetime but not since-reset. A future active reset accumulator must filter by the actual timestamp predicate or apply an explicit correction. Current reset can be validated from recent detail. Historical resets older than detail require checkpoints recorded at the time, plus qualified late-arrival handling; daily summaries cannot split an arbitrary manual reset. Missing historical checkpoints mean unavailable, not invented zero.

Savings/efficiency projections keep the existing external usage snapshot authority and raw internal-unit distinction, same elapsed denominator and reset baseline. No provider calls are needed or permitted for qualification.

## REPORTING_EQUIVALENCE

`qualify.py` imports only the Python standard library. Production connections are explicitly mode=ro/query_only with1MiB cache and75ms/30,000-VM-step per-query bounds. Each sample query uses timestamp index search LIMIT32; four cutoffs (now,7,30,90days) retrieve128 rows. All schema creation, view creation and data insertion occur in :memory: only; no production schema/write action. No full-table scan, dbstat or COUNT over production ranges.

The retained UK-midnight fixture's10/20-credit boundary and paid-call/zero-credit cache values were reused without importing the application. Additional synthetic null/status/cache/dimension edge cases are explicitly synthetic; not missing history. Fixture24rows ->8 compact cells; live bounded sample128rows ->4 cells, deliberately narrow time neighborhoods, **not a production compression estimate**.

For each dataset, exact equality passed for all columns of ten installed view calculations: daily savings, savings by section, cache performance, cumulative savings, daily efficiency, all-time efficiency, section efficiency, health, efficiency trend and rolling savings average. An additional grouped sufficient-statistics check passed for database API source/section count, credit, error/429 and latency inputs. These are11 checks per dataset,22 total. SQL expressions were taken from live schema metadata, not hand-recalled formulas. They include existing integer division/null/rounding behavior. This proves algebra on tested whole-day/all-time populations; not complete historical migration equivalence.

Tested counterexamples prove: identical whole-day count/sum can yield different intra-day results; count/sum/min/max cannot determine exact p95; event timestamp and recorded_at cannot be interchanged. Retain current process-local p95 behavior; a new30day p95 would require an explicit histogram/sketch approximation or retained distributions. Existing APIs do not currently promise a durable30day p95.

Product decisions required before exact compatibility can be claimed:

1. For timestamp rolling requests >168h (database/optimization/component/test APIs), accept explicit UTC day-aligned historical reports or explicitly unavailable windows. Keeping arbitrary second-level boundaries requires finer retained evidence and a different storage budget; hourly buckets alone are insufficient.
2. Bound currently unbounded savings/efficiency `days` parameters to the reporting horizon, or define an explicit separate longer-horizon product. Preserve lifetime summary endpoints independently. Clarify30elapsed days versus30date labels.
3. Choose day-aligned historical replacements/unavailability for sliding last-week/30d SQL views and arbitrary old comparison intervals. They cannot silently keep the same exact names and semantics on daily-only evidence.
4. Accept bounded dimension cardinality/overflow handling and any floating-latency tolerance. No merging source/provider/method labels or changing current average weighting implicitly.

## RESEARCH_EXCEPTIONS

Preserve existing accepted audit artifacts and their immutable inputs/digests. Retained Oct20/21 credit-spike and provider-use reports use aggregate per-PID telemetry and acknowledge coverage gaps; their conclusions are not authority to invent missing rows or equate internal units to bills. No demonstrated blanket research requirement for every old telemetry row was found in the scoped previous dependency closure. This is not proof that all old rows are unreferenced.

Before retirement, require a finite manifest of explicit row IDs or time+PID/provider/method predicates for active investigations, accepted row-level citations, unusual error forensics, unresolved reconciliation and old reset/test comparisons. Include provenance, required fields and release authority. Existing manifests/citations must remain readable; pinned detail can be a small separate bounded evidence exception, never copied wholesale. No research replay or provider reacquisition. If pins cannot fit the qualified budget, HOLD that population.

## ESTIMATED_STORAGE_REDUCTION

Fresh bounded storage sample at2026-10-22T20:23:27Z:64 evenly spaced rowid anchors x LIMIT16 =1024 rows;976/1024 (95.3125%) timestamp-older-than7d. First/last observed rowid4,055,803 /20,443,602; span16,387,800 is an upper bound, **not an exact row count**. Assuming a dense span and representative age clusters gives about15.62million old rows. Dual-clock/protected/pin eligibility remains uncounted. This estimate does not authorize95% retirement.

Mean text-encoded field-content length171.27bytes; extrapolated old field-content proxy2,675,165,317bytes (2.49GiB). It excludes SQLite records/pages/indexes and includes text numeric encodings, so it is **not a measured logical SQLite payload or recoverable allocation**. No exact payload census or current table/index page scan performed. Exact eligible count, payload and current index allocation remain UNKNOWN pending bounded resumable aggregation qualification.

Retained Oct2 footprint map reports rpc_metrics8.928GB decimal: table2.287GB, ten indexes6.641GB. This is dated historical evidence, not current allocation. Applying95.3% to that footprint would be unjustified; index occupancy/age distribution may differ. Recent detail under the same rough density model is0.77million rows, but field-size/traffic variability prevents a guaranteed detailed byte budget.

Compact replacement target <=128MiB inclusive, not a measured forecast. Conditional arithmetic only:2.675GB field-content proxy minus128MiB ceiling is about2.541GB (2.37GiB) smaller representation; these quantities have different measurement bases and this is **not a net SQLite recovery estimate**. Production day/joint-key cardinality and byte bounds remain unqualified. The measured sample compression factor must not be extrapolated to all history.

## PHYSICAL_RECOVERY_LIMITATIONS

Logical row retirement could free internal pages for reuse; it would not shrink existing Core file. Current Core freelist783,460pages x4096=3,209,052,160bytes already internally free. Additional reusable pages from telemetry are UNKNOWN because table/index allocation, shared pages and pin distribution have not been measured. Actual filesystem bytes recovered by this task:0.

auto_vacuum was previously observed0; no incremental shrinking assumption. A future offline copy/rebuild/compaction is a separate authorized milestone requiring writer downtime/coordination, WAL limits, verified rollback and space beyond both replacement and retained original. Current full Core35.65GB plus OPS16.19GB cannot be assumed to fit transient copies in existing headroom. No VACUUM, backup copy or checkpoint was attempted. Do not clone a full database or create a>=500MB file under this milestone.

## GO_HOLD_DECISION

GO for the specified compact-counter design as a **proposed contract** and tested whole-day/all-time sufficient statistics. HOLD for an exact unchanged-API retention implementation, historical backfill or retirement: arbitrary-window semantics, bounded dimensions, exact eligibility/pins and resumable migration/counter certification are unresolved. No implementation, deployment or deletion permission is inferred.

## NEXT_ACTION

One bounded next action: obtain the product decision on items1–4 above using this concrete contract: recommend exact recent rolling windows (<=168h), explicitly day-aligned older reports through the existing30day default horizon, durable lifetime/reset totals, preserved session percentile semantics, and visible unavailable/overflow states. This is a decision on reporting semantics, not authorization to implement or delete. After acceptance, implementation would require its own separately authorized milestone.
