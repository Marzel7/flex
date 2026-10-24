# DEV004 — Compact migration boundary clarified

STATUS: HOLD_DISK_HEADROOM. The corrected representative qualification failed closed before any source database was opened or target file/row was written. No wider migration, source snapshot, production mutation, provider call, service action or cleanup occurred. RUNTIME_DEPLOYMENT_REQUIRED=false: DEV-only qualification harness and evidence, no runtime deployment.

## What V2 retains

The existing DEV004 reference-seed contract remains the authority. V2 starts with operational knowledge, not an empty database and not a full V1 copy:

- All established operation identities, aliases/merges, canonical memberships, confidence/review states, entities, treasuries, SubProviders and distinct funding-account roles.
- The minimum retained creator/CREATE/migration anchors and funding signatures, amounts, units, times, transaction roles and selected/alternative-parent evidence needed to interpret those assignments and recognize known patterns. Null legacy event IDs remain null; no invented provenance or promoted candidates.
- Behavioural evidence, qualification/rule versions, exclusions, conflicts, counterevidence, quarantine and pinned research/forensic conclusions with their sufficient evidence targets.
- Compact creator count/first-last/span/funder inputs plus historical identity/dedup cutoffs; no requirement to duplicate thousands of unrelated launch rows merely to satisfy a legacy query.
- Existing candidate/review/assessment/freshness and non-P3R review authority. P3R research generation is not a new cutover prerequisite; established canonical records bearing those names do not become disposable automatically.
- Required opening market cap and baseline type, canonical ATH/time/provenance/finalization identity, lifecycle/drawdown evidence, necessary observations and compact completion identities for assigned/currently monitored tokens. Preserve unfinished acquisition windows, retries and consumed/reserved budgets; BIRDEYE remains exclusive,20global/4token/60s unchanged.
- Current canonical deltas, pending/retry/quarantine, handoff, deduplication and recovery state. Transfer work, not stale live lease ownership. Existing stable journals/price queues/receipts remain independently bound rather than requiring permanent V1 database access.

Existing row-key/digest manifests define the retained populations. The older seed audit partitions28,454rows into5,004essential,4,306conditionally compactable,19,115unknown and29synthetic controls. These are historical qualification counts, not a fresh V1 inventory or authorization to discard19,115rows. Later accepted graph/role corrections remain included. Unknown dependencies in the already-small seed stay protected; no further seed optimization or historical reconstruction is undertaken here.

## What is omitted, not deleted

No V1 data is deleted in DEV004. The new target excludes whole-table copies of RPC caches, raw RPC/provider request histories, bulk telemetry, unrelated historical token/transaction/candle populations, redundant derived caches/debug/intermediate results and explicit test-only controls, except where an accepted consumer/research dependency requires particular records or compact counters/results. Age alone is not disposal authority. Lifetime/reset reporting inputs and price opening-to-peak/ATH-timing/drawdown dependencies must survive; if their compact closure is unresolved, that item cannot be silently excluded. Historical rows needed for attribution or pinned reproducibility are selected by dependency, not blindly omitted with their parent table.

The prior PASS copied22.7MB of already-compact disposable DEV fixtures into disposable target/rollback pairs. It never duplicated the35.65GB Core V1 or16.19GB OPS V1. Nevertheless, that experiment is not proof of direct selective extraction from V1. This corrected method is specifically that missing boundary; it does not repeat the old snapshot rehearsal or use it as a full-migration claim.

## Corrected source-to-target method and authority

Frozen qualification source `eb9e1d35ea0b87ab38d59bb46a2b3fac458884ff`; accepted runtime candidate0013ccbd unchanged. The harness uses the existing HXpDLpo…pump Watchtower case and retained indexed selector/reader contracts. It is designed to:

