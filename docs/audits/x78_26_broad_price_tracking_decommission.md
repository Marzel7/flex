# X78.26 — Legacy Broad Price Tracking Decommission

Date: 2026-08-10 (Europe/London)

## Final verdicts

- Broad Price Tracking: `A — DECOMMISSIONED_SUCCESSFULLY`.
- Price Data: `A — HISTORICAL_RETAINED / SELECTIVE_ACCESS_HEALTHY`.
- Database: `C — VOLATILE_WITH_IDENTIFIED_BLOCKER`.
- Production Health: `C — DEGRADED / READINESS_BLOCKED`.
- Evidence Activation: `HEALTH_REPAIR_REQUIRED`.
- Acquisition: `HOLD_ACQUISITION`.

The broad subsystem is retired. The production-readiness clock was not
started because independent WAL, lock, Creator Resolution, and API gates fail.

## Purpose and population

The legacy `BackgroundPriceWorker` continuously refreshed the
`tracked_tokens` population, bootstrapped active pool reserves, polled/fetched
prices, maintained current/peak market-cap state, and conditionally appended
price/liquidity snapshots.

Pre-deployment registry:

- 45,893 total `tracked_tokens` rows.
- 2,326 rows marked active.
- 2,304 active rows classified `DISCOVERY`.
- 22 active rows with no tracking reason.

The active flag is now retained historical registry state. It no longer means
that a broad worker processes that token.

## Producer and scheduler census

### Broad producers — decommissioned

1. Listener startup path in `pumpfun_curve_listener.py`:
   `get_price_worker().start()`, historically controlled by
   `LISTENER_PRICE_WORKER_ENABLED`.
2. Flask optional-worker path in `main.py`:
   `start_price_worker()`, historically controlled by
   `FLEX_ENABLE_FLASK_PRICE_WORKER`.
3. Flask startup population sync:
   `_sync_validated_tokens_to_tracker()`.
4. Legacy listener live-price updater, already parked through
   `LISTENER_LIVE_PRICE_UPDATER_ENABLED=0`.

The worker's nominal loop interval was 10 seconds. Its active population was
the registry above, with full-snapshot eligibility narrowed for expired
DISCOVERY tokens but peak/current processing still broad.

### Retained selective/event-driven facilities

- `TokenPriceService` point-in-time and batch lookup.
- Pool/migration event handling in the listener.
- Liquidity-removal state and pool-account processing.
- Trading/portfolio price reads and explicit quote paths.
- Historical price, peak, risk, WATCHTOWER, and analyst reads.

No retained consumer starts or requires the broad population loop.

## Data inventory

Pre-deployment and settled post-deployment counts were identical:

| Data | Rows | Latest observation | Allocated table bytes |
|---|---:|---:|---:|
| `token_price_snapshots` | 3,020 | 1786366918 | 327,680 |
| `token_liquidity_snapshots` | 1,308,653 | 1786366918 | 109,309,952 |
| `token_market_cap_peaks` | 23,643 | 1786366918 | 2,326,528 |
| `tracked_tokens` | 45,893 | retained | 4,632,576 |

The liquidity snapshot index `idx_tls_mint_time` occupies 97,116,160 bytes.
All tables and indexes remain. Nothing was deleted, dropped, vacuumed, or
rewritten.

## Dependency verdict

`SAFE_TO_DISABLE_BROAD_PRICE_TRACKING`.

Consumer classification:

| Consumer | Requires broad continuous history? | Retained source |
|---|---|---|
| Creator Funding | No | creator/funder transaction evidence |
| Creator Resolution | No | creation/RPC evidence |
| WATCHTOWER | No | retained peak/current values and independent live paths |
| Operational Intelligence | No | retained analytical projections |
| Relationship intelligence | No | transaction/relationship evidence |
| Second hop | No | funding lineage |
| Liquidity-removal detection | No | pool-event/account state; retained |
| Alerts | No core dependency proven | retained stored/selective values |
| Portfolio/trading | Selective only | quote/current lookup and retained history |
| API token display | Selective/display only | stored latest values and on-demand paths |
| Mission Control | No | retired capability removed |

Historical research and performance consumers remain read-only consumers of
the retained tables. Their value does not justify live broad collection.

## Runtime change

- Added a permanent `BROAD_PRICE_TRACKING_RUNTIME_ENABLED = False` boundary.
- `BackgroundPriceWorker.start()` now suppresses execution.
- Listener cannot reactivate it through an environment flag.
- Flask no longer starts the worker or populates the broad registry.
- Mission Control no longer models `price_tracking` as a capability, renders a
  card/pill for it, or produces price-tracking incidents.
- Raw health reports the retained historical subsystem as
  `DECOMMISSIONED`, `runtime_enabled=false`.

## Tests

- X78.26 and Mission Control: **36 passed**.
- Retained price singleton/schema, liquidity, peak filtering, X78.25, and
  X78.24: **25 passed**.
- Python compilation and `git diff --check`: passed.

## Deployment

Only affected services were restarted:

- Listener: PID 55812 → PID 60073.
- API master: PID 55249 → PID 60095.
- Creator Resolution remained PID 57921.
- Creator Funding remained PID 56577.

Listener startup logged:

`Broad price tracking decommissioned (historical data retained; selective price access remains)`

Both PumpPortal and PumpSwap connected after restart. The health payload listed
five capabilities and no price-tracking incident. Raw price-worker status was
`DECOMMISSIONED`.

## Settled write proof

Across the captured pre/post boundary:

- broad price snapshot growth: 0;
- liquidity snapshot growth attributable to the retired worker: 0;
- peak-row growth attributable to the retired worker: 0;
- registry writes: 0;
- broad worker executions: 0;
- broad scheduled jobs: 0.

The last recorded price/liquidity/peak observation preceded deployment. Since
the broad worker was already parked by `run_listener.sh` before X78.26, its
measured immediate pre-deployment write rate was also zero. Therefore no DB or
WAL improvement is attributed to this milestone; it closes latent startup and
health/UI paths rather than removing a measured active writer.

## Resource and production qualification

Immediately after deployment the new listener serializer window reported:

- p50 wait: 0 ms;
- p95 wait: 0.30 ms;
- p99 wait: 3.34 ms;
- p95 commit: 0.36 ms;
- p99 commit: 0.66 ms;
- queue depth: 2;
- writes/minute: 89.1.

These are encouraging but not a qualification window and are not claimed as a
price-decommission improvement.

Independent blockers remained:

- WAL grew from 40,944,592 to roughly 65 MB during active non-price writes.
- X78.25 samples showed a genuine checkpoint gap whose checkpointed boundary
  remained at frame 346 over multiple samples before checkpoint-lock samples
  became unavailable.
- Creator Resolution PID 57921 remained stable with no false
  `CRITICAL_WAL_PINNED`, but logged repeated `database is locked` cycles and did
  not demonstrate qualified multi-cycle progress.
- Creator Funding continued running but remained CPU-intensive.
- API served the first post-deploy health proof, then became intermittently
  unreachable while still supervised.
- Operational Intelligence therefore lacks an upstream-healthy window.

## Readiness

- Readiness start: not started.
- 15 minutes: not applicable.
- 30 minutes: not applicable.
- 60 minutes: not applicable.

Before readiness can start, resolve or attribute the independent WAL/checkpoint
and database-lock condition, require genuine Creator Resolution progress, and
confirm consistently responsive API service. Do not reactivate broad price
tracking, Token Prediction, Evidence, or acquisition during that work.
