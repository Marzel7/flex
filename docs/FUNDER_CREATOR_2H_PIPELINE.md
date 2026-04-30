# Funder, Creator, 2H, and IRC Pipeline

This document explains how the app discovers creator funders, funder upstreams, second-hop network links, and Intelligence Refresh Candidates.

## Core Terms

`creator`
: The wallet identified as launching/creating a token.

`funder`
: A wallet that sent meaningful SOL to a creator before or around migration.

`upstream`
: A wallet that funded a funder. This is the funder-of-funder layer.

`2H` / `second-hop`
: The upstream layer used to detect shared funding hubs across funders, creators, and networks.

`IRC`
: Intelligence Refresh Candidates. This is the watchlist/approval system in `intelligence_refresh_candidates`.

## Main Tables

`creator_funding_queue`
: Durable queue of creators/mints waiting for creator funding extraction.

`creator_funders`
: First-hop creator funding result: creator -> funder.

`transfer_index`
: Indexed native SOL transfers seen during creator/funder extraction. This is the shared transfer evidence table.

`second_hop_lite_queue`
: Queue of funders selected for upstream scanning.

`funder_upstream_links`
: Second-hop result: funder -> upstream.

`upstream_network_bridge`
: Network-level bridge rows showing upstream wallets that connect multiple networks.

`creator_second_hop`
: Creator-level second-hop enrichment from upstream bridge evidence.

`network_membership`
: Creator/network membership derived mostly from shared funders.

`funder_network_map`
: Funder/network map derived from network membership.

`networks_release`
: UI-ready network summary table.

`intelligence_refresh_candidates`
: IRC watchlist/approval/scanned state for creators and funders.

## First-Hop Creator Funding

Purpose: find wallets that funded the token creator.

Code path:

`src/core/pumpfun_curve_listener.py`

`src/extractors/realtime_creator_funding_extractor.py`

Flow:

1. A token migration/creator discovery event enqueues a row in `creator_funding_queue`.
2. The listener queue worker claims pending jobs.
3. It calls `extract_funding_for_new_token(creator, migration_timestamp, create_tx_signature, mint)`.
4. The extractor scans bounded creator history around the migration window.
5. Meaningful inbound SOL transfers are written to `creator_funders`.
6. Native SOL transfer evidence is also written to `transfer_index`.
7. Dust is filtered from `creator_funders`, but may still appear in indexed evidence depending on extraction path.

Current intent:

- This is not a full lifetime scan of the creator.
- It is bounded to save RPC.
- It is designed to identify meaningful pre-migration funding, not every historical transfer visible on explorers.

Important setting:

`auto_extract_funders`

This setting does not disable first-hop creator funding extraction. It gates the heavier per-funder transfer extraction step after creator funders are found.

## Auto Extract Funders Setting

UI label:

`Settings -> Token Pipeline -> Auto Extract Funders`

DB setting:

`listener_settings.setting_key = 'auto_extract_funders'`

What it controls:

- The extra `extract_funder_transfers_async(creator)` step after creator funding completes.
- This older/deeper funder transfer analysis writes to tables such as `funder_incoming_transfers` and `funder_outgoing_transfers`.

What it does not control:

- The first-hop creator funding scan.
- IRC watchlist building.
- Second-hop SQL expansion.
- Phase 2 Lite upstream scanning.

## Local Graph Analyzers

Purpose: rebuild local graph-derived tables from existing SQLite data.

No RPC intent:

- These are mainly DB-local graph rebuilds.
- They consume existing tables such as `creator_funders` and `transfer_index`.

Main outputs:

- `wallet_clusters`
- `farm_clusters`
- `funder_overlap`
- `coordinated_creator_edges`
- `creator_c2c_edges`
- `network_membership`
- `funder_network_map`
- `networks_release`

Manual UI action:

`Settings -> Intelligence Auto-Approval -> Refresh Local Graph`

Endpoint:

