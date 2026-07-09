# FLEX Sidebar Page Design Inventory

Generated from the current shared sidebar in `templates/partials/sidebar.html`, the shared shell in `templates/base_shell.html`, and the linked templates/routes in `src/core/main.py` plus `src/core/flex_dashboard_routes.py`.

This document illustrates the UI designs reachable directly from the sidebar and the secondary pages linked from those sidebar hubs.

## Global Shell

All current sidebar pages use the fixed FLEX shell unless noted otherwise.

```text
+------------------+--------------------------------------------------+
| Fixed sidebar    | Page content                                     |
| 180px            | padding-left: 196px                             |
|                  | dark command-center surface                     |
| FLEX             | page-specific header, controls, tables, panels  |
| Ecosystem OS     |                                                  |
| nav sections     |                                                  |
| health strip     |                                                  |
+------------------+--------------------------------------------------+
```

Core visual language:

| Element | Design |
| --- | --- |
| Background | Near-black cockpit surface, `#050709` to `#0d1117` panels |
| Navigation | Compact fixed sidebar, small uppercase section labels, cyan active marker |
| Cards and panels | Dense data surfaces with 4-8px radius, low-contrast borders |
| Text | System font, tight dashboard typography, muted secondary labels |
| Accents | Cyan primary, purple WATCHTOWER, green/yellow/orange/red status colors |
| Data density | Tables, KPI strips, drawers, modal drilldowns, live refresh states |

## Sidebar Route Map

```text
Live Market
  /                         Live Launches
  /pumpfun                  PumpFun Feed

Intelligence
  /token-intelligence       Tokens
  /creator-analysis         Creators
  /ecosystem                Ecosystems
  /funder-intelligence      Funding
  /predictions              Predictions
  /trading-sim              Portfolio

Review & Investigation
  /approval-queue           Token Review
  /network-approval         Ecosystem Review
  /spike-analysis           Flash Spikes
  /network-diagram          Investigations
  /watchtower               WATCHTOWER
  /watchtower/dashboard     WATCHTOWER Dashboard
  /watchtower/intelligence  WATCHTOWER Operations
  /watchtower/operators     WATCHTOWER Operators
  /watchtower/interceptor   WATCHTOWER Interceptor

Diagnostics
  /transfer-graph           Graph Diagnostics
  /webhook-monitor          Webhook Monitor
  /funding-queue            Funding Queue
  /snapshots                Snapshots
  /vaults                   Vaults
  /usage                    Usage
  /system-health            System Health
  /settings                 Settings
```

## Direct Sidebar Pages

