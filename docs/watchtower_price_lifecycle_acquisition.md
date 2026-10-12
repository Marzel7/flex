# Watchtower price lifecycle acquisition

This is the implementation reference for the completed DEV013–DEV020 Watchtower price work. It describes retained, observed facts; it does not claim an absolute historical ATH wherever the provider window is sparse or unobserved. DEV021 did not add a pipeline or activate automatic acquisition.

## Identity and retained evidence first

For a Watchtower assignment, establish the operation, mint, canonical creation timestamp/signature, canonical migration timestamp/signature, and existing retained evidence before requesting data. Reuse qualified evidence rather than reacquiring it. The existing historical cohort journals are rooted by `src.ops.dev016_lifecycle_metrics.COHORT_ROOT`; DEV020 reads completed compact `journal.json` records beneath `COHORT_ROOT / "journals"`. The DEV019 opening journal is `/private/tmp/dev019-watchtower-opening-mc/journal.db` and is read only.

The durable DEV020 journal is `/private/tmp/dev020-watchtower-prepeak/journal.db`. Its `dev020_prepeak_tokens` table stores one row per mint with `selection_json`, terminal/state value, compact `result_json`, and timestamps. `dev020_prepeak_session_control` stores the next eligible session time. These are DEV operational paths, not a canonical production-database replacement.

## Proven acquisition sequence

1. **Opening market cap.** The prospective canonical method is `FIRST_FULL_POST_MIGRATION_SECOND_MC`: Birdeye 1-second market-cap OHLCV, based on the canonical migration timestamp, with the existing strict migration-second qualification. The principal code path is `src/ops/operation_monitor_worker.py` and the provider request builder is `src/ops/token_data_provider_bindings.py`.

   Historical retained evidence may instead use `FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC`. Its facts must be the earliest positive non-padded qualifying 1-minute candle and use the candle **open**, never close. Retain entry timestamp, interval, delay from migration, method, market-cap field, migration signature, and provenance. DEV019 uses `watchtower_historical_openings` with `state='QUALIFIED'`; DEV020 only falls back to it when higher-precedence DEV013/DEV017/canonical opening facts are absent.

2. **Lifecycle peak.** Reuse DEV014 lifecycle facts: `observed_maximum_mc_usd` and `observed_maximum_timestamp`. The resulting metric is an *observed peak*, not a verified absolute lifecycle ATH. Preserve the opening-to-observed-peak multiple and elapsed seconds, along with explicit gaps in retained lifecycle observation.

3. **Pre-peak drawdown.** `scripts/run_dev020_prepeak_session.py` creates one 1-minute MC OHLCV range request from qualified opening timestamp to observed-peak timestamp, then calls `src/ops/dev020_prepeak_metrics.calculate_prepeak_observed_metrics` through `src/ops/watchtower_historical_executor.py`. The reducer retains lowest observed MC/timestamp, low-versus-opening indication, maximum chronological high-to-later-low drawdown, and recovery time only when a later candle close reaches the preceding high. It never orders a candle's own high and low.

4. **Post-peak drawdown.** Reuse retained DEV014 lifecycle evidence for observed post-peak low/timestamp, peak-to-low drawdown, and latest observed MC. Do not manufacture post-peak facts from the DEV020 pre-peak window.

## Technical contract

`build_birdeye_ohlcv_request` in `src/ops/token_data_provider_bindings.py` calls `/defi/v3/ohlcv` with `address`, `chart_type=mcap`, `currency=usd`, `mode=range`, `padding=false`, `time_from`, `time_to`, and `type`. Supported intervals are `1s`, `1m`, `15m`, `1h`, `4h`, and `1d`. Requests require `time_from < time_to`. Market-cap candles are normalized by `src/ops/strict_migration_window.normalize_opening_ohlcv` before DEV020 reduction.