`POST /api/run-graph-analyzers`

Implementation:

`src/core/graph_analyzer_api.py`

This manual UI endpoint runs a local subset:

```text
WalletClusteringEngine
DevReputationUpdater
FunderOverlapAnalyzer
GraphDevFarmDetectionEngine
CoordinatedEdgesBuilder
C2CEdgeBuilder
NetworkMembershipBuilder
NetworksReleaseBuilder
```

It does not run IRC or second-hop RPC scanning.

## Full Scheduled Analyzer Pipeline

Purpose: run the full scheduled intelligence pipeline.

Script:

`scripts/run_graph_analyzers.py`

Intended cron cadence:

Every 30 minutes in the current UI copy, though the script comment also shows a 10-minute cron example. Treat the real deployed cron as authoritative.

Current full analyzer order:

```text
WalletClusteringEngine
DevReputationUpdater
FunderOverlapAnalyzer
GraphDevFarmDetectionEngine
CoordinatedEdgesBuilder
C2CEdgeBuilder
NetworkMembershipBuilder
SecondHopLiteWorker
SecondHopExpansionBuilder
UpstreamExpansionBuilder
NetworksReleaseBuilder
IntelligenceRefreshCandidateBuilder
```

This scheduled path updates both:

- Local graph tables.
- Second-hop / upstream / IRC tables.

## Phase 2 Lite Upstream Scanning

Purpose: discover upstream wallets that funded known funders.

Queue:

`second_hop_lite_queue`

Worker:

`src/core/second_hop_lite_worker.py`

Output:

`funder_upstream_links`

Setting:

`listener_settings.setting_key = 'second_hop_lite_enabled'`

What it does:

1. Picks funders from `second_hop_lite_queue`.
2. Uses RPC to fetch recent funder transactions.
3. Extracts inbound SOL transfers into the funder.
4. Writes funder -> upstream links to `funder_upstream_links`.
5. Uses cache and scan limits to avoid excessive RPC.

Important limits:

- It is a bounded recent-history scan.
- It does not scan all lifetime activity.
- It is focused on practical upstream discovery.
- Default cron-sized batch limits are intentionally small: 25 funders, 750 RPC calls, and 35 transactions per funder. Override with `SHL_MAX_FUNDER_SCANS`, `SHL_MAX_RPC_CALLS`, or `SHL_MAX_TX_PER_FUNDER` only for explicit backfills.

## Second-Hop SQL Expansion

Purpose: turn upstream links into network and creator second-hop signals.

Builder:

`src/core/second_hop_builder.py`

Setting:

`listener_settings.setting_key = 'second_hop_sql_enabled'`

Inputs:

- `creator_funders`
- `transfer_index`
- `funder_network_map`
- `funder_upstream_links`

Outputs:

- `funder_upstream_links` rows from SQL-derived transfer evidence.
- `upstream_network_bridge`
- `creator_second_hop`
- `monitored_upstream_hubs`

What it does:

1. Finds upstream wallets that sent SOL to known non-CEX funders.
2. Filters and excludes CEX/infra/program/pool addresses.
3. Scores upstreams that bridge multiple networks.
4. Writes bridge evidence to `upstream_network_bridge`.
5. Writes creator-level second-hop rows to `creator_second_hop`.
6. Upserts significant hubs to `monitored_upstream_hubs`.

## Upstream Expansion

Purpose: keep expanding around significant upstream hubs.

Builder:

`src/core/upstream_expansion_builder.py`

Output:

More rows in `second_hop_lite_queue`.

What it does:

1. Reads active `monitored_upstream_hubs`.
2. Finds downstream funders linked to those hubs.
3. Enqueues high-signal funders for Phase 2 Lite scanning.
4. Uses cooldowns and cache freshness to avoid repeated scans.

This builder makes zero RPC calls itself.

## IRC: Intelligence Refresh Candidates

Purpose: decide which creators and funders deserve deeper attention.

