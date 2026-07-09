# FLEX Data & UI Reference

_Last updated: 2026-05-15_

## 1. Mental model

FLEX now has several distinct intelligence layers. They should be kept conceptually separate even when surfaced together in the UI.

```text
Raw chain / webhook / pool data
        ↓
Token state + funding extraction
        ↓
Graph intelligence + risk intelligence
        ↓
Two performance ledgers
   1. Simulation PnL          = what our actual paper strategy traded
   2. Historical Performance  = what the wider ecosystem historically produced
```

The most important boundary:

- **Simulation PnL** uses `trade_simulations` + `trade_simulation_sells` and answers: _How did our executed paper-trading policy perform?_
- **Historical Ecosystem Performance** uses `token_analysis` + archived first-observed prices and answers: _What kinds of outcomes do creators, networks, funders, and clusters historically produce?_

Do not merge these ledgers or backfill fake simulation trades.

---

# 2. Core live datasets we continuously collect

## A. Token lifecycle, pricing, and pool data

### Main tables
- `token_analysis`
- `tracked_tokens`
- `token_pool_accounts`
- `token_price_snapshots`
- `token_snapshot_counts`
- `token_market_cap_peaks`
- `token_liquidity_snapshots`
- `token_liquidity_health`
- `token_liquidity_risks`
- `token_behavior`
- `token_behavior_history`
- `token_outcomes`
- `token_lifecycle_snapshots`
- `pumpfun_migration_verification`
- `pumpfun_pre_migration_signals`

### What we capture
- creator identity (`earliest_tx_creator`, `pf_ws_creator`)
- pool discovery / pool vault state
- current price and market cap
- peak market cap
- migration state and migration transaction
- lifecycle stage
- liquidity snapshots and risk signals
- token behavior labels: rug, runner, faded runner, low peak, etc.
- first permanent market anchor from now onward:
  - `first_observed_mc`
  - `first_observed_price`
  - `first_observed_at`
  - `first_observed_source`
  - `first_observed_confidence`

### UI surfaces
- `/` Live
- `/pumpfun`
- `/token-intelligence`
- `/vaults`
- `/snapshots`
- `/system-health`

### Current use
- live token display
- migration detection
- risk prediction inputs
- price/peak tracking
- liquidity and behavior classification
- historical ecosystem multiples once first-observed coverage grows

### Important caveat
`token_price_snapshots` is a prunable history table. First observed MC is now permanently copied into `token_analysis` because old snapshot histories may be deleted by retention.

---

## B. Funding extraction and transfer data

### Main tables
- `creator_funders`
- `creator_funding_queue`
- `creator_funding_graph`
- `funding_chains`
- `funding_networks`
- `funding_network_members`
- `funding_network_shared_tokens`
- `funder_incoming_transfers`
- `funder_outgoing_transfers`
- `creator_inbound_transfers`
- `creator_outgoing_transfers`
- `creator_outbound_classifications`
- `sol_transfers`
- `transfer_index`
- `funder_upstream_links`
- `creator_second_hop`
- `upstream_network_bridge`
- `second_hop_lite_queue`

### What we capture
- who funded each creator
- transfer amounts and timing
- CEX / infra classifications
- creator-to-funder and funder-to-upstream chains
- second-hop and upstream hub structure
- outgoing creator behavior after funding
- indexed native SOL graph edges

### UI surfaces
- `/transfer-graph`
- `/creator-analysis`
- `/funder-intelligence`
- `/top-funding-hubs`
- `/funding-queue` / Performance
- `/webhook-monitor`

### Current use
- graph construction
- creator/network risk scoring
- funding-chain analysis
- creator classification
- network membership construction
- token prediction features

---

## C. Creator, graph, and coordination intelligence

### Main tables
- `network_membership`
- `networks_release`
- `funder_network_map`
- `wallet_clusters`
- `funder_overlap`
- `farm_clusters`
- `farm_cluster_members`
- `farm_cluster_edges`
- `coordinated_creator_edges`
- `creator_c2c_edges`
- `creator_self_funding`
- `dev_reputation`
- `creator_risk_scores`
- `network_risk_scores`
- `risk_score_history`
- `network_coordinators`
- `coordinated_funders`
- `creator_to_creator_networks`
- `super_clusters`
- `unified_creator_clusters`

### What we capture
- network membership and stable released networks
- shared-funder coordination
- wallet clusters / coordinator wallets
- funder overlap ratios
- farm clusters and farm membership
- creator-to-creator transfer edges
- self-funding behavior
- creator reputation and risk
- cross-funder coordinators

### UI surfaces
- `/networks`
- `/network-intelligence`
- `/clusters`
- `/coordinators`
- `/coordinated-funders`
- `/risk-scoring`
- `/creator-analysis`
- `/network-diagram`