For a 1-minute pre-peak window, at least two valid positive OHLC candles are required. Zero missing intervals gives `OBSERVED_CONTIGUOUS`; two or more candles with a gap gives `OBSERVED_SPARSE`; fewer than two gives `INSUFFICIENT_EVIDENCE`. Sparse means observed-only and never establishes complete provider history. A zero/negative opening-to-peak duration is terminal `INSUFFICIENT_EVIDENCE` without a provider request.

The existing monitor/UI projection is `src/ops/watchtower_research_cohorts.py`, exposed through `src/ops/operator_routes.py` and rendered by `templates/operations_live_monitor.html`. It presents retained lifecycle facts and explicitly labels observed-only terminal values. DEV020's separate compact journal is not presently wired as an automatic future-token API/UI publisher; adding that integration is outside this document and requires separate authorization.

## DEV execution budget and durability

The sole authoritative paid credential label is `BIRDEYE`. DEV launchers load the existing Flex project `.env` into the child process only (for example, `set -a; source /Users/kevinkeaveney/Dev/claude/flex/.env`); never print, write, commit, or substitute the value. `BIRDEYE_KK` and other legacy fallbacks are prohibited.

The proven DEV020 executor is `scripts/run_dev020_prepeak_session.py`:

- single concurrency;
- one request per token, no pagination and no automatic transport retry;
- minimum six seconds between requests;
- real `src/ops/dev_provider_budget.DevProviderBudget` admission against the shared `provider_budget.json` ledger;
- rolling limit 10 requests/minute, session cap 50 tokens, and 60-second durable cooldown;
- compact selection snapshot at most 1 MiB; no raw provider response retention.

State ordering is crash-safe: `PENDING → ADMISSION_INTENT → REQUEST_IN_FLIGHT → terminal`. A known `BudgetDenied` occurs before debit or transport and restores `ADMISSION_INTENT` to `PENDING`. An unexpected admission failure retains `ADMISSION_INTENT`; any request already admitted/in flight is excluded from automatic reacquisition. HTTP failures terminalize as `SAFETY_FAILURE` and stop the session. Existing `OBSERVED_CONTIGUOUS`, `OBSERVED_SPARSE`, and `INSUFFICIENT_EVIDENCE` rows are skipped on resume.

All new storage must retain only compact normalized facts, preserve immutable original evidence, and fail closed below the 500 MB single-file prohibition. The existing DEV020 journal stores request metadata and derived metrics, not raw OHLCV payloads.

## Future-token workflow and current automation boundary

For a new Watchtower token:

1. Verify canonical operation assignment, mint, creation and migration evidence.
2. Reuse a qualified opening MC if present; otherwise obtain it using strict 1-second migration evidence, or the qualified 1-minute-open historical fallback.
3. Persist baseline value, timestamp, method, field and provenance.
4. Reuse/acquire qualified lifecycle observed-peak evidence and record unobserved gaps.
5. If opening precedes an eligible observed peak, obtain/reuse the bounded pre-peak observations and calculate observed-only metrics.
6. Reuse retained post-peak lifecycle evidence where available.
7. Publish only through existing lifecycle projection paths after a separately authorized integration; otherwise record the compact journal result and explicit insufficient-evidence state.

Current automatic monitoring has entry and lifecycle worker integration in `src/ops/operation_monitor_worker.py`, but the DEV020 historical pre-peak executor is a manually invoked, bounded session script. There is no proof that new assignments automatically receive DEV019/DEV020 historical fallback and pre-peak acquisition. Any such automation requires an explicit design, budget, durable-state, API/UI, and activation milestone; do not enable it by treating this document as authorization.

## Interpretation limits

Opening-to-observed-peak, pre-peak drawdown, recovery, and post-peak metrics are direct retained observations or derivations from those observations. They do not prove complete market history, absolute ATH, causation, common controller activity, or manipulation. Keep direct, derived, sparse, and insufficient evidence distinct in all future reporting.
