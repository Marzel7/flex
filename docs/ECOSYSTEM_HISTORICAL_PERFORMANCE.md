# Ecosystem Historical Performance

## Boundary
- **Simulation PnL** = actual paper-traded portfolio equity from `trade_simulations` + `trade_simulation_sells`.
- **Historical Performance** = observed token outcomes from `token_analysis`; it is not traded PnL and never writes fake simulations.

## Materialized tables
- `creator_historical_performance`
- `network_historical_performance`
- `funder_historical_performance`
- `cluster_historical_performance`

## Token source and joins
The token grain is `(creator, mint)` from the union of:
- `token_analysis.earliest_tx_creator`
- `token_analysis.pf_ws_creator`

A mint can therefore belong to whichever creator field is available, without forcing one field to impersonate the other.

## Metrics
Only available observations are used:
- `total_tokens`
- `tokens_with_market_data`
- `simulated_tokens`
- `simulation_coverage_pct`
- `migration_count`, `migration_rate_pct`
- `median_peak_mc`, `max_peak_mc`
- `median_current_mc`
- `current_survival_rate_pct` (`current_mc >= $5k` among market-data tokens)
- `rug_or_dead_rate_pct` (`rug_indicator` present or positive current MC below $5k` among market-data tokens)
- `best_token`, `worst_token`, `latest_token`
- `live_eligible_count` under current `$10k..$100k` simulation entry policy

`median_peak_multiple` and 5x/10x runner counts remain null/zero for now because `token_analysis` does not hold a reliable historical initial/entry market cap. We deliberately do not reconstruct that from later observations.

## Aggregation
- networks: `network_membership`
- funders: `creator_funders`, excluding CEX/infra; expectancy attribution only, not funder cashflow PnL
- farm clusters: `farm_cluster_members` with `token_count > 0`, all roles included

## Scoring
`historical_outcome_score` is a bounded outcome score from migration, current survival, median peak MC, dead/rug rate, and market-data coverage. It is designed for ranking observed ecosystems, not trading ledgers.