| Sidebar label | Route | Template | Design illustration |
| --- | --- | --- | --- |
| Live Launches | `/` | `dashboard_home.html` | Full cockpit home screen. Command metrics at top, dense live token table, CEX funder activity area, many modal drilldowns for token metrics, creator details, transactions, funding patterns, and network details. |
| PumpFun Feed | `/pumpfun` | `pumpfun_tokens.html` | Three stacked market panels: Near Graduation, Recent Migrations, Recent Births. Compact tables, status chips, live-feed tone. |
| Tokens | `/token-intelligence` | `token_intelligence.html` | Token intelligence workbench. Header plus summary distribution panels, filter controls, sortable table, detail drawer with metrics, sparkline, network and coordinator chips. |
| Creators | `/creator-analysis` | `creator_analysis.html` | Creator dossier and pipeline screen. Search/input led flow, queue stats, pipeline status panel, movement and recipients tables, findings cards, stats grid. |
| Ecosystems | `/ecosystem` | `ecosystem_home.html` | Hub page with a short explanatory hero, card grid for entity rankings, and lens cards for structural graph views. More editorial than the rest, but still in the shell. |
| Funding | `/funder-intelligence` | `funder_intelligence.html` | Orange-accent funding console. KPI band, funder/network summary tables, linked creator counts, and drill-in affordances. |
| Predictions | `/predictions` | `predictions.html` | Decision queue. Topbar and controls, simulation metrics, prediction table, watch-coordination section, side drawer for prediction evidence, feedback, and drill-through links. |
| Portfolio | `/trading-sim` | `trading_sim.html` | Paper-trading PnL desk. Summary topbar, two-panel grid for LIQ caught and paper positions, strategy PnL/equity cards, quote and route details. |
| Token Review | `/approval-queue` | `approval_queue.html` | Review queue with KPI grid and candidate tables. Uses compact network-intelligence table style and action-oriented rows. |
| Ecosystem Review | `/network-approval` | `network_approval.html` | Network approval operations page. Header, seven KPI cards, toolbar filters/actions, approval table with decision workflow. |
| Flash Spikes | `/spike-analysis` | `spike_analysis.html` | Orange flash-analysis report. Header, six stat cards, pattern overview, known heavy operators grid, shared funder network grid, all-token table. |
| Investigations | `/network-diagram` | `network_diagram_index.html` | Investigation launcher. Minimal dark index with fixed-style header and investigation cards linking to graph exhibits. |
| WATCHTOWER | `/watchtower` | `watchtower_intelligence.html` | WATCHTOWER intelligence overview. Purple-accent header, summary content, multiple evidence tables for candidates, creators, and operational signals. |
| WATCHTOWER Dashboard | `/watchtower/dashboard` | `watchtower_dashboard.html` | Full-viewport topology command center. Escapes the normal container, adds a command metric strip, graph controls, D3 topology canvas, operator cards, feeds, and graph detail panels. |
| WATCHTOWER Operations | `/watchtower/intelligence` | `watchtower_operational_intelligence.html` | Operations overview. Wide metric strip, campaign/corridor panels, event feeds, counterparty and relay telemetry surfaces. |
| WATCHTOWER Operators | `/watchtower/operators` | `watchtower_operators.html` | Operator registry and fingerprint workspace. Main operator list with right-side panels, clipboard-friendly wallet rows, scanner/fingerprint states. |
| WATCHTOWER Interceptor | `/watchtower/interceptor` | `watchtower_interceptor_dashboard.html` | CREATE interceptor performance dashboard. Large metric-card grid for armed status, create observations, build latency, slot deltas, benchmark panels, and top-N entry-rate analysis. |
| Graph Diagnostics | `/transfer-graph` | `transfer_graph.html` | Transfer-index diagnostics. Header, analyzer status panel, stats grid, range cards, two-column panels for top sources and overlap candidates, full recent-transfer table. |
| Webhook Monitor | `/webhook-monitor` | `webhook_monitor.html` | Webhook/listener operations monitor. Status-oriented diagnostics page for webhook ingestion, listener cache, funder/creator movement events, and freshness. |
| Funding Queue | `/funding-queue` | `creator_funding_queue.html` | Queue health console. Coverage and DB health panels, queue/timing states, source health, and action controls for early extraction/funding analysis. |
| Snapshots | `/snapshots` | `snapshots.html` | Snapshot diagnostics and storage visibility. Shell page for recent snapshot state and persistence/freshness inspection. |
| Vaults | `/vaults` | `flex_dashboard.html?page=vaults` | Legacy SPA subpage. Header, four stat cards, vault detail table, validation-oriented status colors. |
| Usage | `/usage` | `usage_dashboard.html` | Usage analytics dashboard. Shell-based metrics view for API/RPC/resource consumption with dashboard panels and tables. |
| System Health | `/system-health` | `system_health_dashboard.html` | Deep health dashboard. Metric grids for WebSocket, pool pricing, DB writer, queues, WAL/locks, snapshot coverage, storage cleanup, and source-specific health tables. |
| Settings | `/settings` | `settings.html` | Configuration surface. Shell page for operational settings, controls, and persisted dashboard/runtime toggles. |

## Ecosystem Hub Secondary Pages

The `/ecosystem` page links to these designs. They are not individual sidebar rows, but the sidebar marks the Ecosystems item active for most of them.

