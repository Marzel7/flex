# Profitability Intelligence Layer

## Canonical accounting
For every entity and strategy, use portfolio equity only:

`portfolio_equity = realised_proceeds + unrealised_mtm_open_value`

`portfolio_pnl = portfolio_equity - deployed_capital`

`roi_pct = portfolio_pnl / deployed_capital * 100`

Open positions are never zeroed. For a strategy-position pair:

`unsold_fraction = max(0, 1 - sum(strategy_sell_fraction))`

`unrealised_mtm_open_value = entry_usd * unsold_fraction * (current_market_cap / entry_market_cap)` when the simulation is OPEN, else `0`.

## Materialized/stat tables
- `creator_profitability`
- `network_profitability`
- `funder_profitability`
- `cluster_profitability`

All request handlers read these tables only; expensive joins stay in the analyzer pipeline.

## Creator formulas
- `tokens_launched = count(cascade positions)`
- `deployed = sum(entry_usd)`
- `realised = sum(cascade realised_usd)`
- `unrealised = sum(cascade open MTM)`
- `equity = realised + unrealised`
- `pnl = equity - deployed`
- `win_rate = profitable cascade tokens / tokens`
- `rug_rate = tokens with rug_indicator / tokens`
- `migration_rate = migrated tokens / tokens`
- `avg_peak_multiple = avg(market_cap_highest / entry_market_cap)`
- `avg_hold_duration = avg((closed_at or now) - opened_at)`
- `best/worst token = max/min token cascade pnl`
- strategy breakdowns repeat the same equity math per preserved strategy.

## Network formulas
Networks exclude rows flagged with CEX or infra funders. Metrics are creator rollups:
- `aggregate_deployed/equity/pnl = sums over creator_profitability`
- `roi = aggregate_pnl / aggregate_deployed`
- `median_creator_roi = median(creator roi)`
- `rug/migration/survival = mean creator rates`
- `repeat-launcher = creators with >=2 launches / creators`
- `coordinator_wallet_count = distinct non-CEX, non-infra wallet_clusters funders linked to network creators`

## Funder/coordinator formulas
Funders exclude CEX and infra addresses:
- `creators_funded = distinct creators`
- `total_funded_sol = sum(creator_funders.amount_sol)`
- `aggregate_creator_roi = sum(creator pnl) / sum(creator deployed)`
- `median_creator_roi = median(creator roi)`
- `creator_survival = mean creator survival`
- `profitable_creator = creators with roi > 0 / creators`
- `repeat_rug = creators with >=2 launches and rug_rate >=50% / creators`

## Farm cluster formulas
- creator set = `farm_cluster_members.wallet_role='creator'`
- `cluster_roi = sum(creator pnl) / sum(creator deployed)`
- `creator_roi_distribution = sorted creator roi array`
- `rug_rate = mean creator rug rate`
- `repeat_launch = creators with >=2 launches / creators`
- `token_count = sum creator tokens`
- `profitable_token = sum creator profitable tokens / token_count`
- `network_concentration = max(network creator count) / total network-linked creators`

## Scoring
Creator score is a bounded weighted blend of ROI, equity retention, migration, repeat profitable launches, network quality, funder quality, survival, and penalties for self-funding, farm risk, and rug history. Network score blends ROI, equity retention, migration, repeat launchers, survival, and penalties for rug rate, farm concentration, and self-funding dominance. Both scores are clipped to `0..100`; formulas live in `src/core/profitability_intelligence.py` so rankings remain inspectable rather than magical.

## Pipeline order
1. Existing graph/network analyzers
2. `CreatorProfitabilityAnalyzer`
3. `NetworkProfitabilityAnalyzer`
4. `FunderProfitabilityAnalyzer`
5. `ClusterProfitabilityAnalyzer`

That order lets creators form the atomic rollup, networks feed creator network quality, funders feed creator funder quality, and clusters consume the final creator stats.

## Validation anchors
- known winners should show positive equity and positive cascade ROI
- known rugs should rank low on rug-heavy metrics
- reconcile every table with `realised + unrealised = equity` and `equity - deployed = pnl`
- inspect `Network_4`, `AuPp4...`, `whamNNP9...`, and WATCH portfolios after each refresh
- rankings should remain stable because handlers read materialized tables rather than live rescans

## Current data gaps
- profitability coverage is limited to tokens present in `trade_simulations`; the graph universe is much larger
- `rug_indicator` is only as good as upstream labeling
- survival currently uses `market_cap_current >= 5000`, not a richer liquidity-duration curve
- creator quality includes network/funder quality only after their analyzers refresh
- funder ROI is attributional (creator outcome after funding), not cashflow-realized wallet PnL
