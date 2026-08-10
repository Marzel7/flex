# X78.18 — Second-Hop Transaction Isolation, RPC Metrics Ownership & API Worker Recovery

Date: 2026-08-10 (Europe/London)  
Branch: `classification-attribution-axis`  
Baseline: `1ef0a5c59c14bf785064b73b25b30b54df6e23e4` (`X78.17`)

## Outcome

The proven second-hop write-lane defect is repaired and covered by deterministic
tests. Expensive source reads and Python graph construction now occur in one
read-only WAL snapshot using TEMP shadow tables. Publication is a separate,
short, atomic replacement transaction. Relationship scoring, exclusions,
destination identities, RPC-backfill preservation, and consumer-visible
all-or-nothing replacement remain unchanged.

No RPC-metrics, Creator Funding extractor, API, or listener code was changed.
Post-X78.17 evidence did not justify those conditional changes.

Production is **not qualified healthy**. The API recovered a serving worker, but
database pressure and listener descriptor growth remain, and three fresh
post-deployment Creator Funding completions were not established.

## Baseline

- X78.13: `463ae476`
- X78.14: `d9da7dfd`
- X78.15: `749bcb9f`
- X78.16: `30eb3921`
- X78.17: `1ef0a5c5`
- Creator Funding: PID `37085` before X78.18 deployment
- Creator Resolution: PID `33850`
- listener: PID changed from the X78.17 checkpoint to `37446`
- API master: PID `30675`
- API worker: initially absent at the X78.17 checkpoint; PID `37691` was alive
  by 10:36:16
- walkback: PID `3120`
- intelligence snapshot scheduler: PID `3119`
- WS cascade: PID `3124`

Unrelated dirty working-tree files present at baseline were preserved.

## Second-hop transaction map

### Previous boundary

1. Open tracked production connection.
2. Apply schema guards.
3. First UPDATE/DELETE acquires global write lane.
4. Scan a 2,350,502-row `transfer_index` population and 83,813
   `creator_funders` rows.
5. Build upstream links.
6. Read and group links, network maps, wallet clusters, and farm clusters.
7. Score graph pairs in Python.
8. Build creator second-hop TEMP representation.
9. Replace destination rows.
10. Commit and release.

The write lane therefore enclosed source reads, temporary B-trees, graph
materialization, and Python scoring.

### New boundary

1. Apply idempotent schema guards in a short P2 transaction.
2. Open production DB with `mode=ro` using the native SQLite connection.
3. Begin one stable WAL read snapshot.
4. Copy the four destination tables into connection-local TEMP shadows.
5. Rebuild links, bridges, hubs, and creator second-hop rows against those
   shadows using the unchanged algorithms.
6. Materialize final rows in memory and close the read snapshot.
7. Open one P2 production transaction.
8. Replace transfer-index links, bridges, and creator-second-hop rows; upsert
   materialized significant hubs; apply infrastructure exclusions.
9. Commit and release.

The intended invariants are both preserved:

- readers never observe a partial destination generation;
- all computed outputs derive from one internally consistent source snapshot.

Concurrent source mutations can make any completed rebuild immediately stale,
as before, but cannot produce a structurally mixed generation. RPC-backfill
upstream rows are not deleted by publication.

Consumers in `relationship_events`, `intelligence_refresh`, risk scoring,
token prediction, second-hop-lite, upstream expansion, release generation, and
the API read complete production tables. They do not require visibility of the
intermediate build.

## Query plans and cardinality

Measured source cardinality:

| Table | Rows |
|---|---:|
| `transfer_index` | 2,350,502 |
| `creator_funders` | 83,813 |
| `funder_upstream_links` | 19,769 |
| `upstream_network_bridge` | 229 |
| `creator_second_hop` | 463 |

The upstream-link query uses `idx_is_cex` and
`idx_transfer_destination_time`, but requires TEMP B-trees for DISTINCT and
GROUP BY. Bridge generation uses `idx_ful_funder` and TEMP B-trees for GROUP BY
and `COUNT(DISTINCT)`. Creator-hop generation uses bridge confidence,
upstream, creator-funder, and network-map indexes, plus a TEMP GROUP BY. These
expensive operations now run outside the global production writer lane.