| Page | Route | Template | Design illustration |
| --- | --- | --- | --- |
| Ecosystem Creators | `/ecosystem-creators` | `ecosystem_rankings.html` | Ranking table with lens tabs for historical performance vs simulation PnL. |
| Ecosystem Networks | `/ecosystem-networks` | `ecosystem_rankings.html` | Same ranking shell, network entity mode. |
| Ecosystem Funders | `/ecosystem-funders` | `ecosystem_rankings.html` | Same ranking shell, funder entity mode. |
| Farm Structures | `/ecosystem-clusters` | `ecosystem_rankings.html` | Same ranking shell, cluster entity mode, no simulation tab. |
| Overview | `/network-intelligence` | `network_intelligence.html` | Care-card framing, KPI grid, funnel table, released networks, farm clusters, second-hop tables, detail drawers. |
| Coordinators | `/coordinators` | `coordinators.html` | Coordinator wallet table with shared ecosystem lens nav and wallet drilldowns. |
| Shared Funding | `/coordinated-funders` | `coordinated_funders.html` | Shared-funder table view, total count in header, ecosystem lens nav. |
| Farm Structure | `/clusters` | `clusters.html` | Graph farm cluster table with drawer metrics for funders, creators, risk score, strength, SOL volume, and transfers. |
| Graph Evidence | `/transfer-graph` | `transfer_graph.html` | Reuses the sidebar Graph Diagnostics page as raw evidence lens. |
| Released Networks | `/networks` | `networks_dashboard.html` | Legacy networks explorer with stats cards, network rows, and modal detail view. |

## Investigation Secondary Pages

The `/network-diagram` index links to specialized graph exhibits. These pages use custom graph layouts rather than the normal shell content density.

| Page | Route | Template | Design illustration |
| --- | --- | --- | --- |
| HTX Syndicate | `/network-diagram/htx` | `network_diagram_htx.html` | Static/dynamic D3 network exhibit with live summary grid and blue investigation header. |
| OKX Syndicate | `/network-diagram/okx` | `network_diagram_okx.html` | D3 graph exhibit with tabbed panels for outcomes, signals, and tokens plus live summary grid. |
| WATCHTOWER Infrastructure | `/network-diagram/watchtower` | `network_diagram_watchtower.html` | Purple WATCHTOWER graph exhibit mapping coordinated launch infrastructure. |
| Coinbase Cluster | `/network-diagram/coinbase-cluster` | `network_diagram_coinbase_cluster.html` | D3-style network exhibit for a specific exchange-linked cluster. |

## Legacy SPA Subpages Still Backed By `flex_dashboard.html`

Several older dashboard views still render through `flex_dashboard.html` and use client-side route functions instead of standalone templates.

| Page | Entry route | Render mode | Design illustration |
| --- | --- | --- | --- |
| Early Signal Predictions | `/early-signals` | `flex_dashboard.html?page=early_signals` | Header, prediction stat cards, likely rug/runner/unknown sections. Not currently visible in the shared sidebar, but still routable. |
| Token Behaviour | `/token-behaviour` | `flex_dashboard.html?page=token_behaviour` | Header, active/finalized stat cards, category cards with live/finalized token lists. Not currently visible in the shared sidebar. |
| Vaults | `/vaults` | `flex_dashboard.html?page=vaults` | Visible in Diagnostics sidebar. Uses the same SPA card/table style. |

## Page Family Sketches

### Live Market

```text
[Header / command metrics]
[Filters]
[Live token table ---------------------------------------------------]
[CEX / migration / modal drilldowns layered over the table]
```

### Intelligence Workbench

```text
[Page header]
[KPI or distribution panels]
[Filters / tabs / toolbar]
[Dense table]
[Drawer or modal: entity detail, graph context, evidence]
```

### Review Queue

```text
[Page header]
[Status KPI strip]
[Queue controls]
[Decision table]
[Approve / reject / evidence actions]
```

### WATCHTOWER Command Center

```text
[Purple command metrics across top]
[Graph controls and topology canvas]
[Operator/campaign panels]
[Live feeds and detail panes]
```

### Diagnostics

```text
[Health/freshness header]
[Metric cards]
[Subsystem panels]
[Recent events or source tables]
```

## Maintenance Notes

When a sidebar item changes, update this document in three places:

1. `Sidebar Route Map`
2. `Direct Sidebar Pages`
3. The relevant secondary-page section if the route is a hub page

Primary source files:

| Concern | File |
| --- | --- |
| Sidebar sections and labels | `templates/partials/sidebar.html` |
| Shared shell and base visual system | `templates/base_shell.html` |
| Main Flask page routes | `src/core/main.py` |
| Legacy FLEX SPA routes | `src/core/flex_dashboard_routes.py` |
| Ecosystem secondary nav | `templates/partials/ecosystem_lens_nav.html` |