1. Open the two existing authoritative V1 paths with SQLite URI mode=ro/query_only. Inspect only selected schema/index metadata and the fixed indexed predicates. Bound each selector to128rows,200,000VM operations/one second,2,048unique rows total and2MB encoded data.
2. Freeze source path/device/inode, observed SQLite header/schema-version identity, exact selected-row hashes, schemas/indexes, checkpoints, provenance, canonical price authority, target identities and abort-before-activation rollback references. No full52GB file hashing, backup, snapshot, ATTACH/whole-table migration, or intermediate row-data file is required. Hashes identify selected logical evidence, not a falsely atomic live Core/OPS generation.
3. Stream selected records directly from V1 into fresh compact Core/OPS using exact existing definitions and parameterized INSERTs. Keep only bounded rows in memory. Existing PK rows must match exactly; never overwrite conflicts. Commit before publishing a durable continuation cursor.
4. Exit once after a compact row commit but before cursor publication; resume the same selection without duplicating that row. Compare all selected fields and11actual accepted reader outputs, then reopen V2 in a process forbidden from opening V1. Verify finalized price suppression and zero provider calls. No permanent V1 lookup dependency is allowed in that reopened representative path.
5. Preserve canonical price values and provenance exactly; apply the0.1%relative ATH tolerance only to numerical ATH comparisons. Reuse the accepted0.0025616522235844718%historical numerical oracle separately from missing original-candle reproducibility. No new historical recalculation or fabricated acquisition cursor.

The representative targets are never activated. Rollback is abandonment of an unactivated disposable target while V1 remains untouched, with partial target/cursor retained for diagnosis. Full paired cutover/journal/retirement/queue/price-budget recovery contracts remain preserved and separately qualified; they are not replaced by this small extraction test.

## Expected initial V2 footprint

Measured current retained reference pair: Core8,970,240bytes + OPS13,754,368bytes =22,724,608bytes (21.67MiB). Compact creator history1,218,041bytes plus freshness mapping151,878bytes bring the known reference package to24,094,527bytes (22.98MiB). This is the useful measured starting estimate: tens of megabytes, not48GB and not a requirement to retain V1 permanently.

A32MiB Core/OPS planning allowance for the known reference closure leaves room over the measured21.67MiB pair for schema/bootstrap/index differences and small current deltas. It is a planning envelope, not a measured complete final V2 or proof of an irreducible minimum. The exact full operational initial size remains UNQUALIFIED until the approved reference selectors and final active queue/price/recovery deltas are materialized. The proposed5GiB combined ceiling is neither a required starting size nor a demonstrated enforceable lifetime cap. No extrapolation from one launch can certify full coverage or sustained growth.

## Peak additional storage and headroom

For this representative qualification, enforce8,000,000bytes per file (also4MiB/page-cap per SQLite DB),64MiB total allocation and at least2,000,000,000+67,108,864bytes free before writes. Only two small new SQLite targets, bounded metadata/progress and their transients are allowed. There is no second V1 pair or large intermediate snapshot. The previous compact case measured roughly0.8MB, but this new run did not progress far enough to measure its peak.

For the existing known reference closure, use128MiB as a provisional additional planning allowance:32MiB target +32MiB WAL +32MiB journal/index/temporary workspace +32MiB metadata/queue/mapping margin. This is a conservative planning calculation, not a qualified full migration bound. Preserve the existing V1 files in place as temporary migration/rollback references; their approximately51.85GB is already allocated and must not be added again as a new copy. Any eventual post-cutover rollback must account for new V2 writes through the accepted contracts, not simply flip back to stale V1.

The previously quoted3.93GB would have been sufficient for the2GB floor plus either the64MiB representative reserve or128MiB reference planning allowance. It is no longer current. This task initially observed3,866,914,816bytes free, then the disk gate failed; a direct recheck found314,187,776bytes and a later observation308,727,808bytes. Both are below the floor even before a new target is created. This drop cannot be attributed to the compact qualification: its directory contains zero files, and no source connection or extraction occurred. Its cause was not investigated under this scope.

## Qualification result and remaining blockers

- Direct compact migration: NOT EXECUTED. Authority freeze stopped at the first disk gate; no complete new manifest or row/schema authority is claimed.
- Historical continuity, attribution, price, restart and idempotency: retained earlier acceptance remains valid, but new direct-extraction acceptance is NOT claimed without execution.
- Production mutations, full-source copy bytes, intermediate source-snapshot bytes, target rows and provider calls: all0.
- Concrete immediate blocker: free space308,727,808bytes, below2,000,000,000floor and2,067,108,864representative admission threshold.
- Before any wider migration: this direct-read representative proof must pass and the full selected-reference/current-state footprint and cutover boundary must be certified. No whole V1 clone or permanent V1 dependency is part of that plan.

NEXT_ACTION: Restore at least2,067,108,864bytes available, recheck the floor, then freeze and qualify one direct-read compact representative migration. Do not retry now, prune data, repair infrastructure, delete V1 or perform a wider migration. Stop at this documented HOLD.