### Current use
- network discovery
- risk scoring
- launch prediction
- network naming
- coordinator detection
- graph explanations
- simulation profitability enrichment (`coordinator_wallet_count`)

---

## D. Prediction and launch intelligence

### Main tables
- `token_prediction_scores`
- `token_prediction_events`
- `token_prediction_outcomes`
- `prediction_features`
- `token_early_signals`
- `master_launch_signals`
- `launch_watchlist`
- `launch_detection_history`
- `launch_waves`
- `launch_wave_creators`
- `org_launch_predictions`
- `org_launch_windows`
- `org_momentum_history`
- `network_scores`
- `network_score_history`

### What we capture
- token-level labels: LOW_RISK, WATCH, LIKELY_DUMP, HIGH_RISK, LIQUIDATION_RISK, etc.
- prediction evidence and outcomes
- launch cadence and wave behavior
- network and organization scores

### UI surfaces
- `/predictions`
- `/approval-queue`
- `/network-approval`
- `/network-intelligence`
- `/risk-scoring`

### Current use
- actionable token ranking
- auto-paper-buy triggering for selected risk buckets
- launch/network review queues
- outcome tracking and rescoring

---

## E. Simulation PnL ledger

### Main tables
- `trade_simulations`
- `trade_simulation_sells`
- `trade_simulation_events`
- `trade_simulation_claims`
- `liq_caught`

### Materialized intelligence tables
- `creator_profitability`
- `network_profitability`
- `funder_profitability`
- `cluster_profitability`

### What we capture
- actual paper entries allowed by live policy
- strategy exits for cascade / targets / peak / watch trailing
- realised proceeds
- unrealised MTM value for open positions
- full portfolio-equity PnL

### UI surfaces
- `/trading-sim`
- inside Ecosystem pages via **Simulation PnL** lens

### Current use
- strategy evaluation
- creator/network/funder expectancy under actual policy
- portfolio-equity accounting

### Boundary
This ledger is intentionally sparse and must stay honest. It is not a historical backfill ledger.

---

## F. Historical Ecosystem Performance ledger

### Materialized tables
- `creator_historical_performance`
- `network_historical_performance`
- `funder_historical_performance`
- `cluster_historical_performance`

### What we compute
- total historical tokens
- market-data coverage
- simulation coverage
- migration rates
- survival / dead-or-rug rates
- peak/current multiples when first-observed MC exists
- 2x / 5x / 10x runner counts
- historical outcome scores

### UI surfaces
- `/ecosystem`
- `/ecosystem-creators`
- `/ecosystem-networks`
- `/ecosystem-funders`
- `/ecosystem-clusters`

### Current use
- ecosystem-wide comparison beyond the simulation universe
- creator/network/funder/cluster historical ranking
- coverage-aware interpretation

### Important caveat
Historical multiple coverage is currently thin for older tokens because permanent first-observed MC retention only began recently. New launches should become much richer over time.

---

## G. Data we collect but do not yet fully exploit

This is the high-leverage backlog.

### 1. Coordinator / coordinated-funder intelligence is under-fused

We collect:
- `wallet_clusters`
- `funder_overlap`
- `coordinated_creator_edges`
- `network_coordinators`
- `coordinated_funders`

Used today in risk scoring and graph views, but not yet strongly folded into the new Ecosystem pages.

#### Opportunity
Add to Ecosystem Funders / Networks:
- coordinator flag
- creator reach
- overlap score
- coordinated-funder concentration
- operator concentration
- organic vs coordinator-driven distinction

This would join _who organizes the ecosystem_ with _who historically performs_.

### 2. Creator-to-creator flow data is underused

We collect:
- `creator_c2c_edges`
- `creator_to_creator_networks`
- `creator_outgoing_transfers`
- `creator_outbound_classifications`

#### Opportunity
Use it for:
- capital recycling signatures
- creator role classification
- hidden operator chains
- ecosystem propagation paths

### 3. Second-hop / upstream hub data is rich but mostly defensive

We collect:
- `funder_upstream_links`
- `creator_second_hop`
- `upstream_network_bridge`
- `monitored_upstream_hubs`

#### Opportunity
Use it not only for risk but for:
- upstream quality scoring
- funder lineage
- identifying repeat capital sources behind profitable vs toxic ecosystems

### 4. Liquidity behavior is collected more richly than it is surfaced

We collect:
- `token_liquidity_snapshots`
- `token_liquidity_health`
- `token_liquidity_risks`
- `cluster_exit_events`
- `liq_caught`

#### Opportunity
Add ecosystem-level metrics:
- low-liquidity survival
- liquidity retention
- liquidation cadence
- migration quality vs post-migration extraction

### 5. Token behavior / outcome labels could enrich ecosystem scoring more

We collect:
- `token_behavior`
- `token_behavior_history`
- `token_outcomes`