Builder:

`src/core/intelligence_refresh.py`

Table:

`intelligence_refresh_candidates`

Candidate states:

- `watchlist`
- `approved`
- `scanned`
- `ignored`
- `failed`

What IRC does:

1. Scores creators from existing DB evidence.
2. Scores funders from existing DB evidence.
3. Inserts or updates watchlist rows.
4. Auto-approves creators if enabled settings match.
5. Enqueues selected creator funders into `second_hop_lite_queue`.
6. Marks approved items as scanned after their queued work completes.

What IRC does not do:

- It does not directly call RPC in the candidate builder.
- It does not itself parse upstream transactions.
- It delegates actual upstream scanning to `SecondHopLiteWorker`.

Auto-approval settings:

- `auto_approve_high_priority`
- `auto_approve_network_member`
- `auto_approve_shared_funders`

These live in `migration_settings.json` via `/api/migration-settings`.

## Post-Creator Extraction Refresh

After a creator funding job completes, the listener runs a targeted refresh.

Code:

`pumpfun_curve_listener.py -> _post_extraction_intelligence_refresh`

What it runs:

```text
Targeted IRC upsert for this creator
SecondHopExpansionBuilder
NetworksReleaseBuilder
Relationship event diff
```

What it does not run:

```text
SecondHopLiteWorker
Full wallet/farm clustering suite
Full IRC rebuild
```

Reason:

This gives the UI fast local/network updates after a creator is processed without triggering heavier RPC work immediately.

## Manual Run Buttons

### Settings: Refresh Local Graph

Location:

`/settings`

Section:

`Intelligence Auto-Approval`

Endpoint:

`POST /api/run-graph-analyzers`

Runs:

Local graph subset only.

Does not run:

- IRC
- Phase 2 Lite RPC
- second-hop upstream discovery

### Network Intelligence: Phase 2 Run Now

Location:

`/network-intelligence`

Endpoint:

`POST /api/second-hop-lite/run-now`

Runs:

```text
SecondHopLiteWorker
SecondHopExpansionBuilder
NetworksReleaseBuilder
IntelligenceRefreshCandidateBuilder
```

This is the manual heavier second-hop/IRC path.

## Settings Summary

`auto_extract_funders`
: Controls extra per-funder transfer extraction after first-hop creator funding. Does not disable creator funding extraction.

`second_hop_lite_enabled`
: Controls Phase 2 Lite RPC scanning of funders for upstream wallets.

`second_hop_sql_enabled`
: Controls SQL second-hop expansion and bridge building.

`auto_approve_high_priority`
: IRC may auto-approve high-priority creators.

`auto_approve_network_member`
: IRC may auto-approve creators already in a known network.

`auto_approve_shared_funders`
: IRC may auto-approve creators whose funders also fund other creators.

## Practical Mental Model

```text
Migration detected
  -> creator_funding_queue
  -> extract creator funders
  -> creator_funders + transfer_index
  -> targeted local refresh

Scheduled full analyzer
  -> local graph rebuild
  -> Phase 2 Lite scans queued funders
  -> second-hop bridge rebuild
  -> upstream hub expansion
  -> networks_release
  -> IRC watchlist/approval updates

IRC
  -> watches and approves targets
  -> can enqueue funders for Phase 2
  -> does not itself perform RPC scanning
```

## Common Confusions

### Why do upstream accounts appear when Auto Extract Funders is off?

Because upstream discovery is controlled by `second_hop_lite_enabled` and `second_hop_sql_enabled`, not by `auto_extract_funders`.

### Does Refresh Local Graph run IRC?

No. It refreshes local graph tables only.

### Does the scheduled analyzer update local graph info?

Yes. The scheduled full analyzer updates local graph tables and then second-hop/IRC tables.

### Does the app scan an entire creator or funder lifetime?

No. The app intentionally uses bounded scans, caches, dust thresholds, page limits, and lookback windows to control RPC cost.