## Conditional blocker findings

### RPC metrics

Verdict: **NOT_REPRODUCED_POST_X78_17_AS_HOLDER**.

The X78.17 owner record pointed to old Creator Funding PID `34622`. Current PID
`37085` showed the metrics flusher as a waiter behind
`price_worker.py:2338`, followed by a same-thread nested retry diagnostic. No
post-X78.17 event proved the flusher was again the long-lived owner blocking
another writer. Its transaction body is a bounded `executemany`, commit, and
finally-close with no reads or aggregation inside the lease. Historical holder
evidence was not patched.

### `extract_for_creator`

Verdict: **CLOSED_BY_X78_17_BOUNDARY_CHANGE** for the named holder mechanism.

Post-deployment logs showed bounded flush timeouts and network/extraction work,
but no new current-owner record identifying the final X78.17
`extract_for_creator` line as a long holder. No extractor change was made.

### API master without workers

Verdict: **TRANSIENT STARTUP/RESPAWN CONTENTION; CURRENTLY RECOVERED**.

API worker PID `36300` experienced a 60-second RPC-cache schema wait while old
Creator Funding PID `34622`'s metrics flusher owned the lane, and a subsequent
price-service startup timeout. The Gunicorn master remained visible to
Supervisor while it respawned. Worker `37691` later completed startup under the
same master. Verification returned:

- `/api/health/full`: HTTP 200 (0.52 s then 0.51 s)
- `/intelligence/operators`: HTTP 200 (0.003 s then 0.002 s)

No API code defect was established and the master was not restarted.

### Listener descriptors

Verdict: **PERSISTENT GROWTH OBSERVED; OWNER UNKNOWN**.

Listener PID `37446` remained the same during the short sample, while primary
DB descriptors increased from 8 to 10. Logs show the prior listener PID exited
after three consecutive samples at/above the fatal threshold, and the current
PID has already emitted a high-count warning. A one-second process stack sample
did not map individual SQLite handles to Python owners. This is not sufficient
to label the cause or safely change the watchdog. No listener code or threshold
was changed.

## Validation

Focused X78.18 tests prove:

- materialization owns no global production write lease;
- an unrelated writer completes during deliberately held materialization;
- isolated output equals the legacy single-transaction output;
- pre-write failure preserves the old generation;
- publication failure rolls back the generation;
- repeated rebuild is idempotent.

Results:

- targeted X78.13–X78.18 regression: **34 passed**;
- focused deployment subset: **22 passed**;
- broader second-hop suite: **71 passed, 1 failed**.

The one broader failure is
`TestUpstreamExpansionBuilder.test_relationship_event_logged`. It reproduces in
isolation and is in the pre-existing upstream-expansion/infra working area; the
X78.18 builder is not invoked by that test. It was not changed or hidden.

## Deployment and readiness

Only `creator_funding_worker` was restarted. Initial X78.18 deployment PID was
`38233`; the final instrumentation-only restart produced PID `38349`. API,
listener, Creator Resolution, walkback, intelligence scheduler, and WS cascade
were not restarted.

The health endpoint still reported:

- platform: `CRITICAL`;
- database p99: 31,250.24 ms;
- serializer depth: 2;
- WAL: 26.8 MB;
- Creator Funding: stale/degraded immediately after restart;
- live ingestion: warning;
- Operational Intelligence: warning.

Therefore:

- second-hop code gate: **PASS**;
- RPC-metrics conditional patch gate: **NO CHANGE JUSTIFIED**;
- extractor conditional patch gate: **NO CHANGE JUSTIFIED**;
- API availability gate: **PASS AT CHECKPOINT**;
- listener 15-minute stability gate: **FAIL / NOT ESTABLISHED**;
- three fresh Creator Funding completions: **FAIL / NOT ESTABLISHED**;
- database stabilization gate: **FAIL**;
- Operational Intelligence recovery gate: **NOT RUN**;
- readiness clock: **NOT STARTED**;
- Evidence Platform: **DISABLED**;
- acquisition: **HOLD_ACQUISITION**.

## Final verdict

`SECOND_HOP_WRITE_BOUNDARY_REPAIRED__PRODUCTION_HEALTH_REMAINS_BLOCKED`