#### Opportunity
Historical ecosystem scores should eventually separate:
- runners
- faded runners
- immediate rugs
- slow rugs
- low-peak churn

Raw migration rate is useful, but behavior composition is much more explanatory.

### 6. Pump bot intelligence is adjacent but not yet fused

We collect:
- `pump_bot_wallets`
- `pump_bot_signals`

#### Opportunity
Measure whether particular creators/networks are repeatedly accompanied by bot support, and whether that correlates with false positive “quality.”

### 7. AML / address classification data is mostly auxiliary today

We collect:
- `blocksec_aml_cache`
- `address_labels`
- `address_tags`
- `address_domains`
- `address_classification`
- `upstream_account_classification`

#### Opportunity
Use more visibly in funding and ecosystem views:
- known entity lineage
- suspicious upstream quality
- infrastructure masking

### 8. Historical first-price moat is new and not yet dense

We now collect durable:
- `first_observed_mc`
- `first_observed_price`
- `first_observed_at`
- source/confidence

#### Opportunity
As coverage accumulates, historical ecosystem performance becomes much sharper:
- true median peak multiple
- current multiple
- repeat 5x / 10x creators
- funders/networks that consistently originate genuine runners

### 9. Simulation vs historical gap itself is an underused signal

We now know:
- which ecosystems produce outcomes historically
- which were actually selected by the live trading policy

#### Opportunity
Measure:
- profitable historical ecosystems the strategy missed
- risky ecosystems the strategy correctly ignored
- selection bias by network / creator / funder type

---

# 3. UI map

## Token / graph / funding surfaces
- `/` — live tokens
- `/pumpfun` — PumpFun tokens
- `/networks` — released networks
- `/network-intelligence` — joined network / graph context
- `/transfer-graph` — indexed transfer graph and analyzer freshness
- `/clusters` — farm clusters
- `/coordinators` — coordinator wallets from wallet clusters
- `/coordinated-funders` — coordinated funder / network coordinator analysis
- `/top-funding-hubs` — upstream hubs
- `/creator-analysis` — creator scans, findings, transfers, network context
- `/webhook-monitor` — webhook transfer ingestion
- `/pump-bots` — bot monitoring

## Intelligence surfaces
- `/approval-queue`
- `/network-approval`
- `/token-intelligence`
- `/risk-scoring`
- `/predictions`
- `/trading-sim`
- `/funder-intelligence`
- `/spike-analysis`
- `/network-diagram`

## Ecosystem surfaces
- `/ecosystem` — landing page
- `/ecosystem-creators`
- `/ecosystem-networks`
- `/ecosystem-funders`
- `/ecosystem-clusters`

Each Ecosystem entity view now supports two lenses where applicable:
- **Historical Performance**
- **Simulation PnL**

## System surfaces
- `/system-health`
- `/vaults`
- `/snapshots`
- `/usage`
- `/funding-queue`
- `/settings`

---

# 4. Current strongest capabilities

1. **Live token detection + pricing**
2. **Funding-chain extraction**
3. **Creator / network / farm / coordinator graph intelligence**
4. **Risk rescoring across creators and token predictions**
5. **Correct portfolio-equity simulation PnL**
6. **Historical ecosystem coverage beyond the simulation universe**
7. **Permanent first-observed MC capture from this point forward**

---

# 5. Current biggest gaps

1. Older historical tokens mostly lack permanent first-observed MC anchors.
2. Coordinator intelligence is powerful but not yet sufficiently fused into Ecosystem scoring.
3. Funder and cluster historical materializations still need reliable refresh completion under live DB contention.
4. Historical behavior composition should become more nuanced than migration/survival alone.
5. Some older pages overlap conceptually and could be consolidated as intelligence matures.

---

# 6. Recommended next intelligence integrations

## Priority 1 — Fuse graph organization into Ecosystem
- coordinator concentration
- shared-funder density
- overlap risk
- organic vs operator-driven networks

## Priority 2 — Enrich historical outcome quality
- behavior composition
- liquidity retention
- current multiple / peak multiple once coverage improves

## Priority 3 — Compare strategy selection against ecosystem truth
- ecosystems that perform historically but were never traded
- ecosystems our policy correctly avoided
- missed-opportunity vs avoided-loss analysis

## Priority 4 — Consolidate UI around concepts, not implementation history
- Ecosystem as master area
- Risk / Graph / Funding as lenses
- reduce orphan pages once their best signals are folded into primary workflows

---

# 7. One-sentence summary

FLEX now collects a deep stack of token, price, funding, graph, coordination, risk, prediction, and strategy-performance data; the main remaining opportunity is not more collection, but fusing the underused graph and behavior layers into ecosystem-quality intelligence that explains both _who performs_ and _how the system behind them is organized_.
