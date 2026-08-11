# X78.24 — Legacy Token Prediction Decommission & Production Stability Qualification

Date: 2026-08-10 (Europe/London)

## Executive result

Legacy Token Prediction is removed from live production execution. Historical
tables, indexes, rows, and the X78.23 audit are retained. No Evidence Platform,
acquisition, prediction, attribution, relationship, Operation, identity,
governance, Primitive, Discovery, motif, or Operational Landscape semantics were
changed.

Dependency verdict before changes: **SAFE_TO_DISABLE**.

Production stability verdict: **READINESS BLOCKED BY AN INDEPENDENT WAL PIN**.
The decommission itself passed; the all-service readiness clock stopped when
Creator Resolution restarted under its existing `CRITICAL_WAL_PINNED` watchdog.

## Repository baseline

- Branch: `classification-attribution-axis`
- HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`
- X78.23 existed as uncommitted working-tree work and was preserved.
- Unrelated dirty files were not modified or discarded.

## Complete runtime producer census

| Producer | Previous trigger | Ownership | X78.24 result |
|---|---|---|---|
| Creator Funding | `score_single(..., FUNDING_COMPLETE)` | synchronous `to_thread` child | removed |
| Listener migration marking | MIGRATED | bounded thread pool | permanently gated off |
| Listener birth persistence | BIRTH | bounded thread pool | permanently gated off |
| Listener fast migration | MIGRATED | bounded thread pool | permanently gated off |
| Listener known migration | MIGRATED | bounded thread pool | permanently gated off |
| Listener migration-tx store | MIGRATED | bounded thread pool | permanently gated off |
| Listener fresh-creator scan | FUNDING_COMPLETE | bounded pool | prediction call removed; funder extraction retained |
| Listener post-funding path | FUNDING_COMPLETE | awaited `to_thread` | removed |
| Listener funding completion | direct `token_rescore_queue` write | listener write batch | removed; funding completion retained |
| Listener stale-job recovery | direct `token_rescore_queue` write | listener recovery transaction | removed; stale completion retained |
| API startup | two-minute prediction daemon | daemon thread | removed |
| API manual run | `POST /api/predictions/run` | request | HTTP 410 |
| API reset | historical-table deletion job | daemon thread | HTTP 410 plus unconditional internal guard |
| Full rescore | builder after shared risk scoring | daemon thread | prediction step removed; shared risk kept |
| Graph analyzer suite | `TokenPredictionBuilder` entry | analyzer worker | removed from registry |
| Outcome timers | 5m / 30m / 2h | detached daemon `threading.Timer` | zero scheduling because every live `score_single` producer is disabled |

The listener and Creator Funding processes were restarted, which proves all
pre-existing process-local timer objects died with their owning PIDs.

## Consumer census

Historical readers remain in the legacy paper-trading/portfolio implementation,
legacy WATCH candidate tools, historical prediction decision-context tooling,
and audit scripts. They are display/research consumers and are not allowed to
reactivate generation. Prediction-specific HTTP routes now return an explicit
410 response rather than fabricated empty data. The Predictions navigation item
was removed. Portfolio functionality remains because it has independent manual,
migration, WATCHTOWER, pricing, and liquidity-removal functions.

Shared `RiskScoringBuilder`, creator/network risk tables, liquidity-removal
facts, creator funding, and network membership are retained because they have
independent live consumers.

## Core dependency proof

| Core capability | Requires Token Prediction output? | Evidence |
|---|---|---|
| Creator Funding | NO | prediction post-step removed; genuine completions continue |
| Creator Resolution | NO | no prediction-table consumer in worker; progress continued |
| Operational Intelligence/OIP | NO | no Evidence/Primitive/Operation authority input from prediction tables |
| WATCHTOWER canonical attribution | NO | legacy display/candidate readers exist, but canonical membership and governance do not consume prediction output |
| Relationship intelligence | NO | no authoritative relationship producer consumes prediction output |
| Second-hop | NO | second-hop enqueueing continued after decommission |
| Creator/funding attribution | NO | extractor and attribution writes precede and are independent from former prediction post-step |
| Canonical Operations | NO | no canonical registry/resolver dependency |
| Identity | NO | no identity lifecycle dependency |
| Governance | NO | no promotion/review mutation dependency |

## Database inventory and retention

Retained tables and settled counts:

| Table | Rows |
|---|---:|
| `token_prediction_scores` | 277,201 |
| `token_prediction_events` | 272,039 |
| `token_prediction_outcomes` | 2,491 |
| `token_rescore_queue` | 9,702 |

The event count increased once across the first deployment boundary due to
already in-flight old-process work. The rescore queue increased once during the
first observation, exposing two remaining direct listener producers; those were
then removed and the listener was restarted. All four counts subsequently
remained fixed.

All 16 prediction-table indexes remain. No tables, indexes, historical rows, or
migrations were deleted or rewritten.

Prediction-exclusive triggers removed after guarded body validation:

1. `trg_token_prediction_creator_resolved`
2. `trg_token_prediction_funding_inserted`
3. `trg_token_prediction_creator_risk_inserted`
4. `trg_token_prediction_creator_risk_updated`
5. `trg_token_prediction_network_assigned`

Final prediction trigger count: **0**. An unknown name or a known name with a
custom body fails closed and is not dropped.

## API and UI

- `/predictions`: HTTP 410
- `/api/predictions/*`: HTTP 410
- `/api/trading-sim/auto-buy-predictions`: HTTP 410
- Response explicitly states `legacy_token_prediction` is decommissioned and
  `historical_data_retained=true`.
- Predictions sidebar link removed.
- No shared creator/network risk or unrelated portfolio route was removed.

## Deterministic and production validation

Focused X78.24 tests: **4 passed**.

They prove:

- five known triggers are removed while all historical rows remain;
- unknown/custom prediction triggers fail closed;
- Creator Funding has no prediction call;
- graph suites no longer register the builder;
- API daemon startup cannot be enabled by an environment default;
- listener runtime is permanently off and has no direct rescore-queue producer;
- retired routes and navigation are explicit.

`py_compile` and `git diff --check` passed for all changed runtime files.

Production proof after deployment:

- Creator Funding completed multiple genuine jobs, including five funders in
  4.0s and four funders in 16.5s.
- Normal second-hop enqueueing continued.
- Creator Resolution advanced from 5,716 to 5,744 completed rows before its
  independent WAL watchdog restart.
- prediction SQL/phase diagnostic file remained fixed at mtime `1786365446`,
  size 689,233 bytes.
- no prediction score/event/outcome write appeared after the settled boundary.
- no prediction trigger reappeared.
- kernel writer was free at the final snapshot.
- API served normal health endpoints and explicit prediction 410 responses.

## Stability qualification and stop condition

The final same-PID clock did **not** qualify. Creator Resolution PID 54802
reported WAL 72.9 MB with three busy cycles and exited under
`CRITICAL_WAL_PINNED`; replacement PID 56256 immediately exited again at 73.0
MB and PID 56353 started. This is independent of Token Prediction: prediction
counts and diagnostics remained frozen, and no prediction owner appeared.

Final point-in-time database metrics:

- p50 wait: 0 ms
- p95 wait: 6.34 ms
- p99 wait: 29,124.18 ms
- p95 commit: 0.31 ms
- p99 commit: 0.86 ms
- queue depth: 0
- kernel owner: FREE
- WAL: API 73.0 MB / listener snapshot 76.51 MB
- checkpoint busy at final instant: 0
- lock-bound timeouts: 0

The p99/commit split again shows waiting rather than slow commits. The WAL pin
and Creator Resolution restart prevent a healthy production verdict regardless
of the successful prediction decommission.

## Verdicts

- Token Prediction dependency: **SAFE_TO_DISABLE**
- Token Prediction runtime: **DECOMMISSIONED**
- New prediction generation: **ZERO AFTER SETTLED BOUNDARY**
- Prediction timers: **ZERO SCHEDULED / ZERO EXECUTED AFTER FINAL RESTART**
- Historical prediction data: **RETAINED**
- Creator Funding: **HEALTHY / ONGOING FRESH COMPLETIONS**
- Creator Resolution: **DEGRADED — INDEPENDENT WAL WATCHDOG RESTART**
- Listener: **FUNCTIONAL, QUALIFICATION WINDOW RESET/INCOMPLETE**
- Database: **VOLATILE WITH IDENTIFIED INDEPENDENT WAL BLOCKER**
- Production Health: **DEGRADED / READINESS BLOCKED**
- Operational Intelligence: **INSUFFICIENT QUALIFICATION WINDOW**
- Evidence Activation: **HOLD**
- Acquisition: **HOLD_ACQUISITION**

No 5K or Evidence acquisition was executed. The next production-health action
must address or attribute the independent WAL pin; it must not reactivate or
further optimize Legacy Token Prediction.
